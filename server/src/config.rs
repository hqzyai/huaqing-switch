use anyhow::{bail, Context, Result};
use std::{
    net::SocketAddr,
    path::{Path, PathBuf},
};

#[derive(Clone)]
pub struct Config {
    pub web_bind: SocketAddr,
    pub gateway_bind: SocketAddr,
    pub gateway_url: String,
    pub public_origin: String,
    pub data_dir: PathBuf,
    pub static_dir: PathBuf,
    pub admin_token: String,
    pub inference_token: String,
}
impl Config {
    pub fn from_env() -> Result<Self> {
        let required =
            |name: &str| std::env::var(name).with_context(|| format!("{name} is required"));
        let public_origin = origin(&required("HQ_SWITCH_PUBLIC_ORIGIN")?)?;
        let gateway_url = gateway_url(&required("HQ_SWITCH_GATEWAY_URL")?)?;
        let admin_token = read_token(Path::new(&required("HQ_SWITCH_ADMIN_TOKEN_FILE")?))?;
        let inference_token = read_token(Path::new(&required("HQ_SWITCH_INFERENCE_TOKEN_FILE")?))?;
        if admin_token == inference_token {
            bail!("admin and inference tokens must be different");
        }
        let data_dir = PathBuf::from(required("HQ_SWITCH_DATA_DIR")?);
        let static_dir = PathBuf::from(required("HQ_SWITCH_STATIC_DIR")?);
        if !data_dir.is_absolute() || !static_dir.is_absolute() {
            bail!("data and static directories must be absolute");
        }
        if data_dir
            .symlink_metadata()
            .is_ok_and(|m| m.file_type().is_symlink())
        {
            bail!("data directory must not be a symlink");
        }
        std::fs::create_dir_all(&data_dir)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&data_dir, std::fs::Permissions::from_mode(0o700))?;
        }
        if !static_dir.join("index.html").is_file() {
            bail!("static directory must contain the built index.html");
        }
        let bind = |name, default| -> Result<SocketAddr> {
            std::env::var(name)
                .unwrap_or_else(|_| String::from(default))
                .parse()
                .with_context(|| format!("invalid {name}"))
        };
        let config = Self {
            web_bind: bind("HQ_SWITCH_WEB_BIND", "0.0.0.0:8080")?,
            gateway_bind: bind("HQ_SWITCH_GATEWAY_BIND", "0.0.0.0:17621")?,
            gateway_url,
            public_origin,
            data_dir,
            static_dir,
            admin_token,
            inference_token,
        };
        if config.web_bind.port() == 0
            || config.gateway_bind.port() == 0
            || config.web_bind == config.gateway_bind
        {
            bail!("web and inference listeners must use distinct nonzero addresses");
        }
        Ok(config)
    }
}
pub fn origin(value: &str) -> Result<String> {
    let url = url::Url::parse(value).context("invalid public origin")?;
    let local = url.host_str().is_some_and(|h| {
        h == "localhost" || h.parse::<std::net::IpAddr>().is_ok_and(|i| i.is_loopback())
    });
    if (url.scheme() != "https" && !(url.scheme() == "http" && local))
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
        || url.host_str().is_none()
    {
        bail!("public origin must be an HTTPS origin (HTTP is allowed only on loopback for local tests)");
    }
    Ok(url.origin().ascii_serialization())
}
pub fn gateway_url(value: &str) -> Result<String> {
    let url = url::Url::parse(value).context("invalid inference URL")?;
    if !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        bail!("inference URL must be an HTTP(S) origin without credentials");
    }
    Ok(url.origin().ascii_serialization())
}
pub fn read_token(path: &Path) -> Result<String> {
    use std::io::Read;
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    let file = options.open(path).context("cannot open credential file")?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || metadata.len() > 128 {
        bail!("credential must be a small regular file");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.mode() & 0o777 != 0o600 || metadata.uid() != unsafe { libc::geteuid() } {
            bail!("credential files must be owned by the service user with mode 0600");
        }
    }
    let mut token = String::new();
    file.take(129).read_to_string(&mut token)?;
    let token = token.trim_end_matches(['\r', '\n']);
    if token.len() != 64 || !token.bytes().all(|c| c.is_ascii_hexdigit()) {
        bail!("credential must contain exactly 64 hexadecimal characters");
    }
    Ok(token.to_string())
}

// Only desktop discovery uses this helper; server Gateway never writes discovery files.
pub fn write_json_file_private(path: &Path, value: &serde_json::Value) -> Result<()> {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.create_new(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options
        .open(path)?
        .write_all(serde_json::to_vec(value)?.as_slice())?;
    Ok(())
}
