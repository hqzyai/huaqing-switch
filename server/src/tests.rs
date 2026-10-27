use super::*;
use axum::{
    body::{to_bytes, Body},
    http::Request,
};
use tower::ServiceExt;
fn fixture() -> (tempfile::TempDir, App) {
    let temp = tempfile::tempdir().unwrap();
    std::fs::write(
        temp.path().join("index.html"),
        "<!doctype html><title>HQ fixture</title>",
    )
    .unwrap();
    let config = Config {
        web_bind: "127.0.0.1:8080".parse().unwrap(),
        gateway_bind: "127.0.0.1:17621".parse().unwrap(),
        gateway_url: "http://switch.test:17621".into(),
        public_origin: "https://switch.example".into(),
        data_dir: temp.path().into(),
        static_dir: temp.path().into(),
        admin_token: "a".repeat(64),
        inference_token: "b".repeat(64),
    };
    let app = App::open(config).unwrap();
    (temp, app)
}
fn command_request(name: &str, args: Value, token: &str) -> Request<Body> {
    Request::builder()
        .method("POST")
        .uri(format!("/api/commands/{name}"))
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {token}"))
        .body(Body::from(args.to_string()))
        .unwrap()
}
async fn value(response: Response) -> Value {
    serde_json::from_slice(&to_bytes(response.into_body(), 1024 * 1024).await.unwrap()).unwrap()
}
fn connection(url: &str) -> Value {
    json!({"name":"fixture","protocol":"openai-chat","baseUrl":url,"model":"fixture-model","apiKey":"upstream-secret-never-in-http-admin-results","contextWindow":1000000,"maxOutputTokens":393216,"supportsImages":false})
}
#[tokio::test]
async fn web_whitelist_roles_csrf_cookie_and_session_logout() {
    let (_temp, app) = fixture();
    let denied = app
        .web()
        .oneshot(command_request(
            "hq_get_connections",
            json!({}),
            &app.config.inference_token,
        ))
        .await
        .unwrap();
    assert_eq!(denied.status(), StatusCode::FORBIDDEN);
    let mut request = command_request("hq_get_connections", json!({}), &app.config.inference_token);
    request
        .headers_mut()
        .insert("origin", "https://switch.example".parse().unwrap());
    request
        .headers_mut()
        .insert("x-hq-csrf", "1".parse().unwrap());
    assert_eq!(
        app.web().oneshot(request).await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        app.web()
            .oneshot(command_request(
                "delete_all_providers",
                json!({}),
                &app.config.admin_token
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::NOT_FOUND
    );
    let mut request = Request::builder()
        .method("POST")
        .uri("/api/session")
        .header("content-type", "application/json")
        .header("origin", "https://evil.example")
        .header("x-hq-csrf", "1")
        .body(Body::from(
            json!({"token":app.config.admin_token}).to_string(),
        ))
        .unwrap();
    request
        .extensions_mut()
        .insert(ConnectInfo("127.0.0.1:1000".parse::<SocketAddr>().unwrap()));
    assert_eq!(
        app.web().oneshot(request).await.unwrap().status(),
        StatusCode::FORBIDDEN
    );
    let mut request = Request::builder()
        .method("POST")
        .uri("/api/session")
        .header("content-type", "application/json")
        .header("origin", "https://switch.example")
        .header("x-hq-csrf", "1")
        .body(Body::from(
            json!({"token":app.config.admin_token}).to_string(),
        ))
        .unwrap();
    request
        .extensions_mut()
        .insert(ConnectInfo("127.0.0.1:1000".parse::<SocketAddr>().unwrap()));
    let response = app.web().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let cookie = response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    assert_eq!(
        value(
            app.web()
                .oneshot(
                    Request::builder()
                        .uri("/api/session")
                        .header("cookie", &cookie)
                        .body(Body::empty())
                        .unwrap()
                )
                .await
                .unwrap()
        )
        .await["authenticated"],
        true
    );
    let request = Request::builder()
        .method("POST")
        .uri("/api/commands/hq_get_connections")
        .header("content-type", "application/json")
        .header("origin", "https://switch.example")
        .header("cookie", &cookie)
        .body(Body::from("{}"))
        .unwrap();
    assert_eq!(
        app.web().oneshot(request).await.unwrap().status(),
        StatusCode::FORBIDDEN
    );
    let request = Request::builder()
        .method("POST")
        .uri("/api/commands/hq_get_connections")
        .header("content-type", "application/json")
        .header("origin", "https://switch.example")
        .header("x-hq-csrf", "1")
        .header("cookie", &cookie)
        .body(Body::from("{}"))
        .unwrap();
    assert_eq!(
        app.web().oneshot(request).await.unwrap().status(),
        StatusCode::OK
    );
    let request = Request::builder()
        .method("DELETE")
        .uri("/api/session")
        .header("origin", "https://switch.example")
        .header("x-hq-csrf", "1")
        .header("cookie", &cookie)
        .body(Body::empty())
        .unwrap();
    let response = app.web().oneshot(request).await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert!(response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .contains("Max-Age=0"));
    assert_eq!(
        value(
            app.web()
                .oneshot(
                    Request::builder()
                        .uri("/api/session")
                        .header("cookie", &cookie)
                        .body(Body::empty())
                        .unwrap()
                )
                .await
                .unwrap()
        )
        .await["authenticated"],
        false
    );
}
#[tokio::test]
async fn management_persists_revision_secrets_and_usage_across_restart() {
    let (temp, app) = fixture();
    let config = app.config.clone();
    let response = app
        .web()
        .oneshot(command_request(
            "hq_save_connection",
            json!({"revision":0,"connection":connection("https://fixture.invalid/v1")}),
            &config.admin_token,
        ))
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let saved = value(response).await;
    assert!(!saved.to_string().contains("upstream-secret"));
    let id = saved["connections"][0]["id"].as_str().unwrap();
    assert_eq!(
        app.web()
            .oneshot(command_request(
                "hq_save_connection",
                json!({"revision":0,"connection":connection("https://fixture.invalid/v1")}),
                &config.admin_token
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::BAD_REQUEST
    );
    let selected = value(
        app.web()
            .oneshot(command_request(
                "hq_select_connection",
                json!({"revision":1,"id":id}),
                &config.admin_token,
            ))
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(selected["activeId"], id);
    drop(app);
    let app = App::open(config.clone()).unwrap();
    let snapshot = value(
        app.web()
            .oneshot(command_request(
                "hq_get_connections",
                json!({}),
                &config.admin_token,
            ))
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(snapshot["revision"], 2);
    assert_eq!(snapshot["activeId"], id);
    assert_eq!(snapshot["connections"][0]["apiKeyConfigured"], true);
    assert!(!snapshot.to_string().contains("upstream-secret"));
    let report = value(
        app.web()
            .oneshot(command_request(
                "hq_get_usage",
                json!({"filter":{}}),
                &config.admin_token,
            ))
            .await
            .unwrap(),
    )
    .await;
    assert_eq!(report["summary"]["requests"], 0);
    assert_eq!(
        app.web()
            .oneshot(command_request(
                "hq_get_usage",
                json!({"filter":{"limit":201}}),
                &config.admin_token
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::BAD_REQUEST
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            temp.path().join("switch.sqlite").metadata().unwrap().mode() & 0o777,
            0o600
        );
    }
}
#[tokio::test]
async fn inference_uses_separate_token_advertised_host_and_rejects_browser_origin() {
    let (_temp, app) = fixture();
    let request = |host: &str, token: &str, origin: bool| {
        let mut b = Request::builder()
            .uri("/hq/manifest")
            .header("host", host)
            .header("authorization", format!("Bearer {token}"));
        if origin {
            b = b.header("origin", "https://switch.example");
        }
        b.body(Body::empty()).unwrap()
    };
    assert_eq!(
        hq::gateway::router(app.gateway.clone())
            .oneshot(request(
                "switch.test:17621",
                &app.config.inference_token,
                false
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    assert_eq!(
        hq::gateway::router(app.gateway.clone())
            .oneshot(request("switch.test:17621", &app.config.admin_token, false))
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        hq::gateway::router(app.gateway.clone())
            .oneshot(request(
                "127.0.0.1:17621",
                &app.config.inference_token,
                false
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        hq::gateway::router(app.gateway.clone())
            .oneshot(request(
                "switch.test:17621",
                &app.config.inference_token,
                true
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::FORBIDDEN
    );
}
#[tokio::test]
async fn static_security_headers_api_missing_and_body_limit() {
    let (_temp, app) = fixture();
    let response = app
        .web()
        .oneshot(Request::builder().uri("/").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(response.headers()["x-content-type-options"], "nosniff");
    assert!(response.headers()["content-security-policy"]
        .to_str()
        .unwrap()
        .contains("frame-ancestors 'none'"));
    let response = app
        .web()
        .oneshot(
            Request::builder()
                .uri("/api/unknown")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    assert!(value(response).await["error"].is_string());
    assert_eq!(
        app.web()
            .oneshot(
                Request::builder()
                    .uri("/missing.js")
                    .body(Body::empty())
                    .unwrap()
            )
            .await
            .unwrap()
            .status(),
        StatusCode::NOT_FOUND
    );
    let args = json!({"padding":"x".repeat(65536)});
    assert_eq!(
        app.web()
            .oneshot(command_request(
                "hq_get_connections",
                args,
                &app.config.admin_token
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::PAYLOAD_TOO_LARGE
    );
}
#[test]
fn configuration_rejects_credential_permissions_symlinks_and_unsafe_origins() {
    assert!(config::origin("https://switch.example/").is_ok());
    assert!(config::origin("http://192.168.2.11").is_err());
    assert!(config::origin("https://switch.example/path").is_err());
    assert!(config::gateway_url("http://switch:17621/path").is_err());
    assert_eq!(
        config::gateway_url("http://switch:80/").unwrap(),
        "http://switch"
    );
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("token");
    std::fs::write(&path, "a".repeat(64)).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(config::read_token(&path).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        assert!(config::read_token(&path).is_ok());
        let link = dir.path().join("link");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(config::read_token(&link).is_err());
    }
    std::fs::write(&path, "short").unwrap();
    assert!(config::read_token(&path).is_err());
    let lock_path = dir.path().join("service.lock");
    let first = database::instance_lock(&lock_path).unwrap();
    assert!(database::instance_lock(&lock_path).is_err());
    drop(first);
    assert!(database::instance_lock(&lock_path).is_ok());
}
#[tokio::test]
async fn actual_listener_preserves_usage_and_gracefully_stops() {
    let (_temp, app) = fixture();
    let upstream = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = upstream.local_addr().unwrap().port();
    let upstream_task = tokio::spawn(async move {
        axum::serve(upstream,Router::new().route("/v1/chat/completions",post(||async {Json(json!({"id":"fixture","choices":[],"usage":{"prompt_tokens":11,"completion_tokens":4,"prompt_tokens_details":{"cached_tokens":3}}}))}))).await.unwrap();
    });
    let saved = app
        .dispatch(
            "hq_save_connection",
            json!({"revision":0,"connection":connection(&format!("http://127.0.0.1:{port}/v1"))}),
        )
        .await
        .unwrap();
    let id = saved["connections"][0]["id"].as_str().unwrap().to_string();
    let probe = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let bind = probe.local_addr().unwrap();
    drop(probe);
    let mut config = app.config.clone();
    config.gateway_bind = bind;
    config.gateway_url = format!("http://{bind}");
    drop(app);
    let app = App::open(config.clone()).unwrap();
    app.start_gateway().await.unwrap();
    let response=reqwest::Client::new().post(format!("http://{bind}/hq/connections/{id}/1/v1/chat/completions")).bearer_auth(&config.inference_token).json(&json!({"model":format!("hq-{id}-v1"),"messages":[{"role":"user","content":"test"}],"max_tokens":4})).send().await.unwrap();
    assert!(response.status().is_success());
    let body = response.bytes().await.unwrap();
    assert!(body.windows(13).any(|s| s == b"prompt_tokens"));
    app.shutdown().await;
    assert!(tokio::net::TcpListener::bind(bind).await.is_ok());
    drop(app);
    let app = App::open(config.clone()).unwrap();
    let report = app
        .dispatch("hq_get_usage", json!({"filter":{}}))
        .await
        .unwrap();
    assert_eq!(report["summary"]["requests"], 1);
    assert_eq!(report["summary"]["inputTokens"], 11);
    assert_eq!(report["summary"]["outputTokens"], 4);
    assert_eq!(report["summary"]["totalTokens"], 15);
    assert_eq!(report["summary"]["cacheReadTokens"], 3);
    let connections = app.dispatch("hq_get_connections", json!({})).await.unwrap();
    assert_eq!(connections["connections"][0]["id"], id);
    upstream_task.abort();
}
