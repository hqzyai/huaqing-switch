//! Product policy compiled into the application, never taken from user settings.

use std::sync::OnceLock;

pub fn hq_only() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(|| {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../../brand.config.json"))
                .expect("invalid bundled brand.config.json");
        config["clientMode"].as_str() == Some("hq-only")
    })
}

pub fn updates_enabled() -> bool {
    static ENABLED: OnceLock<bool> = OnceLock::new();
    *ENABLED.get_or_init(|| {
        let config: serde_json::Value =
            serde_json::from_str(include_str!("../../brand.config.json"))
                .expect("invalid bundled brand.config.json");
        config["updatesEnabled"].as_bool().unwrap_or(false)
    })
}

pub fn require_updates() -> Result<(), String> {
    if !updates_enabled() {
        return Err("Huaqing Switch updates are not configured for this build".into());
    }
    Ok(())
}

pub fn release_repository() -> Option<&'static str> {
    // No upstream release URL can become a fallback for this fork.
    None
}
