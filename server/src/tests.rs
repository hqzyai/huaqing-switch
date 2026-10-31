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
    let data_dir = temp.path().join("data");
    std::fs::create_dir(&data_dir).unwrap();
    let provider_key_file = temp.path().join("provider-key");
    std::fs::write(&provider_key_file, [42u8; 32]).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&provider_key_file, std::fs::Permissions::from_mode(0o600))
            .unwrap();
    }
    let provider_key_file = provider_key_file.canonicalize().unwrap();
    let config = Config {
        web_bind: "127.0.0.1:8080".parse().unwrap(),
        gateway_bind: "127.0.0.1:17621".parse().unwrap(),
        gateway_url: "http://switch.test:17621".into(),
        public_origin: "https://switch.example".into(),
        http: None,
        data_dir,
        static_dir: temp.path().into(),
        admin_token: "a".repeat(64),
        inference_token: "b".repeat(64),
        provider_key_file,
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
async fn stable_services_use_current_model_without_changing_pinned_connectors() {
    use std::sync::Mutex;
    for (protocol, operation) in [
        ("openai-chat", "chat/completions"),
        ("openai-responses", "responses"),
        ("anthropic", "messages"),
    ] {
        let observed = Arc::new(Mutex::new(Vec::<(String, HeaderMap, Value)>::new()));
        let captured = observed.clone();
        let upstream = Router::new().fallback(move |request: axum::extract::Request| {
            let captured = captured.clone();
            async move {
                let (parts, body) = request.into_parts();
                let payload: Value = serde_json::from_slice(&to_bytes(body, 4096).await.unwrap()).unwrap();
                captured.lock().unwrap().push((parts.uri.path().into(), parts.headers, payload));
                Json(json!({"usage":{"prompt_tokens":3,"completion_tokens":2,"input_tokens":3,"output_tokens":2},"choices":[]}))
            }
        });
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let task = tokio::spawn(async move { axum::serve(listener, upstream).await.unwrap() });
        let (_temp, app) = fixture();
        let mut input = connection(&format!("http://{address}/v1"));
        input["protocol"] = json!(protocol);
        input["maxOutputTokens"] = json!(32);
        let snapshot = value(
            app.web()
                .oneshot(command_request(
                    "hq_save_connection",
                    json!({"revision":0,"connection":input}),
                    &app.config.admin_token,
                ))
                .await
                .unwrap(),
        )
        .await;
        let id = snapshot["connections"][0]["id"]
            .as_str()
            .unwrap()
            .to_owned();
        let path = format!("/hq/services/{id}/v1/{operation}");
        let request = |path: &str, model: &str| {
            let payload = if protocol == "openai-responses" {
                json!({"model":model,"input":"fixture","max_output_tokens":16})
            } else {
                json!({"model":model,"messages":[{"role":"user","content":"fixture"}],"max_tokens":16})
            };
            Request::builder()
                .method("POST")
                .uri(path)
                .header("host", "switch.test:17621")
                .header(
                    "authorization",
                    format!("Bearer {}", app.config.inference_token),
                )
                .header("content-type", "application/json")
                .body(Body::from(payload.to_string()))
                .unwrap()
        };
        let response = hq::gateway::router(app.gateway.clone())
            .oneshot(request(&path, "application-existing-alias"))
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        to_bytes(response.into_body(), 4096).await.unwrap();
        input["id"] = json!(id);
        input["model"] = json!("centrally-changed-model");
        input["apiKey"] = json!("rotated-provider-fixture");
        let updated = app
            .web()
            .oneshot(command_request(
                "hq_save_connection",
                json!({"revision":1,"connection":input}),
                &app.config.admin_token,
            ))
            .await
            .unwrap();
        assert_eq!(updated.status(), StatusCode::OK);
        let old_path = format!("/hq/connections/{id}/1/v1/{operation}");
        let stale = hq::gateway::router(app.gateway.clone())
            .oneshot(request(&old_path, &format!("hq-{id}-v1")))
            .await
            .unwrap();
        assert_eq!(stale.status(), StatusCode::CONFLICT);
        let current = hq::gateway::router(app.gateway.clone())
            .oneshot(request(&path, "application-existing-alias"))
            .await
            .unwrap();
        assert_eq!(current.status(), StatusCode::OK);
        to_bytes(current.into_body(), 4096).await.unwrap();
        {
            let rows = observed.lock().unwrap();
            assert_eq!(rows.len(), 2);
            assert_eq!(rows[0].0, format!("/v1/{operation}"));
            assert_eq!(rows[0].2["model"], "fixture-model");
            assert_eq!(rows[1].2["model"], "centrally-changed-model");
            let header = if protocol == "anthropic" {
                "x-api-key"
            } else {
                "authorization"
            };
            let expected = if protocol == "anthropic" {
                "rotated-provider-fixture".to_owned()
            } else {
                "Bearer rotated-provider-fixture".to_owned()
            };
            assert_eq!(rows[1].1[header], expected);
            assert!(!rows[1].2.to_string().contains(&app.config.inference_token));
        }
        let models = hq::gateway::router(app.gateway.clone())
            .oneshot(
                Request::builder()
                    .uri(format!("/hq/services/{id}/v1/models"))
                    .header("host", "switch.test:17621")
                    .header(
                        "authorization",
                        format!("Bearer {}", app.config.inference_token),
                    )
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(models.headers()["cache-control"], "no-store");
        assert_eq!(
            value(models).await["data"][0]["id"],
            "centrally-changed-model"
        );
        task.abort();
    }
}

#[tokio::test]
async fn stable_services_keep_roles_origin_host_protocol_limits_and_removal_checks() {
    let (_temp, app) = fixture();
    let snapshot = value(
        app.web()
            .oneshot(command_request(
                "hq_save_connection",
                json!({"revision":0,"connection":connection("https://not-contacted.invalid/v1")}),
                &app.config.admin_token,
            ))
            .await
            .unwrap(),
    )
    .await;
    let id = snapshot["connections"][0]["id"].as_str().unwrap();
    for (token, host, origin, operation, model, output, status) in [
        (
            app.config.admin_token.as_str(),
            "switch.test:17621",
            None,
            "chat/completions",
            json!("legacy"),
            1,
            StatusCode::UNAUTHORIZED,
        ),
        (
            app.config.inference_token.as_str(),
            "wrong-host",
            None,
            "chat/completions",
            json!("legacy"),
            1,
            StatusCode::FORBIDDEN,
        ),
        (
            app.config.inference_token.as_str(),
            "switch.test:17621",
            Some("https://browser.invalid"),
            "chat/completions",
            json!("legacy"),
            1,
            StatusCode::FORBIDDEN,
        ),
        (
            app.config.inference_token.as_str(),
            "switch.test:17621",
            None,
            "responses",
            json!("legacy"),
            1,
            StatusCode::CONFLICT,
        ),
        (
            app.config.inference_token.as_str(),
            "switch.test:17621",
            None,
            "chat/completions",
            Value::Null,
            1,
            StatusCode::BAD_REQUEST,
        ),
        (
            app.config.inference_token.as_str(),
            "switch.test:17621",
            None,
            "chat/completions",
            json!("legacy"),
            393217,
            StatusCode::BAD_REQUEST,
        ),
    ] {
        let mut request = Request::builder()
            .method("POST")
            .uri(format!("/hq/services/{id}/v1/{operation}"))
            .header("host", host)
            .header("authorization", format!("Bearer {token}"));
        if let Some(origin) = origin {
            request = request.header("origin", origin);
        }
        let response = hq::gateway::router(app.gateway.clone())
            .oneshot(
                request
                    .body(Body::from(
                        json!({"model":model,"max_tokens":output}).to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), status);
    }
    let double_budget = hq::gateway::router(app.gateway.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/hq/services/{id}/v1/chat/completions"))
                .header("host", "switch.test:17621")
                .header(
                    "authorization",
                    format!("Bearer {}", app.config.inference_token),
                )
                .body(Body::from(
                    json!({"model":"legacy","max_completion_tokens":1,"max_tokens":393217})
                        .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(double_budget.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        app.web()
            .oneshot(command_request(
                "hq_delete_connection",
                json!({"revision":1,"id":id}),
                &app.config.admin_token
            ))
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    for operation in ["models", "chat/completions"] {
        let response = hq::gateway::router(app.gateway.clone())
            .oneshot(
                Request::builder()
                    .method(if operation == "models" { "GET" } else { "POST" })
                    .uri(format!("/hq/services/{id}/v1/{operation}"))
                    .header("host", "switch.test:17621")
                    .header(
                        "authorization",
                        format!("Bearer {}", app.config.inference_token),
                    )
                    .body(Body::from(json!({"model":"legacy"}).to_string()))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::CONFLICT);
    }
}

#[tokio::test]
async fn stable_services_reject_provider_urls_pointing_to_the_public_switch() {
    let (_temp, app) = fixture();
    let snapshot = value(app.web().oneshot(command_request("hq_save_connection",
        json!({"revision":0,"connection":connection("https://switch.example/model-api/hq/services/recursive/v1")}),
        &app.config.admin_token)).await.unwrap()).await;
    let id = snapshot["connections"][0]["id"].as_str().unwrap();
    let response = hq::gateway::router(app.gateway.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(format!("/hq/services/{id}/v1/chat/completions"))
                .header("host", "switch.test:17621")
                .header(
                    "authorization",
                    format!("Bearer {}", app.config.inference_token),
                )
                .body(Body::from(json!({"model":"legacy"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(value(response).await["error"]["code"], "GATEWAY_LOOP");
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
    let (_temp, app) = fixture();
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
            config
                .data_dir
                .join("switch.sqlite")
                .metadata()
                .unwrap()
                .mode()
                & 0o777,
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

#[test]
fn administrator_prefix_is_role_specific_and_keeps_private_file_validation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("admin-token");
    std::fs::write(&path, "a".repeat(64)).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
    let legacy = "a".repeat(64);
    assert!(config::validate_token_roles(&legacy, &"b".repeat(64)).is_ok());
    assert!(config::validate_token_roles(&legacy, &legacy).is_err());
    assert!(config::validate_token_roles(&format!("sk-{legacy}"), &legacy).is_err());
    assert!(config::validate_token_roles(&format!("sk-{legacy}"), &legacy.to_uppercase()).is_err());
    assert!(config::validate_token_roles(&format!("sk-{legacy}"), &"b".repeat(64)).is_ok());
    assert_eq!(config::read_admin_token(&path).unwrap(), legacy);
    assert_eq!(config::read_token(&path).unwrap(), legacy);
    let prefixed = format!("sk-{}", "a".repeat(64));
    std::fs::write(&path, format!("{prefixed}\n")).unwrap();
    assert_eq!(config::read_admin_token(&path).unwrap(), prefixed);
    assert!(
        config::read_token(&path).is_err(),
        "inference cannot accept an admin prefix"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(config::read_admin_token(&path).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        let link = directory.path().join("link");
        std::os::unix::fs::symlink(&path, &link).unwrap();
        assert!(config::read_admin_token(&link).is_err());
    }
    for invalid in [
        format!("sk-{}", "a".repeat(63)),
        format!("sk-{}", "a".repeat(65)),
        format!("SK-{}", "a".repeat(64)),
        format!("sk-{}g", "a".repeat(63)),
        format!("sk-sk-{}", "a".repeat(64)),
        format!(" sk-{}", "a".repeat(64)),
        format!("sk-{} ", "a".repeat(64)),
        format!("sk-{}\n{}", "a".repeat(32), "a".repeat(32)),
        "a".repeat(129),
    ] {
        std::fs::write(&path, invalid).unwrap();
        assert!(config::read_admin_token(&path).is_err());
    }
}

#[tokio::test]
async fn prefixed_admin_file_supports_real_http_login_without_stripping_or_inference_access() {
    let (temp, fixture) = fixture();
    let mut config = fixture.config.clone();
    drop(fixture);
    let token_file = temp.path().join("admin-token");
    let suffix = "a".repeat(64);
    let prefixed = format!("sk-{suffix}");
    std::fs::write(&token_file, format!("{prefixed}\n")).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&token_file, std::fs::Permissions::from_mode(0o600)).unwrap();
    }
    config.admin_token = config::read_admin_token(&token_file).unwrap();
    assert_eq!(config.admin_token, prefixed);
    let app = App::open(config.clone()).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let web = app.web();
    let (stop, receive) = tokio::sync::oneshot::channel();
    let web_task = tokio::spawn(async move {
        axum::serve(
            listener,
            web.into_make_service_with_connect_info::<SocketAddr>(),
        )
        .with_graceful_shutdown(async {
            let _ = receive.await;
        })
        .await
        .unwrap();
    });
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let login = |token: String| {
        client
            .post(format!("http://{address}/api/session"))
            .header("origin", "https://switch.example")
            .header("x-hq-csrf", "1")
            .json(&json!({"token":token}))
    };
    assert_eq!(
        login(suffix).send().await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        login("c".repeat(64)).send().await.unwrap().status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        login(config.inference_token.clone())
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );
    let response = login(prefixed.clone()).send().await.unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let cookie = response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .to_string();
    assert!(cookie.contains("HttpOnly; Secure; SameSite=Strict"));
    let session: Value = client
        .get(format!("http://{address}/api/session"))
        .header("cookie", cookie.split(';').next().unwrap())
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(session["authenticated"], true);
    assert_eq!(
        app.web()
            .oneshot(command_request("hq_get_connections", json!({}), &prefixed))
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    let inference = Request::builder()
        .uri("/hq/manifest")
        .header("host", "switch.test:17621")
        .header("authorization", format!("Bearer {prefixed}"))
        .body(Body::empty())
        .unwrap();
    assert_eq!(
        hq::gateway::router(app.gateway.clone())
            .oneshot(inference)
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );
    let _ = stop.send(());
    web_task.await.unwrap();
}

#[test]
fn optional_plaintext_management_requires_same_private_host_port_and_explicit_pair() {
    let public = "https://192.168.2.11:28001";
    assert!(config::http_management(public, None, None)
        .unwrap()
        .is_none());
    assert!(config::http_management(public, Some("http://192.168.2.11:28001"), None).is_err());
    assert!(config::http_management(public, None, Some("0.0.0.0:8081")).is_err());
    let accepted = config::http_management(
        public,
        Some("http://192.168.2.11:28001/"),
        Some("0.0.0.0:8081"),
    )
    .unwrap()
    .unwrap();
    assert_eq!(accepted.origin, "http://192.168.2.11:28001");
    assert_eq!(accepted.bind, "0.0.0.0:8081".parse::<SocketAddr>().unwrap());
    for host in [
        "10.2.3.4",
        "172.16.0.1",
        "192.168.2.11",
        "127.0.0.1",
        "localhost",
        "[::1]",
    ] {
        assert!(config::http_management(
            &format!("https://{host}:28001"),
            Some(&format!("http://{host}:28001")),
            Some("0.0.0.0:8081")
        )
        .is_ok());
    }
    for host in [
        "8.8.8.8",
        "172.32.0.1",
        "169.254.0.1",
        "0.0.0.0",
        "100.64.0.1",
        "public.example",
        "[fd00::1]",
    ] {
        assert!(config::http_management(
            &format!("https://{host}:28001"),
            Some(&format!("http://{host}:28001")),
            Some("0.0.0.0:8081")
        )
        .is_err());
    }
    for invalid in [
        "http://192.168.2.12:28001",
        "http://192.168.2.11:28002",
        "https://192.168.2.11:28001",
        "http://192.168.2.11:28001/path",
        "http://192.168.2.11:28001/?query=1",
        "http://192.168.2.11:28001/#fragment",
        "http://user:pass@192.168.2.11:28001",
    ] {
        assert!(config::http_management(public, Some(invalid), Some("0.0.0.0:8081")).is_err());
    }
    assert!(
        config::http_management(public, Some("http://192.168.2.11:28001"), Some("0.0.0.0:0"))
            .is_err()
    );
    assert!(config::http_management(
        "http://127.0.0.1:28001",
        Some("http://127.0.0.1:28001"),
        Some("0.0.0.0:8081")
    )
    .is_err());
}

#[tokio::test]
async fn two_real_management_listeners_keep_cookie_origin_csrf_and_logout_isolated() {
    let (_temp, fixture) = fixture();
    assert!(
        fixture.http_web().is_none(),
        "HTTP is disabled unless explicitly configured"
    );
    let mut config = fixture.config.clone();
    config.public_origin = "https://127.0.0.1:28001".into();
    config.http = config::http_management(
        &config.public_origin,
        Some("http://127.0.0.1:28001"),
        Some("127.0.0.1:8081"),
    )
    .unwrap();
    drop(fixture);
    let app = App::open(config.clone()).unwrap();
    let secure_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let http_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let secure_address = secure_listener.local_addr().unwrap();
    let http_address = http_listener.local_addr().unwrap();
    let (stop, receive) = tokio::sync::watch::channel(false);
    let mut tasks = tokio::task::JoinSet::new();
    for (listener, web) in [
        (secure_listener, app.web()),
        (http_listener, app.http_web().unwrap()),
    ] {
        let mut receive = receive.clone();
        tasks.spawn(async move {
            axum::serve(
                listener,
                web.into_make_service_with_connect_info::<SocketAddr>(),
            )
            .with_graceful_shutdown(async move {
                let _ = receive.changed().await;
            })
            .await
            .unwrap();
        });
    }
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let secure_origin = config.public_origin;
    let http_origin = config.http.unwrap().origin;
    let login = |address: SocketAddr, origin: &str| {
        client
            .post(format!("http://{address}/api/session"))
            .header("origin", origin)
            .header("x-hq-csrf", "1")
            .header(
                "x-forwarded-proto",
                if origin.starts_with("https:") {
                    "http"
                } else {
                    "https"
                },
            )
            .json(&json!({"token":config.admin_token}))
    };
    let secure_response = login(secure_address, &secure_origin).send().await.unwrap();
    assert_eq!(secure_response.status(), StatusCode::OK);
    let secure_cookie = secure_response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .to_string();
    assert!(secure_cookie.starts_with(auth::COOKIE));
    assert!(secure_cookie.contains("; Secure;"));
    let http_response = login(http_address, &http_origin).send().await.unwrap();
    assert_eq!(http_response.status(), StatusCode::OK);
    let http_cookie = http_response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .to_string();
    assert!(http_cookie.starts_with(auth::HTTP_COOKIE));
    assert!(!http_cookie.contains("Secure"));
    assert!(http_cookie.contains("HttpOnly; SameSite=Strict"));
    let secure_pair = secure_cookie.split(';').next().unwrap();
    let http_pair = http_cookie.split(';').next().unwrap();
    let secure_id = secure_pair.split_once('=').unwrap().1;
    let http_id = http_pair.split_once('=').unwrap().1;
    let command = |address: SocketAddr, origin: &str, cookie: &str, name: &str| {
        client
            .post(format!("http://{address}/api/commands/{name}"))
            .header("origin", origin)
            .header("x-hq-csrf", "1")
            .header("cookie", cookie)
            .json(&json!({}))
    };
    for (address, origin, cookie) in [
        (secure_address, secure_origin.as_str(), secure_pair),
        (http_address, http_origin.as_str(), http_pair),
    ] {
        let response = command(address, origin, cookie, "hq_get_connections")
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.json::<Value>().await.unwrap()["connections"],
            json!([])
        );
        let response = command(address, origin, cookie, "hq_get_usage")
            .send()
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.json::<Value>().await.unwrap()["summary"]["requests"],
            0
        );
        let status = client
            .get(format!("http://{address}/api/session"))
            .header("cookie", cookie)
            .send()
            .await
            .unwrap()
            .json::<Value>()
            .await
            .unwrap();
        assert_eq!(status["authenticated"], true);
    }
    for (address, origin, cookie) in [
        (
            secure_address,
            secure_origin.as_str(),
            http_pair.to_string(),
        ),
        (http_address, http_origin.as_str(), secure_pair.to_string()),
        (
            secure_address,
            secure_origin.as_str(),
            format!("{}={http_id}", auth::COOKIE),
        ),
        (
            http_address,
            http_origin.as_str(),
            format!("{}={secure_id}", auth::HTTP_COOKIE),
        ),
    ] {
        assert_eq!(
            command(address, origin, &cookie, "hq_get_connections")
                .send()
                .await
                .unwrap()
                .status(),
            StatusCode::UNAUTHORIZED
        );
    }
    for (address, wrong_origin, cookie) in [
        (secure_address, http_origin.as_str(), secure_pair),
        (http_address, secure_origin.as_str(), http_pair),
    ] {
        assert_eq!(
            command(address, wrong_origin, cookie, "hq_get_connections")
                .send()
                .await
                .unwrap()
                .status(),
            StatusCode::FORBIDDEN
        );
    }
    let missing_csrf = client
        .post(format!(
            "http://{http_address}/api/commands/hq_get_connections"
        ))
        .header("origin", &http_origin)
        .header("cookie", http_pair)
        .json(&json!({}))
        .send()
        .await
        .unwrap();
    assert_eq!(missing_csrf.status(), StatusCode::FORBIDDEN);
    let logout = client
        .delete(format!("http://{http_address}/api/session"))
        .header("origin", &http_origin)
        .header("x-hq-csrf", "1")
        .header("cookie", http_pair)
        .send()
        .await
        .unwrap();
    assert_eq!(logout.status(), StatusCode::OK);
    let cleared = logout.headers()["set-cookie"].to_str().unwrap();
    assert!(cleared.starts_with(auth::HTTP_COOKIE));
    assert!(!cleared.contains("Secure"));
    assert!(cleared.contains("Max-Age=0"));
    assert_eq!(
        command(http_address, &http_origin, http_pair, "hq_get_usage")
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        command(secure_address, &secure_origin, secure_pair, "hq_get_usage")
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    let _ = stop.send(true);
    while let Some(result) = tasks.join_next().await {
        result.unwrap();
    }
}
