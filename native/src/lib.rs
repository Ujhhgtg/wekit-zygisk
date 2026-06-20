use std::ffi::CStr;
use std::ffi::c_char;
use std::sync::LazyLock;
use std::sync::atomic::AtomicPtr;
use std::sync::atomic::Ordering;

#[cxx::bridge(namespace = "zygisk")]
mod ffi {
    unsafe extern "C++" {
        include!("src/bridge.hpp");

        type Api;
        type AppSpecializeArgs;
        type ServerSpecializeArgs;

        unsafe fn zygisk_set_option(api: *mut Api, opt: i32);
        unsafe fn zygisk_connect_companion(api: *mut Api) -> i32;
    }

    extern "Rust" {
        unsafe fn on_load(api: *mut Api);
        unsafe fn pre_app_specialize(a: *mut AppSpecializeArgs, process: *const c_char);
        unsafe fn post_app_specialize(a: *mut AppSpecializeArgs);
        unsafe fn pre_server_specialize(a: *mut ServerSpecializeArgs, process: *const c_char);
        unsafe fn post_server_specialize(a: *mut ServerSpecializeArgs);
    }
}

unsafe extern "C" {
    fn __android_log_print(prio: i32, tag: *const c_char, fmt: *const c_char, ...) -> i32;
}

const ANDROID_LOG_DEBUG: i32 = 3;

static API: AtomicPtr<ffi::Api> = AtomicPtr::new(std::ptr::null_mut());

pub unsafe fn on_load(api: *mut ffi::Api) {
    API.store(api, Ordering::Relaxed);
}

pub unsafe fn pre_app_specialize(_a: *mut ffi::AppSpecializeArgs, process: *const c_char) {
    let process_str = if process.is_null() {
        ""
    } else {
        // SAFETY: process is a valid C string from GetStringUTFChars
        unsafe { CStr::from_ptr(process) }.to_str().unwrap_or("<?>")
    };
    pre_specialize(process_str);
}

pub unsafe fn post_app_specialize(_a: *mut ffi::AppSpecializeArgs) {}

pub unsafe fn pre_server_specialize(_a: *mut ffi::ServerSpecializeArgs, _process: *const c_char) {
    pre_specialize("system_server");
}

pub unsafe fn post_server_specialize(_a: *mut ffi::ServerSpecializeArgs) {}

fn pre_specialize(process: &str) {
    let api = API.load(Ordering::Relaxed);
    if api.is_null() {
        return;
    }

    unsafe {
        let fd = ffi::zygisk_connect_companion(api);
        if fd < 0 {
            return;
        }

        let mut r: u32 = 0;
        libc::read(fd, &mut r as *mut u32 as *mut libc::c_void, 4);
        libc::close(fd);

        let tag = b"WeKitZygisk\0".as_ptr() as *const c_char;
        let fmt = b"process=[%s], r=[%u]\0".as_ptr() as *const c_char;
        let c_process = std::ffi::CString::new(process).unwrap_or_default();
        __android_log_print(ANDROID_LOG_DEBUG, tag, fmt, c_process.as_ptr(), r);

        ffi::zygisk_set_option(api, 1); // DlcloseModuleLibrary = 1
    }
}

static URANDOM: LazyLock<i32> = LazyLock::new(|| unsafe {
    libc::open(b"/dev/urandom\0".as_ptr() as *const c_char, libc::O_RDONLY)
});

// C++ helper that initializes the Zygisk module (has friend access to Api::tbl).
// entry_impl<StubModule> is defined in the zygisk::internal namespace in shim.cpp.
// The extern "C" wrapper lets Rust call it.
unsafe extern "C" {
    fn zygisk_init_from_rust(table: *const std::ffi::c_void, env: *const std::ffi::c_void);
}

// Rust entry points are #[no_mangle] pub extern "C" so Rust's cdylib export
// mechanism puts them in the dynamic symbol table where Zygisk can find them.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn zygisk_module_entry(
    table: *const std::ffi::c_void,
    env: *mut std::ffi::c_void,
) {
    unsafe { zygisk_init_from_rust(table, env as *const std::ffi::c_void) };
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn zygisk_companion_entry(fd: i32) {
    rust_companion_handler(fd);
}

pub extern "C" fn rust_companion_handler(fd: i32) {
    unsafe {
        let mut r: u32 = 0;
        libc::read(*URANDOM, &mut r as *mut u32 as *mut libc::c_void, 4);

        let tag = b"WeKitZygisk\0".as_ptr() as *const c_char;
        let fmt = b"companion r=[%u]\0".as_ptr() as *const c_char;
        __android_log_print(ANDROID_LOG_DEBUG, tag, fmt, r);

        libc::write(fd, &r as *const u32 as *const libc::c_void, 4);
    }
}
