//! Provider credentials at rest. Bootstrap distribution is deliberately separate.
//! No backend here uses a key compiled into an application or stored in SQLite.
use base64::{engine::general_purpose::STANDARD, Engine};
use ring::{
    aead::{self, Aad, LessSafeKey, Nonce, UnboundKey},
    rand::{SecureRandom, SystemRandom},
};
use serde::{Deserialize, Serialize};
use std::path::Path;

const VERSION: u8 = 1;
const AES_BACKEND: &str = "aes-256-gcm-v1";
#[cfg(all(feature = "desktop", target_os = "windows"))]
const DPAPI_BACKEND: &str = "windows-dpapi-user-v1";
const MAX_PLAINTEXT: usize = 8192;
const MAX_AAD: usize = 16 * 1024;
const MAX_CIPHERTEXT: usize = 64 * 1024;
const ERROR: &str = "无法保护或读取模型密钥，请检查系统密钥存储和配置完整性";

/// An authenticated, versioned persisted value; it never contains a plaintext key.
#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ProtectedSecret {
    version: u8,
    backend: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    nonce: Option<String>,
    ciphertext: String,
}

pub(crate) struct SecretProtector {
    backend: Backend,
}

enum Backend {
    Aes(Box<LessSafeKey>),
    #[cfg(not(all(feature = "desktop", any(target_os = "macos", target_os = "windows"))))]
    File(Option<std::path::PathBuf>),
    #[cfg(all(feature = "desktop", target_os = "macos"))]
    Keychain(Box<macos::KeychainKey>),
    #[cfg(all(feature = "desktop", target_os = "windows"))]
    Dpapi,
}

impl SecretProtector {
    /// Explicitly ephemeral protection for in-memory databases and synthetic tests.
    #[cfg_attr(not(feature = "desktop"), allow(dead_code))]
    pub(crate) fn memory() -> Result<Self, String> {
        let mut bytes = [0; 32];
        SystemRandom::new()
            .fill(&mut bytes)
            .map_err(|_| failure())?;
        Ok(Self {
            backend: Backend::Aes(Box::new(aes_key(&bytes)?)),
        })
    }

    /// Native desktop protection. Construction itself never opens the OS vault.
    #[cfg_attr(not(feature = "desktop"), allow(dead_code))]
    pub(crate) fn desktop(_data_dir: &Path) -> Result<Self, String> {
        #[cfg(all(feature = "desktop", target_os = "macos"))]
        {
            Ok(Self {
                backend: Backend::Keychain(Box::new(macos::KeychainKey::production())),
            })
        }
        #[cfg(all(feature = "desktop", target_os = "windows"))]
        {
            Ok(Self {
                backend: Backend::Dpapi,
            })
        }
        #[cfg(not(all(feature = "desktop", any(target_os = "macos", target_os = "windows"))))]
        {
            // Linux is shipped as a server. An explicitly provisioned secret is
            // required; absence never falls back to a plaintext or bundled key.
            Ok(Self {
                backend: Backend::File(
                    std::env::var_os("HQ_SWITCH_PROVIDER_KEY_FILE").map(Into::into),
                ),
            })
        }
    }

    /// The server owns and provisions this key independently of its SQLite data.
    #[cfg_attr(feature = "desktop", allow(dead_code))]
    pub(crate) fn from_key_file(path: &Path) -> Result<Self, String> {
        let bytes = read_private_key_file(path)?;
        Ok(Self {
            backend: Backend::Aes(Box::new(aes_key(&bytes)?)),
        })
    }

