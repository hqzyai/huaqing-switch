//! Shared build/runtime validation for the install-ready provider seed.
//!
//! The wrapping key intentionally travels with the seed: the official client
//! must recover this shared default without user input. This reduces accidental
//! plaintext exposure; it is not a boundary against reverse engineering.
//! Persisted credentials must use a separate OS-protected key.
use base64::{engine::general_purpose::STANDARD, Engine};
use ring::aead::{Aad, LessSafeKey, Nonce, UnboundKey, AES_256_GCM};
use serde::Deserialize;

const FORMAT: &str = "hq-default-provider-v1";
const AAD_PREFIX: &[u8] = b"app.hqmodelswitch.desktop/default-provider/v1:";
const MAX_BUNDLE_BYTES: usize = 32 * 1024;
const MAX_CIPHERTEXT_BYTES: usize = 16 * 1024;
const INVALID: &str = "默认连接配置无效，请重新安装官方客户端";
const BASE_URL: &str =
    "https://ws-8ydu3kv5yatgl3a2.cn-beijing.maas.aliyuncs.com/compatible-mode/v1";
const MODEL: &str = "deepseek-v4.1-flash";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Bundle {
    format: String,
    version: String,
    key: String,
    nonce: String,
    ciphertext: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct BootstrapPayload {
    pub(crate) version: String,
    pub(crate) connection: BootstrapConnection,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct BootstrapConnection {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) protocol: String,
    pub(crate) base_url: String,
    pub(crate) model: String,
    pub(crate) api_key: String,
    pub(crate) context_window: u32,
    pub(crate) max_output_tokens: u32,
    pub(crate) supports_images: bool,
}

fn valid_version(version: &str) -> bool {
    !version.is_empty()
        && version.len() <= 128
        && version
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

fn valid_id(id: &str) -> bool {
    id.len() == 36
        && id.bytes().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                byte == b'-'
            } else {
                byte.is_ascii_hexdigit()
            }
        })
}

