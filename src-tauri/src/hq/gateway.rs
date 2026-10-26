//! Authenticated loopback transport. Wire protocols stay native to each provider.
use super::{load, Connection, Protocol};
use crate::database::Database;
use axum::{
    body::{to_bytes, Body},
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures::StreamExt;
use hmac::{Hmac, Mac};
use serde::Serialize;
use serde_json::{json, Value};
use sha2::Sha256;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio::{
    sync::{Mutex, OwnedSemaphorePermit, Semaphore},
    task::JoinHandle,
};

const MAX_BODY: usize = 25 * 1024 * 1024;
const REQUEST_DEADLINE: Duration = Duration::from_secs(300);

#[derive(Clone)]
pub struct Gateway(Arc<Inner>);
struct Inner {
    db: Arc<Database>,
    directory: PathBuf,
    token: String,
    port: u16,
    client: reqwest::Client,
    lifecycle: Mutex<Option<JoinHandle<()>>>,
    error: std::sync::Mutex<Option<String>>,
    slots: Arc<Semaphore>,
    active: AtomicU64,
    requests: AtomicU64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GatewayStatus {
    running: bool,
    address: String,
    port: u16,
    error: Option<String>,
    active_requests: u64,
    total_requests: u64,
}

impl Gateway {
    pub fn new(db: Arc<Database>, directory: PathBuf) -> Self {
        let brand: Value = serde_json::from_str(include_str!("../../../brand.config.json"))
            .expect("valid bundled product policy");
        Self(Arc::new(Inner {
            db,
            directory,
            port: brand["proxyPort"]
                .as_u64()
                .expect("configured gateway port") as u16,
            token: format!(
                "{}{}",
                uuid::Uuid::new_v4().simple(),
                uuid::Uuid::new_v4().simple()
            ),
            client: reqwest::Client::builder()
                .connect_timeout(Duration::from_secs(10))
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .expect("TLS client initialization"),
            lifecycle: Mutex::new(None),
            error: std::sync::Mutex::new(None),
            slots: Arc::new(Semaphore::new(16)),
            active: AtomicU64::new(0),
            requests: AtomicU64::new(0),
        }))
    }

    fn endpoint(&self) -> String {
        format!("http://127.0.0.1:{}", self.0.port)
    }
    pub async fn start(&self) {
        let mut task = self.0.lifecycle.lock().await;
        if task.as_ref().is_some_and(|t| !t.is_finished()) {
            return;
        }
        let result = async {
            std::fs::create_dir_all(&self.0.directory).map_err(|_| "无法创建本机连接目录".to_string())?;
            #[cfg(unix)]
            { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(&self.0.directory,std::fs::Permissions::from_mode(0o700)).map_err(|_| "无法保护本机连接目录".to_string())?; }
            let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, self.0.port)).await
                .map_err(|_| format!("无法监听本机端口 {}，请检查端口占用后重试", self.0.port))?;
            let path = self.0.directory.join("connector.json");
            let discovery = json!({"version":1,"product":"huaqing-switch","endpoint":self.endpoint(),"token":self.0.token});
            crate::config::write_json_file_private(&path, &discovery).map_err(|_| "无法写入本机连接凭证".to_string())?;
            let router = router(self.clone());
            let handle = tokio::spawn(async move {
                if let Err(error) = axum::serve(listener, router).await { log::error!("Huaqing gateway listener stopped: {error}"); }
            });
            Ok::<_, String>(handle)
        }.await;
        match result {
            Ok(handle) => {
                *task = Some(handle);
                if let Ok(mut e) = self.0.error.lock() {
                    *e = None;
                }
                log::info!(
                    "Huaqing Switch gateway listening on 127.0.0.1:{}",
                    self.0.port
                );
            }
            Err(error) => {
                if let Ok(mut e) = self.0.error.lock() {
                    *e = Some(error.clone());
                }
                log::error!("Huaqing Switch gateway startup failed: {error}");
            }
        }
    }

    pub async fn status(&self) -> GatewayStatus {
        let task = self.0.lifecycle.lock().await;
        GatewayStatus {
            running: task.as_ref().is_some_and(|t| !t.is_finished()),
            address: self.endpoint(),
            port: self.0.port,
            error: self.0.error.lock().ok().and_then(|e| e.clone()),
            active_requests: self.0.active.load(Ordering::Relaxed),
            total_requests: self.0.requests.load(Ordering::Relaxed),
        }
    }

    fn authorize(&self, headers: &HeaderMap) -> Result<(), Response> {
        if headers.contains_key("origin")
            || headers.get("host").and_then(|h| h.to_str().ok())
                != Some(format!("127.0.0.1:{}", self.0.port).as_str())
        {
            return Err(error(
                StatusCode::FORBIDDEN,
                "LOCAL_ACCESS_DENIED",
                "仅允许受信任的本机客户端",
            ));
        }
        let supplied = headers
            .get("authorization")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .or_else(|| headers.get("x-api-key").and_then(|v| v.to_str().ok()))
            .unwrap_or("");
        // Use the existing HMAC library's constant-time verification, not string equality.
        let sign = |key: &[u8]| {
            let mut mac =
                Hmac::<Sha256>::new_from_slice(key).expect("HMAC accepts arbitrary key lengths");
            mac.update(b"huaqing-connector-v1");
            mac
        };
        if supplied.len() != 64
            || sign(self.0.token.as_bytes())
                .verify_slice(&sign(supplied.as_bytes()).finalize().into_bytes())
                .is_err()
        {
            return Err(error(
                StatusCode::UNAUTHORIZED,
                "LOCAL_TOKEN_INVALID",
                "本机连接凭证已失效，请重试连接",
            ));
        }
        Ok(())
    }

    async fn store(&self) -> Result<super::Store, Response> {
        let db = self.0.db.clone();
        tokio::task::spawn_blocking(move || {
            let conn = db.conn.lock().map_err(|_| "存储暂不可用".to_string())?;
            load(&conn)
        })
        .await
        .map_err(|_| {
            error(
                StatusCode::SERVICE_UNAVAILABLE,
                "STORE_UNAVAILABLE",
                "连接存储暂不可用",
            )
        })?
        .map_err(|_| {
            error(
                StatusCode::SERVICE_UNAVAILABLE,
                "STORE_UNAVAILABLE",
                "请检查 Huaqing Switch 的连接配置",
            )
        })
    }
}

