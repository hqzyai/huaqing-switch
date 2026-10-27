//! HQ-only product surface. Never projects configurations into another client.
use crate::database::Database;
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tauri::Manager;
pub(crate) mod gateway;
pub(crate) mod usage;

const STORE_KEY: &str = "hq.connections.v1";
pub struct HqState(pub Arc<Database>);

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Connection {
    id: String,
    name: String,
    protocol: Protocol,
    base_url: String,
    model: String,
    api_key: String,
    #[serde(default = "default_revision")]
    revision: u64,
    #[serde(default = "default_context")]
    context_window: u32,
    #[serde(default = "default_output")]
    max_output_tokens: u32,
    #[serde(default)]
    supports_images: bool,
}

fn default_revision() -> u64 {
    1
}
fn default_context() -> u32 {
    32768
}
fn default_output() -> u32 {
    4096
}

#[derive(Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Protocol {
    OpenaiResponses,
    OpenaiChat,
    Anthropic,
}

#[derive(Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct Store {
    revision: u64,
    active_id: Option<String>,
    connections: Vec<Connection>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ConnectionView {
    id: String,
    name: String,
    protocol: Protocol,
    base_url: String,
    model: String,
    api_key_configured: bool,
    context_window: u32,
    max_output_tokens: u32,
    supports_images: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    revision: u64,
    active_id: Option<String>,
    connections: Vec<ConnectionView>,
}

