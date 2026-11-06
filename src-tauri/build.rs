fn main() {
    println!(
        "cargo:rustc-env=EASY_PUBLISH_TARGET={}",
        std::env::var("TARGET").unwrap()
    );
    #[cfg(feature = "desktop")]
    tauri_build::build()
}