pub fn router(gateway: Gateway) -> Router {
    Router::new()
        .route("/hq/manifest", get(manifest))
        .route(
            "/hq/connections/:id/:revision/v1/chat/completions",
            post(chat),
        )
        .route(
            "/hq/connections/:id/:revision/v1/responses",
            post(responses),
        )
        .route("/hq/connections/:id/:revision/v1/messages", post(messages))
        .with_state(gateway)
}

fn model_id(connection: &Connection) -> String {
    format!("hq-{}-v{}", connection.id, connection.revision)
}

async fn manifest(State(gateway): State<Gateway>, headers: HeaderMap) -> Response {
    if let Err(response) = gateway.authorize(&headers) {
        return response;
    }
    let store = match gateway.store().await {
        Ok(store) => store,
        Err(e) => return e,
    };
    let default = store
        .connections
        .iter()
        .find(|c| Some(&c.id) == store.active_id.as_ref())
        .map(model_id);
    let models: Vec<_> = store.connections.iter().map(|c| {
        let root = format!("{}/hq/connections/{}/{}",gateway.endpoint(),c.id,c.revision);
        json!({"id":model_id(c),"name":format!("{} · {}",c.name,c.model),"protocol":c.protocol,
            "baseUrl":if c.protocol==Protocol::Anthropic {root} else {format!("{root}/v1")},
            "contextWindow":c.context_window,"maxOutputTokens":c.max_output_tokens,"supportsImages":c.supports_images})
    }).collect();
    let mut response = Json(
        json!({"version":1,"product":"huaqing-switch","defaultModel":default,"models":models}),
    )
    .into_response();
    response
        .headers_mut()
        .insert("cache-control", "no-store".parse().unwrap());
    response
}

async fn chat(
    State(g): State<Gateway>,
    Path((id, rev)): Path<(String, u64)>,
    req: axum::extract::Request,
) -> Response {
    forward(g, id, rev, Protocol::OpenaiChat, req).await
}
async fn responses(
    State(g): State<Gateway>,
    Path((id, rev)): Path<(String, u64)>,
    req: axum::extract::Request,
) -> Response {
    forward(g, id, rev, Protocol::OpenaiResponses, req).await
}
async fn messages(
    State(g): State<Gateway>,
    Path((id, rev)): Path<(String, u64)>,
    req: axum::extract::Request,
) -> Response {
    forward(g, id, rev, Protocol::Anthropic, req).await
}