impl Store {
    fn snapshot(&self) -> Snapshot {
        Snapshot {
            revision: self.revision,
            active_id: self.active_id.clone(),
            connections: self
                .connections
                .iter()
                .map(|c| ConnectionView {
                    id: c.id.clone(),
                    name: c.name.clone(),
                    protocol: c.protocol,
                    base_url: c.base_url.clone(),
                    model: c.model.clone(),
                    api_key_configured: !c.api_key.is_empty(),
                    context_window: c.context_window,
                    max_output_tokens: c.max_output_tokens,
                    supports_images: c.supports_images,
                })
                .collect(),
        }
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ConnectionInput {
    id: Option<String>,
    name: String,
    protocol: Protocol,
    base_url: String,
    model: String,
    // None preserves an existing secret; an explicit empty string clears it.
    api_key: Option<String>,
    #[serde(default = "default_context")]
    context_window: u32,
    #[serde(default = "default_output")]
    max_output_tokens: u32,
    #[serde(default)]
    supports_images: bool,
}

fn load(conn: &rusqlite::Connection) -> Result<Store, String> {
    let value: Option<String> = conn
        .query_row(
            "SELECT value FROM settings WHERE key = ?1",
            [STORE_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| "无法读取 HQ Harness 连接配置".to_string())?;
    value
        .map(|v| {
            serde_json::from_str(&v)
                .map_err(|_| "HQ Harness 连接配置损坏，请从备份恢复".to_string())
        })
        .unwrap_or_else(|| Ok(Store::default()))
}

fn mutate(
    db: &Database,
    revision: u64,
    edit: impl FnOnce(&mut Store) -> Result<(), String>,
) -> Result<Snapshot, String> {
    let mut conn = db
        .conn
        .lock()
        .map_err(|_| "无法锁定 HQ 连接存储".to_string())?;
    let tx = conn
        .transaction()
        .map_err(|_| "无法开始保存连接配置".to_string())?;
    let mut store = load(&tx)?;
    if store.revision != revision {
        return Err("连接配置已发生变化，请刷新后重试".into());
    }
    edit(&mut store)?;
    store.revision = store.revision.checked_add(1).ok_or("配置版本超出范围")?;
    let json = serde_json::to_string(&store).map_err(|_| "无法序列化连接配置".to_string())?;
    tx.execute("INSERT INTO settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rusqlite::params![STORE_KEY, json]).map_err(|_| "无法保存连接配置".to_string())?;
    tx.commit().map_err(|_| "保存连接配置失败".to_string())?;
    Ok(store.snapshot())
}

fn save(db: &Database, revision: u64, mut input: ConnectionInput) -> Result<Snapshot, String> {
    if !(1024..=2_000_000).contains(&input.context_window)
        || input.max_output_tokens == 0
        || input.max_output_tokens > input.context_window
    {
        return Err("请设置有效的本地上下文与输出限制（输出不得超过上下文）".into());
    }
    input.name = input.name.trim().to_string();
    input.model = input.model.trim().to_string();
    if input.name.is_empty() || input.name.chars().count() > 80 {
        return Err("连接名称须为 1–80 个字符".into());
    }
    if input.model.is_empty()
        || input.model.len() > 256
        || input.model.chars().any(char::is_control)
    {
        return Err("请输入有效的模型 ID（最多 256 字节）".into());
    }
    let url =
        url::Url::parse(input.base_url.trim()).map_err(|_| "请输入有效的 API 地址".to_string())?;
    if !matches!(url.scheme(), "https" | "http")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.as_str().len() > 2048
    {
        return Err("API 地址须为 HTTP(S) 基础地址，不能包含账号、查询参数或片段".into());
    }
    input.base_url = url.to_string().trim_end_matches('/').to_string();
    if let Some(key) = &mut input.api_key {
        *key = key.trim().to_string();
        if key.len() > 8192 || key.chars().any(char::is_control) {
            return Err("API Key 格式无效".into());
        }
    }
    mutate(db, revision, |store| {
        let existing = input
            .id
            .as_ref()
            .and_then(|id| store.connections.iter().position(|c| &c.id == id));
        if input.id.is_some() && existing.is_none() {
            return Err("该连接已被删除，请刷新后重试".into());
        }
        if existing.is_none() && store.connections.len() >= 100 {
            return Err("最多保存 100 个连接".into());
        }
        let connection = Connection {
            revision: existing
                .map(|i| {
                    store.connections[i]
                        .revision
                        .checked_add(1)
                        .ok_or("连接版本超出范围")
                })
                .transpose()?
                .unwrap_or(1),
            context_window: input.context_window,
            max_output_tokens: input.max_output_tokens,
            supports_images: input.supports_images,
            id: input.id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string()),
            name: input.name,
            protocol: input.protocol,
            base_url: input.base_url,
            model: input.model,
            api_key: input.api_key.unwrap_or_else(|| {
                existing
                    .map(|i| store.connections[i].api_key.clone())
                    .unwrap_or_default()
            }),
        };
        if let Some(index) = existing {
            store.connections[index] = connection;
        } else {
            store.connections.push(connection);
        }
        Ok(())
    })
}

#[tauri::command]
pub fn hq_get_connections(state: tauri::State<'_, HqState>) -> Result<Snapshot, String> {
    let conn = state
        .0
        .conn
        .lock()
        .map_err(|_| "无法锁定 HQ 连接存储".to_string())?;
    Ok(load(&conn)?.snapshot())
}

#[tauri::command]
pub fn hq_save_connection(
    state: tauri::State<'_, HqState>,
    revision: u64,
    connection: ConnectionInput,
) -> Result<Snapshot, String> {
    save(&state.0, revision, connection)
}

#[tauri::command]
pub fn hq_select_connection(
    state: tauri::State<'_, HqState>,
    revision: u64,
    id: String,
) -> Result<Snapshot, String> {
    select(&state.0, revision, &id)
}

fn select(db: &Database, revision: u64, id: &str) -> Result<Snapshot, String> {
    mutate(db, revision, |store| {
        if !store.connections.iter().any(|c| c.id == id) {
            return Err("连接不存在".into());
        }
        store.active_id = Some(id.to_string());
        Ok(())
    })
}

#[tauri::command]
pub fn hq_delete_connection(
    state: tauri::State<'_, HqState>,
    revision: u64,
    id: String,
) -> Result<Snapshot, String> {
    remove(&state.0, revision, &id)
}

fn remove(db: &Database, revision: u64, id: &str) -> Result<Snapshot, String> {
    mutate(db, revision, |store| {
        let index = store
            .connections
            .iter()
            .position(|c| c.id == id)
            .ok_or("连接不存在")?;
        store.connections.remove(index);
        if store.active_id.as_deref() == Some(id) {
            store.active_id = None;
        }
        Ok(())
    })
}

/// Early HQ-only startup: no legacy client recovery/import, scanning, auth or proxy restoration.
pub fn setup(app: &mut tauri::App, db: Arc<Database>) -> Result<(), Box<dyn std::error::Error>> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let directory = crate::config::get_app_config_dir();
        std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700))?;
        std::fs::set_permissions(
            directory.join("hq-model-switch.db"),
            std::fs::Permissions::from_mode(0o600),
        )?;
    }
    use tauri::{
        menu::{Menu, MenuItem},
        tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    };
    let gateway = gateway::Gateway::new(
        db.clone(),
        crate::config::get_home_dir().join(".huaqing-switch"),
    );
    app.manage(gateway.clone());
    tauri::async_runtime::spawn(async move {
        gateway.start().await;
    });
    app.manage(HqState(db));
    let open = MenuItem::with_id(app, "hq-open", "HQ Harness", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "hq-quit", "退出 Huaqing Switch", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let mut builder = TrayIconBuilder::with_id(crate::tray::TRAY_ID)
        .tooltip("Huaqing Switch")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "hq-open" => show(app),
            "hq-quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                }
            ) {
                show(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    show(app.handle());
    log::info!("HQ-only workspace ready; legacy client startup is disabled");
    Ok(())
}

pub fn show(app: &tauri::AppHandle) {
    #[cfg(target_os = "macos")]
    crate::tray::apply_tray_policy(app, true);
    if let Some(window) = app.get_webview_window("main") {
        #[cfg(target_os = "windows")]
        let _ = window.set_skip_taskbar(false);
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> ConnectionInput {
        ConnectionInput {
            id: None,
            name: "Local API".into(),
            protocol: Protocol::OpenaiResponses,
            base_url: "https://api.example.invalid/v1/".into(),
            model: "model-1".into(),
            api_key: Some("test-secret".into()),
            context_window: default_context(),
            max_output_tokens: default_output(),
            supports_images: false,
        }
    }
    #[test]
    fn independent_store_redacts_secrets_and_preserves_them_on_edit() {
        let db = Database::memory().unwrap();
        let snapshot = save(&db, 0, input()).unwrap();
        let json = serde_json::to_string(&snapshot).unwrap();
        assert!(!json.contains("test-secret"));
        assert!(snapshot.connections[0].api_key_configured);
        assert!(db.get_all_providers("codex").unwrap().is_empty());
        let id = snapshot.connections[0].id.clone();
        let mut edit = input();
        edit.id = Some(id.clone());
        edit.api_key = None;
        save(&db, 1, edit).unwrap();
        let conn = db.conn.lock().unwrap();
        assert_eq!(load(&conn).unwrap().connections[0].api_key, "test-secret");
        drop(conn);
        select(&db, 2, &id).unwrap();
        let deleted = remove(&db, 3, &id).unwrap();
        assert!(deleted.connections.is_empty());
        assert!(deleted.active_id.is_none());
    }
    #[test]
    fn stale_or_invalid_mutations_do_not_overwrite_saved_connections() {
        let db = Database::memory().unwrap();
        save(&db, 0, input()).unwrap();
        assert!(save(&db, 0, input()).is_err());
        for url in [
            "file:///tmp/test",
            "https://user:secret@example.invalid",
            "https://example.invalid/?key=secret",
        ] {
            let mut value = input();
            value.base_url = url.into();
            assert!(save(&db, 1, value).is_err());
        }
        assert!(select(&db, 1, "missing").is_err());
        let conn = db.conn.lock().unwrap();
        assert_eq!(load(&conn).unwrap().revision, 1);
    }
}
