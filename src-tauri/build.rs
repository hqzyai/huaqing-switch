#[path = "src/hq/bootstrap_format.rs"]
#[allow(dead_code)]
mod bootstrap_format;

fn main() {
    emit_default_provider_bundle();
    tauri_build::build();

    // Windows: Embed Common Controls v6 manifest for test binaries
    //
    // When running `cargo test`, the generated test executables don't include
    // the standard Tauri application manifest. Without Common Controls v6,
    // `tauri::test` calls fail with STATUS_ENTRYPOINT_NOT_FOUND.
    //
    // This workaround:
    // 1. Embeds the manifest into test binaries via /MANIFEST:EMBED
    // 2. Uses /MANIFEST:NO for the main binary to avoid duplicate resources
    //    (Tauri already handles manifest embedding for the app binary)
    #[cfg(target_os = "windows")]
    {
        let manifest_path = std::path::PathBuf::from(
            std::env::var("CARGO_MANIFEST_DIR").expect("missing CARGO_MANIFEST_DIR"),
        )
        .join("common-controls.manifest");
        let manifest_arg = format!("/MANIFESTINPUT:{}", manifest_path.display());

        println!("cargo:rustc-link-arg=/MANIFEST:EMBED");
        println!("cargo:rustc-link-arg={}", manifest_arg);
        // Avoid duplicate manifest resources in binary builds.
        println!("cargo:rustc-link-arg-bins=/MANIFEST:NO");
        println!("cargo:rerun-if-changed={}", manifest_path.display());
    }
}

fn emit_default_provider_bundle() {
    println!("cargo:rerun-if-env-changed=HQ_SWITCH_BOOTSTRAP_BUNDLE");
    println!("cargo:rerun-if-env-changed=HQ_SWITCH_REQUIRE_BOOTSTRAP");
    println!("cargo:rerun-if-changed=src/hq/bootstrap_format.rs");
    let bundle = match std::env::var("HQ_SWITCH_BOOTSTRAP_BUNDLE") {
        Ok(value) => value.into_bytes(),
        Err(std::env::VarError::NotPresent) => Vec::new(),
        Err(std::env::VarError::NotUnicode(_)) => {
            panic!("Default provider bootstrap configuration is invalid")
        }
    };
    let required = match std::env::var("HQ_SWITCH_REQUIRE_BOOTSTRAP") {
        Ok(value) if value == "1" => true,
        Ok(value) if value.is_empty() || value == "0" => false,
        Err(std::env::VarError::NotPresent) => false,
        _ => panic!("Default provider bootstrap requirement is invalid"),
    };
    let configured = bootstrap_format::parse_bundle(&bundle)
        .unwrap_or_else(|_| panic!("Default provider bootstrap configuration is invalid"))
        .is_some();
    assert!(
        !required || configured,
        "Release packaging requires a configured default provider bootstrap"
    );
    let output = std::path::PathBuf::from(
        std::env::var_os("OUT_DIR").expect("missing Cargo output directory"),
    )
    .join("hq-default-provider.bundle");
    std::fs::write(output, bundle).expect("Cannot write default provider bootstrap bundle");
}
