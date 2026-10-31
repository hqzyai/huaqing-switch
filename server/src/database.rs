use std::{path::Path, sync::Mutex, time::Duration};

/// Server database deliberately excludes every legacy desktop/client table.
pub struct Database {
    #[cfg(not(test))]
    _instance_lock: std::fs::File,
    pub(crate) conn: Mutex<rusqlite::Connection>,
    pub(crate) secrets: crate::hq::secrets::SecretProtector,
    // Shared HQ storage tests construct the desktop-shaped test adapter.
    #[cfg(test)]
    #[allow(dead_code)]
    pub(crate) log_count_cache: Mutex<Option<u64>>,
}
impl Database {
    pub fn open(path: &Path, provider_key_file: &Path) -> anyhow::Result<Self> {
        let secrets = crate::hq::secrets::SecretProtector::from_key_file(provider_key_file)
            .map_err(anyhow::Error::msg)?;
        if path
            .symlink_metadata()
            .is_ok_and(|m| m.file_type().is_symlink())
        {
            anyhow::bail!("database must not be a symlink");
        }
        #[cfg(not(test))]
        let instance_lock = instance_lock(&path.with_extension("lock"))?;
        // Create privately before SQLite opens it (also covers WAL/SHM inheritance).
        let file = std::fs::OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(std::fs::Permissions::from_mode(0o600))?;
        }
        drop(file);
        let conn = rusqlite::Connection::open(path)?;
        conn.busy_timeout(Duration::from_secs(5))?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL);")?;
        crate::hq::migrate_connection_storage(&conn, &secrets).map_err(anyhow::Error::msg)?;
        Ok(Self {
            #[cfg(not(test))]
            _instance_lock: instance_lock,
            conn: Mutex::new(conn),
            secrets,
            #[cfg(test)]
            log_count_cache: Mutex::new(None),
        })
    }
    #[cfg(test)]
    pub fn memory() -> anyhow::Result<Self> {
        let conn = rusqlite::Connection::open_in_memory()?;
        conn.execute_batch(
            "CREATE TABLE settings(key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL)",
        )?;
        Ok(Self {
            conn: Mutex::new(conn),
            secrets: crate::hq::secrets::SecretProtector::memory().map_err(anyhow::Error::msg)?,
            log_count_cache: Mutex::new(None),
        })
    }
}

/// One process owns a persistent store; recovery must never mark another replica's calls interrupted.
pub fn instance_lock(path: &Path) -> anyhow::Result<std::fs::File> {
    let mut options = std::fs::OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::{fd::AsRawFd, unix::fs::OpenOptionsExt};
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        let file = options.open(path)?;
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            anyhow::bail!("data directory is already owned by another Switch server process");
        }
        Ok(file)
    }
    #[cfg(not(unix))]
    {
        anyhow::bail!("server requires a Unix deployment platform")
    }
}
