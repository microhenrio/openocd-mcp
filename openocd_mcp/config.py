"""
Configuration for the OpenOCD MCP server.

This server is PROJECT-AGNOSTIC — it works with any target/chip/project. Chip-
specific things (target config, SVD, firmware ELF) are NOT hardcoded here; they
are supplied per project. Settings resolve in this order (later wins):

  1. Machine defaults below — the OpenOCD install on this PC (same for every
     project) plus the debug probe.
  2. A per-project config file: 'openocd-mcp.json' found in the server's working
     directory, or the path given in the OPENOCD_MCP_CONFIG environment variable.
  3. The `configure` tool, called at runtime within a session.
  4. Explicit arguments to individual tools (start_openocd, load_svd, load_elf).

A project config file (see openocd-mcp.example.json) looks like:

  {
    "interface_cfg": "interface/stlink.cfg",
    "target_cfg":    "target/stm32g0x.cfg",
    "svd_file":      "C:\\\\Program Files\\\\...\\\\SVD\\\\STM32G0B0.svd",
    "elf_file":      "C:\\\\path\\\\to\\\\build\\\\firmware.elf"
  }
"""
import json
import os
import shutil

from . import provision

# Package dir (.../openocd_mcp) and the repo/install root that holds the bundled
# 'openocd/' and 'svd/' folders (the parent, in an editable/zip layout).
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_PKG_DIR)


def openocd_paths() -> tuple[str | None, str]:
    """Locate the OpenOCD binary + script library, resolved live. Order:
      1. OPENOCD_BIN / OPENOCD_SCRIPTS environment variables.
      2. The copy bundled with this project (<repo>/openocd) — present in the
         git/zip distribution, makes it self-contained and offline.
      3. A copy auto-downloaded by `provision` (the PyPI-install path).
      4. 'openocd' on the system PATH (it locates its own scripts).
    Returns (None, "") if OpenOCD is not found anywhere (callers can then offer
    to download it). Never downloads — this is a read-only lookup.
    """
    env_bin = os.environ.get("OPENOCD_BIN")
    if env_bin:
        return env_bin, os.environ.get("OPENOCD_SCRIPTS", "")

    bundled_bin = os.path.join(_REPO_DIR, "openocd", "bin", "openocd.exe")
    bundled_scripts = os.path.join(_REPO_DIR, "openocd", "openocd", "scripts")
    if os.path.isfile(bundled_bin):
        return bundled_bin, bundled_scripts

    cached_bin, cached_scripts = provision.cached_paths()
    if cached_bin:
        return cached_bin, cached_scripts

    on_path = shutil.which("openocd")
    if on_path:
        return on_path, ""

    return None, ""

# TCL-RPC port the MCP client connects to (4444 is the human telnet port).
HOST = "localhost"
PORT = 6666

# --- Per-project settings (override via project file / configure / tool args)
# The debug probe defaults to ST-Link (this machine's probe). target_cfg,
# svd_file and elf_file are intentionally empty: a general server makes no
# assumption about which chip or firmware you're debugging.
_DEFAULTS = {
    "interface_cfg": "interface/stlink.cfg",
    "target_cfg": "",   # e.g. "target/stm32g0x.cfg"
    "transport": "",    # e.g. "swd" — needed for J-Link on Cortex-M (else it picks JTAG)
    "svd_file": "",     # e.g. r"...\SVD\STM32G0B0.svd"
    "elf_file": "",     # e.g. r"...\build\firmware.elf"
}

PROJECT_CONFIG_NAME = "openocd-mcp.json"

# Safety/permission defaults. Override via a "permissions" object in
# openocd-mcp.json, the set_permissions tool, or OPENOCD_MCP_READONLY=1.
# read_only is a master switch that blocks every mutating operation.
_PERM_DEFAULTS = {
    "read_only": False,          # master: block all writes/flash/erase/raw
    "allow_memory_write": True,  # write_memory / write_variable / write_register / write_peripheral_register
    "allow_flash": True,         # flash_write (program)
    "allow_flash_erase": False,  # flash_erase_sector (destructive — opt in)
    "allow_raw_command": True,   # run_command escape hatch
    "flash_allowed_paths": [],   # if set, flash files must live under one of these dirs
    "flash_max_bytes": 0,        # if > 0, reject flashing files larger than this
}


def _load_project_file() -> tuple[dict, str | None]:
    """Find and read a per-project config file, if any."""
    path = os.environ.get("OPENOCD_MCP_CONFIG") or os.path.join(
        os.getcwd(), PROJECT_CONFIG_NAME
    )
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return {k: v for k, v in data.items() if k in _DEFAULTS}, path
        except (OSError, json.JSONDecodeError):
            pass
    return {}, None


class Settings:
    """Effective per-project settings, layered over machine defaults."""

    def __init__(self):
        self.values = dict(_DEFAULTS)
        self.source = "machine defaults"
        loaded, path = _load_project_file()
        if loaded:
            self.values.update(loaded)
        if path:
            self.source = path

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    def update(self, **kw) -> None:
        for k, v in kw.items():
            if k in self.values and v:
                self.values[k] = v

    def as_dict(self) -> dict:
        return dict(self.values)


class Permissions:
    """Safety gates for mutating operations, layered over safe defaults."""

    def __init__(self):
        self.values = dict(_PERM_DEFAULTS)
        path = os.environ.get("OPENOCD_MCP_CONFIG") or os.path.join(
            os.getcwd(), PROJECT_CONFIG_NAME
        )
        if path and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    perms = json.load(f).get("permissions", {})
                for k, v in perms.items():
                    if k in self.values:
                        self.values[k] = v
            except (OSError, json.JSONDecodeError):
                pass
        if os.environ.get("OPENOCD_MCP_READONLY"):
            self.values["read_only"] = True

    def get(self, key):
        return self.values.get(key)

    def update(self, **kw) -> None:
        for k, v in kw.items():
            if k in self.values and v is not None:
                self.values[k] = v

    def as_dict(self) -> dict:
        return dict(self.values)

    # -- gate checks (return True if the op is allowed) --
    def can_write_memory(self) -> bool:
        return not self.values["read_only"] and self.values["allow_memory_write"]

    def can_flash(self) -> bool:
        return not self.values["read_only"] and self.values["allow_flash"]

    def can_erase(self) -> bool:
        return not self.values["read_only"] and self.values["allow_flash_erase"]

    def can_raw(self) -> bool:
        return not self.values["read_only"] and self.values["allow_raw_command"]


def resolve_path(path: str) -> str:
    """Resolve a possibly-relative svd/elf path against the project directory,
    so a config like {"svd_file": "svd/STM32G0B0.svd"} works no matter where the
    project is cloned. Absolute paths and empty strings pass through unchanged."""
    if not path or os.path.isabs(path):
        return path
    return os.path.join(_REPO_DIR, path)


# Shared singletons the server reads from.
settings = Settings()
permissions = Permissions()
