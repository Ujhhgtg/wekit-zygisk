#!/usr/bin/env -S uv run --script

import json
import os
import shutil
import subprocess as sp
import sys
import zipfile
from argparse import ArgumentParser
from hashlib import sha256
from pathlib import Path

# --- Global Configurations ---
MODULE_ID = "zygisk-wekit"
MODULE_NAME = "WeKit on Zygisk"
RELEASE_NAME = "1"

# Fail-fast if mandatory Linux environment coordinates are missing
if "ANDROID_HOME" not in os.environ or "ANDROID_NDK_HOME" not in os.environ:
    sys.exit(
        "Error: Both ANDROID_HOME and ANDROID_NDK_HOME environment variables must be defined."
    )

ANDROID_HOME = Path(os.environ["ANDROID_HOME"])
ANDROID_NDK_HOME = Path(os.environ["ANDROID_NDK_HOME"])

ROOT_DIR = Path(__file__).parent.resolve()
BUILD_DIR = ROOT_DIR / "my_build"
OUTPUT_DIR = ROOT_DIR / "output"
RELEASE_DIR = ROOT_DIR / "release"
SOURCE_DIR = ROOT_DIR / "native"

CONFIG_FILE = ROOT_DIR / "project-config.json"
if not CONFIG_FILE.exists():
    sys.exit(f"Error: Missing configuration map at {CONFIG_FILE}")

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    PROJECT_CONFIG = json.load(f)

PLATFORM = PROJECT_CONFIG.get("platform")
if not PLATFORM:
    sys.exit("Error: 'platform' key missing from project-config.json")

BUILD_TYPE = None
BUILD_DIR_NAME = None
NATIVE_OUTPUT_DIR = None
BIN_OUTPUT_DIR = None
LIB_OUTPUT_DIR = None
UNSTRIPPED_OUTPUT_DIR = None
CMAKE_TOOLCHAIN_FILE = None


def initialize(args):
    global \
        ANDROID_NDK_HOME, \
        CMAKE_TOOLCHAIN_FILE, \
        BUILD_TYPE, \
        BUILD_DIR_NAME, \
        NATIVE_OUTPUT_DIR, \
        BIN_OUTPUT_DIR, \
        LIB_OUTPUT_DIR, \
        UNSTRIPPED_OUTPUT_DIR

    if args.ndk:
        ANDROID_NDK_HOME = ANDROID_HOME / "ndk" / args.ndk

    if not ANDROID_NDK_HOME.is_dir():
        sys.exit(f"Error: Target NDK directory does not exist: {ANDROID_NDK_HOME}")

    CMAKE_TOOLCHAIN_FILE = ANDROID_NDK_HOME / "build/cmake/android.toolchain.cmake"
    BUILD_TYPE = args.build_type
    BUILD_DIR_NAME = BUILD_TYPE

    NATIVE_OUTPUT_DIR = OUTPUT_DIR / "native" / BUILD_DIR_NAME
    BIN_OUTPUT_DIR = NATIVE_OUTPUT_DIR / "bin"
    LIB_OUTPUT_DIR = NATIVE_OUTPUT_DIR / "lib"
    UNSTRIPPED_OUTPUT_DIR = OUTPUT_DIR / "unstripped" / BUILD_DIR_NAME


# --- Subprocess Wrappers ---
def exec_out(cmd):
    return sp.run(
        cmd, stdout=sp.PIPE, stderr=sp.DEVNULL, text=True, check=True
    ).stdout.strip()


def exec_cmd(cmd, ignore_error=False, **kwargs):
    res = sp.run(cmd, check=not ignore_error, **kwargs)
    return res.returncode


def exec_adb_cmd(c, device=None, **kwargs):
    cmd = ["adb"] + (["-s", device] if device else []) + c
    exec_cmd(cmd, **kwargs)


def exec_adb_shell(c, device=None, root=False, **kwargs):
    cmd = ["adb"] + (["-s", device] if device else []) + ["shell"]
    cmd += [f"exec su -c '{c}'"] if root else [c]
    exec_cmd(cmd, **kwargs)


# Initialize Git Details Early
try:
    GIT_COMMIT_COUNT = int(exec_out(["git", "rev-list", "HEAD", "--count"]))
    GIT_COMMIT_HASH = exec_out(["git", "rev-parse", "--verify", "--short", "HEAD"])