pub(crate) fn parse_bundle(bytes: &[u8]) -> Result<Option<BootstrapPayload>, &'static str> {
    if bytes.is_empty() {
        return Ok(None);
    }
    if bytes.len() > MAX_BUNDLE_BYTES {
        return Err(INVALID);
    }
    let bundle: Bundle = serde_json::from_slice(bytes).map_err(|_| INVALID)?;
    if bundle.format != FORMAT
        || !valid_version(&bundle.version)
        || bundle.key.len() > 64
        || bundle.nonce.len() > 24
        || bundle.ciphertext.len() > MAX_CIPHERTEXT_BYTES * 2
    {
        return Err(INVALID);
    }
    let key = STANDARD.decode(&bundle.key).map_err(|_| INVALID)?;
    let nonce: [u8; 12] = STANDARD
        .decode(&bundle.nonce)
        .map_err(|_| INVALID)?
        .try_into()
        .map_err(|_| INVALID)?;
    let mut ciphertext = STANDARD.decode(&bundle.ciphertext).map_err(|_| INVALID)?;
    if key.len() != 32
        || ciphertext.len() <= AES_256_GCM.tag_len()
        || ciphertext.len() > MAX_CIPHERTEXT_BYTES
    {
        return Err(INVALID);
    }
    let key = LessSafeKey::new(UnboundKey::new(&AES_256_GCM, &key).map_err(|_| INVALID)?);
    let mut aad = AAD_PREFIX.to_vec();
    aad.extend_from_slice(bundle.version.as_bytes());
    let plaintext = key
        .open_in_place(
            Nonce::assume_unique_for_key(nonce),
            Aad::from(aad),
            &mut ciphertext,
        )
        .map_err(|_| INVALID)?;
    let payload: BootstrapPayload = serde_json::from_slice(plaintext).map_err(|_| INVALID)?;
    let connection = &payload.connection;
    if payload.version != bundle.version
        || !valid_id(&connection.id)
        || connection.name.is_empty()
        || connection.name.trim() != connection.name
        || connection.name.chars().count() > 80
        || connection.name.chars().any(char::is_control)
        || connection.protocol != "openai-chat"
        || connection.base_url != BASE_URL
        || connection.model != MODEL
        || !connection.api_key.starts_with("sk-")
        || connection.api_key.len() <= 3
        || connection.api_key.len() > 8192
        || connection.api_key.trim() != connection.api_key
        || !connection
            .api_key
            .bytes()
            .all(|byte| byte.is_ascii_graphic())
        || !(1024..=2_000_000).contains(&connection.context_window)
        || connection.max_output_tokens == 0
        || connection.max_output_tokens > connection.context_window
        || connection.supports_images
    {
        return Err(INVALID);
    }
    Ok(Some(payload))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};

    const TEST_SECRET: &str = "sk-synthetic-bootstrap-fixture-not-a-real-key";
    const VERSION: &str = "fixture-v1";

    fn payload() -> Value {
        json!({
            "version": VERSION,
            "connection": {
                "id": "2d5b235b-1723-4d65-b23a-48e72e584ef9",
                "name": "Fixture default",
                "protocol": "openai-chat",
                "baseUrl": BASE_URL,
                "model": MODEL,
                "apiKey": TEST_SECRET,
                "contextWindow": 1_000_000,
                "maxOutputTokens": 8192,
                "supportsImages": false
            }
        })
    }

    fn seal(payload: &Value, version: &str) -> Value {
        let key_bytes = [7_u8; 32];
        let nonce = [3_u8; 12];
        let key = LessSafeKey::new(UnboundKey::new(&AES_256_GCM, &key_bytes).unwrap());
        let mut aad = AAD_PREFIX.to_vec();
        aad.extend_from_slice(version.as_bytes());
        let mut bytes = serde_json::to_vec(payload).unwrap();
        key.seal_in_place_append_tag(
            Nonce::assume_unique_for_key(nonce),
            Aad::from(aad),
            &mut bytes,
        )
        .unwrap();
        json!({"format":FORMAT,"version":version,"key":STANDARD.encode(key_bytes),"nonce":STANDARD.encode(nonce),"ciphertext":STANDARD.encode(bytes)})
    }

    fn rejected(bundle: Value) {
        let result = parse_bundle(&serde_json::to_vec(&bundle).unwrap());
        assert_eq!(result.err(), Some(INVALID));
    }

    #[test]
    fn configured_seed_roundtrips_without_plaintext_in_the_bundle() {
        let bundle = serde_json::to_vec(&seal(&payload(), VERSION)).unwrap();
        assert!(!String::from_utf8_lossy(&bundle).contains(TEST_SECRET));
        let decoded = parse_bundle(&bundle).unwrap().unwrap();
        assert_eq!(decoded.version, VERSION);
        assert_eq!(decoded.connection.api_key, TEST_SECRET);
        assert_eq!(decoded.connection.context_window, 1_000_000);
        assert_eq!(decoded.connection.max_output_tokens, 8192);
    }

    #[test]
    fn developer_builds_can_omit_the_seed_but_not_supply_invalid_bytes() {
        assert!(parse_bundle(&[]).unwrap().is_none());
        assert_eq!(parse_bundle(b" ").err(), Some(INVALID));
        assert_eq!(
            parse_bundle(&vec![0; MAX_BUNDLE_BYTES + 1]).err(),
            Some(INVALID)
        );
    }

    #[test]
    fn envelope_shape_format_and_version_are_strict() {
        for field in ["format", "version", "key", "nonce", "ciphertext"] {
            let mut bundle = seal(&payload(), VERSION);
            bundle.as_object_mut().unwrap().remove(field);
            rejected(bundle);
        }
        let mut extra = seal(&payload(), VERSION);
        extra["apiKey"] = json!(TEST_SECRET);
        rejected(extra);
        let mut wrong_format = seal(&payload(), VERSION);
        wrong_format["format"] = json!("hq-default-provider-v2");
        rejected(wrong_format);
        for version in ["", "contains space", "contains\nnewline", "version:other"] {
            rejected(seal(&payload(), version));
        }
        let mut duplicate = serde_json::to_string(&seal(&payload(), VERSION)).unwrap();
        duplicate.pop();
        duplicate.push_str(",\"version\":\"different\"}");
        assert_eq!(parse_bundle(duplicate.as_bytes()).err(), Some(INVALID));
    }

    #[test]
    fn tamper_and_invalid_encryption_lengths_fail_with_private_errors() {
        for field in ["key", "nonce", "ciphertext"] {
            let mut invalid_base64 = seal(&payload(), VERSION);
            invalid_base64[field] = json!("not base64!");
            rejected(invalid_base64);
        }
        for (field, length) in [("key", 31), ("nonce", 11), ("ciphertext", 16)] {
            let mut invalid_length = seal(&payload(), VERSION);
            invalid_length[field] = json!(STANDARD.encode(vec![0; length]));
            rejected(invalid_length);
        }
        for field in ["key", "nonce", "ciphertext"] {
            let mut tampered = seal(&payload(), VERSION);
            let mut bytes = STANDARD.decode(tampered[field].as_str().unwrap()).unwrap();
            bytes[0] ^= 1;
            tampered[field] = json!(STANDARD.encode(bytes));
            rejected(tampered);
        }
        let mut changed_aad = seal(&payload(), VERSION);
        changed_aad["version"] = json!("fixture-v2");
        rejected(changed_aad);
    }

    #[test]
    fn payload_identity_and_extra_fields_are_rejected() {
        let mut inner_version = payload();
        inner_version["version"] = json!("different");
        rejected(seal(&inner_version, VERSION));
        let mut extra = payload();
        extra["extra"] = json!(true);
        rejected(seal(&extra, VERSION));
        let mut nested_extra = payload();
        nested_extra["connection"]["extra"] = json!(true);
        rejected(seal(&nested_extra, VERSION));
        for (field, value) in [
            ("id", json!("../../elsewhere")),
            ("name", json!("")),
            ("name", json!(" has whitespace ")),
            ("name", json!("contains\ncontrol")),
            ("protocol", json!("anthropic")),
            ("baseUrl", json!("http://other.invalid/v1")),
            ("model", json!("different-model")),
            ("supportsImages", json!(true)),
            ("apiKey", json!("")),
            ("apiKey", json!("sk-")),
            ("apiKey", json!("sk-synthetic\ninvalid")),
            ("apiKey", json!(" sk-synthetic-invalid")),
            ("apiKey", json!("sk-synthetic invalid")),
            ("apiKey", json!("sk-非ASCII")),
        ] {
            let mut invalid = payload();
            invalid["connection"][field] = value;
            rejected(seal(&invalid, VERSION));
        }
    }

    #[test]
    fn limits_and_oversized_secrets_fail_closed() {
        for (field, value) in [
            ("contextWindow", json!(1023)),
            ("contextWindow", json!(2_000_001)),
            ("maxOutputTokens", json!(0)),
            ("maxOutputTokens", json!(1_000_001)),
            ("apiKey", json!(format!("sk-{}", "x".repeat(8190)))),
        ] {
            let mut invalid = payload();
            invalid["connection"][field] = value;
            rejected(seal(&invalid, VERSION));
        }
    }
}