    pub(crate) fn protect(
        &self,
        plaintext: &str,
        associated_data: &[u8],
    ) -> Result<ProtectedSecret, String> {
        if plaintext.len() > MAX_PLAINTEXT || associated_data.len() > MAX_AAD {
            return Err(failure());
        }
        match &self.backend {
            Backend::Aes(key) => seal_aes(key, plaintext, associated_data),
            #[cfg(not(all(feature = "desktop", any(target_os = "macos", target_os = "windows"))))]
            Backend::File(path) => {
                let bytes = read_private_key_file(path.as_deref().ok_or_else(failure)?)?;
                seal_aes(&aes_key(&bytes)?, plaintext, associated_data)
            }
            #[cfg(all(feature = "desktop", target_os = "macos"))]
            Backend::Keychain(key) => {
                key.with_key(true, |key| seal_aes(key, plaintext, associated_data))
            }
            #[cfg(all(feature = "desktop", target_os = "windows"))]
            Backend::Dpapi => Ok(ProtectedSecret {
                version: VERSION,
                backend: DPAPI_BACKEND.into(),
                nonce: None,
                ciphertext: STANDARD.encode(windows::transform(
                    plaintext.as_bytes(),
                    associated_data,
                    true,
                )?),
            }),
        }
    }

    pub(crate) fn unprotect(
        &self,
        envelope: &ProtectedSecret,
        associated_data: &[u8],
    ) -> Result<String, String> {
        if envelope.version != VERSION
            || envelope.ciphertext.len() > MAX_CIPHERTEXT * 2
            || associated_data.len() > MAX_AAD
        {
            return Err(failure());
        }
        match &self.backend {
            Backend::Aes(key) => open_aes(key, envelope, associated_data),
            #[cfg(not(all(feature = "desktop", any(target_os = "macos", target_os = "windows"))))]
            Backend::File(path) => {
                let bytes = read_private_key_file(path.as_deref().ok_or_else(failure)?)?;
                open_aes(&aes_key(&bytes)?, envelope, associated_data)
            }
            #[cfg(all(feature = "desktop", target_os = "macos"))]
            Backend::Keychain(key) => {
                // A missing key on read is loss of access, never a new identity.
                key.with_key(false, |key| open_aes(key, envelope, associated_data))
            }
            #[cfg(all(feature = "desktop", target_os = "windows"))]
            Backend::Dpapi => {
                if envelope.backend != DPAPI_BACKEND || envelope.nonce.is_some() {
                    return Err(failure());
                }
                let encrypted = decode(&envelope.ciphertext, MAX_CIPHERTEXT)?;
                if encrypted.is_empty() {
                    return Err(failure());
                }
                let plaintext = windows::transform(&encrypted, associated_data, false)?;
                if plaintext.len() > MAX_PLAINTEXT {
                    return Err(failure());
                }
                String::from_utf8(plaintext).map_err(|_| failure())
            }
        }
    }
}

fn failure() -> String {
    ERROR.to_owned()
}

fn aes_key(bytes: &[u8; 32]) -> Result<LessSafeKey, String> {
    UnboundKey::new(&aead::AES_256_GCM, bytes)
        .map(LessSafeKey::new)
        .map_err(|_| failure())
}

fn seal_aes(key: &LessSafeKey, plaintext: &str, aad: &[u8]) -> Result<ProtectedSecret, String> {
    let mut nonce = [0; 12];
    SystemRandom::new()
        .fill(&mut nonce)
        .map_err(|_| failure())?;
    let mut encrypted = plaintext.as_bytes().to_vec();
    key.seal_in_place_append_tag(
        Nonce::assume_unique_for_key(nonce),
        Aad::from(aad),
        &mut encrypted,
    )
    .map_err(|_| failure())?;
    Ok(ProtectedSecret {
        version: VERSION,
        backend: AES_BACKEND.into(),
        nonce: Some(STANDARD.encode(nonce)),
        ciphertext: STANDARD.encode(encrypted),
    })
}

fn decode(value: &str, maximum: usize) -> Result<Vec<u8>, String> {
    if value.len()
        > maximum
            .saturating_add(2)
            .saturating_div(3)
            .saturating_mul(4)
    {
        return Err(failure());
    }
    let bytes = STANDARD.decode(value).map_err(|_| failure())?;
    if bytes.len() > maximum || STANDARD.encode(&bytes) != value {
        return Err(failure());
    }
    Ok(bytes)
}

