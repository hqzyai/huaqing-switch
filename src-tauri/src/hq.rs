//! HQ-only product surface. Never projects configurations into another client.
use crate::database::Database;
use rusqlite::OptionalExtension;
use serde::{Deserialize, Serialize};
#[cfg(feature = "desktop")]
use std::sync::Arc;
#[cfg(feature = "desktop")]
use tauri::Manager;
#[cfg(feature = "desktop")]
#[path = "hq/bootstrap.rs"]
mod bootstrap;
#[path = "hq/gateway.rs"]
pub(crate) mod gateway;
#[path = "hq/secrets.rs"]
pub(crate) mod secrets;
#[path = "hq/usage.rs"]
pub(crate) mod usage;

const STORE_KEY: &str = "hq.connections.v1";
const SCRUB_PENDING_KEY: &str = "hq.credentials.scrub-pending.v1";
#[cfg(feature = "desktop")]
const BOOTSTRAP_VERSION_KEY: &str = "hq.bootstrap.applied.v1";
#[cfg(feature = "desktop")]
const BOOTSTRAP_CONNECTION_KEY: &str = "hq.bootstrap.connection-id.v1";
const STORE_FORMAT: u32 = 2;
#[cfg(feature = "desktop")]
pub struct HqState(pub Arc<Database>);

// Runtime credentials deliberately cannot be serialized. Only the persisted
// projection below may cross the SQLite/backup boundary.
#[derive(Clone)]
pub struct Connection {
    id: String,
    name: String,
    protocol: Protocol,
    base_url: String,
    model: String,
    api_key: String,
    revision: u64,
    context_window: u32,
    max_output_tokens: u32,
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

#[derive(Default)]
struct Store {
    revision: u64,
    active_id: Option<String>,
    connections: Vec<Connection>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PersistedStore {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    format_version: Option<u32>,
    revision: u64,
    active_id: Option<String>,
    connections: Vec<PersistedConnection>,
}

#[derive(Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct PersistedConnection {
    id: String,
    name: String,
    protocol: Protocol,
    base_url: String,
    model: String,
    #[serde(default = "default_revision")]
    revision: u64,
    #[serde(default = "default_context")]
    context_window: u32,
    #[serde(default = "default_output")]
    max_output_tokens: u32,
    #[serde(default)]
    supports_images: bool,
    // Only accepted while importing the pre-encryption format; never emitted.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    api_key: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    credential: Option<PersistedCredential>,
}

#[derive(Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
enum PersistedCredential {
    None,
    Protected { envelope: secrets::ProtectedSecret },
}

fn credential_context(connection: &Connection) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&(
        "app.hqmodelswitch.desktop/provider-credential/v1",
        &connection.id,
        &connection.name,
        connection.protocol,
        &connection.base_url,
        &connection.model,
        connection.revision,
        connection.context_window,
        connection.max_output_tokens,
        connection.supports_images,
    ))
    .map_err(|_| "无法编码密钥保护上下文".into())
}

fn encode_store(store: &Store, protector: &secrets::SecretProtector) -> Result<String, String> {
    let mut connections = Vec::with_capacity(store.connections.len());
    for c in &store.connections {
        let credential = if c.api_key.is_empty() {
            PersistedCredential::None
        } else {
            PersistedCredential::Protected {
                envelope: protector.protect(&c.api_key, &credential_context(c)?)?,
            }
        };
        connections.push(PersistedConnection {
            id: c.id.clone(),
            name: c.name.clone(),
            protocol: c.protocol,
            base_url: c.base_url.clone(),
            model: c.model.clone(),
            revision: c.revision,
            context_window: c.context_window,
            max_output_tokens: c.max_output_tokens,
            supports_images: c.supports_images,
            api_key: None,
            credential: Some(credential),
        });
    }
    serde_json::to_string(&PersistedStore {
        format_version: Some(STORE_FORMAT),
        revision: store.revision,
        active_id: store.active_id.clone(),
        connections,
    })
    .map_err(|_| "无法序列化加密连接配置".into())
}

