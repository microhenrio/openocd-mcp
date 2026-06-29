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

# Package dir (.../openocd_mcp) and the repo/install root that holds the bundled
# 'openocd/' and 'svd/' folders (the parent, in an editable/zip layout).
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_DIR = os.path.dirname(_PKG_DIR)


def _resolve_openocd() -> tuple[str, str]:
    """Locate the OpenOCD binary + script library. Resolution order:
      1. OPENOCD_BIN / OPENOCD_SCRIPTS environment variables.
      2. The copy bundled with this project (<repo>/openocd) — makes the project
         self-contained and distributable; no separate download needed.
      3. 'openocd' on the system PATH (it finds its own scripts).
    """
    env_bin = os.environ.get("OPENOCD_BIN")
    if env_bin:
        return env_bin, os.environ.get("OPENOCD_SCRIPTS", "")

    bundled_bin = os.path.join(_REPO_DIR, "openocd", "bin", "openocd.exe")
    bundled_scripts = os.path.join(_REPO_DIR, "openocd", "openocd", "scripts")
    if os.path.isfile(bundled_bin):
        return bundled_bin, bundled_scripts

    return "openocd", ""  # rely on PATH; OpenOCD locates its own scripts


# --- The OpenOCD installation (bundled with the project by default) --------
OPENOCD_BIN, OPENOCD_SCRIPTS = _resolve_openocd()

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
    "svd_file": "",     # e.g. r"...\SVD\STM32G0B0.svd"
    "elf_file": "",     # e.g. r"...\build\firmware.elf"
}

PROJECT_CONFIG_NAME = "openocd-mcp.json"


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


def resolve_path(path: str) -> str:
    """Resolve a possibly-relative svd/elf path against the project directory,
    so a config like {"svd_file": "svd/STM32G0B0.svd"} works no matter where the
    project is cloned. Absolute paths and empty strings pass through unchanged."""
    if not path or os.path.isabs(path):
        return path
    return os.path.join(_REPO_DIR, path)


# Single shared settings object the server reads from.
settings = Settings()