fn open_aes(key: &LessSafeKey, envelope: &ProtectedSecret, aad: &[u8]) -> Result<String, String> {
    if envelope.backend != AES_BACKEND {
        return Err(failure());
    }
    let nonce = decode(envelope.nonce.as_deref().ok_or_else(failure)?, 12)?;
    let nonce: [u8; 12] = nonce.try_into().map_err(|_| failure())?;
    let mut encrypted = decode(&envelope.ciphertext, MAX_PLAINTEXT + aead::MAX_TAG_LEN)?;
    let decrypted = key
        .open_in_place(
            Nonce::assume_unique_for_key(nonce),
            Aad::from(aad),
            &mut encrypted,
        )
        .map_err(|_| failure())?;
    if decrypted.len() > MAX_PLAINTEXT {
        return Err(failure());
    }
    String::from_utf8(decrypted.to_vec()).map_err(|_| failure())
}

#[cfg(unix)]
fn read_private_key_file(path: &Path) -> Result<[u8; 32], String> {
    use std::{
        fs::OpenOptions,
        io::Read,
        os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
    };
    let fingerprint = |m: &std::fs::Metadata| {
        (
            m.dev(),
            m.ino(),
            m.len(),
            m.mtime(),
            m.mtime_nsec(),
            m.ctime(),
            m.ctime_nsec(),
        )
    };
    let permitted = |m: &std::fs::Metadata| {
        // SAFETY: geteuid has no arguments or memory preconditions.
        m.file_type().is_file()
            && m.uid() == unsafe { libc::geteuid() }
            && m.permissions().mode() & 0o7777 == 0o600
    };
    if !path.is_absolute() || path.canonicalize().map_err(|_| failure())? != path {
        return Err(failure());
    }
    let before = path.symlink_metadata().map_err(|_| failure())?;
    if !permitted(&before) || before.len() != 32 {
        return Err(failure());
    }
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)
        .map_err(|_| failure())?;
    let opened = file.metadata().map_err(|_| failure())?;
    if !permitted(&opened) || fingerprint(&before) != fingerprint(&opened) {
        return Err(failure());
    }
    let mut key = [0; 32];
    file.read_exact(&mut key).map_err(|_| failure())?;
    let after = file.metadata().map_err(|_| failure())?;
    let current = path.symlink_metadata().map_err(|_| failure())?;
    if !permitted(&after)
        || !permitted(&current)
        || fingerprint(&opened) != fingerprint(&after)
        || fingerprint(&after) != fingerprint(&current)
        || path.canonicalize().map_err(|_| failure())? != path
    {
        return Err(failure());
    }
    Ok(key)
}

#[cfg(not(unix))]
fn read_private_key_file(_path: &Path) -> Result<[u8; 32], String> {
    // A plaintext master file is not a native Windows desktop backend.
    Err(failure())
}

#[cfg(all(feature = "desktop", target_os = "macos"))]
mod macos {
    use super::*;
    use core_foundation::{
        base::TCFType, boolean::CFBoolean, data::CFData, dictionary::CFDictionary, string::CFString,
    };
    use security_framework::passwords::{generic_password, PasswordOptions};
    use security_framework_sys::{
        base::{errSecDuplicateItem, errSecItemNotFound},
        item::{
            kSecAttrAccount, kSecAttrService, kSecAttrSynchronizable, kSecClass,
            kSecClassGenericPassword, kSecValueData,
        },
        keychain_item::SecItemAdd,
    };
    use std::sync::Mutex;

    pub(super) struct KeychainKey {
        service: String,
        account: String,
        cached: Mutex<Option<LessSafeKey>>,
    }

    impl KeychainKey {
        pub(super) fn production() -> Self {
            Self::new(
                "app.hqmodelswitch.desktop.provider-credentials",
                "aes-master-v1",
            )
        }

        fn new(service: &str, account: &str) -> Self {
            Self {
                service: service.into(),
                account: account.into(),
                cached: Mutex::new(None),
            }
        }

        fn existing(&self) -> Result<Vec<u8>, security_framework::base::Error> {
            let mut options = PasswordOptions::new_generic_password(&self.service, &self.account);
            options.set_access_synchronized(Some(false));
            generic_password(options)
        }

