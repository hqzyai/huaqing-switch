//! Authenticated loopback transport. Wire protocols stay native to each provider.
use super::{load, usage, Connection, Protocol};
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
    net::SocketAddr,
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio::{
    sync::{oneshot, Mutex, OwnedSemaphorePermit, Semaphore},
    task::JoinHandle,
};

const MAX_BODY: usize = 25 * 1024 * 1024;
const REQUEST_DEADLINE: Duration = Duration::from_secs(300);

#[derive(Clone)]
pub struct Gateway(Arc<Inner>);
struct Inner {
    db: Arc<Database>,
    directory: Option<PathBuf>,
    bind: SocketAddr,
    endpoint: String,
    authority: String,
    token: String,
    port: u16,
    client: reqwest::Client,
    lifecycle: Mutex<Option<ListenerTask>>,
    error: std::sync::Mutex<Option<String>>,
    slots: Arc<Semaphore>,
    active: AtomicU64,
    requests: AtomicU64,
    usage: usage::Store,
}

struct ListenerTask {
    handle: JoinHandle<()>,
    #[cfg_attr(feature = "desktop", allow(dead_code))]
    shutdown: oneshot::Sender<()>,
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

enum AuthorizationError {
    AccessDenied,
    InvalidToken,
}

impl IntoResponse for AuthorizationError {
    fn into_response(self) -> Response {
        match self {
            Self::AccessDenied => error(
                StatusCode::FORBIDDEN,
                "LOCAL_ACCESS_DENIED",
                "仅允许受信任的本机客户端",
            ),
            Self::InvalidToken => error(
                StatusCode::UNAUTHORIZED,
                "LOCAL_TOKEN_INVALID",
                "本机连接凭证已失效，请重试连接",
            ),
        }
    }
}

impl Gateway {
    pub fn new(db: Arc<Database>, directory: PathBuf) -> Self {
        let brand: Value = serde_json::from_str(include_str!("../../../brand.config.json"))
            .expect("valid bundled product policy");
        let port = brand["proxyPort"]
            .as_u64()
            .expect("configured gateway port") as u16;
        Self::configured(
            db,
            Some(directory),
            SocketAddr::from(([127, 0, 0, 1], port)),
            format!("http://127.0.0.1:{port}"),
            format!(
                "{}{}",
                uuid::Uuid::new_v4().simple(),
                uuid::Uuid::new_v4().simple()
            ),
        )
    }

    /// Explicit deployment policy. Desktop always uses the loopback constructor above.
    #[cfg(not(feature = "desktop"))]
    pub fn server(db: Arc<Database>, bind: SocketAddr, endpoint: String, token: String) -> Self {
        Self::configured(db, None, bind, endpoint, token)
    }

    fn configured(
        db: Arc<Database>,
        directory: Option<PathBuf>,
        bind: SocketAddr,
        endpoint: String,
        token: String,
    ) -> Self {
        let authority = endpoint
            .trim_start_matches("http://")
            .trim_start_matches("https://")
            .to_string();
        let usage = usage::Store::new(db.clone());
        Self(Arc::new(Inner {
            db,
            directory,
            bind,
            authority,
            endpoint,
            port: bind.port(),
            token,
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
            usage,
        }))
    }

