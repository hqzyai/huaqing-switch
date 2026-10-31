//! HQ-only request accounting. Stores allowlisted metadata, never wire bodies or secrets.
use super::{Connection, Protocol};
use crate::database::Database;
use rusqlite::{params, params_from_iter, types::Value as SqlValue, Connection as Sqlite, Row};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};

const TABLE: &str = "hq_usage_requests";
const STORAGE_ERROR: &str = "HQ 用量存储暂不可用，请检查磁盘与数据库后重试";
// Covers long model outputs while bounding each JSON document/SSE event.
const MAX_PARSE_BYTES: usize = 16 * 1024 * 1024;
// Refuse nonsensical counters rather than truncating provider-controlled integers.
const MAX_TOKENS: u64 = 1_000_000_000_000;

fn schema(conn: &Sqlite) -> rusqlite::Result<()> {
    conn.execute_batch("CREATE TABLE IF NOT EXISTS hq_usage_requests (
        id TEXT PRIMARY KEY NOT NULL, started_at INTEGER NOT NULL,
        connection_id TEXT NOT NULL, connection_name TEXT NOT NULL,
        connection_revision INTEGER NOT NULL, model TEXT NOT NULL, protocol TEXT NOT NULL,
        streamed INTEGER NOT NULL, outcome TEXT NOT NULL,
        http_status INTEGER, failure_code TEXT, duration_ms INTEGER,
        usage_status TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
        cache_read_tokens INTEGER, cache_write_tokens INTEGER, total_tokens INTEGER
    );
    CREATE INDEX IF NOT EXISTS hq_usage_started ON hq_usage_requests(started_at DESC,id DESC);
    CREATE INDEX IF NOT EXISTS hq_usage_connection_started ON hq_usage_requests(connection_id,started_at DESC);")
}

#[derive(Clone)]
pub(crate) struct Store {
    db: Arc<Database>,
    failed: Arc<AtomicBool>,
}
impl Store {
    pub fn new(db: Arc<Database>) -> Self {
        let store = Self {
            db,
            failed: Arc::new(AtomicBool::new(false)),
        };
        // Called once during gateway construction, before it accepts requests.
        let recovered = store.db.conn.lock().ok().is_some_and(|conn| {
            schema(&conn).and_then(|()| conn.execute("UPDATE hq_usage_requests SET outcome='interrupted',failure_code='PROCESS_INTERRUPTED',duration_ms=NULL WHERE outcome='pending'", []).map(|_| ())).is_ok()
        });
        store.failed.store(!recovered, Ordering::Relaxed);
        store
    }
    pub fn storage_error(&self) -> bool {
        self.failed.load(Ordering::Relaxed)
    }
    pub async fn begin(&self, row: Record) {
        let store = self.clone();
        if tokio::task::spawn_blocking(move || store.save(&row, false))
            .await
            .is_err()
        {
            self.failed.store(true, Ordering::Relaxed);
        }
    }
    pub fn finish(&self, row: Record, permit: tokio::sync::OwnedSemaphorePermit) {
        let store = self.clone();
        // Holding admission through the write bounds pending blocking jobs to 16.
        // Drop never waits on SQLite or blocks forwarding/client cancellation.
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            store.save(&row, true);
        });
    }
    fn save(&self, row: &Record, final_row: bool) {
        let result = (|| {
            let conn = self.db.conn.lock().map_err(|_| ())?;
            schema(&conn).map_err(|_| ())?;
            write_record(&conn, row, final_row).map_err(|_| ())
        })();
        if result.is_err() {
            self.failed.store(true, Ordering::Relaxed);
            log::error!("HQ usage metadata persistence failed");
        }
    }
    pub async fn query(&self, filter: UsageFilter) -> Result<UsageReport, String> {
        filter.validate()?;
        let store = self.clone();
        tokio::task::spawn_blocking(move || {
            let conn = store
                .db
                .conn
                .lock()
                .map_err(|_| STORAGE_ERROR.to_string())?;
            schema(&conn).map_err(|_| STORAGE_ERROR.to_string())?;
            query(&conn, &filter, store.storage_error()).map_err(|_| STORAGE_ERROR.to_string())
        })
        .await
        .map_err(|_| STORAGE_ERROR.to_string())?
    }
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Tokens {
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cache_read_tokens: Option<u64>,
    pub cache_write_tokens: Option<u64>,
    pub total_tokens: Option<u64>,
}
impl Tokens {
    fn reported(&self) -> bool {
        self.input_tokens.is_some()
            || self.output_tokens.is_some()
            || self.cache_read_tokens.is_some()
            || self.cache_write_tokens.is_some()
            || self.total_tokens.is_some()
    }
}
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct Record {
    pub id: String,
    pub started_at: i64,
    pub connection_id: String,
    pub connection_name: String,
    pub connection_revision: u64,
    pub model: String,
    pub protocol: String,
    pub streamed: bool,
    pub outcome: String,
    pub http_status: Option<u16>,
    pub failure_code: Option<String>,
    pub duration_ms: Option<u64>,
    pub usage_status: String,
    pub usage_reported: bool,
    #[serde(flatten)]
    pub tokens: Tokens,
}
impl Record {
    pub fn pending(id: String, started_at: i64, connection: &Connection) -> Self {
        Self {
            id,
            started_at,
            connection_id: connection.id.clone(),
            connection_name: connection.name.clone(),
            connection_revision: connection.revision,
            model: connection.model.clone(),
            protocol: protocol_name(connection.protocol).into(),
            streamed: false,
            outcome: "pending".into(),
            http_status: None,
            failure_code: None,
            duration_ms: None,
            usage_status: "unknown".into(),
            usage_reported: false,
            tokens: Tokens::default(),
        }
    }
}
fn protocol_name(protocol: Protocol) -> &'static str {
    match protocol {
        Protocol::OpenaiChat => "openai-chat",
        Protocol::OpenaiResponses => "openai-responses",
        Protocol::Anthropic => "anthropic",
    }
}
fn write_record(conn: &Sqlite, row: &Record, final_row: bool) -> rusqlite::Result<()> {
    let suffix = if final_row {
        " ON CONFLICT(id) DO UPDATE SET streamed=excluded.streamed,outcome=excluded.outcome,http_status=excluded.http_status,failure_code=excluded.failure_code,duration_ms=excluded.duration_ms,usage_status=excluded.usage_status,input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,cache_read_tokens=excluded.cache_read_tokens,cache_write_tokens=excluded.cache_write_tokens,total_tokens=excluded.total_tokens WHERE hq_usage_requests.outcome='pending'"
    } else {
        " ON CONFLICT(id) DO NOTHING"
    };
    conn.execute(&format!("INSERT INTO {TABLE}(id,started_at,connection_id,connection_name,connection_revision,model,protocol,streamed,outcome,http_status,failure_code,duration_ms,usage_status,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,total_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?){suffix}"),
        params![row.id,row.started_at,row.connection_id,row.connection_name,row.connection_revision,row.model,row.protocol,row.streamed,row.outcome,row.http_status,row.failure_code,row.duration_ms,row.usage_status,row.tokens.input_tokens,row.tokens.output_tokens,row.tokens.cache_read_tokens,row.tokens.cache_write_tokens,row.tokens.total_tokens])?;
    Ok(())
}