fn decode_store(
    value: &str,
    protector: &secrets::SecretProtector,
) -> Result<(Store, bool), String> {
    if value.len() > 2 * 1024 * 1024 {
        return Err("HQ 连接配置超过大小限制".into());
    }
    let persisted: PersistedStore = serde_json::from_str(value)
        .map_err(|_| "HQ 连接配置格式损坏或需要使用新版客户端".to_string())?;
    let legacy = match persisted.format_version {
        None => true,
        Some(STORE_FORMAT) => false,
        _ => return Err("HQ 连接配置加密版本不受支持".into()),
    };
    if persisted.connections.len() > 100 {
        return Err("最多保存 100 个连接".into());
    }
    let mut ids = std::collections::HashSet::new();
    let mut connections = Vec::with_capacity(persisted.connections.len());
    for c in persisted.connections {
        if c.id.is_empty()
            || c.id.len() > 128
            || c.id.chars().any(char::is_control)
            || !ids.insert(c.id.clone())
        {
            return Err("HQ 连接标识无效或重复".into());
        }
        let mut connection = Connection {
            id: c.id,
            name: c.name,
            protocol: c.protocol,
            base_url: c.base_url,
            model: c.model,
            revision: c.revision,
            context_window: c.context_window,
            max_output_tokens: c.max_output_tokens,
            supports_images: c.supports_images,
            api_key: String::new(),
        };
        connection.api_key = if legacy {
            if c.credential.is_some() {
                return Err("HQ 连接配置混用了不同密钥格式".into());
            }
            c.api_key.ok_or("旧版连接配置缺少密钥字段")?
        } else {
            if c.api_key.is_some() {
                return Err("加密连接配置不允许包含明文密钥字段".into());
            }
            match c.credential.ok_or("加密连接配置缺少凭据")? {
                PersistedCredential::None => String::new(),
                PersistedCredential::Protected { envelope } => {
                    protector.unprotect(&envelope, &credential_context(&connection)?)?
                }
            }
        };
        if connection.api_key.len() > 8192 || connection.api_key.chars().any(char::is_control) {
            return Err("API Key 格式无效".into());
        }
        connections.push(connection);
    }
    if persisted
        .active_id
        .as_ref()
        .is_some_and(|id| !ids.contains(id))
    {
        return Err("HQ 默认连接不存在".into());
    }
    Ok((
        Store {
            revision: persisted.revision,
            active_id: persisted.active_id,
            connections,
        },
        legacy,
    ))
}

/// Used by the generic settings/import boundary before any SQLite write.
#[cfg(feature = "desktop")]
pub(crate) fn protect_setting_value(
    key: &str,
    value: &str,
    protector: &secrets::SecretProtector,
) -> Result<Option<String>, String> {
    if key != STORE_KEY {
        return Ok(None);
    }
    let (store, _) = decode_store(value, protector)?;
    encode_store(&store, protector).map(Some)
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

fn stored_value(conn: &rusqlite::Connection) -> Result<Option<String>, String> {
    conn.query_row(
        "SELECT value FROM main.settings WHERE key = ?1",
        [STORE_KEY],
        |row| row.get(0),
    )
    .optional()
    .map_err(|_| "无法读取 HQ Harness 连接配置".to_string())
}

fn reject_settings_triggers(conn: &rusqlite::Connection) -> Result<(), String> {
    let count: i64 = conn.query_row(
        "SELECT (SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='settings' COLLATE NOCASE) + (SELECT COUNT(*) FROM sqlite_temp_master WHERE type='trigger' AND tbl_name='settings' COLLATE NOCASE)",
        [], |row| row.get(0),
    ).map_err(|_| "无法检查密钥存储完整性".to_string())?;
    if count != 0 {
        return Err("连接存储存在非标准触发器，请恢复可信数据库后重试".into());
    }
    Ok(())
}

/// Complete credential normalization before backup/export/startup. The pending
/// flag makes physical-remnant cleanup resumable after an interrupted migration.
pub(crate) fn migrate_connection_storage(
    conn: &rusqlite::Connection,
    protector: &secrets::SecretProtector,
) -> Result<bool, String> {
    if !conn.is_autocommit() {
        return Err("密钥迁移必须在独立事务中完成".into());
    }
    conn.pragma_update(None, "secure_delete", "ON")
        .map_err(|_| "无法开启密钥安全删除".to_string())?;
    let pending = conn
        .query_row(
            "SELECT value FROM main.settings WHERE key=?1",
            [SCRUB_PENDING_KEY],
            |r| r.get::<_, String>(0),
        )
        .optional()
        .map_err(|_| "无法读取密钥迁移状态".to_string())?
        .is_some();
    let mut changed = false;
    if let Some(raw) = stored_value(conn)? {
        let (store, legacy) = decode_store(&raw, protector)?;
        if legacy {
            reject_settings_triggers(conn)?;
            let encrypted = encode_store(&store, protector)?;
            let tx = conn
                .unchecked_transaction()
                .map_err(|_| "无法开始密钥迁移".to_string())?;
            let count = tx
                .execute(
                    "UPDATE main.settings SET value=?1 WHERE key=?2 AND value=?3",
                    rusqlite::params![encrypted, STORE_KEY, raw],
                )
                .map_err(|_| "无法保存加密密钥配置".to_string())?;
            if count != 1 {
                return Err("连接配置已发生变化，请刷新后重试".into());
            }
            tx.execute("INSERT INTO main.settings(key,value) VALUES(?1,'true') ON CONFLICT(key) DO UPDATE SET value=excluded.value", [SCRUB_PENDING_KEY])
                .map_err(|_| "无法记录密钥迁移状态".to_string())?;
            tx.commit().map_err(|_| "无法提交密钥迁移".to_string())?;
            changed = true;
        }
    }
    if changed || pending {
        reject_settings_triggers(conn)?;
        // Rebuild live pages, including pre-existing freelist remnants, then
        // truncate WAL. No unencrypted safety copy is created by this step.
        conn.execute_batch("VACUUM;")
            .map_err(|_| "无法清理旧密钥存储页，请释放磁盘空间后重试".to_string())?;
        let busy: i32 = conn
            .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |r| r.get(0))
            .map_err(|_| "无法清理旧密钥日志".to_string())?;
        if busy != 0 {
            return Err("旧密钥日志仍被占用，请关闭旧客户端后重试".into());
        }
        conn.execute(
            "DELETE FROM main.settings WHERE key=?1",
            [SCRUB_PENDING_KEY],
        )
        .map_err(|_| "无法完成密钥迁移".to_string())?;
    }
    Ok(changed || pending)
}