except Exception:
    GIT_COMMIT_COUNT = 0
    GIT_COMMIT_HASH = "unknown"


# --- Core Architecture Mapping ---
SUPPORTED_ABIS = ["arm64-v8a", "armeabi-v7a", "x86_64", "x86", "riscv64"]
ABI_NAME_ALIAS = {
    "arm64-v8a": ["arm64", "a64", "aarch64", "arm64_v8a"],
    "armeabi-v7a": ["armeabi", "arm", "arm32", "a32", "armeabi_v7a"],
    "x86": ["i386", "x32"],
    "x86_64": ["x64", "x86-64"],
    "riscv64": ["riscv"],
}
ABI_CHOICES = list(ABI_NAME_ALIAS.keys()) + sum(ABI_NAME_ALIAS.values(), [])
ABI_MAP = {None: None}
for k, aliases in ABI_NAME_ALIAS.items():
    ABI_MAP[k] = k
    for alias in aliases:
        ABI_MAP[alias] = k

DEFAULT_ABI = "arm64-v8a"
ABI_TO_MAGISK_ARCH = {
    "arm64-v8a": "arm64",
    "armeabi-v7a": "arm",
    "x86_64": "x64",
    "x86": "x86",
    "riscv64": "riscv64",
}
BUILD_TYPE_CHOICES = ["debug", "release"]
BUILD_TYPE_CHOICES_MAP = {"debug": "Debug", "release": "RelWithDebInfo"}


# --- Build & Configurations Command Engines ---
def config(abi, plat, build_type):
    bin_build_type = BUILD_TYPE_CHOICES_MAP[build_type]
    build_dir = BUILD_DIR / BUILD_DIR_NAME / abi
    exec_cmd(
        [
            "cmake",
            f"-H{SOURCE_DIR}",
            f"-B{build_dir}",
            f"-DANDROID_ABI={abi}",
            f"-DANDROID_PLATFORM={plat}",
            f"-DANDROID_NDK={ANDROID_NDK_HOME}",
            "-DANDROID_STL=c++_static",
            "-DANDROID_SUPPORT_FLEXIBLE_PAGE_SIZES=ON",
            f"-DCMAKE_TOOLCHAIN_FILE={CMAKE_TOOLCHAIN_FILE}",
            f"-DCMAKE_RUNTIME_OUTPUT_DIRECTORY={BIN_OUTPUT_DIR / abi}",
            f"-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={LIB_OUTPUT_DIR / abi}",
            f"-DDEBUG_SYMBOLS_PATH={UNSTRIPPED_OUTPUT_DIR / abi}",
            f"-DCMAKE_BUILD_TYPE={bin_build_type}",
            f"-DMODULE_NAME={MODULE_ID}",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
            "-G",
            "Ninja",
        ]
    )


def build_target(target, abi, plat, build_type):
    build_dir = BUILD_DIR / BUILD_DIR_NAME / abi
    config(abi, plat, build_type=build_type)
    exec_cmd(["cmake", "--build", build_dir, "--", target, f"-j{os.cpu_count()}"])

    bin_path = BIN_OUTPUT_DIR / abi / target
    lib_path = LIB_OUTPUT_DIR / abi / target
    return bin_path if bin_path.exists() else lib_path


def build_all(abi, plat, build_type, force):
    if force:
        shutil.rmtree(LIB_OUTPUT_DIR / abi, ignore_errors=True)
        shutil.rmtree(BIN_OUTPUT_DIR / abi, ignore_errors=True)
        shutil.rmtree(UNSTRIPPED_OUTPUT_DIR / abi, ignore_errors=True)
    build_dir = BUILD_DIR / BUILD_DIR_NAME / abi
    config(abi, plat, build_type=build_type)
    exec_cmd(["cmake", "--build", build_dir, "--", f"-j{os.cpu_count()}"])


def get_device_abi(device):
    return exec_out(
        ["adb"]
        + (["-s", device] if device else [])
        + ["shell", "getprop ro.product.cpu.abi"]
    )


