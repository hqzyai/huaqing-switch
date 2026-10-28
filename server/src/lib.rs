pub mod auth;
pub mod config;
pub mod database;
#[path = "../../src-tauri/src/hq.rs"]
#[allow(dead_code)]
mod hq;

use auth::{ApiError, Auth};
use axum::{
    extract::{ConnectInfo, DefaultBodyLimit, Path, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    middleware,
    response::{IntoResponse, Response},
    routing::{any, get, post},
    Json, Router,
};
use config::Config;
use serde::Deserialize;
use serde_json::{json, Value};
use std::{net::SocketAddr, sync::Arc};
use tower_http::services::ServeDir;

#[derive(Clone)]
pub struct App {
    config: Config,
    db: Arc<database::Database>,
    gateway: hq::gateway::Gateway,
    auth: Arc<Auth>,
}
impl App {
    pub fn open(config: Config) -> anyhow::Result<Self> {
        let db = Arc::new(database::Database::open(
            &config.data_dir.join("switch.sqlite"),
        )?);
        let gateway = hq::gateway::Gateway::server(
            db.clone(),
            config.gateway_bind,
            config.gateway_url.clone(),
            config.inference_token.clone(),
            std::iter::once(config.public_origin.as_str())
                .chain(config.http.as_ref().map(|http| http.origin.as_str()))
                .map(url::Url::parse)
                .collect::<Result<Vec<_>, _>>()?,
        );
        Ok(Self {
            auth: Arc::new(Auth::new(config.admin_token.clone())),
            config,
            db,
            gateway,
        })
    }
    pub async fn start_gateway(&self) -> anyhow::Result<()> {
        self.gateway.start().await;
        let status = serde_json::to_value(self.gateway.status().await)?;
        if status["running"] != true {
            anyhow::bail!("inference listener failed to start");
        }
        if self.gateway.usage().storage_error() {
            anyhow::bail!("usage storage initialization failed");
        }
        Ok(())
    }
    pub async fn shutdown(&self) {
        self.gateway.shutdown().await;
    }
    /// Enabled only by the explicit HTTP origin/listener pair in deployment config.
    pub fn http_web(&self) -> Option<Router> {
        let http = self.config.http.as_ref()?;
        let mut app = self.clone();
        app.config.public_origin = http.origin.clone();
        app.auth = Arc::new(self.auth.http_variant());
        Some(app.web())
    }
    /// TLS-facing internal listener: its policy never derives from forwarded headers.
    pub fn web(&self) -> Router {
        Router::new()
            .route("/healthz", get(health))
            .route("/api/session", get(session).post(login).delete(logout))
            .route("/api/commands/:name", post(command))
            .route("/api", any(api_not_found))
            .route("/api/*rest", any(api_not_found))
            .fallback_service(
                ServeDir::new(&self.config.static_dir).append_index_html_on_directories(true),
            )
            .layer(DefaultBodyLimit::max(64 * 1024))
            .layer(tower_http::timeout::TimeoutLayer::new(
                std::time::Duration::from_secs(15),
            ))
            .layer(middleware::map_response(security_headers))
            .with_state(self.clone())
    }
}
async fn health(State(app): State<App>) -> Response {
    let db = app.db.clone();
    let db_ok = tokio::task::spawn_blocking(move || {
        db.conn.lock().ok().is_some_and(|conn| {
            conn.query_row("SELECT 1", [], |row| row.get::<_, u8>(0))
                .is_ok()
        })
    })
    .await
    .unwrap_or(false);
    let status = serde_json::to_value(app.gateway.status().await).unwrap_or(Value::Null);
    let ok = db_ok && status["running"] == true && !app.gateway.usage().storage_error();
    (if ok{StatusCode::OK}else{StatusCode::SERVICE_UNAVAILABLE},Json(json!({"status":if ok{"ok"}else{"unavailable"},"product":"huaqing-switch-server","version":env!("CARGO_PKG_VERSION")}))).into_response()
}
async fn session(State(app): State<App>, headers: HeaderMap) -> Result<Json<Value>, ApiError> {
    auth::check_origin(
        &headers,
        &app.config.public_origin,
        false,
        app.auth.cli(&headers),
    )?;
    Ok(Json(json!({"authenticated":app.auth.authorized(&headers)})))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Login {
    token: String,
}
async fn login(
    State(app): State<App>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Json(input): Json<Login>,
) -> Result<Response, ApiError> {
    auth::check_origin(&headers, &app.config.public_origin, true, false)?;
    let cookie = app.auth.login(peer.ip(), &input.token)?;
    let mut response = Json(json!({"authenticated":true})).into_response();
    response.headers_mut().insert(
        "set-cookie",
        HeaderValue::from_str(&cookie).expect("hex cookie"),
    );
    Ok(response)
}
async fn logout(State(app): State<App>, headers: HeaderMap) -> Result<Response, ApiError> {
    auth::check_origin(
        &headers,
        &app.config.public_origin,
        true,
        app.auth.cli(&headers),
    )?;
    app.auth.logout(&headers);
    let mut response = Json(json!({"authenticated":false})).into_response();
    response.headers_mut().insert(
        "set-cookie",
        HeaderValue::from_str(&app.auth.clear_cookie()).expect("static cookie"),
    );
    Ok(response)
}
async fn command(
    State(app): State<App>,
    Path(name): Path<String>,
    headers: HeaderMap,
    Json(args): Json<Value>,
) -> Result<Json<Value>, ApiError> {
    auth::check_origin(
        &headers,
        &app.config.public_origin,
        true,
        app.auth.cli(&headers),
    )?;
    if !app.auth.authorized(&headers) {
        return Err(ApiError(StatusCode::UNAUTHORIZED, "请先使用管理凭证登录"));
    }
    if !matches!(
        name.as_str(),
        "hq_get_connections"
            | "hq_save_connection"
            | "hq_select_connection"
            | "hq_delete_connection"
            | "hq_get_usage"
            | "hq_get_gateway_status"
            | "hq_retry_gateway"
    ) {
        return Err(ApiError(StatusCode::NOT_FOUND, "管理命令不存在"));
    }
    app.dispatch(&name, args)
        .await
        .map(Json)
        .map_err(|message| ApiError(StatusCode::BAD_REQUEST, message))
}
impl App {
    async fn dispatch(&self, name: &str, args: Value) -> Result<Value, &'static str> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Save {
            revision: u64,
            connection: hq::ConnectionInput,
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Select {
            revision: u64,
            id: String,
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Usage {
            #[serde(default)]
            filter: hq::usage::UsageFilter,
        }
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Empty {}
        // Domain errors are controlled, private error messages. Never return parser errors with input.
        let value = match name {
            "hq_get_connections" => {
                serde_json::from_value::<Empty>(args).map_err(|_| "命令参数无效")?;
                let db = self.db.clone();
                tokio::task::spawn_blocking(move || {
                    hq::connections(&db)
                        .and_then(|v| serde_json::to_value(v).map_err(|_| "无法编码配置".into()))
                })
                .await
                .map_err(|_| "配置服务暂不可用")?
                .map_err(domain_error)?
            }
            "hq_save_connection" => {
                let a: Save = serde_json::from_value(args).map_err(|_| "连接参数无效")?;
                let db = self.db.clone();
                tokio::task::spawn_blocking(move || {
                    hq::save(&db, a.revision, a.connection)
                        .and_then(|v| serde_json::to_value(v).map_err(|_| "无法编码配置".into()))
                })
                .await
                .map_err(|_| "配置服务暂不可用")?
                .map_err(domain_error)?
            }
            "hq_select_connection" | "hq_delete_connection" => {
                let a: Select = serde_json::from_value(args).map_err(|_| "命令参数无效")?;
                let db = self.db.clone();
                let remove = name == "hq_delete_connection";
                tokio::task::spawn_blocking(move || {
                    let v = if remove {
                        hq::remove(&db, a.revision, &a.id)
                    } else {
                        hq::select(&db, a.revision, &a.id)
                    }?;
                    serde_json::to_value(v).map_err(|_| "无法编码配置".into())
                })
                .await
                .map_err(|_| "配置服务暂不可用")?
                .map_err(domain_error)?
            }
            "hq_get_usage" => {
                let a: Usage = serde_json::from_value(args).map_err(|_| "统计参数无效")?;
                serde_json::to_value(
                    self.gateway
                        .usage()
                        .query(a.filter)
                        .await
                        .map_err(domain_error)?,
                )
                .map_err(|_| "无法编码用量")?
            }
            "hq_get_gateway_status" | "hq_retry_gateway" => {
                serde_json::from_value::<Empty>(args).map_err(|_| "命令参数无效")?;
                serde_json::to_value(self.gateway.status().await).map_err(|_| "无法编码服务状态")?
            }
            _ => return Err("管理命令不存在"),
        };
        Ok(value)
    }
}
fn domain_error(message: String) -> &'static str {
    // Return an allowlist only: stored data and dependency errors can never become HTTP error content.
    match message.as_str() {
        "连接配置已发生变化，请刷新后重试" => "连接配置已发生变化，请刷新后重试",
        "请设置有效的本地上下文与输出限制（输出不得超过上下文）" => {
            "请设置有效的本地上下文与输出限制（输出不得超过上下文）"
        }
        "连接名称须为 1–80 个字符" => "连接名称须为 1–80 个字符",
        "请输入有效的模型 ID（最多 256 字节）" => {
            "请输入有效的模型 ID（最多 256 字节）"
        }
        "请输入有效的 API 地址" => "请输入有效的 API 地址",
        "API 地址须为 HTTP(S) 基础地址，不能包含账号、查询参数或片段" => {
            "API 地址须为 HTTP(S) 基础地址，不能包含账号、查询参数或片段"
        }
        "API Key 格式无效" => "API Key 格式无效",
        "该连接已被删除，请刷新后重试" => "该连接已被删除，请刷新后重试",
        "最多保存 100 个连接" => "最多保存 100 个连接",
        "连接不存在" => "连接不存在",
        _ => "操作失败，请检查参数、磁盘和服务状态后重试",
    }
}
async fn api_not_found() -> ApiError {
    ApiError(StatusCode::NOT_FOUND, "接口不存在")
}
async fn security_headers(mut response: Response) -> Response {
    let h = response.headers_mut();
    h.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    h.insert("x-frame-options", HeaderValue::from_static("DENY"));
    h.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    h.insert("cache-control", HeaderValue::from_static("no-store"));
    h.insert("content-security-policy",HeaderValue::from_static("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'"));
    response
}

#[cfg(test)]
mod tests;