fn load(db: &Database, conn: &rusqlite::Connection) -> Result<Store, String> {
    match stored_value(conn)? {
        None => Ok(Store::default()),
        Some(raw) => {
            let (store, legacy) = decode_store(&raw, &db.secrets)?;
            if legacy {
                migrate_connection_storage(conn, &db.secrets)?;
            }
            Ok(store)
        }
    }
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
    migrate_connection_storage(&conn, &db.secrets)?;
    reject_settings_triggers(&conn)?;
    let tx = conn
        .transaction()
        .map_err(|_| "无法开始保存连接配置".to_string())?;
    let mut store = load(db, &tx)?;
    if store.revision != revision {
        return Err("连接配置已发生变化，请刷新后重试".into());
    }
    edit(&mut store)?;
    store.revision = store.revision.checked_add(1).ok_or("配置版本超出范围")?;
    let json = encode_store(&store, &db.secrets)?;
    tx.execute("INSERT INTO main.settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        rusqlite::params![STORE_KEY, json]).map_err(|_| "无法保存连接配置".to_string())?;
    tx.commit().map_err(|_| "保存连接配置失败".to_string())?;
    Ok(store.snapshot())
}

pub(crate) fn save(
    db: &Database,
    revision: u64,
    mut input: ConnectionInput,
) -> Result<Snapshot, String> {
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

pub(crate) fn connections(db: &Database) -> Result<Snapshot, String> {
    let conn = db
        .conn
        .lock()
        .map_err(|_| "无法锁定 HQ 连接存储".to_string())?;
    Ok(load(db, &conn)?.snapshot())
}

#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_get_connections(state: tauri::State<'_, HqState>) -> Result<Snapshot, String> {
    let db = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || connections(&db))
        .await
        .map_err(|_| "连接存储暂不可用".to_string())?
}

#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_save_connection(
    state: tauri::State<'_, HqState>,
    revision: u64,
    connection: ConnectionInput,
) -> Result<Snapshot, String> {
    let db = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || save(&db, revision, connection))
        .await
        .map_err(|_| "连接存储暂不可用".to_string())?
}