def deploy(target, device, abi, dest, build_type):
    if abi is None:
        abi = get_device_abi(device)
    if abi not in SUPPORTED_ABIS:
        raise ValueError(f"Device has unsupported ABI: {abi}")
    print(f"** Deploying to {abi}")
    output = build_target(target, abi, PLATFORM, build_type=build_type)
    exec_adb_cmd(["push", str(output), dest], device=device)
    exec_adb_cmd(["shell", f"chmod +x {dest}/{target}"], device=device)
    print(f"Successfully deployed {target} -> {dest}/{target}")


# --- Command Targets Parsing handlers ---
def build_cmd(args):
    if args.target is None:
        build_all(
            abi=ABI_MAP[args.abi],
            plat=PLATFORM,
            build_type=args.build_type,
            force=args.force,
        )
    else:
        build_target(
            args.target,
            abi=ABI_MAP[args.abi],
            plat=PLATFORM,
            build_type=args.build_type,
        )


def config_cmd(args):
    config(ABI_MAP[args.abi], plat=PLATFORM, build_type=args.build_type)


def deploy_cmd(args):
    deploy(
        args.target,
        args.device,
        abi=ABI_MAP[args.abi],
        dest=args.dest,
        build_type=args.build_type,
    )


def clean_cmd(args):
    abi = args.abi if args.abi else "*"
    build_type = BUILD_TYPE_CHOICES_MAP[args.build_type] if args.abi else "*"
    for p in BUILD_DIR.glob(f"{build_type}/{abi}"):
        print("Cleaning path:", p)
        shutil.rmtree(p, ignore_errors=True)


def build_zip(args):
    build_type = BUILD_TYPE
    for abi in SUPPORTED_ABIS:
        build_all(abi=abi, plat=PLATFORM, build_type=build_type, force=args.force)

    module_path = OUTPUT_DIR / "module" / BUILD_DIR_NAME
    module_template = ROOT_DIR / "template"
    shutil.rmtree(module_path, ignore_errors=True)
    module_path.mkdir(parents=True, exist_ok=True)

    def fix_crlf(p: Path):
        if p.exists():
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))

    def expand_text_file(p: Path, expand=None):
        if not p.exists():
            return
        text = p.read_text(encoding="utf-8")
        if expand:
            for k, v in expand.items():
                text = text.replace(f"@{k}@", v).replace(f"${{{k}}}", v)
        p.write_text(text, encoding="utf-8")

    shutil.copytree(module_template, module_path, dirs_exist_ok=True)
    shutil.copy(ROOT_DIR / "README.md", module_path / "README.md")

    for p, _, fns in module_path.walk():
        for fn in fns:
            if fn != "mazoku":
                fix_crlf(p / fn)

    expand_text_file(
        module_path / "module.prop",
        {
            "moduleId": MODULE_ID,
            "moduleName": MODULE_NAME,
            "versionName": f"{RELEASE_NAME} ({GIT_COMMIT_COUNT}-{GIT_COMMIT_HASH}-{build_type})",
            "versionCode": str(GIT_COMMIT_COUNT),
        },
    )

    script_vars = {
        "DEBUG": str(build_type == "debug").lower(),
        "SONAME": MODULE_ID,
        "SUPPORTED_ABIS": " ".join(ABI_TO_MAGISK_ARCH[x] for x in SUPPORTED_ABIS),
    }

    for script in [
        "customize.sh",
        "post-fs-data.sh",
        "service.sh",
        "uninstall.sh",
        "cleanup.sh",
    ]:
        expand_text_file(module_path / script, script_vars)

    sepolicy_file = module_path / "sepolicy.rule"
    if sepolicy_file.exists():
        lines = sepolicy_file.read_text(encoding="utf-8").splitlines()
        filtered = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        sepolicy_file.write_text("\n".join(filtered) + "\n", encoding="utf-8")

    shutil.copytree(NATIVE_OUTPUT_DIR, module_path, dirs_exist_ok=True)

    build_name = f"{MODULE_NAME}-{RELEASE_NAME}-{GIT_COMMIT_COUNT}-{GIT_COMMIT_HASH}-{build_type}".replace(
        " ", "-"
    )
    output_path = RELEASE_DIR / f"{build_name}.zip"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists():
        output_path.unlink()

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as out_zip:
        for p, dns, fns in module_path.walk():
            for dn in dns:
                out_zip.mkdir((p / dn).relative_to(module_path).as_posix())
            for fn in fns:
                f = p / fn
                rp = f.relative_to(module_path).as_posix()
                data = f.read_bytes()
                out_zip.writestr(rp, data)
                out_zip.writestr(f"{rp}.sha256", sha256(data).hexdigest())

    print(f"* output written to: {output_path}")

    if args.save_debug:
        symbols_name = f"{build_name}-symbols.zip"
        symbols_out = ROOT_DIR / "symbols" / symbols_name
        symbols_out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            symbols_out, "w", compression=zipfile.ZIP_DEFLATED
        ) as out_zip:
            for p, dns, fns in UNSTRIPPED_OUTPUT_DIR.walk():
                for dn in dns:
                    out_zip.mkdir(
                        (p / dn).relative_to(UNSTRIPPED_OUTPUT_DIR).as_posix()
                    )
                for fn in fns:
                    f = p / fn
                    rp = f.relative_to(UNSTRIPPED_OUTPUT_DIR).as_posix()
                    out_zip.writestr(rp, f.read_bytes())
        print(f"* symbols written to: {symbols_out}")

    return output_path


