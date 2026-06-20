#include <jni.h>
#include <unistd.h>
#include "zygisk.hpp"
#include "rust/cxx.h"
#include "zygisk-wekit/src/lib.rs.h"

using namespace zygisk;

// JNIEnv* stashed from on_load for JNI string extraction in pre_app_thunk
static JNIEnv* g_env = nullptr;

// Stub — Rust owns all module state; thunks ignore m.
struct StubModule : public ModuleBase {
    void onLoad(Api*, JNIEnv*) override { /* no-op — thunks call Rust directly */ }
};

// Thunks forward Zygisk callbacks to Rust.
static void pre_app_thunk(ModuleBase*, AppSpecializeArgs* a) {
    const char* process = "";
    if (g_env && a) {
        process = g_env->GetStringUTFChars(a->nice_name, nullptr);
    }
    pre_app_specialize(a, process);
    if (g_env && a) g_env->ReleaseStringUTFChars(a->nice_name, process);
}
static void post_app_thunk(ModuleBase*, const AppSpecializeArgs* a) {
    post_app_specialize(const_cast<AppSpecializeArgs*>(a));
}
static void pre_server_thunk(ModuleBase*, ServerSpecializeArgs* a) {
    pre_server_specialize(a, "system_server");
}
static void post_server_thunk(ModuleBase*, const ServerSpecializeArgs* a) {
    post_server_specialize(const_cast<ServerSpecializeArgs*>(a));
}

// C++ helpers callable from Rust via cxx bridge.
// Must be in the zygisk namespace because cxx generates ::zygisk::zygisk_set_option etc.
namespace zygisk {
    extern "C" void zygisk_set_option(Api* api, int opt) { api->setOption((Option)opt); }
    extern "C" int  zygisk_connect_companion(Api* api) { return api->connectCompanion(); }
}

// Specialize entry_impl to get friend access to Api::tbl and override the thunks.
// Called via extern "C" wrapper below.
namespace zygisk::internal {
template<>
void entry_impl<StubModule>(api_table *table, JNIEnv *env) {
    g_env = env;
    static Api api;
    api.tbl = table;           // friend access granted by Api class
    static StubModule module;
    ModuleBase *m = &module;
    static module_abi abi(m);
    abi.preAppSpecialize      = pre_app_thunk;
    abi.postAppSpecialize     = post_app_thunk;
    abi.preServerSpecialize   = pre_server_thunk;
    abi.postServerSpecialize  = post_server_thunk;
    if (!table->registerModule(table, &abi)) return;
    m->onLoad(&api, env);      // calls StubModule::onLoad (no-op)
    on_load(&api);             // notifies Rust
}
} // namespace zygisk::internal

// Thin wrapper callable from Rust's #[no_mangle] entry points.
// Rust cdylib only exports Rust-defined extern "C" symbols, so the actual
// entry points must live in Rust. This wrapper has friend access.
extern "C" void zygisk_init_from_rust(void *table, void *env) {
    internal::entry_impl<StubModule>(
        static_cast<internal::api_table*>(table),
        static_cast<JNIEnv*>(env));
}