        fn create_without_replacing(&self, key: &[u8; 32]) -> Result<(), String> {
            // SAFETY: Security.framework constants are immutable valid CFString
            // objects. Every dictionary value stays retained through SecItemAdd.
            let entries = unsafe {
                vec![
                    (
                        CFString::wrap_under_get_rule(kSecClass).into_CFType(),
                        CFString::wrap_under_get_rule(kSecClassGenericPassword).into_CFType(),
                    ),
                    (
                        CFString::wrap_under_get_rule(kSecAttrService).into_CFType(),
                        CFString::new(&self.service).into_CFType(),
                    ),
                    (
                        CFString::wrap_under_get_rule(kSecAttrAccount).into_CFType(),
                        CFString::new(&self.account).into_CFType(),
                    ),
                    (
                        CFString::wrap_under_get_rule(kSecAttrSynchronizable).into_CFType(),
                        CFBoolean::false_value().into_CFType(),
                    ),
                    (
                        CFString::wrap_under_get_rule(kSecValueData).into_CFType(),
                        CFData::from_buffer(key).into_CFType(),
                    ),
                ]
            };
            let attributes = CFDictionary::from_CFType_pairs(&entries);
            // SAFETY: attributes is a live CFDictionary; no return object is requested.
            let status =
                unsafe { SecItemAdd(attributes.as_concrete_TypeRef(), std::ptr::null_mut()) };
            if status == 0 || status == errSecDuplicateItem {
                Ok(())
            } else {
                Err(failure())
            }
        }

        pub(super) fn with_key<T>(
            &self,
            create: bool,
            action: impl FnOnce(&LessSafeKey) -> Result<T, String>,
        ) -> Result<T, String> {
            let mut cached = self.cached.lock().map_err(|_| failure())?;
            if cached.is_none() {
                let bytes = match self.existing() {
                    Ok(bytes) => bytes,
                    Err(error) if error.code() == errSecItemNotFound && create => {
                        let mut fresh = [0; 32];
                        SystemRandom::new()
                            .fill(&mut fresh)
                            .map_err(|_| failure())?;
                        self.create_without_replacing(&fresh)?;
                        // A competing process may have created the item first.
                        // Always reread the winning item; never overwrite it.
                        self.existing().map_err(|_| failure())?
                    }
                    Err(_) => return Err(failure()),
                };
                let key: [u8; 32] = bytes.try_into().map_err(|_| failure())?;
                *cached = Some(aes_key(&key)?);
            }
            action(cached.as_ref().ok_or_else(failure)?)
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        #[ignore = "explicit native Keychain integration test; synthetic entry only"]
        fn native_keychain_restart_missing_key_and_no_overwrite() {
            let service = format!("app.hqmodelswitch.crypto-test.{}", uuid::Uuid::new_v4());
            let account = "synthetic-fixture";
            struct Cleanup(String);
            impl Drop for Cleanup {
                fn drop(&mut self) {
                    let _ = security_framework::passwords::delete_generic_password(
                        &self.0,
                        "synthetic-fixture",
                    );
                }
            }
            let _cleanup = Cleanup(service.clone());
            let protector = || SecretProtector {
                backend: Backend::Keychain(Box::new(KeychainKey::new(&service, account))),
            };
            let sealed = protector()
                .protect("native-keychain-fixture", b"connection-a")
                .unwrap();
            assert_eq!(
                protector().unprotect(&sealed, b"connection-a").unwrap(),
                "native-keychain-fixture"
            );
            let first = KeychainKey::new(&service, account).existing().unwrap();
            KeychainKey::new(&service, account)
                .create_without_replacing(&[42; 32])
                .unwrap();
            assert_eq!(
                KeychainKey::new(&service, account).existing().unwrap(),
                first
            );
            security_framework::passwords::delete_generic_password(&service, account).unwrap();
            assert!(protector().unprotect(&sealed, b"connection-a").is_err());
            assert!(KeychainKey::new(&service, account).existing().is_err());
        }
    }
}

#[cfg(all(feature = "desktop", target_os = "windows"))]
mod windows {
    use super::*;
    use windows_sys::Win32::{
        Foundation::LocalFree,
        Security::Cryptography::{
            CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
        },
    };