    fn endpoint(&self) -> String {
        self.0.endpoint.clone()
    }
    pub(crate) fn usage(&self) -> usage::Store {
        self.0.usage.clone()
    }
    pub async fn start(&self) {
        let mut task = self.0.lifecycle.lock().await;
        if task.as_ref().is_some_and(|t| !t.handle.is_finished()) {
            return;
        }
        let result = async {
            let listener = tokio::net::TcpListener::bind(self.0.bind).await
                .map_err(|_| format!("无法监听服务端口 {}，请检查端口占用后重试", self.0.port))?;
            if let Some(directory) = &self.0.directory {
                std::fs::create_dir_all(directory).map_err(|_| "无法创建本机连接目录".to_string())?;
                #[cfg(unix)]
                { use std::os::unix::fs::PermissionsExt; std::fs::set_permissions(directory,std::fs::Permissions::from_mode(0o700)).map_err(|_| "无法保护本机连接目录".to_string())?; }
                let path = directory.join("connector.json");
                let discovery = json!({"version":1,"product":"huaqing-switch","endpoint":self.endpoint(),"token":self.0.token});
                crate::config::write_json_file_private(&path, &discovery).map_err(|_| "无法写入本机连接凭证".to_string())?;
            }
            let router = router(self.clone());
            let (shutdown, receiver) = oneshot::channel();
            let handle = tokio::spawn(async move {
                if let Err(error) = axum::serve(listener, router).with_graceful_shutdown(async { let _ = receiver.await; }).await {
                    log::error!("Huaqing gateway listener stopped: {error}");
                }
            });
            Ok::<_, String>(ListenerTask { handle, shutdown })
        }.await;
        match result {
            Ok(handle) => {
                *task = Some(handle);
                if let Ok(mut e) = self.0.error.lock() {
                    *e = None;
                }
                log::info!("Huaqing Switch gateway listener ready");
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
            running: task.as_ref().is_some_and(|t| !t.handle.is_finished()),
            address: self.endpoint(),
            port: self.0.port,
            error: self.0.error.lock().ok().and_then(|e| e.clone()),
            active_requests: self.0.active.load(Ordering::Relaxed),
            total_requests: self.0.requests.load(Ordering::Relaxed),
        }
    }

    #[cfg(not(feature = "desktop"))]
    pub async fn shutdown(&self) {
        if let Some(task) = self.0.lifecycle.lock().await.take() {
            let _ = task.shutdown.send(());
            let mut handle = task.handle;
            if tokio::time::timeout(Duration::from_secs(310), &mut handle)
                .await
                .is_err()
            {
                handle.abort();
                let _ = handle.await;
            }
        }
        // Admission permits are retained through final SQLite writes.
        if let Ok(Ok(permit)) = tokio::time::timeout(
            Duration::from_secs(5),
            self.0.slots.clone().acquire_many_owned(16),
        )
        .await
        {
            drop(permit);
        }
    }

    fn authorize(&self, headers: &HeaderMap) -> Result<(), AuthorizationError> {
        if headers.contains_key("origin")
            || headers.get("host").and_then(|h| h.to_str().ok()) != Some(self.0.authority.as_str())
        {
            return Err(AuthorizationError::AccessDenied);
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
            return Err(AuthorizationError::InvalidToken);
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
    if let Err(error) = gateway.authorize(&headers) {
        return error.into_response();
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
    started_at: i64,
    record: Option<usage::Record>,
    observer: Option<usage::Observer>,
    permit: Option<OwnedSemaphorePermit>,
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
        if let Some(mut record) = self.record.take() {
            if let Some(observer) = &self.observer {
                observer.apply(&mut record);
                if matches!(record.outcome.as_str(), "pending" | "success") {
                    if let Some(code) = observer.semantic_error {
                        record.outcome = if code == "UPSTREAM_INCOMPLETE" {
                            "interrupted"
                        } else {
                            "failed"
                        }
                        .into();
                        record.failure_code = Some(code.into());
                    }
                }
            }
            if record.outcome == "pending" {
                if self
                    .observer
                    .as_ref()
                    .is_some_and(|observer| observer.terminal)
                {
                    // SSE clients commonly stop reading at the terminal marker
                    // without polling HTTP EOF; the model work already completed.
                    record.outcome = "success".into();
                } else {
                    record.outcome = "cancelled".into();
                    record.failure_code = Some("CLIENT_CANCELLED".into());
                }
            }
            record.duration_ms =
                Some(self.start.elapsed().as_millis().min(i64::MAX as u128) as u64);
            if let Some(permit) = self.permit.take() {
                self.gateway.0.usage.finish(record, permit);
            }
        }
    }
}
impl RequestGuard {
    fn failed(&mut self, status: u16, code: &'static str, interrupted: bool) {
        self.status = status;
        if let Some(record) = &mut self.record {
            record.outcome = if interrupted { "interrupted" } else { "failed" }.into();
            record.failure_code = Some(code.into());
            // Once headers arrived, keep the actual upstream HTTP status.
            if record.http_status.is_none() {
                record.http_status = Some(status);
            }
        }
    }
    fn completed(&mut self, status: u16, sse: bool) {
        self.status = status;
        if let Some(observer) = &mut self.observer {
            observer.eof();
        }
        if sse
            && !self
                .observer
                .as_ref()
                .is_some_and(|observer| observer.terminal)
        {
            self.failed(status, "STREAM_INCOMPLETE", true);
        } else if let Some(record) = &mut self.record {
            record.outcome = "success".into();
        }
    }
}

/// Standard opt-in without overriding caller options or retrying a charged call.
fn request_chat_usage(payload: &mut Value) {
    if payload.get("stream").and_then(Value::as_bool) != Some(true) {
        return;
    }
    let options = payload
        .as_object_mut()
        .expect("validated request object")
        .entry("stream_options")
        .or_insert_with(|| json!({}));
    if let Some(options) = options.as_object_mut() {
        options.entry("include_usage").or_insert(Value::Bool(true));
    }
}

async fn forward(
    gateway: Gateway,
    id: String,
    revision: u64,
    protocol: Protocol,
    request: axum::extract::Request,
) -> Response {
    if let Err(error) = gateway.authorize(request.headers()) {
        return error.into_response();
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
        started_at: chrono::Utc::now().timestamp(),
        record: None,
        observer: None,
        permit: Some(permit),
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
    let record =
        usage::Record::pending(guard.request_id.to_string(), guard.started_at, &connection);
    guard.record = Some(record.clone());
    gateway.0.usage.begin(record).await;
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
    if let Some(record) = &mut guard.record {
        record.streamed = payload
            .get("stream")
            .and_then(Value::as_bool)
            .unwrap_or(false);
    }
    if protocol == Protocol::OpenaiChat {
        request_chat_usage(&mut payload);
    }
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
        let own_endpoint =
            url::Url::parse(&gateway.0.endpoint).expect("validated gateway endpoint");
        let advertised_loop = url.host_str() == own_endpoint.host_str()
            && url.port_or_known_default() == own_endpoint.port_or_known_default();
        if advertised_loop
            || (url.port_or_known_default() == Some(gateway.0.port)
                && matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "[::1]")))
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
    if let Some(record) = &mut guard.record {
        record.http_status = Some(status.as_u16());
    }

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
    let sse = content_type.to_str().ok().is_some_and(|value| {
        value
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .eq_ignore_ascii_case("text/event-stream")
    });
    if let Some(record) = &mut guard.record {
        record.streamed = sse;
    }
    guard.observer = Some(usage::Observer::new(protocol, sse));
    let deadline = tokio::time::Instant::from_std(guard.start + REQUEST_DEADLINE);
    let stream = async_stream::stream! {
        let mut guard=guard;
        let mut stream=upstream.bytes_stream();
        loop {
            let until=std::cmp::min(deadline,tokio::time::Instant::now()+Duration::from_secs(120));
            match tokio::time::timeout_at(until,stream.next()).await {
                Ok(Some(Ok(bytes)))=>{ if let Some(observer)=&mut guard.observer { observer.push(&bytes); } yield Ok::<_,std::io::Error>(bytes); },
                Ok(None)=>{ guard.completed(status.as_u16(),sse); break; },
                Ok(Some(Err(_)))=>{ guard.failed(502,"STREAM_ERROR",true); yield Err(std::io::Error::new(std::io::ErrorKind::ConnectionReset,"模型响应已中断")); break; },
                Err(_)=>{ guard.failed(504,"STREAM_TIMEOUT",true); yield Err(std::io::Error::new(std::io::ErrorKind::TimedOut,"模型响应已中断或超时")); break; },
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

fn rejected(
    guard: &mut RequestGuard,
    status: StatusCode,
    code: &'static str,
    message: &str,
) -> Response {
    guard.failed(status.as_u16(), code, false);
    error(status, code, message)
}

fn error(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        Json(json!({"error":{"type":"huaqing_switch","code":code,"message":message}})),
    )
        .into_response()
}

#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_get_gateway_status(
    state: tauri::State<'_, Gateway>,
) -> Result<GatewayStatus, String> {
    Ok(state.status().await)
}
#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_retry_gateway(state: tauri::State<'_, Gateway>) -> Result<GatewayStatus, String> {
    state.start().await;
    Ok(state.status().await)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::Request;
    use http_body_util::BodyExt;

    #[test]
    fn chat_usage_opt_in_preserves_explicit_options() {
        let mut payload = json!({"stream":true,"stream_options":{"custom_option":7},"messages":[]});
        request_chat_usage(&mut payload);
        assert_eq!(
            payload["stream_options"],
            json!({"custom_option":7,"include_usage":true})
        );
        payload["stream_options"]["include_usage"] = json!(false);
        request_chat_usage(&mut payload);
        assert_eq!(payload["stream_options"]["include_usage"], false);
        let mut nonstream = json!({"stream":false});
        request_chat_usage(&mut nonstream);
        assert!(nonstream.get("stream_options").is_none());
    }
    async fn fixture(
        wire: &'static [u8],
        sse: bool,
        status: StatusCode,
        pending: bool,
    ) -> (Gateway, Connection, JoinHandle<()>) {
        let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, 0))
            .await
            .unwrap();
        let port = listener.local_addr().unwrap().port();
        let router=Router::new().fallback(move || async move {
            let body=if pending { Body::from_stream(async_stream::stream! { yield Ok::<_,std::io::Error>(bytes::Bytes::from_static(wire)); futures::future::pending::<()>().await; }) } else { Body::from(wire) };
            let mut response=Response::new(body);*response.status_mut()=status;
            response.headers_mut().insert("content-type",if sse {"text/event-stream"} else {"application/json"}.parse().unwrap());response
        });
        let server = tokio::spawn(async move {
            axum::serve(listener, router).await.unwrap();
        });
        let gateway = gateway();
        let connection = Connection {
            id: uuid::Uuid::new_v4().to_string(),
            name: "owned fixture".into(),
            protocol: Protocol::OpenaiChat,
            base_url: format!("http://127.0.0.1:{port}/v1"),
            model: "native-model".into(),
            api_key: "fixture-upstream-secret".into(),
            revision: 1,
            context_window: 32768,
            max_output_tokens: 4096,
            supports_images: false,
        };
        let value = json!({"revision":1,"activeId":connection.id,"connections":[connection]});
        gateway
            .0
            .db
            .conn
            .lock()
            .unwrap()
            .execute(
                "INSERT INTO settings(key,value) VALUES('hq.connections.v1',?)",
                [value.to_string()],
            )
            .unwrap();
        (gateway, connection, server)
    }
    async fn invoke(gateway: &Gateway, connection: &Connection, streamed: bool) -> Response {
        let request = Request::builder()
            .header("host", format!("127.0.0.1:{}", gateway.0.port))
            .header("authorization", format!("Bearer {}", gateway.0.token))
            .body(Body::from(
                json!({"model":model_id(connection),"stream":streamed,"messages":[]}).to_string(),
            ))
            .unwrap();
        forward(
            gateway.clone(),
            connection.id.clone(),
            1,
            Protocol::OpenaiChat,
            request,
        )
        .await
    }
    async fn history(gateway: &Gateway, count: u64) -> Value {
        for _ in 0..200 {
            let value = serde_json::to_value(
                gateway
                    .usage()
                    .query(usage::UsageFilter::default())
                    .await
                    .unwrap(),
            )
            .unwrap();
            if value["total"].as_u64() == Some(count) && value["inFlight"] == 0 {
                return value;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("owned metadata did not finish");
    }
    #[tokio::test]
    async fn forwarding_preserves_bytes_and_terminal_record_is_exactly_once() {
        const WIRE:&[u8]=b"data: {\"choices\":[],\"usage\":{\"prompt_tokens\":7,\"completion_tokens\":3}}\r\n\r\ndata: [DONE]\n\ndata: [DONE]\n\n";
        let (gateway, connection, server) = fixture(WIRE, true, StatusCode::OK, false).await;
        let response = invoke(&gateway, &connection, true).await;
        assert_eq!(
            to_bytes(response.into_body(), 4096).await.unwrap().as_ref(),
            WIRE
        );
        let report = history(&gateway, 1).await;
        assert_eq!(report["recent"][0]["outcome"], "success");
        assert_eq!(report["recent"][0]["usageStatus"], "final");
        assert_eq!(report["summary"]["totalTokens"], 10);
        assert_eq!(gateway.0.slots.available_permits(), 16);
        let mut body = invoke(&gateway, &connection, true).await.into_body();
        assert!(body.frame().await.unwrap().is_ok());
        drop(body);
        let report = history(&gateway, 2).await;
        assert_eq!(report["summary"]["successful"], 2);
        server.abort();
    }
    #[tokio::test]
    async fn dropping_unpolled_or_partially_consumed_response_records_cancellation() {
        const WIRE: &[u8] = b"data: {\"choices\":[{\"delta\":{\"content\":\"waiting\"}}]}\n\n";
        let (gateway, connection, server) = fixture(WIRE, true, StatusCode::OK, true).await;
        drop(invoke(&gateway, &connection, true).await);
        let first = history(&gateway, 1).await;
        assert_eq!(first["recent"][0]["outcome"], "cancelled");
        assert_eq!(first["recent"][0]["usageStatus"], "unknown");
        let mut body = invoke(&gateway, &connection, true).await.into_body();
        assert!(body.frame().await.unwrap().is_ok());
        drop(body);
        let second = history(&gateway, 2).await;
        assert_eq!(second["summary"]["cancelled"], 2);
        assert_eq!(gateway.0.active.load(Ordering::Relaxed), 0);
        server.abort();
    }
    #[tokio::test]
    async fn upstream_http_failure_and_premature_eof_have_distinct_outcomes() {
        let (gateway, connection, server) = fixture(
            b"sensitive-provider-error-do-not-store",
            false,
            StatusCode::TOO_MANY_REQUESTS,
            false,
        )
        .await;
        let response = invoke(&gateway, &connection, false).await;
        assert_eq!(response.status(), StatusCode::TOO_MANY_REQUESTS);
        drop(response);
        let report = history(&gateway, 1).await;
        assert_eq!(report["recent"][0]["outcome"], "failed");
        assert_eq!(report["recent"][0]["failureCode"], "UPSTREAM_ERROR");
        assert!(!report.to_string().contains("sensitive-provider-error"));
        assert!(!report.to_string().contains("fixture-upstream-secret"));
        server.abort();
        let (gateway, connection, server) = fixture(
            b"data: {\"usage\":{\"prompt_tokens\":7,\"completion_tokens\":1}}\n\n",
            true,
            StatusCode::OK,
            false,
        )
        .await;
        to_bytes(invoke(&gateway, &connection, true).await.into_body(), 4096)
            .await
            .unwrap();
        let report = history(&gateway, 1).await;
        assert_eq!(report["recent"][0]["outcome"], "interrupted");
        assert_eq!(report["recent"][0]["failureCode"], "STREAM_INCOMPLETE");
        assert_eq!(report["recent"][0]["usageStatus"], "partial");
        server.abort();
    }
    #[tokio::test]
    async fn http_200_semantic_error_is_not_success() {
        let (gateway, connection, server) = fixture(
            b"data: {\"error\":{\"message\":\"sensitive-error\"}}\n\n",
            true,
            StatusCode::OK,
            false,
        )
        .await;
        to_bytes(invoke(&gateway, &connection, true).await.into_body(), 4096)
            .await
            .unwrap();
        let report = history(&gateway, 1).await;
        assert_eq!(report["recent"][0]["outcome"], "failed");
        assert_eq!(report["recent"][0]["failureCode"], "UPSTREAM_FAILED");
        server.abort();
    }

    #[tokio::test]
    async fn changed_known_connection_is_counted_and_storage_failure_does_not_break_inference() {
        let (gateway, connection, server) =
            fixture(b"{\"choices\":[]}", false, StatusCode::OK, false).await;
        let request = Request::builder()
            .header("host", format!("127.0.0.1:{}", gateway.0.port))
            .header("authorization", format!("Bearer {}", gateway.0.token))
            .body(Body::empty())
            .unwrap();
        let response = forward(
            gateway.clone(),
            connection.id.clone(),
            2,
            Protocol::OpenaiChat,
            request,
        )
        .await;
        assert_eq!(response.status(), StatusCode::CONFLICT);
        drop(response);
        let report = history(&gateway, 1).await;
        assert_eq!(report["recent"][0]["failureCode"], "CONNECTION_CHANGED");
        gateway
            .0
            .db
            .conn
            .lock()
            .unwrap()
            .execute_batch("PRAGMA query_only=ON")
            .unwrap();
        let response = invoke(&gateway, &connection, false).await;
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            to_bytes(response.into_body(), 4096).await.unwrap().as_ref(),
            b"{\"choices\":[]}"
        );
        assert!(gateway.usage().storage_error());
        gateway
            .0
            .db
            .conn
            .lock()
            .unwrap()
            .execute_batch("PRAGMA query_only=OFF")
            .unwrap();
        server.abort();
    }

    fn gateway() -> Gateway {
        Gateway::new(Arc::new(Database::memory().unwrap()), PathBuf::new())
    }

    #[tokio::test]
    async fn rejected_authorization_preserves_errors_before_inference_admission() {
        let gateway = gateway();
        let _permits = gateway
            .0
            .slots
            .clone()
            .acquire_many_owned(16)
            .await
            .unwrap();
        let local_host = format!("127.0.0.1:{}", gateway.0.port);
        for (host, origin, token, status, code, message) in [
            (
                "localhost",
                None,
                gateway.0.token.as_str(),
                StatusCode::FORBIDDEN,
                "LOCAL_ACCESS_DENIED",
                "仅允许受信任的本机客户端",
            ),
            (
                local_host.as_str(),
                Some("https://example.invalid"),
                gateway.0.token.as_str(),
                StatusCode::FORBIDDEN,
                "LOCAL_ACCESS_DENIED",
                "仅允许受信任的本机客户端",
            ),
            (
                local_host.as_str(),
                None,
                "",
                StatusCode::UNAUTHORIZED,
                "LOCAL_TOKEN_INVALID",
                "本机连接凭证已失效，请重试连接",
            ),
            (
                local_host.as_str(),
                None,
                "0000000000000000000000000000000000000000000000000000000000000000",
                StatusCode::UNAUTHORIZED,
                "LOCAL_TOKEN_INVALID",
                "本机连接凭证已失效，请重试连接",
            ),
        ] {
            let mut headers = HeaderMap::new();
            headers.insert("host", host.parse().unwrap());
            if let Some(origin) = origin {
                headers.insert("origin", origin.parse().unwrap());
            }
            if !token.is_empty() {
                headers.insert("authorization", format!("Bearer {token}").parse().unwrap());
            }
            let mut request = Request::new(Body::from("invalid JSON"));
            *request.headers_mut() = headers.clone();
            for response in [
                manifest(State(gateway.clone()), headers).await,
                forward(
                    gateway.clone(),
                    "missing".into(),
                    1,
                    Protocol::OpenaiChat,
                    request,
                )
                .await,
            ] {
                assert_eq!(response.status(), status);
                let bytes = to_bytes(response.into_body(), 4096).await.unwrap();
                let body: Value = serde_json::from_slice(&bytes).unwrap();
                assert_eq!(
                    body,
                    json!({"error":{"type":"huaqing_switch","code":code,"message":message}})
                );
            }
        }
        let status = gateway.status().await;
        assert_eq!(status.active_requests, 0);
        assert_eq!(status.total_requests, 0);
    }

    #[tokio::test]
    async fn manifest_accepts_bearer_and_native_anthropic_credentials() {
        let gateway = gateway();
        for (name, token) in [
            ("authorization", format!("Bearer {}", gateway.0.token)),
            ("x-api-key", gateway.0.token.clone()),
        ] {
            let mut headers = HeaderMap::new();
            headers.insert(
                "host",
                format!("127.0.0.1:{}", gateway.0.port).parse().unwrap(),
            );
            headers.insert(name, token.parse().unwrap());
            let response = manifest(State(gateway.clone()), headers).await;
            assert_eq!(response.status(), StatusCode::OK);
            assert_eq!(response.headers()["cache-control"], "no-store");
            let bytes = to_bytes(response.into_body(), 4096).await.unwrap();
            let body: Value = serde_json::from_slice(&bytes).unwrap();
            assert_eq!(body["product"], "huaqing-switch");
            assert_eq!(body["models"], json!([]));
        }
    }
}
