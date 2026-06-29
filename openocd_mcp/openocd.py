"""
Client for OpenOCD's TCL-RPC port (default: localhost:6666).

This is OpenOCD's programmatic interface: unlike the telnet port (4444), it does
NOT echo commands, print a prompt, or mirror OpenOCD's log stream into the
channel — so command responses never get polluted by asynchronous log lines.

Protocol: each request and response is terminated by a single 0x1a byte.
Most OpenOCD commands write their human-readable output to the log rather than
returning it as a Tcl value, so we wrap every command in `capture {...}` to
collect that text as the response.

OpenOCD must already be running before any tool is called.
"""
import socket
import threading

_SEP = b"\x1a"  # OpenOCD TCL-RPC command/response terminator (Ctrl-Z / SUB)


class OpenOCDError(Exception):
    pass


class OpenOCDClient:
    _RECV_TIMEOUT = 30.0  # seconds to wait for a response (flashing can be slow)

    def __init__(self, host: str = "localhost", port: int = 6666):
        self.host = host
        self.port = port
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self._RECV_TIMEOUT)
        try:
            sock.connect((self.host, self.port))
        except OSError as e:
            sock.close()
            raise OpenOCDError(
                f"Cannot connect to OpenOCD at {self.host}:{self.port}: {e}"
            ) from e
        self._sock = sock  # TCL-RPC has no welcome banner — nothing to consume

    def close(self) -> None:
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None

    def is_connected(self) -> bool:
        return self._sock is not None

    # ------------------------------------------------------------------
    # Core send / receive
    # ------------------------------------------------------------------

    def _read_until_sep(self) -> str:
        """Read bytes until the 0x1a response terminator."""
        assert self._sock is not None
        data = b""
        while _SEP not in data:
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout as e:
                raise OpenOCDError(
                    f"Timed out waiting for OpenOCD response after {self._RECV_TIMEOUT}s"
                ) from e
            if not chunk:
                raise OpenOCDError("OpenOCD closed the connection unexpectedly")
            data += chunk
        return data.split(_SEP, 1)[0].decode("utf-8", errors="replace").strip()

    def send_command(self, command: str) -> str:
        """Send an OpenOCD command and return its captured text output."""
        with self._lock:
            if self._sock is None:
                self.connect()
            assert self._sock is not None
            # `capture` collects the command's log output as the Tcl return value.
            payload = "capture {" + command + "}"
            self._sock.sendall(payload.encode() + _SEP)
            return self._read_until_sep()