struct RequestGuard {
    gateway: Gateway,
    start: Instant,
    request_id: uuid::Uuid,
    status: u16,
    _permit: OwnedSemaphorePermit,
}
impl Drop for RequestGuard {
    fn drop(&mut self) {
        self.gateway.0.active.fetch_sub(1, Ordering::Relaxed);
        log::info!(
            "Huaqing inference request={} status={} elapsed_ms={}",
            self.request_id,
            self.status,
            self.start.elapsed().as_millis()
        );
    }
}

async fn forward(
    gateway: Gateway,
    id: String,
    revision: u64,
    protocol: Protocol,
    request: axum::extract::Request,
) -> Response {
    if let Err(response) = gateway.authorize(request.headers()) {
        return response;
    }
    let permit = match gateway.0.slots.clone().try_acquire_owned() {
        Ok(p) => p,
        Err(_) => {
            return error(
                StatusCode::TOO_MANY_REQUESTS,
                "LOCAL_BUSY",
                "本机模型请求已达并发上限，请稍后重试",
            )
        }
    };
    gateway.0.active.fetch_add(1, Ordering::Relaxed);
    gateway.0.requests.fetch_add(1, Ordering::Relaxed);
    let mut guard = RequestGuard {
        gateway: gateway.clone(),
        start: Instant::now(),
        request_id: uuid::Uuid::new_v4(),
        status: 499,
        _permit: permit,
    };
    let store = match gateway.store().await {
        Ok(store) => store,
        Err(e) => {
            guard.status = e.status().as_u16();
            return e;
        }
    };
    let connection = match store.connections.into_iter().find(|c| c.id == id) {
        Some(c) => c,
        None => {
            return rejected(
                &mut guard,
                StatusCode::CONFLICT,
                "CONNECTION_REMOVED",
                "此模型连接已删除，请在 Huaqing Switch 中选择连接并新建会话",
            )
        }
    };
    if connection.revision != revision || connection.protocol != protocol {
        return rejected(
            &mut guard,
            StatusCode::CONFLICT,
            "CONNECTION_CHANGED",
            "此模型连接已修改，请重新选择模型或新建会话",
        );
    }
    let bytes = match tokio::time::timeout(
        Duration::from_secs(30),
        to_bytes(request.into_body(), MAX_BODY),
    )
    .await
    {
        Ok(Ok(bytes)) => bytes,
        Ok(Err(_)) => {
            return rejected(
                &mut guard,
                StatusCode::PAYLOAD_TOO_LARGE,
                "REQUEST_TOO_LARGE",
                "模型请求超过大小限制",
            )
        }
        Err(_) => {
            return rejected(
                &mut guard,
                StatusCode::REQUEST_TIMEOUT,
                "INPUT_TIMEOUT",
                "读取模型请求超时",
            )
        }
    };
    let mut payload: Value = match serde_json::from_slice(&bytes) {
        Ok(Value::Object(obj)) => Value::Object(obj),
        _ => {
            return rejected(
                &mut guard,
                StatusCode::BAD_REQUEST,
                "INVALID_REQUEST",
                "模型请求须为 JSON 对象",
            )
        }
    };
    if payload["model"].as_str() != Some(model_id(&connection).as_str()) {
        return rejected(
            &mut guard,
            StatusCode::BAD_REQUEST,
            "MODEL_MISMATCH",
            "模型标识与连接不一致",
        );
    }
    payload["model"] = Value::String(connection.model.clone());
    let output_field = match protocol {
        Protocol::OpenaiChat => {
            if payload.get("max_completion_tokens").is_some() {
                "max_completion_tokens"
            } else {
                "max_tokens"
            }
        }
        Protocol::OpenaiResponses => "max_output_tokens",
        Protocol::Anthropic => "max_tokens",
    };
    if let Some(value) = payload.get(output_field) {
        if !value
            .as_u64()
            .is_some_and(|n| n > 0 && n <= u64::from(connection.max_output_tokens))
        {
            return rejected(
                &mut guard,
                StatusCode::BAD_REQUEST,
                "OUTPUT_LIMIT",
                "请求输出上限超过 Huaqing Switch 连接中设置的本地限制",
            );
        }
    } else {
        payload[output_field] = json!(connection.max_output_tokens);
    }
    let suffix = match protocol {
        Protocol::OpenaiChat => "chat/completions",
        Protocol::OpenaiResponses => "responses",
        Protocol::Anthropic => {
            if connection.base_url.ends_with("/v1") {
                "messages"
            } else {
                "v1/messages"
            }
        }
    };
    let target = format!("{}/{suffix}", connection.base_url);
    if let Ok(url) = url::Url::parse(&target) {
        if url.port_or_known_default() == Some(gateway.0.port)
            && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"))
        {
            return rejected(
                &mut guard,
                StatusCode::BAD_REQUEST,
                "GATEWAY_LOOP",
                "供应商地址不能指向 Huaqing Switch 自身",
            );
        }
    }
    let mut outbound = gateway
        .0
        .client
        .post(target)
        .header("content-type", "application/json")
        .header("accept", "application/json, text/event-stream");
    if !connection.api_key.is_empty() {
        outbound = if protocol == Protocol::Anthropic {
            outbound.header("x-api-key", &connection.api_key)
        } else {
            outbound.bearer_auth(&connection.api_key)
        };
    }
    if protocol == Protocol::Anthropic {
        outbound = outbound.header("anthropic-version", "2023-06-01");
    }
    let upstream =
        match tokio::time::timeout(Duration::from_secs(60), outbound.json(&payload).send()).await {
            Ok(Ok(res)) => res,
            Ok(Err(_)) => {
                guard.status = 502;
                return rejected(
                    &mut guard,
                    StatusCode::BAD_GATEWAY,
                    "UPSTREAM_UNAVAILABLE",
                    "无法连接模型 API，请检查供应商地址与网络",
                );
            }
            Err(_) => {
                guard.status = 504;
                return rejected(
                    &mut guard,
                    StatusCode::GATEWAY_TIMEOUT,
                    "UPSTREAM_TIMEOUT",
                    "模型 API 响应超时",
                );
            }
        };
    let status = upstream.status();

    if !status.is_success() {
        return rejected(
            &mut guard,
            status,
            "UPSTREAM_ERROR",
            &format!(
                "模型 API 返回 HTTP {}，请检查供应商配置、权限与额度",
                status.as_u16()
            ),
        );
    }
    let content_type = upstream
        .headers()
        .get("content-type")
        .cloned()
        .unwrap_or_else(|| "application/json".parse().unwrap());
    let deadline = tokio::time::Instant::from_std(guard.start + REQUEST_DEADLINE);
    let stream = async_stream::stream! {
        let mut guard=guard;
        let mut stream=upstream.bytes_stream();
        loop {
            let until=std::cmp::min(deadline,tokio::time::Instant::now()+Duration::from_secs(120));
            match tokio::time::timeout_at(until,stream.next()).await {
                Ok(Some(Ok(bytes)))=>yield Ok::<_,std::io::Error>(bytes),
                Ok(None)=>{ guard.status=status.as_u16(); break; },
                _=>{ guard.status=504; yield Err(std::io::Error::new(std::io::ErrorKind::TimedOut,"模型响应已中断或超时")); break; },
            }
        }
    };
    let mut response = Response::new(Body::from_stream(stream));
    *response.status_mut() = status;
    response.headers_mut().insert("content-type", content_type);
    response
        .headers_mut()
        .insert("cache-control", "no-store".parse().unwrap());
    response
}

fn rejected(guard: &mut RequestGuard, status: StatusCode, code: &str, message: &str) -> Response {
    guard.status = status.as_u16();
    error(status, code, message)
}

fn error(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"error":{"type":"huaqing_switch","code":code,"message":message}})),
    )
        .into_response()
}

#[tauri::command]
pub async fn hq_get_gateway_status(
    state: tauri::State<'_, Gateway>,
) -> Result<GatewayStatus, String> {
    Ok(state.status().await)
}
#[tauri::command]
pub async fn hq_retry_gateway(state: tauri::State<'_, Gateway>) -> Result<GatewayStatus, String> {
    state.start().await;
    Ok(state.status().await)
}
