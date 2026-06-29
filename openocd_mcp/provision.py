"""
Download and cache the xPack OpenOCD binaries for the current OS/arch.

This is what makes the package usable when installed from PyPI (where no binaries
are bundled): on first use the server fetches the right OpenOCD build, verifies a
pinned SHA-256, and caches it under a per-user data directory. Cross-platform,
and it avoids us redistributing the GPL binaries ourselves (the user fetches them
straight from the xPack release).
"""
import hashlib
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile

# Pinned xPack OpenOCD release. Bump together with the bundled copy.
XPACK_VERSION = "0.12.0-7"
_BASE = (
    "https://github.com/xpack-dev-tools/openocd-xpack/releases/download/"
    f"v{XPACK_VERSION}"
)

# (platform.system(), platform.machine()) -> (asset filename, sha256)
_ASSETS = {
    ("Windows", "AMD64"): (
        f"xpack-openocd-{XPACK_VERSION}-win32-x64.zip",
        "6bfd3c97135aafef8affc9af1acf34fd0e2b9ca26044506f6abd7f95b7630052",
    ),
    ("Linux", "x86_64"): (
        f"xpack-openocd-{XPACK_VERSION}-linux-x64.tar.gz",
        "94b3790983beaf8ed57e646c0620dd66d705fddae03d290823a6ed3b439468d6",
    ),
    ("Linux", "aarch64"): (
        f"xpack-openocd-{XPACK_VERSION}-linux-arm64.tar.gz",
        "db73a3ab91c556ecec2405a7e02d404b11139df6aba1031cad94a7e6766d06cc",
    ),
    ("Darwin", "x86_64"): (
        f"xpack-openocd-{XPACK_VERSION}-darwin-x64.tar.gz",
        "668ad25350103a4357e11629ec833eae5982e973889ce25bad0c2963e37fa8bf",
    ),
    ("Darwin", "arm64"): (
        f"xpack-openocd-{XPACK_VERSION}-darwin-arm64.tar.gz",
        "667342c086984f3e5a55b4e0d5f711add13fb04de040fca493303000e6c19327",
    ),
}


def _data_dir() -> str:
    """Per-user data directory for cached downloads."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "openocd-mcp")


def _install_root() -> str:
    # xPack archives extract to a top-level 'xpack-openocd-<version>/' folder.
    return os.path.join(_data_dir(), "openocd", f"xpack-openocd-{XPACK_VERSION}")


def _bin_and_scripts(root: str) -> tuple[str, str]:
    exe = "openocd.exe" if sys.platform == "win32" else "openocd"
    return os.path.join(root, "bin", exe), os.path.join(root, "openocd", "scripts")


def cached_paths() -> tuple[str | None, str | None]:
    """(binary, scripts) if a valid cached install exists, else (None, None).
    Cheap — never downloads. Safe to call from config resolution."""
    binp, scripts = _bin_and_scripts(_install_root())
    return (binp, scripts) if os.path.isfile(binp) else (None, None)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install(log=print) -> tuple[str, str]:
    """Download (if needed), verify, and extract OpenOCD for this platform.
    Returns (binary, scripts). Idempotent. Raises RuntimeError on failure."""
    binp, scripts = cached_paths()
    if binp:
        log(f"OpenOCD already provisioned at {binp}")
        return binp, scripts

    key = (platform.system(), platform.machine())
    asset = _ASSETS.get(key)
    if not asset:
        raise RuntimeError(
            f"No prebuilt OpenOCD for platform {key}. Install OpenOCD manually "
            "and set the OPENOCD_BIN environment variable."
        )
    name, expected_sha = asset
    url = f"{_BASE}/{name}"

    dl_dir = os.path.join(_data_dir(), "downloads")
    os.makedirs(dl_dir, exist_ok=True)
    archive = os.path.join(dl_dir, name)

    log(f"Downloading {url} ...")
    urllib.request.urlretrieve(url, archive)

    log("Verifying SHA-256 ...")
    actual = _sha256(archive)
    if actual.lower() != expected_sha.lower():
        os.remove(archive)
        raise RuntimeError(
            f"Checksum mismatch for {name}: expected {expected_sha}, got {actual}"
        )

    dest = os.path.join(_data_dir(), "openocd")
    os.makedirs(dest, exist_ok=True)
    log("Extracting ...")
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            if sys.version_info >= (3, 12):
                t.extractall(dest, filter="data")
            else:
                t.extractall(dest)
    os.remove(archive)

    binp, scripts = cached_paths()
    if not binp:
        raise RuntimeError("Extraction did not produce the expected OpenOCD binary.")
    if sys.platform != "win32":
        os.chmod(binp, 0o755)
    log(f"OpenOCD ready at {binp}")
    return binp, scripts
