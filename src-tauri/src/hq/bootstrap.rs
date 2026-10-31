//! Install-ready direct-provider defaults, applied by the HQ storage layer.
//!
//! This bundled seed is recoverable by the client by design. Its wrapping key
//! must never be reused for OS-protected persisted provider credentials.
#[path = "bootstrap_format.rs"]
mod format;

use super::{ConnectionInput, Protocol};

pub(crate) struct BootstrapConfig {
    pub(crate) version: String,
    pub(crate) connection: ConnectionInput,
}

pub(crate) fn bundled_default() -> Result<Option<BootstrapConfig>, String> {
    let bytes = include_bytes!(concat!(env!("OUT_DIR"), "/hq-default-provider.bundle"));
    let payload = format::parse_bundle(bytes).map_err(str::to_string)?;
    Ok(payload.map(|payload| {
        let connection = payload.connection;
        BootstrapConfig {
            version: payload.version,
            connection: ConnectionInput {
                id: Some(connection.id),
                name: connection.name,
                // The shared parser rejects any different protocol.
                protocol: Protocol::OpenaiChat,
                base_url: connection.base_url,
                model: connection.model,
                api_key: Some(connection.api_key),
                context_window: connection.context_window,
                max_output_tokens: connection.max_output_tokens,
                supports_images: connection.supports_images,
            },
        }
    }))
}
