use axum::{
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::{
    collections::HashMap,
    net::IpAddr,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};

pub const COOKIE: &str = "__Host-hq_switch_session";
pub const HTTP_COOKIE: &str = "hq_switch_http_session";
#[derive(Clone, Copy)]
enum CookiePolicy {
    Https,
    Http,
}
impl CookiePolicy {
    fn name(self) -> &'static str {
        match self {
            Self::Https => COOKIE,
            Self::Http => HTTP_COOKIE,
        }
    }
    fn namespace(self) -> &'static str {
        match self {
            Self::Https => "https:",
            Self::Http => "http:",
        }
    }
    fn secure(self) -> &'static str {
        match self {
            Self::Https => "; Secure",
            Self::Http => "",
        }
    }
}
const SESSION_AGE: Duration = Duration::from_secs(8 * 60 * 60);
const MAX_SESSIONS: usize = 256;
const MAX_LOGIN_CLIENTS: usize = 1024;
#[derive(Default)]
struct AuthState {
    sessions: HashMap<String, Instant>,
    attempts: HashMap<IpAddr, (Instant, u32)>,
    window: Option<(Instant, u32)>,
}
pub struct Auth {
    token: String,
    state: Arc<Mutex<AuthState>>,
    policy: CookiePolicy,
}
#[derive(Debug)]
pub struct ApiError(pub StatusCode, pub &'static str);
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (self.0, Json(serde_json::json!({"error":self.1}))).into_response()
    }
}
impl Auth {
    pub fn new(token: String) -> Self {
        Self {
            token,
            state: Arc::new(Mutex::new(AuthState::default())),
            policy: CookiePolicy::Https,
        }
    }
    /// Both entry points share login limits and a total session cap, but not sessions.
    pub fn http_variant(&self) -> Self {
        Self {
            token: self.token.clone(),
            state: self.state.clone(),
            policy: CookiePolicy::Http,
        }
    }
    fn session_key(&self, id: &str) -> String {
        format!("{}{id}", self.policy.namespace())
    }
    pub fn clear_cookie(&self) -> String {
        format!(
            "{}=; Path=/; HttpOnly{}; SameSite=Strict; Max-Age=0",
            self.policy.name(),
            self.policy.secure()
        )
    }
    pub fn token_matches(&self, token: &str) -> bool {
        constant_time_equal(&self.token, token)
    }
    /// Browsers authenticate only by a host-only HttpOnly cookie. CLI callers may use admin Bearer.
    pub fn authorized(&self, headers: &HeaderMap) -> bool {
        if let Some(value) = headers.get("authorization") {
            return value
                .to_str()
                .ok()
                .and_then(|s| s.strip_prefix("Bearer "))
                .is_some_and(|t| self.token_matches(t));
        }
        let Some(session) = cookie(headers, self.policy.name()) else {
            return false;
        };
        let now = Instant::now();
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        state.sessions.retain(|_, expiry| *expiry > now);
        state
            .sessions
            .get(&self.session_key(session))
            .is_some_and(|expiry| *expiry > now)
    }
    pub fn cli(&self, headers: &HeaderMap) -> bool {
        headers
            .get("authorization")
            .and_then(|h| h.to_str().ok())
            .and_then(|s| s.strip_prefix("Bearer "))
            .is_some_and(|token| self.token_matches(token))
    }
    pub fn login(&self, ip: IpAddr, token: &str) -> Result<String, ApiError> {
        let now = Instant::now();
        let mut state = self
            .state
            .lock()
            .map_err(|_| ApiError(StatusCode::SERVICE_UNAVAILABLE, "登录服务暂不可用"))?;
        state
            .attempts
            .retain(|_, (start, _)| now.duration_since(*start) < Duration::from_secs(60));
        let global = state.window.get_or_insert((now, 0));
        if now.duration_since(global.0) >= Duration::from_secs(60) {
            *global = (now, 0);
        }
        if global.1 >= 30 {
            return Err(ApiError(
                StatusCode::TOO_MANY_REQUESTS,
                "登录尝试过多，请稍后再试",
            ));
        }
        global.1 += 1;
        if !state.attempts.contains_key(&ip) && state.attempts.len() >= MAX_LOGIN_CLIENTS {
            return Err(ApiError(
                StatusCode::TOO_MANY_REQUESTS,
                "登录尝试过多，请稍后再试",
            ));
        }
        let attempt = state.attempts.entry(ip).or_insert((now, 0));
        if attempt.1 >= 10 {
            return Err(ApiError(
                StatusCode::TOO_MANY_REQUESTS,
                "登录尝试过多，请稍后再试",
            ));
        }
        attempt.1 += 1;
        if !self.token_matches(token) {
            return Err(ApiError(StatusCode::UNAUTHORIZED, "管理凭证无效"));
        }
        state.sessions.retain(|_, expiry| *expiry > now);
        if state.sessions.len() >= MAX_SESSIONS {
            return Err(ApiError(
                StatusCode::SERVICE_UNAVAILABLE,
                "管理会话已达上限，请稍后再试",
            ));
        }
        let session = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        state
            .sessions
            .insert(self.session_key(&session), now + SESSION_AGE);
        Ok(format!(
            "{}={session}; Path=/; HttpOnly{}; SameSite=Strict; Max-Age=28800",
            self.policy.name(),
            self.policy.secure()
        ))
    }
    pub fn logout(&self, headers: &HeaderMap) {
        if let Some(session) = cookie(headers, self.policy.name()) {
            if let Ok(mut state) = self.state.lock() {
                state.sessions.remove(&self.session_key(session));
            }
        }
    }
}
fn cookie<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    // Duplicate names are rejected rather than accepting ambiguous cookie parser precedence.
    let mut matches = headers
        .get_all("cookie")
        .iter()
        .filter_map(|h| h.to_str().ok())
        .flat_map(|s| s.split(';'))
        .filter_map(|p| p.trim().split_once('='))
        .filter(|(candidate, _)| *candidate == name)
        .map(|(_, value)| value);
    let first = matches.next()?;
    if matches.next().is_some() || first.len() != 64 {
        return None;
    }
    Some(first)
}
pub fn constant_time_equal(expected: &str, supplied: &str) -> bool {
    if supplied.len() != expected.len() {
        return false;
    }
    let sign = |key: &[u8]| {
        let mut mac = Hmac::<Sha256>::new_from_slice(key).expect("valid HMAC key");
        mac.update(b"hq-switch-server-admin-v1");
        mac
    };
    sign(expected.as_bytes())
        .verify_slice(&sign(supplied.as_bytes()).finalize().into_bytes())
        .is_ok()
}
/// Safe GET session status may omit Origin. Every browser mutation requires exact Origin plus CSRF header.
pub fn check_origin(
    headers: &HeaderMap,
    expected: &str,
    mutation: bool,
    cli: bool,
) -> Result<(), ApiError> {
    if headers
        .get("sec-fetch-site")
        .is_some_and(|h| h != "same-origin" && h != "none")
    {
        return Err(ApiError(StatusCode::FORBIDDEN, "仅允许同源管理请求"));
    }
    let origin = headers.get("origin").and_then(|h| h.to_str().ok());
    if origin.is_some_and(|origin| origin != expected)
        || (!cli
            && mutation
            && (origin != Some(expected) || headers.get("x-hq-csrf").is_none_or(|h| h != "1")))
    {
        return Err(ApiError(
            StatusCode::FORBIDDEN,
            "管理请求来源或 CSRF 校验失败",
        ));
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sessions_expire_logout_bound_and_rate_limit() {
        let token = "a".repeat(64);
        let auth = Auth::new(token.clone());
        let ip = "127.0.0.1".parse().unwrap();
        let cookie = auth.login(ip, &token).unwrap();
        assert!(cookie.contains("HttpOnly; Secure; SameSite=Strict"));
        let mut headers = HeaderMap::new();
        headers.insert("cookie", cookie.split(';').next().unwrap().parse().unwrap());
        assert!(auth.authorized(&headers));
        auth.logout(&headers);
        assert!(!auth.authorized(&headers));
        let cookie = auth.login(ip, &token).unwrap();
        headers.insert("cookie", cookie.split(';').next().unwrap().parse().unwrap());
        auth.state
            .lock()
            .unwrap()
            .sessions
            .values_mut()
            .for_each(|expiry| *expiry = Instant::now() - Duration::from_secs(1));
        assert!(!auth.authorized(&headers));
        for _ in 0..8 {
            assert_eq!(
                auth.login(ip, "bad").unwrap_err().0,
                StatusCode::UNAUTHORIZED
            );
        }
        assert_eq!(
            auth.login(ip, &token).unwrap_err().0,
            StatusCode::TOO_MANY_REQUESTS
        );
        let auth = Auth::new(token.clone());
        {
            let mut s = auth.state.lock().unwrap();
            for i in 0..MAX_SESSIONS {
                s.sessions
                    .insert(i.to_string(), Instant::now() + SESSION_AGE);
            }
        }
        assert_eq!(
            auth.login(ip, &token).unwrap_err().0,
            StatusCode::SERVICE_UNAVAILABLE
        );
    }
    #[test]
    fn origin_and_bearer_separation() {
        let mut h = HeaderMap::new();
        assert!(check_origin(&h, "https://switch.example", false, false).is_ok());
        assert!(check_origin(&h, "https://switch.example", true, false).is_err());
        h.insert("origin", "https://switch.example".parse().unwrap());
        h.insert("x-hq-csrf", "1".parse().unwrap());
        assert!(check_origin(&h, "https://switch.example", true, false).is_ok());
        h.insert("origin", "https://evil.example".parse().unwrap());
        assert!(check_origin(&h, "https://switch.example", true, true).is_err());
        h.remove("origin");
        assert!(check_origin(&h, "https://switch.example", true, true).is_ok());
        let auth = Auth::new("a".repeat(64));
        h.insert(
            "authorization",
            format!("Bearer {}", "b".repeat(64)).parse().unwrap(),
        );
        assert!(!auth.authorized(&h));
    }
}

#[cfg(test)]
mod transport_tests {
    use super::*;
    #[test]
    fn both_transports_share_limits_but_never_accept_renamed_session_ids() {
        let token = "a".repeat(64);
        let secure = Auth::new(token.clone());
        let plain = secure.http_variant();
        assert!(Arc::ptr_eq(&secure.state, &plain.state));
        let peer = "127.0.0.1".parse().unwrap();
        let secure_cookie = secure.login(peer, &token).unwrap();
        let plain_cookie = plain.login(peer, &token).unwrap();
        assert!(secure_cookie.starts_with(COOKIE));
        assert!(secure_cookie.contains("; Secure;"));
        assert!(plain_cookie.starts_with(HTTP_COOKIE));
        assert!(!plain_cookie.contains("Secure"));
        assert!(plain_cookie.contains("HttpOnly; SameSite=Strict"));
        let secure_id = secure_cookie
            .split(';')
            .next()
            .unwrap()
            .split_once('=')
            .unwrap()
            .1;
        let plain_id = plain_cookie
            .split(';')
            .next()
            .unwrap()
            .split_once('=')
            .unwrap()
            .1;
        let mut headers = HeaderMap::new();
        headers.insert(
            "cookie",
            format!("{HTTP_COOKIE}={secure_id}").parse().unwrap(),
        );
        assert!(!plain.authorized(&headers));
        headers.insert("cookie", format!("{COOKIE}={plain_id}").parse().unwrap());
        assert!(!secure.authorized(&headers));
        headers.insert(
            "cookie",
            plain_cookie.split(';').next().unwrap().parse().unwrap(),
        );
        assert!(plain.authorized(&headers));
        plain.logout(&headers);
        assert!(!plain.authorized(&headers));
        headers.insert(
            "cookie",
            secure_cookie.split(';').next().unwrap().parse().unwrap(),
        );
        assert!(secure.authorized(&headers));
        assert!(plain.clear_cookie().starts_with(HTTP_COOKIE));
        assert!(!plain.clear_cookie().contains("Secure"));
        assert!(secure.clear_cookie().contains("; Secure;"));
        let secure = Auth::new(token.clone());
        let plain = secure.http_variant();
        for attempt in 0..10 {
            let auth = if attempt % 2 == 0 { &secure } else { &plain };
            assert_eq!(
                auth.login(peer, "invalid").unwrap_err().0,
                StatusCode::UNAUTHORIZED
            );
        }
        assert_eq!(
            plain.login(peer, &token).unwrap_err().0,
            StatusCode::TOO_MANY_REQUESTS
        );
        assert_eq!(
            secure.login(peer, &token).unwrap_err().0,
            StatusCode::TOO_MANY_REQUESTS
        );
        let secure = Auth::new(token.clone());
        let plain = secure.http_variant();
        for id in 0..MAX_SESSIONS {
            secure
                .state
                .lock()
                .unwrap()
                .sessions
                .insert(format!("https:{id}"), Instant::now() + SESSION_AGE);
        }
        assert_eq!(
            plain.login(peer, &token).unwrap_err().0,
            StatusCode::SERVICE_UNAVAILABLE
        );
    }
}
