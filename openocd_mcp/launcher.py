"""
Launch and manage an OpenOCD process from Python.

The MCP server uses this to start OpenOCD on demand (so the user doesn't have
to run a .bat manually) and to stop it cleanly when done. If OpenOCD is already
running on the port (e.g. started externally), start() detects it and does not
launch a duplicate.
"""
import socket
import subprocess
import threading
import time
from collections import deque

from . import config


class OpenOCDLauncher:
    def __init__(
        self,
        binary: str = "",
        scripts: str = "",
        interface_cfg: str = "",
        target_cfg: str = "",
        host: str = config.HOST,
        port: int = config.PORT,
    ):
        # All empty -> resolved live at start() time (OpenOCD path + project cfgs).
        self.binary = binary
        self.scripts = scripts
        self.interface_cfg = interface_cfg
        self.target_cfg = target_cfg
        self.host = host
        self.port = port
        self._proc: subprocess.Popen | None = None
        self._output: deque[str] = deque(maxlen=200)  # rolling startup log
        self._reader: threading.Thread | None = None

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def started_by_us(self) -> bool:
        """True if we launched an OpenOCD process that is still alive."""
        return self._proc is not None and self._proc.poll() is None

    def port_open(self) -> bool:
        """True if something is listening on the telnet command port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((self.host, self.port)) == 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _drain_output(self) -> None:
        """Background thread: keep reading OpenOCD's output so its pipe never
        fills up (which would otherwise block the process)."""
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._output.append(line.rstrip("\n"))

    def start(self, timeout: float = 15.0) -> str:
        """Start OpenOCD and wait until the command port accepts connections.

        Returns a human-readable status string. Raises RuntimeError on failure.
        """
        if self.port_open():
            return f"OpenOCD already running on {self.host}:{self.port} (not starting a new one)."
        if self.started_by_us():
            return "OpenOCD already started by this server."

        # Resolve the OpenOCD binary live: explicit -> env/bundled/cached/PATH ->
        # auto-download as a last resort (first-run provisioning for pip installs).
        binary, scripts = self.binary, self.scripts
        if not binary:
            binary, scripts = config.openocd_paths()
        if not binary:
            try:
                from . import provision
                binary, scripts = provision.install()
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    "OpenOCD not found and auto-download failed: "
                    f"{e}. Set OPENOCD_BIN or put 'openocd' on PATH."
                ) from e

        interface_cfg = self.interface_cfg or config.settings.get("interface_cfg")
        target_cfg = self.target_cfg or config.settings.get("target_cfg")
        if not target_cfg:
            raise RuntimeError(
                "No target config set. Configure the chip first, e.g. "
                "configure(target_cfg='target/stm32g0x.cfg'), set it in "
                "openocd-mcp.json, or pass target_cfg to start_openocd."
            )

        cmd = [binary]
        if scripts:  # bundled/explicit scripts dir; omit to let OpenOCD find its own
            cmd += ["-s", scripts]
        cmd += ["-f", interface_cfg, "-f", target_cfg]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"OpenOCD binary not found: {binary}. Set OPENOCD_BIN."
            ) from e

        self._output.clear()
        self._reader = threading.Thread(target=self._drain_output, daemon=True)
        self._reader.start()

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                # OpenOCD exited during startup — surface why (no probe, bad cfg, etc.)
                raise RuntimeError(
                    "OpenOCD exited during startup:\n" + self._recent_output()
                )
            if self.port_open():
                return (
                    f"OpenOCD started and listening on {self.host}:{self.port}.\n"
                    + self._recent_output()
                )
            time.sleep(0.2)

        # Timed out — kill the half-started process and report.
        self.stop()
        raise RuntimeError(
            f"OpenOCD did not open {self.host}:{self.port} within {timeout}s:\n"
            + self._recent_output()
        )

    def stop(self) -> str:
        """Stop the OpenOCD process we launched (no-op if we didn't launch it)."""
        if self._proc is None:
            return "No OpenOCD process was started by this server."
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        return "OpenOCD stopped."

    def _recent_output(self, lines: int = 20) -> str:
        snapshot = list(self._output)[-lines:]
        return "\n".join(snapshot)