    pub(super) fn transform(input: &[u8], aad: &[u8], encrypt: bool) -> Result<Vec<u8>, String> {
        let entropy_bytes = [b"Huaqing Switch provider credentials v1\0".as_slice(), aad].concat();
        let input = CRYPT_INTEGER_BLOB {
            cbData: input.len().try_into().map_err(|_| failure())?,
            pbData: input.as_ptr().cast_mut(),
        };
        let entropy = CRYPT_INTEGER_BLOB {
            cbData: entropy_bytes.len().try_into().map_err(|_| failure())?,
            pbData: entropy_bytes.as_ptr().cast_mut(),
        };
        let mut output = CRYPT_INTEGER_BLOB {
            cbData: 0,
            pbData: std::ptr::null_mut(),
        };
        // SAFETY: input/entropy point to live immutable slices; DPAPI only reads
        // them. Output is allocated by DPAPI and released with LocalFree below.
        // No LOCAL_MACHINE flag is used: this is CurrentUser protection.
        let success = unsafe {
            if encrypt {
                CryptProtectData(
                    &input,
                    std::ptr::null(),
                    &entropy,
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut output,
                )
            } else {
                CryptUnprotectData(
                    &input,
                    std::ptr::null_mut(),
                    &entropy,
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut output,
                )
            }
        };
        if success == 0 {
            return Err(failure());
        }
        if !encrypt && output.cbData == 0 {
            if !output.pbData.is_null() {
                // SAFETY: successful DPAPI owns this optional empty allocation.
                unsafe {
                    LocalFree(output.pbData.cast());
                }
            }
            return Ok(Vec::new());
        }
        if output.pbData.is_null() || output.cbData as usize > MAX_CIPHERTEXT {
            if !output.pbData.is_null() {
                // SAFETY: output was allocated by successful DPAPI call.
                unsafe {
                    LocalFree(output.pbData.cast());
                }
            }
            return Err(failure());
        }
        // SAFETY: successful DPAPI call owns exactly cbData readable bytes.
        let bytes =
            unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec();
        // SAFETY: this buffer belongs to this call. Wipe the OS-owned plaintext
        // copy before freeing; the returned value remains in process memory.
        unsafe {
            if !encrypt {
                for index in 0..output.cbData as usize {
                    std::ptr::write_volatile(output.pbData.add(index), 0);
                }
            }
            LocalFree(output.pbData.cast());
        }
        Ok(bytes)
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn native_dpapi_roundtrip_tamper_and_entropy() {
            let protector = SecretProtector::desktop(Path::new("unused")).unwrap();
            let first = protector
                .protect("dpapi-synthetic-fixture", b"connection-a")
                .unwrap();
            let second = protector
                .protect("dpapi-synthetic-fixture", b"connection-a")
                .unwrap();
            assert_ne!(first.ciphertext, second.ciphertext);
            assert_eq!(
                protector.unprotect(&first, b"connection-a").unwrap(),
                "dpapi-synthetic-fixture"
            );
            assert!(protector.unprotect(&first, b"connection-b").is_err());
            let mut altered = first;
            let mut bytes = STANDARD.decode(&altered.ciphertext).unwrap();
            let last = bytes.len() - 1;
            bytes[last] ^= 1;
            altered.ciphertext = STANDARD.encode(bytes);
            assert!(protector.unprotect(&altered, b"connection-a").is_err());
            let empty = protector.protect("", b"connection-a").unwrap();
            assert_eq!(protector.unprotect(&empty, b"connection-a").unwrap(), "");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_random_nonce_and_wrong_context_or_key() {
        let protector = SecretProtector::memory().unwrap();
        let first = protector
            .protect("synthetic-provider-secret", b"connection-a")
            .unwrap();
        let second = protector
            .protect("synthetic-provider-secret", b"connection-a")
            .unwrap();
        assert_ne!(first.nonce, second.nonce);
        assert_ne!(first.ciphertext, second.ciphertext);
        assert!(!serde_json::to_string(&first)
            .unwrap()
            .contains("synthetic-provider-secret"));
        assert_eq!(
            protector.unprotect(&first, b"connection-a").unwrap(),
            "synthetic-provider-secret"
        );
        assert!(protector.unprotect(&first, b"connection-b").is_err());
        assert!(SecretProtector::memory()
            .unwrap()
            .unprotect(&first, b"connection-a")
            .is_err());
    }

    #[test]
    fn malformed_unknown_or_tampered_values_fail_closed() {
        let protector = SecretProtector::memory().unwrap();
        let initial = protector.protect("fixture", b"a").unwrap();
        for mode in 0..6 {
            let mut changed = initial.clone();
            match mode {
                0 => changed.version = 2,
                1 => changed.backend = "unknown".into(),
                2 => changed.nonce = None,
                3 => changed.nonce = Some(STANDARD.encode([0; 11])),
                4 => changed.ciphertext = "not base64!".into(),
                _ => {
                    let mut bytes = STANDARD.decode(&changed.ciphertext).unwrap();
                    bytes[0] ^= 1;
                    changed.ciphertext = STANDARD.encode(bytes);
                }
            }
            assert_eq!(protector.unprotect(&changed, b"a").unwrap_err(), ERROR);
        }
        let mut value = serde_json::to_value(initial).unwrap();
        value["extra"] = serde_json::json!("unsupported");
        assert!(serde_json::from_value::<ProtectedSecret>(value).is_err());
    }

    #[test]
    fn input_and_envelope_sizes_are_bounded() {
        let protector = SecretProtector::memory().unwrap();
        assert!(protector
            .protect(&"x".repeat(MAX_PLAINTEXT + 1), b"a")
            .is_err());
        assert!(protector.protect("fixture", &vec![0; MAX_AAD + 1]).is_err());
        let mut secret = protector.protect("fixture", b"a").unwrap();
        secret.ciphertext = "A".repeat(MAX_CIPHERTEXT * 2 + 1);
        assert!(protector.unprotect(&secret, b"a").is_err());
    }

    #[cfg(not(all(feature = "desktop", any(target_os = "macos", target_os = "windows"))))]
    #[test]
    fn unsupported_desktop_constructs_lazily_but_never_uses_a_fallback_key() {
        assert!(SecretProtector::desktop(Path::new("unused")).is_ok());
        let unavailable = SecretProtector {
            backend: Backend::File(None),
        };
        assert!(unavailable.protect("fixture", b"a").is_err());
        let encrypted = SecretProtector::memory()
            .unwrap()
            .protect("fixture", b"a")
            .unwrap();
        assert!(unavailable.unprotect(&encrypted, b"a").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn private_file_restart_and_missing_wrong_or_unsafe_key_fail() {
        use std::{
            fs,
            os::unix::fs::{symlink, PermissionsExt},
        };
        let directory = tempfile::tempdir().unwrap();
        let canonical_directory = directory.path().canonicalize().unwrap();
        let path = canonical_directory.join("master");
        assert!(SecretProtector::from_key_file(&path).is_err());
        fs::write(&path, [7; 32]).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        let first = SecretProtector::from_key_file(&path).unwrap();
        let secret = first.protect("private-file-fixture", b"a").unwrap();
        assert_eq!(
            SecretProtector::from_key_file(&path)
                .unwrap()
                .unprotect(&secret, b"a")
                .unwrap(),
            "private-file-fixture"
        );
        fs::write(&path, [8; 32]).unwrap();
        assert!(SecretProtector::from_key_file(&path)
            .unwrap()
            .unprotect(&secret, b"a")
            .is_err());
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(SecretProtector::from_key_file(&path).is_err());
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        let link = canonical_directory.join("linked-master");
        symlink(&path, &link).unwrap();
        assert!(SecretProtector::from_key_file(&link).is_err());
        let parent_link = canonical_directory.join("parent-link");
        symlink(&canonical_directory, &parent_link).unwrap();
        assert!(SecretProtector::from_key_file(&parent_link.join("master")).is_err());
        fs::write(&path, [8; 31]).unwrap();
        assert!(SecretProtector::from_key_file(&path).is_err());
        fs::remove_file(&path).unwrap();
        assert!(SecretProtector::from_key_file(&path).is_err());
    }
}