/// A bounded observer; raw bytes are always forwarded separately and unchanged.
pub(crate) struct Observer {
    protocol: Protocol,
    sse: bool,
    buffer: Vec<u8>,
    data: Vec<u8>,
    discard_line: bool,
    discard_event: bool,
    uncertain: bool,
    authoritative_usage: bool,
    pub terminal: bool,
    pub semantic_error: Option<&'static str>,
    raw_input: Option<u64>,
    pub tokens: Tokens,
}
impl Observer {
    pub fn new(protocol: Protocol, sse: bool) -> Self {
        Self {
            protocol,
            sse,
            buffer: Vec::new(),
            data: Vec::new(),
            discard_line: false,
            discard_event: false,
            uncertain: false,
            authoritative_usage: false,
            terminal: false,
            semantic_error: None,
            raw_input: None,
            tokens: Tokens::default(),
        }
    }
    pub fn push(&mut self, bytes: &[u8]) {
        if !self.sse {
            if !self.discard_line
                && self.buffer.len().saturating_add(bytes.len()) <= MAX_PARSE_BYTES
            {
                self.buffer.extend_from_slice(bytes);
            } else {
                self.buffer.clear();
                self.discard_line = true;
                self.uncertain = true;
            }
            return;
        }
        for &byte in bytes {
            if byte == b'\n' {
                let mut line = std::mem::take(&mut self.buffer);
                if line.last() == Some(&b'\r') {
                    line.pop();
                }
                if self.discard_line {
                    self.discard_line = false;
                    self.discard_event = true;
                } else if line.is_empty() {
                    self.event();
                } else if let Some(mut data) = line.strip_prefix(b"data:") {
                    if data.first() == Some(&b' ') {
                        data = &data[1..];
                    }
                    if !self.discard_event
                        && self.data.len().saturating_add(data.len()).saturating_add(1)
                            <= MAX_PARSE_BYTES
                    {
                        self.data.extend_from_slice(data);
                        self.data.push(b'\n');
                    } else {
                        self.data.clear();
                        self.discard_event = true;
                        self.uncertain = true;
                    }
                }
            } else if !self.discard_line {
                if self.buffer.len() < MAX_PARSE_BYTES {
                    self.buffer.push(byte);
                } else {
                    self.buffer.clear();
                    self.discard_line = true;
                    self.uncertain = true;
                }
            }
        }
    }
    fn event(&mut self) {
        if !self.discard_event {
            let data = std::mem::take(&mut self.data);
            let trimmed = data.as_slice().trim_ascii();
            if trimmed == b"[DONE]" {
                self.terminal = true;
            } else if let Ok(value) = serde_json::from_slice::<Value>(trimmed) {
                self.value(&value);
            } else if !trimmed.is_empty() {
                self.uncertain = true;
            }
        }
        self.data.clear();
        self.discard_event = false;
    }
    pub fn eof(&mut self) {
        if self.sse {
            if !self.buffer.is_empty() {
                self.push(b"\n");
            }
            self.event();
        } else if !self.discard_line {
            if let Ok(value) = serde_json::from_slice::<Value>(&self.buffer) {
                self.value(&value);
                self.terminal = true;
            }
        }
        self.buffer.clear();
        self.data.clear();
    }
    fn value(&mut self, value: &Value) {
        let kind = value.get("type").and_then(Value::as_str).unwrap_or("");
        if value.get("error").is_some_and(|error| !error.is_null())
            || kind == "error"
            || kind == "response.failed"
        {
            self.semantic_error = Some("UPSTREAM_FAILED");
            self.terminal = true;
        }
        if kind == "response.incomplete"
            || value.get("status").and_then(Value::as_str) == Some("incomplete")
        {
            self.semantic_error = Some("UPSTREAM_INCOMPLETE");
            self.terminal = true;
        }
        if kind == "response.completed"
            || kind == "message_stop"
            || value
                .get("choices")
                .and_then(Value::as_array)
                .is_some_and(|choices| {
                    choices.iter().any(|choice| {
                        choice
                            .get("finish_reason")
                            .is_some_and(|reason| !reason.is_null())
                    })
                })
        {
            self.terminal = true;
        }
        let usage = match self.protocol {
            Protocol::Anthropic => value
                .pointer("/message/usage")
                .or_else(|| value.get("usage")),
            Protocol::OpenaiResponses
                if !self.sse
                    || matches!(
                        kind,
                        "response.completed" | "response.failed" | "response.incomplete"
                    ) =>
            {
                value
                    .pointer("/response/usage")
                    .filter(|usage| usage.is_object())
                    .or_else(|| value.get("usage"))
            }
            Protocol::OpenaiResponses => None,
            Protocol::OpenaiChat => value.get("usage"),
        };
        let Some(usage) = usage.filter(|usage| usage.is_object()) else {
            return;
        };
        let count = |field: &Value| field.as_u64().filter(|value| *value <= MAX_TOKENS);
        for path in [
            "/input_tokens",
            "/prompt_tokens",
            "/output_tokens",
            "/completion_tokens",
            "/total_tokens",
            "/cache_read_input_tokens",
            "/cache_creation_input_tokens",
            "/input_tokens_details/cached_tokens",
            "/prompt_tokens_details/cached_tokens",
            "/input_tokens_details/cache_write_tokens",
            "/prompt_tokens_details/cache_write_tokens",
            "/prompt_cache_hit_tokens",
        ] {
            if usage
                .pointer(path)
                .is_some_and(|field| count(field).is_none())
            {
                // Preserve prior valid snapshots, but never call stale counters final.
                self.uncertain = true;
            }
        }
        let first = |fields: &[&str]| {
            fields
                .iter()
                .find_map(|field| usage.pointer(field).and_then(count))
        };
        if let Some(input) = first(&["/input_tokens", "/prompt_tokens"]) {
            self.raw_input = Some(input);
        }
        if let Some(output) = first(&["/output_tokens", "/completion_tokens"]) {
            self.tokens.output_tokens = Some(output);
            if !self.sse || self.protocol != Protocol::Anthropic || kind == "message_delta" {
                self.authoritative_usage = true;
            }
        }
        if let Some(read) = first(&[
            "/cache_read_input_tokens",
            "/input_tokens_details/cached_tokens",
            "/prompt_tokens_details/cached_tokens",
            "/prompt_cache_hit_tokens",
        ]) {
            self.tokens.cache_read_tokens = Some(read);
        }
        if let Some(write) = first(&[
            "/cache_creation_input_tokens",
            "/input_tokens_details/cache_write_tokens",
            "/prompt_tokens_details/cache_write_tokens",
        ]) {
            self.tokens.cache_write_tokens = Some(write);
        }
        self.tokens.input_tokens = if self.protocol == Protocol::Anthropic {
            match (
                self.raw_input,
                self.tokens.cache_read_tokens,
                self.tokens.cache_write_tokens,
            ) {
                (Some(input), Some(read), Some(write)) => Some(input + read + write),
                _ => None,
            }
        } else {
            self.raw_input
        };
        self.tokens.total_tokens = match (self.tokens.input_tokens, self.tokens.output_tokens) {
            (Some(input), Some(output)) => Some(input + output),
            _ if self.protocol != Protocol::Anthropic => {
                first(&["/total_tokens"]).or(self.tokens.total_tokens)
            }
            _ => None,
        };
    }
    pub fn apply(&self, record: &mut Record) {
        record.tokens = self.tokens.clone();
        record.usage_reported = self.raw_input.is_some() || record.tokens.reported();
        record.usage_status = if !record.usage_reported {
            "unknown"
        } else if self.terminal
            && self.authoritative_usage
            && !self.uncertain
            && self.semantic_error.is_none()
            && record.tokens.input_tokens.is_some()
            && record.tokens.output_tokens.is_some()
        {
            "final"
        } else {
            "partial"
        }
        .into();
    }
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct UsageFilter {
    pub start_time: Option<i64>,
    pub end_time: Option<i64>,
    pub connection_id: Option<String>,
    pub model: Option<String>,
    pub offset: Option<u32>,
    pub limit: Option<u32>,
}
impl UsageFilter {
    fn validate(&self) -> Result<(), String> {
        if self.start_time.is_some_and(|n| n < 0)
            || self.end_time.is_some_and(|n| n < 0)
            || matches!((self.start_time,self.end_time),(Some(start),Some(end)) if end <= start)
            || self.limit.is_some_and(|n| n == 0 || n > 200)
            || self
                .connection_id
                .as_ref()
                .is_some_and(|id| uuid::Uuid::parse_str(id).is_err())
            || self
                .model
                .as_ref()
                .is_some_and(|model| model.len() > 256 || model.chars().any(char::is_control))
        {
            return Err("HQ 用量筛选条件无效".into());
        }
        Ok(())
    }
    fn sql(&self) -> (String, Vec<SqlValue>) {
        let mut clauses = vec!["1=1"];
        let mut values = vec![];
        if let Some(value) = self.start_time {
            clauses.push("started_at>=?");
            values.push(value.into());
        }
        if let Some(value) = self.end_time {
            clauses.push("started_at<?");
            values.push(value.into());
        }
        if let Some(value) = &self.connection_id {
            clauses.push("connection_id=?");
            values.push(value.clone().into());
        }
        if let Some(value) = &self.model {
            clauses.push("model=?");
            values.push(value.clone().into());
        }
        (clauses.join(" AND "), values)
    }
}
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Summary {
    requests: u64,
    successful: u64,
    failed: u64,
    cancelled: u64,
    interrupted: u64,
    usage_reported: u64,
    usage_missing: u64,
    usage_partial: u64,
    usage_final: u64,
    input_tokens: Option<u64>,
    output_tokens: Option<u64>,
    cache_read_tokens: Option<u64>,
    cache_write_tokens: Option<u64>,
    total_tokens: Option<u64>,
    duration_ms: u64,
    avg_duration_ms: Option<f64>,
}
const SUMMARY: &str = "COUNT(*),COALESCE(SUM(outcome='success'),0),COALESCE(SUM(outcome='failed'),0),COALESCE(SUM(outcome='cancelled'),0),COALESCE(SUM(outcome='interrupted'),0),COALESCE(SUM(usage_status!='unknown'),0),COALESCE(SUM(usage_status='unknown'),0),COALESCE(SUM(usage_status='partial'),0),COALESCE(SUM(usage_status='final'),0),SUM(input_tokens),SUM(output_tokens),SUM(cache_read_tokens),SUM(cache_write_tokens),SUM(total_tokens),COALESCE(SUM(duration_ms),0),AVG(duration_ms)";
fn summary(row: &Row<'_>, offset: usize) -> rusqlite::Result<Summary> {
    Ok(Summary {
        requests: row.get(offset)?,
        successful: row.get(offset + 1)?,
        failed: row.get(offset + 2)?,
        cancelled: row.get(offset + 3)?,
        interrupted: row.get(offset + 4)?,
        usage_reported: row.get(offset + 5)?,
        usage_missing: row.get(offset + 6)?,
        usage_partial: row.get(offset + 7)?,
        usage_final: row.get(offset + 8)?,
        input_tokens: row.get(offset + 9)?,
        output_tokens: row.get(offset + 10)?,
        cache_read_tokens: row.get(offset + 11)?,
        cache_write_tokens: row.get(offset + 12)?,
        total_tokens: row.get(offset + 13)?,
        duration_ms: row.get(offset + 14)?,
        avg_duration_ms: row.get(offset + 15)?,
    })
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Group {
    connection_id: String,
    connection_name: String,
    model: String,
    protocol: String,
    summary: Summary,
}
#[derive(Serialize)]
pub struct ConnectionOption {
    id: String,
    name: String,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UsageReport {
    summary: Summary,
    per_connection_model: Vec<Group>,
    recent: Vec<Record>,
    total: u64,
    connections: Vec<ConnectionOption>,
    models: Vec<String>,
    in_flight: u64,
    storage_error: bool,
}
fn query(
    conn: &Sqlite,
    filter: &UsageFilter,
    storage_error: bool,
) -> rusqlite::Result<UsageReport> {
    let (conditions, values) = filter.sql();
    let ended = format!("{conditions} AND outcome!='pending'");
    let summary = conn.query_row(
        &format!("SELECT {SUMMARY} FROM {TABLE} WHERE {ended}"),
        params_from_iter(values.iter()),
        |row| summary(row, 0),
    )?;
    let mut groups = conn.prepare(&format!("SELECT u.connection_id,(SELECT n.connection_name FROM {TABLE} n WHERE n.connection_id=u.connection_id ORDER BY n.started_at DESC,n.id DESC LIMIT 1),u.model,u.protocol,{SUMMARY} FROM {TABLE} u WHERE {ended} GROUP BY u.connection_id,u.model,u.protocol ORDER BY COUNT(*) DESC,u.connection_id,u.model"))?;
    let per_connection_model = groups
        .query_map(params_from_iter(values.iter()), |row| {
            Ok(Group {
                connection_id: row.get(0)?,
                connection_name: row.get(1)?,
                model: row.get(2)?,
                protocol: row.get(3)?,
                summary: summary_from_row(row)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut page_values = values.clone();
    page_values.push(filter.limit.unwrap_or(50).into());
    page_values.push(filter.offset.unwrap_or(0).into());
    let mut recent_query = conn.prepare(&format!("SELECT id,started_at,connection_id,connection_name,connection_revision,model,protocol,streamed,outcome,http_status,failure_code,duration_ms,usage_status,input_tokens,output_tokens,cache_read_tokens,cache_write_tokens,total_tokens FROM {TABLE} WHERE {ended} ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?"))?;
    let recent = recent_query
        .query_map(params_from_iter(page_values.iter()), |row| {
            let tokens = Tokens {
                input_tokens: row.get(13)?,
                output_tokens: row.get(14)?,
                cache_read_tokens: row.get(15)?,
                cache_write_tokens: row.get(16)?,
                total_tokens: row.get(17)?,
            };
            Ok(Record {
                id: row.get(0)?,
                started_at: row.get(1)?,
                connection_id: row.get(2)?,
                connection_name: row.get(3)?,
                connection_revision: row.get(4)?,
                model: row.get(5)?,
                protocol: row.get(6)?,
                streamed: row.get(7)?,
                outcome: row.get(8)?,
                http_status: row.get(9)?,
                failure_code: row.get(10)?,
                duration_ms: row.get(11)?,
                usage_status: row.get(12)?,
                usage_reported: row.get::<_, String>(12)? != "unknown",
                tokens,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut connection_query = conn.prepare(&format!("SELECT connection_id,(SELECT connection_name FROM {TABLE} n WHERE n.connection_id=u.connection_id ORDER BY started_at DESC,id DESC LIMIT 1) FROM {TABLE} u GROUP BY connection_id ORDER BY 2,1"))?;
    let connections = connection_query
        .query_map([], |row| {
            Ok(ConnectionOption {
                id: row.get(0)?,
                name: row.get(1)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let mut model_query = conn.prepare(&format!(
        "SELECT DISTINCT model FROM {TABLE} ORDER BY model"
    ))?;
    let models = model_query
        .query_map([], |row| row.get(0))?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    let in_flight = conn.query_row(
        &format!("SELECT COUNT(*) FROM {TABLE} WHERE {conditions} AND outcome='pending'"),
        params_from_iter(values.iter()),
        |row| row.get(0),
    )?;
    Ok(UsageReport {
        total: summary.requests,
        summary,
        per_connection_model,
        recent,
        connections,
        models,
        in_flight,
        storage_error,
    })
}
fn summary_from_row(row: &Row<'_>) -> rusqlite::Result<Summary> {
    summary(row, 4)
}

#[cfg(feature = "desktop")]
#[tauri::command]
pub async fn hq_get_usage(
    state: tauri::State<'_, super::gateway::Gateway>,
    filter: UsageFilter,
) -> Result<UsageReport, String> {
    state.usage().query(filter).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::Mutex;
    fn connection() -> Connection {
        Connection {
            id: "11111111-1111-4111-8111-111111111111".into(),
            name: "test connection".into(),
            protocol: Protocol::OpenaiChat,
            base_url: "https://secret-url.invalid/do-not-persist".into(),
            model: "test-model".into(),
            api_key: "secret-key-do-not-persist".into(),
            revision: 1,
            context_window: 32768,
            max_output_tokens: 4096,
            supports_images: false,
        }
    }
    fn record() -> Record {
        Record::pending(uuid::Uuid::new_v4().to_string(), 100, &connection())
    }
    fn observe(protocol: Protocol, sse: bool, wire: &[u8], width: usize) -> (Observer, Record) {
        let mut observer = Observer::new(protocol, sse);
        for chunk in wire.chunks(width) {
            observer.push(chunk);
        }
        observer.eof();
        let mut row = record();
        observer.apply(&mut row);
        (observer, row)
    }
    #[test]
    fn json_protocols_zero_missing_partial_and_invalid_counters() {
        for (protocol, body, input) in [
            (
                Protocol::OpenaiChat,
                json!({"usage":{"prompt_tokens":10,"completion_tokens":5,"prompt_tokens_details":{"cached_tokens":4}}}),
                Some(10),
            ),
            (
                Protocol::OpenaiResponses,
                json!({"usage":{"input_tokens":10,"output_tokens":5,"input_tokens_details":{"cached_tokens":4}}}),
                Some(10),
            ),
            (
                Protocol::Anthropic,
                json!({"usage":{"input_tokens":6,"output_tokens":5,"cache_read_input_tokens":4,"cache_creation_input_tokens":0}}),
                Some(10),
            ),
        ] {
            let (_, row) = observe(protocol, false, body.to_string().as_bytes(), 1);
            assert_eq!(row.tokens.input_tokens, input);
            assert_eq!(row.tokens.total_tokens, Some(15));
            assert_eq!(row.tokens.cache_read_tokens, Some(4));
            assert_eq!(row.usage_status, "final");
        }
        let (_, zero) = observe(
            Protocol::OpenaiChat,
            false,
            br#"{"usage":{"prompt_tokens":0,"completion_tokens":0}}"#,
            2,
        );
        assert!(zero.usage_reported);
        assert_eq!(zero.tokens.input_tokens, Some(0));
        assert_eq!(zero.usage_status, "final");
        let (_, missing) = observe(Protocol::OpenaiChat, false, br#"{"choices":[]}"#, 3);
        assert!(!missing.usage_reported);
        assert_eq!(missing.tokens.total_tokens, None);
        let (_, partial) = observe(
            Protocol::Anthropic,
            false,
            br#"{"usage":{"input_tokens":5,"output_tokens":2}}"#,
            1,
        );
        assert_eq!(partial.tokens.input_tokens, None);
        assert_eq!(partial.tokens.total_tokens, None);
        assert_eq!(partial.tokens.cache_read_tokens, None);
        assert_eq!(partial.tokens.output_tokens, Some(2));
        assert_eq!(partial.usage_status, "partial");
        let (_,zero_cache)=observe(Protocol::Anthropic,false,br#"{"usage":{"input_tokens":5,"output_tokens":2,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}"#,7);
        assert_eq!(zero_cache.tokens.input_tokens, Some(5));
        assert_eq!(zero_cache.tokens.total_tokens, Some(7));
        assert_eq!(zero_cache.usage_status, "final");
        let (_, mut raw_only) = observe(
            Protocol::Anthropic,
            false,
            br#"{"usage":{"input_tokens":5}}"#,
            1,
        );
        assert!(raw_only.usage_reported);
        assert_eq!(raw_only.usage_status, "partial");
        assert!(raw_only.tokens.input_tokens.is_none());
        raw_only.outcome = "success".into();
        let conn = Sqlite::open_in_memory().unwrap();
        schema(&conn).unwrap();
        write_record(&conn, &raw_only, true).unwrap();
        let restored = query(&conn, &UsageFilter::default(), false).unwrap();
        assert!(restored.recent[0].usage_reported);
        assert_eq!(restored.summary.usage_reported, 1);
        assert_eq!(restored.summary.usage_missing, 0);
        let (_,invalid)=observe(Protocol::OpenaiChat,false,br#"{"usage":{"prompt_tokens":-1,"completion_tokens":1.2,"total_tokens":18446744073709551615}}"#,1);
        assert!(!invalid.usage_reported);
        assert_eq!(invalid.usage_status, "unknown");
    }
    #[test]
    fn native_sse_fragments_multiline_utf8_and_cumulative_updates() {
        let cases=[
            (Protocol::OpenaiChat,"data: {\"choices\":[{\"delta\":{\"content\":\"中文\"}}]}\r\n\r\ndata: {\n data: invalid}\n\ndata: {\"usage\":{\"prompt_tokens\":25,\"completion_tokens\":12,\"prompt_tokens_details\":{\"cached_tokens\":13}}}\n\ndata: [DONE]\n\n".replace("data: {\n data: invalid}\n\n","data: {\ndata: \"choices\": []}\n\n")),
            (Protocol::OpenaiResponses,"event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"usage\":{\"input_tokens\":0,\"output_tokens\":0}}}\n\nevent: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"usage\":{\"input_tokens\":25,\"output_tokens\":12}}}\n\n".into()),
            (Protocol::Anthropic,"event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":5,\"output_tokens\":2,\"cache_read_input_tokens\":13,\"cache_creation_input_tokens\":3}}}\r\n\r\ndata: {\"type\":\"message_delta\",\"usage\":{\"input_tokens\":9,\"output_tokens\":8}}\n\ndata: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":12}}\n\ndata: {\"type\":\"message_stop\"}\n\n".into()),
        ];
        for (protocol, wire) in cases {
            for width in [1, 2, 3, 7, 4096] {
                let (observer, row) = observe(protocol, true, wire.as_bytes(), width);
                assert!(observer.terminal);
                assert_eq!(row.tokens.input_tokens, Some(25));
                assert_eq!(row.tokens.output_tokens, Some(12));
                assert_eq!(row.tokens.total_tokens, Some(37));
                assert_eq!(row.usage_status, "final");
            }
        }
        let (_,created)=observe(Protocol::OpenaiResponses,true,b"data: {\"type\":\"response.created\",\"response\":{\"usage\":{\"input_tokens\":0,\"output_tokens\":0}}}\n\ndata: {\"type\":\"response.completed\"}\n\n",1);
        assert_eq!(created.usage_status, "unknown");
    }
    #[test]
    fn error_and_incomplete_never_promote_partial_usage_to_final() {
        for ending in [
            "{\"type\":\"error\",\"error\":{\"message\":\"sensitive-error-do-not-persist\"}}",
            "{\"type\":\"response.failed\"}",
            "{\"type\":\"response.incomplete\"}",
        ] {
            let wire=format!("data: {{\"type\":\"message_start\",\"message\":{{\"usage\":{{\"input_tokens\":5,\"output_tokens\":2,\"cache_read_input_tokens\":0,\"cache_creation_input_tokens\":0}}}}}}\n\ndata: {ending}\n\n");
            let (observer, row) = observe(Protocol::Anthropic, true, wire.as_bytes(), 1);
            assert!(observer.semantic_error.is_some());
            assert_eq!(row.usage_status, "partial");
        }
        let wire=b"data: {\"usage\":{\"prompt_tokens\":5,\"completion_tokens\":2}}\n\ndata: {\"usage\":{\"completion_tokens\":-1}}\n\ndata: [DONE]\n\n";
        let (_, row) = observe(Protocol::OpenaiChat, true, wire, 1);
        assert_eq!(row.tokens.output_tokens, Some(2));
        assert_eq!(row.usage_status, "partial");
    }
    #[test]
    fn long_documents_keep_tail_usage_and_overflow_stays_bounded() {
        let text = "x".repeat(2 * 1024 * 1024);
        for sse in [false, true] {
            let body = if sse {
                format!("data: {{\"type\":\"response.completed\",\"response\":{{\"output\":[{{\"text\":\"{text}\"}}],\"usage\":{{\"input_tokens\":9,\"output_tokens\":8}}}}}}\n\n")
            } else {
                format!("{{\"output\":[{{\"text\":\"{text}\"}}],\"usage\":{{\"input_tokens\":9,\"output_tokens\":8}}}}")
            };
            let (_, row) = observe(Protocol::OpenaiResponses, sse, body.as_bytes(), 8191);
            assert_eq!(row.tokens.total_tokens, Some(17));
            assert_eq!(row.usage_status, "final");
        }
        let mut observer = Observer::new(Protocol::Anthropic, true);
        observer.push(b"data: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":5,\"output_tokens\":0,\"cache_read_input_tokens\":0,\"cache_creation_input_tokens\":0}}}\n\n");
        observer.push(b"data: ");
        for _ in 0..(MAX_PARSE_BYTES / 4096 + 1) {
            observer.push(&[b'x'; 4096]);
        }
        observer.push(b"\n\ndata: {\"type\":\"message_stop\"}\n\n");
        observer.eof();
        let mut row = record();
        observer.apply(&mut row);
        assert_eq!(row.usage_status, "partial");
        assert!(observer.buffer.len() <= MAX_PARSE_BYTES && observer.data.len() <= MAX_PARSE_BYTES);
    }
    #[tokio::test]
    async fn durable_pending_recovery_pagination_filters_nulls_and_privacy() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("hq.sqlite");
        let open = || {
            Arc::new(Database {
                conn: Mutex::new(Sqlite::open(&path).unwrap()),
                secrets: super::super::secrets::SecretProtector::memory().unwrap(),
                log_count_cache: Mutex::new(None),
            })
        };
        let db = open();
        let store = Store::new(db.clone());
        let mut pending = record();
        pending.started_at = 99;
        store.begin(pending).await;
        let mut done = record();
        done.started_at = 100;
        done.outcome = "success".into();
        done.duration_ms = Some(25);
        done.usage_status = "final".into();
        done.tokens.input_tokens = Some(0);
        done.tokens.output_tokens = Some(0);
        done.tokens.total_tokens = Some(0);
        {
            let conn = db.conn.lock().unwrap();
            write_record(&conn, &done, true).unwrap();
            write_record(&conn, &done, true).unwrap();
        }
        let mut later = record();
        later.started_at = 101;
        later.outcome = "failed".into();
        later.failure_code = Some("UPSTREAM_ERROR".into());
        {
            write_record(&db.conn.lock().unwrap(), &later, true).unwrap();
        }
        let before = store.query(UsageFilter::default()).await.unwrap();
        assert_eq!(before.in_flight, 1);
        assert_eq!(before.total, 2);
        assert_eq!(before.summary.input_tokens, Some(0));
        assert_eq!(before.summary.usage_missing, 1);
        drop(store);
        drop(db);
        let db = open();
        let recovered = Store::new(db.clone());
        let all = recovered.query(UsageFilter::default()).await.unwrap();
        assert_eq!(all.in_flight, 0);
        assert_eq!(all.total, 3);
        assert_eq!(all.summary.interrupted, 1);
        assert!(all.recent.iter().any(|row| row.failure_code.as_deref()
            == Some("PROCESS_INTERRUPTED")
            && row.duration_ms.is_none()));
        let filter = UsageFilter {
            start_time: Some(100),
            end_time: Some(102),
            connection_id: Some(connection().id),
            model: Some("test-model".into()),
            limit: Some(1),
            offset: Some(1),
        };
        let page = recovered.query(filter).await.unwrap();
        assert_eq!(page.total, 2);
        assert_eq!(page.recent.len(), 1);
        assert_eq!(page.recent[0].started_at, 100);
        assert_eq!(page.summary.requests, 2);
        assert_eq!(page.connections.len(), 1);
        assert_eq!(page.models, vec!["test-model"]);
        let wire = serde_json::to_string(&all).unwrap();
        assert!(!wire.contains("secret-url"));
        assert!(!wire.contains("secret-key"));
        assert!(!wire.contains("sensitive-error"));
        let conn = db.conn.lock().unwrap();
        let tables: Vec<String> = conn
            .prepare("SELECT name FROM sqlite_master WHERE type='table'")
            .unwrap()
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        assert_eq!(tables, vec![TABLE]);
    }
    #[tokio::test]
    async fn concurrent_completion_is_idempotent_even_if_pending_arrives_later() {
        let db = Arc::new(Database::memory().unwrap());
        let store = Store::new(db.clone());
        let slots = Arc::new(tokio::sync::Semaphore::new(16));
        for _ in 0..16 {
            let mut row = record();
            row.outcome = "success".into();
            row.duration_ms = Some(1);
            let mut pending = row.clone();
            pending.outcome = "pending".into();
            store.finish(row, slots.clone().acquire_owned().await.unwrap());
            store.begin(pending).await;
        }
        for _ in 0..200 {
            let result = store.query(UsageFilter::default()).await.unwrap();
            if result.total == 16 {
                assert_eq!(result.in_flight, 0);
                assert_eq!(result.summary.successful, 16);
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        panic!("terminal metadata writes did not complete");
    }
}
