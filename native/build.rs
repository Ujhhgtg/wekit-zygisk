fn main() {
    cxx_build::bridge("src/lib.rs")
        .file("src/shim.cpp")
        .flag("-fno-exceptions")
        .flag("-fno-rtti")
        .std("c++17")
        .include(".")
        .compile("zygisk_shim");

    println!("cargo:rustc-link-lib=log");
    println!("cargo:rustc-link-lib=dl");
    println!("cargo:rustc-link-lib=unwind");
    println!("cargo:rerun-if-changed=src/shim.cpp");
    println!("cargo:rerun-if-changed=src/lib.rs");
    println!("cargo:rerun-if-changed=zygisk.hpp");
}