#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_select_connection(
    state: tauri::State<'_, HqState>,
    revision: u64,
    id: String,
) -> Result<Snapshot, String> {
    let db = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || select(&db, revision, &id))
        .await
        .map_err(|_| "连接存储暂不可用".to_string())?
}

pub(crate) fn select(db: &Database, revision: u64, id: &str) -> Result<Snapshot, String> {
    mutate(db, revision, |store| {
        if !store.connections.iter().any(|c| c.id == id) {
            return Err("连接不存在".into());
        }
        store.active_id = Some(id.to_string());
        Ok(())
    })
}

#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_delete_connection(
    state: tauri::State<'_, HqState>,
    revision: u64,
    id: String,
) -> Result<Snapshot, String> {
    let db = state.0.clone();
    tauri::async_runtime::spawn_blocking(move || remove(&db, revision, &id))
        .await
        .map_err(|_| "连接存储暂不可用".to_string())?
}

pub(crate) fn remove(db: &Database, revision: u64, id: &str) -> Result<Snapshot, String> {
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

/// A package seed is applied once per published seed version, never on every
/// restart. User choices made afterwards remain authoritative.
#[cfg(feature = "desktop")]
pub(crate) fn apply_bundled_default(db: &Database) -> Result<(), String> {
    match bootstrap::bundled_default()? {
        None => Ok(()),
        Some(config) => apply_default(db, config),
    }
}

#[cfg(feature = "desktop")]
fn apply_default(db: &Database, config: bootstrap::BootstrapConfig) -> Result<(), String> {
    let mut conn = db
        .conn
        .lock()
        .map_err(|_| "无法锁定默认连接配置".to_string())?;
    migrate_connection_storage(&conn, &db.secrets)?;
    reject_settings_triggers(&conn)?;
    let tx = conn
        .transaction()
        .map_err(|_| "无法开始默认连接初始化".to_string())?;
    let applied: Option<String> = tx
        .query_row(
            "SELECT value FROM main.settings WHERE key=?1",
            [BOOTSTRAP_VERSION_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| "无法读取默认连接初始化状态".to_string())?;
    if applied.as_deref() == Some(config.version.as_str()) {
        return Ok(());
    }
    let input = config.connection;
    let preset_id = input.id.ok_or("默认连接缺少标识")?;
    let preset_key = input.api_key.ok_or("默认连接缺少凭据")?;
    let mut store = load(db, &tx)?;
    let managed_id: Option<String> = tx
        .query_row(
            "SELECT value FROM main.settings WHERE key=?1",
            [BOOTSTRAP_CONNECTION_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|_| "无法读取默认连接绑定状态".to_string())?;
    let same_workspace = |c: &Connection| {
        url::Url::parse(&c.base_url)
            .ok()
            .is_some_and(|url| url.as_str().trim_end_matches('/') == input.base_url)
    };
    let target = managed_id
        .as_ref()
        .and_then(|id| {
            store
                .connections
                .iter()
                .position(|c| &c.id == id && same_workspace(c))
        })
        .or_else(|| {
            store
                .connections
                .iter()
                .position(|c| c.id == preset_id && same_workspace(c))
        })
        .or_else(|| {
            if applied.is_none() {
                store.connections.iter().position(|c| {
                    store.active_id.as_deref() == Some(c.id.as_str()) && same_workspace(c)
                })
            } else {
                None
            }
        });
    let mut changed = false;
    let bound_id;
    if let Some(index) = target {
        // Adopt the existing default on the first upgrade; other connections,
        // including other keys for the same workspace, remain user-managed.
        let c = &mut store.connections[index];
        bound_id = c.id.clone();
        if c.api_key != preset_key {
            c.api_key.clone_from(&preset_key);
            c.revision = c.revision.checked_add(1).ok_or("连接版本超出范围")?;
            changed = true;
        }
    } else {
        if !store.connections.is_empty() {
            // A pre-existing custom workspace is not a fresh installation.
            // Do not inject another model or replace an unrelated default.
            tx.execute("INSERT INTO main.settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value", rusqlite::params![BOOTSTRAP_VERSION_KEY, config.version])
                .map_err(|_| "无法记录默认连接初始化状态".to_string())?;
            return tx
                .commit()
                .map_err(|_| "无法完成默认连接初始化".to_string());
        }
        bound_id = preset_id.clone();
        store.connections.push(Connection {
            id: preset_id.clone(),
            name: input.name,
            protocol: input.protocol,
            base_url: input.base_url,
            model: input.model,
            api_key: preset_key,
            revision: 1,
            context_window: input.context_window,
            max_output_tokens: input.max_output_tokens,
            supports_images: input.supports_images,
        });
        store.active_id = Some(preset_id);
        changed = true;
    }
    if changed {
        store.revision = store.revision.checked_add(1).ok_or("配置版本超出范围")?;
        tx.execute("INSERT INTO main.settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value", rusqlite::params![STORE_KEY, encode_store(&store, &db.secrets)?])
            .map_err(|_| "无法保存默认连接".to_string())?;
    }
    tx.execute("INSERT INTO main.settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value", rusqlite::params![BOOTSTRAP_VERSION_KEY, config.version])
        .map_err(|_| "无法记录默认连接初始化状态".to_string())?;
    tx.execute("INSERT INTO main.settings(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value", rusqlite::params![BOOTSTRAP_CONNECTION_KEY, bound_id])
        .map_err(|_| "无法记录默认连接绑定状态".to_string())?;
    tx.commit()
        .map_err(|_| "无法完成默认连接初始化".to_string())
}

/// Early HQ-only startup: no legacy client recovery/import, scanning, auth or proxy restoration.
#[cfg(feature = "desktop")]
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

#[cfg(feature = "desktop")]
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
        #[cfg(feature = "desktop")]
        assert!(db.get_all_providers("codex").unwrap().is_empty());
        let id = snapshot.connections[0].id.clone();
        let mut edit = input();
        edit.id = Some(id.clone());
        edit.api_key = None;
        save(&db, 1, edit).unwrap();
        let conn = db.conn.lock().unwrap();
        assert_eq!(
            load(&db, &conn).unwrap().connections[0].api_key,
            "test-secret"
        );
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
        assert_eq!(load(&db, &conn).unwrap().revision, 1);
    }

    fn legacy_value() -> serde_json::Value {
        serde_json::json!({"revision":7,"activeId":"legacy-owned","connections":[{
            "id":"legacy-owned","name":"Legacy","protocol":"openai-chat",
            "baseUrl":"https://example.invalid/v1","model":"legacy-model",
            "apiKey":"sk-legacy-fixture-private-value","revision":3,
            "contextWindow":1000000,"maxOutputTokens":8192,"supportsImages":false
        }]})
    }

    #[test]
    fn persisted_secrets_are_encrypted_and_not_portable_without_the_vault() {
        let db = Database::memory().unwrap();
        save(&db, 0, input()).unwrap();
        let raw = stored_value(&db.conn.lock().unwrap()).unwrap().unwrap();
        assert!(!raw.contains("test-secret"));
        assert!(!raw.contains("\"apiKey\""));
        assert!(raw.contains("\"formatVersion\":2"));
        let other = Database::memory().unwrap();
        other
            .conn
            .lock()
            .unwrap()
            .execute(
                "INSERT INTO main.settings VALUES(?1,?2)",
                rusqlite::params![STORE_KEY, &raw],
            )
            .unwrap();
        assert!(connections(&other).is_err());
        assert_eq!(
            stored_value(&other.conn.lock().unwrap()).unwrap().unwrap(),
            raw
        );
    }

    #[test]
    fn ciphertext_is_bound_to_every_connection_metadata_field() {
        for field in [
            "id",
            "name",
            "baseUrl",
            "model",
            "protocol",
            "revision",
            "contextWindow",
            "maxOutputTokens",
            "supportsImages",
        ] {
            let db = Database::memory().unwrap();
            save(&db, 0, input()).unwrap();
            let mut raw: serde_json::Value =
                serde_json::from_str(&stored_value(&db.conn.lock().unwrap()).unwrap().unwrap())
                    .unwrap();
            let value = &mut raw["connections"][0][field];
            *value = match field {
                "revision" | "contextWindow" | "maxOutputTokens" => {
                    serde_json::json!(value.as_u64().unwrap() + 1)
                }
                "supportsImages" => serde_json::json!(true),
                "protocol" => serde_json::json!("anthropic"),
                _ => serde_json::json!(format!("{}-changed", value.as_str().unwrap())),
            };
            db.conn
                .lock()
                .unwrap()
                .execute(
                    "UPDATE main.settings SET value=?1 WHERE key=?2",
                    rusqlite::params![raw.to_string(), STORE_KEY],
                )
                .unwrap();
            assert!(
                connections(&db).is_err(),
                "tampered field {field} must fail closed"
            );
        }
    }

    #[test]
    fn legacy_migration_keeps_ids_and_revisions_and_scrubs_live_file_bytes() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("fixture.sqlite");
        let conn = rusqlite::Connection::open(&path).unwrap();
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA secure_delete=OFF; CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT);").unwrap();
        let raw = legacy_value().to_string();
        conn.execute(
            "INSERT INTO main.settings VALUES(?1,?2)",
            rusqlite::params![STORE_KEY, &raw],
        )
        .unwrap();
        let secret = b"sk-legacy-fixture-private-value";
        assert!(std::fs::read(path.with_extension("sqlite-wal"))
            .unwrap()
            .windows(secret.len())
            .any(|v| v == secret));
        let protector = secrets::SecretProtector::memory().unwrap();
        assert!(migrate_connection_storage(&conn, &protector).unwrap());
        assert!(!migrate_connection_storage(&conn, &protector).unwrap());
        let sealed = stored_value(&conn).unwrap().unwrap();
        let (store, legacy) = decode_store(&sealed, &protector).unwrap();
        assert!(!legacy);
        assert_eq!(store.revision, 7);
        assert_eq!(store.active_id.as_deref(), Some("legacy-owned"));
        assert_eq!(store.connections[0].revision, 3);
        assert_eq!(store.connections[0].api_key.as_bytes(), secret);
        for suffix in ["", "-wal", "-shm", "-journal"] {
            if let Ok(bytes) = std::fs::read(format!("{}{suffix}", path.display())) {
                assert!(
                    !bytes.windows(secret.len()).any(|v| v == secret),
                    "plaintext survived in {suffix}"
                );
            }
        }
    }

    #[test]
    fn legacy_settings_trigger_cannot_copy_plaintext_during_migration() {
        let db = Database::memory().unwrap();
        let conn = db.conn.lock().unwrap();
        let raw = legacy_value().to_string();
        conn.execute(
            "INSERT INTO main.settings VALUES(?1,?2)",
            rusqlite::params![STORE_KEY, &raw],
        )
        .unwrap();
        conn.execute_batch("CREATE TABLE stolen(value TEXT); CREATE TRIGGER unsafe_copy AFTER UPDATE ON settings BEGIN INSERT INTO stolen VALUES(OLD.value); END;").unwrap();
        assert!(migrate_connection_storage(&conn, &db.secrets).is_err());
        assert_eq!(stored_value(&conn).unwrap().unwrap(), raw);
        assert_eq!(
            conn.query_row("SELECT COUNT(*) FROM stolen", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            0
        );
    }

    #[test]
    fn mixed_or_unknown_persistence_formats_never_become_plaintext() {
        let db = Database::memory().unwrap();
        let mut value = legacy_value();
        value["formatVersion"] = serde_json::json!(2);
        assert!(decode_store(&value.to_string(), &db.secrets).is_err());
        value["formatVersion"] = serde_json::json!(999);
        assert!(decode_store(&value.to_string(), &db.secrets).is_err());
        let mut empty = input();
        empty.api_key = None;
        save(&db, 0, empty).unwrap();
        assert!(!connections(&db).unwrap().connections[0].api_key_configured);
    }

    #[cfg(feature = "desktop")]
    fn seed(version: &str, key: &str) -> bootstrap::BootstrapConfig {
        let mut c = input();
        c.id = Some("2d5b235b-1723-4d65-b23a-48e72e584ef9".into());
        c.protocol = Protocol::OpenaiChat;
        c.base_url =
            "https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1".into();
        c.model = "deepseek-v4.1-flash".into();
        c.api_key = Some(key.into());
        bootstrap::BootstrapConfig {
            version: version.into(),
            connection: c,
        }
    }

    #[cfg(feature = "desktop")]
    #[test]
    fn install_ready_seed_is_encrypted_and_runs_once_per_default_version() {
        let db = Database::memory().unwrap();
        apply_default(&db, seed("fixture-1", "sk-default-fixture")).unwrap();
        let snapshot = connections(&db).unwrap();
        assert_eq!(snapshot.connections.len(), 1);
        assert_eq!(
            snapshot.active_id.as_deref(),
            Some("2d5b235b-1723-4d65-b23a-48e72e584ef9")
        );
        let mut edit = seed("ignored", "sk-user-edited-fixture").connection;
        edit.max_output_tokens = 12;
        save(&db, 1, edit).unwrap();
        apply_default(&db, seed("fixture-1", "sk-default-fixture")).unwrap();
        {
            let conn = db.conn.lock().unwrap();
            let store = load(&db, &conn).unwrap();
            assert_eq!(store.connections[0].api_key, "sk-user-edited-fixture");
            assert_eq!(store.connections[0].max_output_tokens, 12);
            assert_eq!(store.revision, 2);
        }
        apply_default(&db, seed("fixture-2", "sk-rotated-default-fixture")).unwrap();
        let conn = db.conn.lock().unwrap();
        let store = load(&db, &conn).unwrap();
        assert_eq!(store.connections[0].api_key, "sk-rotated-default-fixture");
        assert_eq!(store.connections[0].revision, 3);
        assert_eq!(store.connections[0].max_output_tokens, 12);
        assert_eq!(store.revision, 3);
        assert!(!stored_value(&conn)
            .unwrap()
            .unwrap()
            .contains("sk-rotated-default-fixture"));
    }

    #[cfg(feature = "desktop")]
    #[test]
    fn upgrade_adopts_only_the_current_default_and_keeps_other_keys_for_the_same_workspace() {
        let db = Database::memory().unwrap();
        let mut current = seed("unused", "sk-old-default-fixture").connection;
        current.id = None;
        let snapshot = save(&db, 0, current).unwrap();
        let original_id = snapshot.connections[0].id.clone();
        select(&db, 1, &original_id).unwrap();
        let mut other = seed("unused", "sk-user-owned-other-fixture").connection;
        other.id = None;
        other.name = "Other key for same workspace".into();
        save(&db, 2, other).unwrap();
        apply_default(&db, seed("package-v1", "sk-new-default-fixture")).unwrap();
        let conn = db.conn.lock().unwrap();
        let store = load(&db, &conn).unwrap();
        assert_eq!(store.connections.len(), 2);
        assert_eq!(store.active_id.as_deref(), Some(original_id.as_str()));
        assert_eq!(store.connections[0].api_key, "sk-new-default-fixture");
        assert_eq!(store.connections[0].revision, 2);
        assert_eq!(store.connections[1].api_key, "sk-user-owned-other-fixture");
        assert_eq!(store.connections[1].revision, 1);
    }

    #[cfg(feature = "desktop")]
    #[test]
    fn preset_does_not_inject_models_into_an_existing_custom_workspace() {
        let db = Database::memory().unwrap();
        let saved = save(&db, 0, input()).unwrap();
        let id = saved.connections[0].id.clone();
        select(&db, 1, &id).unwrap();
        let before = connections(&db).unwrap();
        apply_default(&db, seed("official-preset", "sk-default-fixture")).unwrap();
        let after = connections(&db).unwrap();
        assert_eq!(after.revision, before.revision);
        assert_eq!(after.active_id, before.active_id);
        assert_eq!(after.connections.len(), 1);
        assert_eq!(after.connections[0].id, id);
        let conn = db.conn.lock().unwrap();
        assert_eq!(
            load(&db, &conn).unwrap().connections[0].api_key,
            "test-secret"
        );
    }

    #[cfg(feature = "desktop")]
    #[test]
    fn next_seed_preserves_a_preset_repurposed_for_another_provider() {
        let db = Database::memory().unwrap();
        apply_default(&db, seed("fixture-1", "sk-default-fixture")).unwrap();
        let mut edit = seed("ignored", "sk-custom-provider-fixture").connection;
        edit.base_url = "https://custom.example.invalid/v1".into();
        save(&db, 1, edit).unwrap();
        apply_default(&db, seed("fixture-2", "sk-next-default-fixture")).unwrap();
        let conn = db.conn.lock().unwrap();
        let store = load(&db, &conn).unwrap();
        assert_eq!(store.revision, 2);
        assert_eq!(store.connections.len(), 1);
        assert_eq!(store.connections[0].api_key, "sk-custom-provider-fixture");
        assert_eq!(
            store.connections[0].base_url,
            "https://custom.example.invalid/v1"
        );
    }
}
