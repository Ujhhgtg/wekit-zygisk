#include <sys/types.h>
#include "zygisk.hpp"

// Forward declarations needed by cxx-generated code.
// Definitions live in shim.cpp.
namespace zygisk {
    extern "C" void zygisk_set_option(Api* api, int opt);
    extern "C" int  zygisk_connect_companion(Api* api);
}