def zip_cmd(args):
    build_zip(args)


def flash(args):
    if args.skip:
        zip_file = max(RELEASE_DIR.glob(f"*-{BUILD_TYPE}.zip"), key=os.path.getmtime)
    else:
        zip_file = build_zip(args)
    print("Flashing ZIP target:", zip_file)

    name = zip_file.name
    exec_adb_cmd(["push", str(zip_file), f"/data/local/tmp/{name}"], device=args.device)
    exec_adb_cmd(
        [
            "push",
            str(ROOT_DIR / "scripts/install_module.sh"),
            "/data/local/tmp/install_module.sh",
        ],
        device=args.device,
    )

    exec_adb_shell(
        f"sh /data/local/tmp/install_module.sh /data/local/tmp/{name}",
        device=args.device,
        root=True,
    )
    exec_adb_shell(
        f"rm /data/local/tmp/install_module.sh /data/local/tmp/{name}",
        device=args.device,
        root=True,
        ignore_error=True,
    )

    if args.reboot:
        exec_adb_shell(
            "svc power reboot || reboot",
            device=args.device,
            root=True,
            ignore_error=True,
        )


def main():
    ap = ArgumentParser(
        prog="build", description="Zygisk module unified engine assembler script"
    )
    ap.add_argument("--ndk", dest="ndk", help="Custom NDK override context path")
    ap.add_argument("--save-debug", dest="save_debug", action="store_true")
    ap.add_argument(
        "--force",
        dest="force",
        help="Purge caches before executing build",
        action="store_true",
    )
    ap.add_argument(
        "-t",
        dest="build_type",
        choices=BUILD_TYPE_CHOICES,
        default=BUILD_TYPE_CHOICES[0],
    )

    subps = ap.add_subparsers(required=True)

    build_args = subps.add_parser("build")
    build_args.add_argument("target", nargs="?")
    build_args.add_argument("-a", dest="abi", choices=ABI_CHOICES, default=DEFAULT_ABI)
    build_args.set_defaults(func=build_cmd)

    config_args = subps.add_parser("config")
    config_args.set_defaults(func=config_cmd)
    config_args.add_argument("-a", dest="abi", choices=ABI_CHOICES, default=DEFAULT_ABI)

    deploy_args = subps.add_parser("deploy")
    deploy_args.add_argument("target")
    deploy_args.set_defaults(func=deploy_cmd)
    deploy_args.add_argument("-s", dest="device")
    deploy_args.add_argument("-a", dest="abi", choices=ABI_CHOICES, default=None)
    deploy_args.add_argument("-d", dest="dest", default="/data/local/tmp")

    clean_args = subps.add_parser("clean")
    clean_args.add_argument("-a", "--abi", default=None)
    clean_args.set_defaults(func=clean_cmd)

    zip_args = subps.add_parser("zip")
    zip_args.set_defaults(func=zip_cmd)

    flash_args = subps.add_parser("flash")
    flash_args.add_argument("-s", "--device")
    flash_args.add_argument("--root")
    flash_args.add_argument("-r", "--reboot", action="store_true")
    flash_args.add_argument("--skip", action="store_true")
    flash_args.set_defaults(func=flash)

    args = ap.parse_args()
    initialize(args)
    args.func(args)


if __name__ == "__main__":
    main()
