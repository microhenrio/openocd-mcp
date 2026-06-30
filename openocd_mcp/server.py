"""
MCP server that exposes OpenOCD debugging capabilities to Claude.

Run with the installed console script `openocd-mcp`, or `python -m openocd_mcp`.

Claude calls these tools to inspect and debug a microcontroller target through
OpenOCD (which the server can start itself).
"""
import atexit
import re
import time

from mcp.server.fastmcp import FastMCP

from . import config
from .launcher import OpenOCDLauncher
from .openocd import OpenOCDClient, OpenOCDError
from .symbols import SymbolTable
from .svd import PeripheralMap

mcp = FastMCP("openocd-debugger")

# Single shared connection — reconnects automatically if not yet connected.
_client = OpenOCDClient()

# Manages an OpenOCD process the server can start/stop itself.
_launcher = OpenOCDLauncher()

# Variable names (from .elf) and peripheral registers (from .svd).
_symbols = SymbolTable()
_periph = PeripheralMap()

# Conditional breakpoints: normalized (even) address -> TCL condition string.
# Evaluation/auto-resume is driven from Python (resume/reset), NOT an OpenOCD
# event handler — OpenOCD ignores `resume` called from inside a 'halted' event.
_cond_bps: dict[int, str] = {}
_COND_TIMEOUT = 10.0  # seconds to keep skipping false conditions before giving up


def _target_halted() -> bool:
    return "halted" in _client.send_command("targets").lower()


def _read_pc() -> int | None:
    m = re.search(r"0x[0-9a-fA-F]+", _client.send_command("reg pc"))
    return int(m.group(0), 16) if m else None


def _continue_conditional() -> str:
    """Drive conditional breakpoints: the target is running; wait for each halt,
    and at a conditional breakpoint evaluate its condition — resume past it when
    false, stop when true. Returns a human-readable outcome."""
    deadline = time.time() + _COND_TIMEOUT
    skips = 0
    while time.time() < deadline:
        while time.time() < deadline and not _target_halted():
            time.sleep(0.03)
        if not _target_halted():
            return f"Target running - no conditional breakpoint hit within {_COND_TIMEOUT:.0f}s ({skips} skip(s))."
        pc = _read_pc()
        cond = _cond_bps.get(pc & ~1) if pc is not None else None
        if cond is None:
            # Stopped somewhere that isn't a conditional breakpoint.
            return _client.send_command("reg pc")
        result = _client.send_command("echo [eval {" + cond + "}]").strip()
        try:
            is_true = int(result) != 0
        except ValueError:
            return f"Conditional breakpoint at 0x{pc:08X}: condition error: {result}"
        if is_true:
            return f"Halted at 0x{pc:08X} - condition true ({skips} skip(s) before it matched)."
        skips += 1
        _client.send_command("resume")
        time.sleep(0.05)  # let the resume take effect before re-checking
    return f"Gave up after {_COND_TIMEOUT:.0f}s and {skips} skip(s); target left running."


def _parse_words(resp: str) -> list[int]:
    """Extract hex values from OpenOCD mdb/mdh/mdw output lines
    like '0x40021000: 03030500 0000408d ...'."""
    vals = []
    for line in resp.splitlines():
        if ":" not in line:
            continue
        for tok in line.split(":", 1)[1].split():
            try:
                vals.append(int(tok, 16))
            except ValueError:
                pass
    return vals


def _ensure_svd() -> str:
    """Lazy-load the project's SVD on first use. Returns '' on success or an error."""
    if _periph.count() == 0:
        svd = config.settings.get("svd_file")
        if not svd:
            return (
                "ERROR: no SVD file configured. Set it with "
                "configure(svd_file=r'...\\STM32xxx.svd'), in openocd-mcp.json, "
                "or call load_svd(path=...)."
            )
        svd = config.resolve_path(svd)
        try:
            _periph.load(svd)
        except Exception as e:  # noqa: BLE001 - surface any parse/IO error to the user
            return f"ERROR: could not load SVD ({svd}): {e}"
    return ""

# Make sure we don't leave an orphaned OpenOCD running when the server exits.
atexit.register(_launcher.stop)


# ---------------------------------------------------------------------------
# Project configuration
# ---------------------------------------------------------------------------

@mcp.tool()
def configure(
    interface_cfg: str = "",
    target_cfg: str = "",
    svd_file: str = "",
    elf_file: str = "",
) -> str:
    """
    Set the chip/project settings for this session. Only non-empty arguments are
    applied; the rest keep their current values.

    interface_cfg : debug-probe OpenOCD config, e.g. 'interface/stlink.cfg'
    target_cfg    : chip OpenOCD config, e.g. 'target/stm32g0x.cfg'
    svd_file      : path to the chip's CMSIS-SVD file (peripheral registers)
    elf_file      : path to the firmware .elf (variables by name)

    Alternatively, put these in an 'openocd-mcp.json' file in your project so
    they load automatically. Loading a new SVD/ELF takes effect on next use.
    """
    config.settings.update(
        interface_cfg=interface_cfg,
        target_cfg=target_cfg,
        svd_file=svd_file,
        elf_file=elf_file,
    )
    # Force reload of SVD/ELF on next use if those paths changed.
    if svd_file:
        _periph.__init__()
    if elf_file:
        _symbols.__init__()
    return "Configured:\n" + show_config()


@mcp.tool()
def show_config() -> str:
    """Show the current effective project settings and where they came from."""
    s = config.settings
    lines = [f"settings source: {s.source}"]
    for k, v in s.as_dict().items():
        lines.append(f"  {k}: {v or '(unset)'}")
    binp, _ = config.openocd_paths()
    lines.append(f"  openocd_bin: {binp or '(not found — call install_openocd)'}")
    lines.append(f"  port: {config.HOST}:{config.PORT}")
    return "\n".join(lines)


@mcp.tool()
def install_openocd() -> str:
    """
    Download and cache OpenOCD for this OS/architecture (if not already present),
    so the server can run without a separate OpenOCD install. Verifies a pinned
    SHA-256. Needed only when OpenOCD isn't bundled or on PATH (e.g. a pip install).
    """
    from . import provision
    msgs: list[str] = []
    try:
        binp, _ = provision.install(log=msgs.append)
    except Exception as e:  # noqa: BLE001
        return "ERROR: " + str(e) + (("\n" + "\n".join(msgs)) if msgs else "")
    return "\n".join(msgs) + f"\nOpenOCD ready: {binp}"


# ---------------------------------------------------------------------------
# OpenOCD process management
# ---------------------------------------------------------------------------

@mcp.tool()
def start_openocd(interface_cfg: str = "", target_cfg: str = "") -> str:
    """
    Start OpenOCD as a subprocess (so you don't have to launch it manually).
    Detects an already-running instance and won't start a duplicate.

    interface_cfg : debug-probe config (default: project setting, else ST-Link)
    target_cfg    : chip config, e.g. 'target/stm32f4x.cfg' (default: project setting)
    Leave both empty to use the configured project settings (see configure /
    openocd-mcp.json). A target_cfg must be set somewhere or this errors.
    """
    global _launcher
    _launcher = OpenOCDLauncher(interface_cfg=interface_cfg, target_cfg=target_cfg)
    atexit.register(_launcher.stop)
    try:
        return _launcher.start()
    except RuntimeError as e:
        return f"ERROR: {e}"


@mcp.tool()
def stop_openocd() -> str:
    """Stop the OpenOCD process this server started."""
    _client.close()
    return _launcher.stop()


@mcp.tool()
def status() -> str:
    """
    Report current debug state: whether OpenOCD is running, whether the target
    is halted or running, and the current program counter (PC) if halted.
    Does not disturb execution.
    """
    if not _launcher.port_open():
        return "OpenOCD: not running (no listener on the command port)."

    lines = [f"OpenOCD: running on {config.HOST}:{config.PORT}"]
    lines.append(
        "  (started by this server)" if _launcher.started_by_us()
        else "  (started externally)"
    )
    try:
        targets = _client.send_command("targets")  # non-intrusive: just lists state
    except OpenOCDError as e:
        return "\n".join(lines) + f"\nTarget: could not query ({e})"

    lines.append("Target:\n" + targets)
    if "halted" in targets.lower():
        lines.append("PC: " + _client.send_command("reg pc"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@mcp.tool()
def connect(host: str = "localhost", port: int = 6666, auto_start: bool = True) -> str:
    """
    Connect to OpenOCD. Call this first.

    If nothing is listening and auto_start is True, the server launches OpenOCD
    itself using the configured project settings (probe + target) and connects.
    Set auto_start=False to require an already-running OpenOCD.
    """
    global _client
    if auto_start and not _launcher.port_open():
        try:
            _launcher.start()
        except RuntimeError as e:
            return f"ERROR: could not start OpenOCD: {e}"
    _client = OpenOCDClient(host, port)
    try:
        _client.connect()
    except OpenOCDError as e:
        return f"ERROR: {e}"
    return f"Connected to OpenOCD at {host}:{port}"


# ---------------------------------------------------------------------------
# CPU control
# ---------------------------------------------------------------------------

@mcp.tool()
def halt() -> str:
    """Halt the target CPU so registers and memory can be inspected."""
    return _client.send_command("halt")


@mcp.tool()
def resume(address: str = "") -> str:
    """Resume CPU execution. Optionally resume from a specific address.
    If conditional breakpoints are set, this skips past those whose condition is
    false and stops at the first one whose condition is true (or on any other halt)."""
    out = _client.send_command(f"resume {address}".strip())
    if _cond_bps:
        return _continue_conditional()
    return out


@mcp.tool()
def reset(mode: str = "halt") -> str:
    """
    Reset the target.
    mode: 'halt'  — reset and immediately halt (good for debugging)
          'run'   — reset and start running
          'init'  — reset and run init scripts
    With 'run' and conditional breakpoints set, this honors those conditions
    (skipping false ones) just like resume.
    """
    out = _client.send_command(f"reset {mode}")
    if mode == "run" and _cond_bps:
        return _continue_conditional()
    return out


@mcp.tool()
def step() -> str:
    """Execute a single instruction and halt again."""
    return _client.send_command("step")


# ---------------------------------------------------------------------------
# Registers
# ---------------------------------------------------------------------------

@mcp.tool()
def read_registers() -> str:
    """Dump all CPU registers (r0-r15, pc, sp, lr, xpsr, etc.)."""
    return _client.send_command("reg")


@mcp.tool()
def read_register(name: str) -> str:
    """
    Read a single CPU register by name.
    Examples: 'r0', 'pc', 'sp', 'lr', 'xpsr', 'msp', 'psp'
    """
    return _client.send_command(f"reg {name}")


@mcp.tool()
def write_register(name: str, value: str) -> str:
    """
    Write a value to a CPU register.
    value: hex string, e.g. '0x20001000'
    """
    return _client.send_command(f"reg {name} {value}")


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@mcp.tool()
def read_memory(address: str, count: int = 1, width: int = 32) -> str:
    """
    Read memory from the target.
    address : hex address, e.g. '0x20000000'
    count   : number of units to read
    width   : unit size in bits — 8, 16, or 32
    """
    cmd_map = {8: "mdb", 16: "mdh", 32: "mdw"}
    if width not in cmd_map:
        return "ERROR: width must be 8, 16, or 32"
    return _client.send_command(f"{cmd_map[width]} {address} {count}")


@mcp.tool()
def write_memory(address: str, value: str, width: int = 32) -> str:
    """
    Write a value to a memory address.
    address : hex address, e.g. '0x20000000'
    value   : hex value,   e.g. '0xDEADBEEF'
    width   : 8, 16, or 32
    """
    cmd_map = {8: "mwb", 16: "mwh", 32: "mww"}
    if width not in cmd_map:
        return "ERROR: width must be 8, 16, or 32"
    return _client.send_command(f"{cmd_map[width]} {address} {value}")


@mcp.tool()
def read_peripheral(base_address: str, count: int = 16) -> str:
    """
    Read memory-mapped peripheral registers.
    base_address : peripheral base, e.g. '0x40020000' for GPIOA on STM32
    count        : number of 32-bit registers to read (default 16)
    """
    return _client.send_command(f"mdw {base_address} {count}")


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------

@mcp.tool()
def add_breakpoint(address: str, hardware: bool = True) -> str:
    """
    Add a breakpoint.
    address  : hex address, e.g. '0x08001234'
    hardware : True for hardware breakpoint (recommended for flash); False for software
    """
    bp_type = "hw" if hardware else ""
    cmd = f"bp {address} 2 {bp_type}".strip()
    return _client.send_command(cmd)


@mcp.tool()
def remove_breakpoint(address: str) -> str:
    """Remove the breakpoint at the given address."""
    return _client.send_command(f"rbp {address}")


@mcp.tool()
def add_watchpoint(
    address: str,
    length: int = 4,
    type: str = "w",
    value: str = "",
    mask: str = "",
) -> str:
    """
    Add a data watchpoint.
    address : hex address to watch
    length  : size in bytes (usually 1, 2, or 4)
    type    : 'r' (read), 'w' (write), or 'a' (access/any)
    value   : optional hex value to match (hardware dependent)
    mask    : optional hex mask for the value match
    """
    cmd = f"wp {address} {length} {type}"
    if value:
        cmd += f" {value}"
        if mask:
            cmd += f" {mask}"
    return _client.send_command(cmd)


@mcp.tool()
def remove_watchpoint(address: str) -> str:
    """Remove the watchpoint at the given address."""
    return _client.send_command(f"rwp {address}")


@mcp.tool()
def list_breakpoints() -> str:
    """List all currently set breakpoints and watchpoints."""
    bp = _client.send_command("bp")
    wp = _client.send_command("wp")
    return f"Breakpoints:\n{bp}\n\nWatchpoints:\n{wp}"


@mcp.tool()
def remove_all_breakpoints() -> str:
    """Remove every breakpoint and watchpoint that is currently set."""
    _cond_bps.clear()
    _client.send_command("rwp all")
    return _client.send_command("rbp all")


# Defines the TCL helper procs that conditions may use (get_reg / get_mem).
# Sent once; idempotent.
_COND_HELPERS = (
    'if { [info procs get_reg] == "" } { '
    'proc get_reg { name } { set r [reg $name]; '
    'if { [regexp {0x([0-9a-fA-F]+)} $r m v] } { return [expr 0x$v] }; return 0 } }; '
    'if { [info procs get_mem] == "" } { '
    'proc get_mem { addr {width 32} } { set c mdw; '
    'if { $width == 16 } { set c mdh }; if { $width == 8 } { set c mdb }; '
    'set r [$c $addr 1]; if { [regexp {:\\s+([0-9a-fA-F]+)} $r m v] } { return [expr 0x$v] }; '
    'return 0 } }'
)


@mcp.tool()
def add_conditional_breakpoint(address: str, condition: str) -> str:
    """
    Add a conditional breakpoint. The target halts at `address` only if
    `condition` evaluates true; otherwise resume/reset-run skips past it.

    address   : hex address for the breakpoint
    condition : a TCL expression/block. Use get_reg <name> and get_mem <addr> ?width?.
                It is evaluated when the breakpoint is hit; non-zero = halt.

    Examples:
      'expr {[get_reg r0] > 100}'
      'expr {[get_mem 0x20000000] == 0xdeadbeef}'
      'incr ::hit_count; expr {$::hit_count >= 5}'
    """
    _client.send_command(_COND_HELPERS)            # ensure helper procs exist
    _cond_bps[int(address, 16) & ~1] = condition   # store condition in the server
    return _client.send_command(f"bp {address} 2 hw")


@mcp.tool()
def remove_conditional_breakpoint(address: str) -> str:
    """Remove the conditional breakpoint at the given address."""
    _cond_bps.pop(int(address, 16) & ~1, None)
    return _client.send_command(f"rbp {address}")


# ---------------------------------------------------------------------------
# Flash
# ---------------------------------------------------------------------------

@mcp.tool()
def flash_write(path: str, verify: bool = True, reset_after: bool = True) -> str:
    """
    Program a firmware file onto the target flash.
    path        : full path to .elf, .bin, or .hex file
    verify      : read back and verify after programming (recommended)
    reset_after : reset and run the new firmware after flashing
    """
    parts = ["program", path]
    if verify:
        parts.append("verify")
    if reset_after:
        parts.append("reset")
    return _client.send_command(" ".join(parts))


@mcp.tool()
def flash_info(bank: int = 0) -> str:
    """Display information about the flash bank (size, sectors, erase state)."""
    return _client.send_command(f"flash info {bank}")


@mcp.tool()
def flash_erase_sector(bank: int, first: int, last: int) -> str:
    """
    Erase a range of flash sectors.
    bank  : flash bank index (usually 0)
    first : first sector number to erase
    last  : last sector number to erase (inclusive)
    """
    return _client.send_command(f"flash erase_sector {bank} {first} {last}")


# ---------------------------------------------------------------------------
# Variables by name (from the firmware .elf)
# ---------------------------------------------------------------------------

@mcp.tool()
def load_elf(path: str = "") -> str:
    """
    Load variable names and addresses from a firmware .elf file, so you can read
    and write variables by name. Build the firmware with debug symbols.
    path: full path to the .elf (defaults to the configured elf_file if set).
    """
    p = config.resolve_path(path or config.settings.get("elf_file"))
    if not p:
        return "ERROR: no ELF path given and no elf_file configured (see configure)."
    try:
        n = _symbols.load(p)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not load ELF '{p}': {e}"
    return f"Loaded {n} variables from {p}"


@mcp.tool()
def read_variable(name: str) -> str:
    """
    Read a global/static variable by name (requires load_elf first).
    Scalars (1/2/4 bytes) are decoded to hex and decimal; larger objects
    (arrays, structs) are dumped as words.
    """
    if _symbols.count() == 0 and config.settings.get("elf_file"):
        _symbols.load(config.resolve_path(config.settings.get("elf_file")))
    info = _symbols.lookup(name)
    if not info:
        sugg = _symbols.find(name)[:5]
        hint = f" Close matches: {', '.join(sugg)}" if sugg else " (call load_elf first?)"
        return f"ERROR: variable '{name}' not found.{hint}"
    addr, size = info
    if size in (1, 2, 4):
        width = {1: 8, 2: 16, 4: 32}[size]
        resp = read_memory(hex(addr), 1, width)
        words = _parse_words(resp)
        if not words:
            return resp
        val = words[0]
        return f"{name} @ 0x{addr:08X} (uint{width}) = 0x{val:0{width // 4}X} ({val})"
    count = (size + 3) // 4
    resp = read_memory(hex(addr), count, 32)
    return f"{name} @ 0x{addr:08X} ({size} bytes):\n{resp}"


@mcp.tool()
def write_variable(name: str, value: str) -> str:
    """
    Write a scalar global/static variable by name (requires load_elf first).
    value: hex (e.g. '0x2A') or decimal. Use write_memory for arrays/structs.
    """
    info = _symbols.lookup(name)
    if not info:
        return f"ERROR: variable '{name}' not found (load_elf first?)."
    addr, size = info
    if size not in (1, 2, 4):
        return f"ERROR: '{name}' is {size} bytes; use write_memory for aggregates."
    return write_memory(hex(addr), value, {1: 8, 2: 16, 4: 32}[size])


@mcp.tool()
def list_variables(filter: str = "") -> str:
    """List known variable names, optionally filtered by a substring."""
    if _symbols.count() == 0:
        elf = config.settings.get("elf_file")
        if elf:
            _symbols.load(config.resolve_path(elf))
        else:
            return "No ELF loaded. Call load_elf with the path to your firmware .elf."
    names = _symbols.find(filter)
    head = f"{len(names)} variable(s)" + (f" matching '{filter}'" if filter else "")
    shown = names[:50]
    tail = "" if len(names) <= 50 else f"\n... (+{len(names) - 50} more)"
    return head + ":\n" + "\n".join(shown) + tail


@mcp.tool()
def watch_variables(names: str, samples: int = 10, interval_ms: int = 200) -> str:
    """
    Live-watch one or more variables WITHOUT halting the CPU: sample them
    repeatedly while the target runs and return a time-series table.

    names       : comma- or space-separated variable names (requires load_elf).
    samples     : number of snapshots, 1-200.
    interval_ms : delay between snapshots in milliseconds.

    Works for RAM globals/statics (read live via background memory access).
    CPU registers need a halt and aren't supported here. Multi-word values are
    read non-atomically, so a >4-byte value may be momentarily inconsistent.
    """
    if _symbols.count() == 0:
        elf = config.settings.get("elf_file")
        if elf:
            _symbols.load(config.resolve_path(elf))
        else:
            return "No ELF loaded. Call load_elf with the path to your firmware .elf."

    requested = [n for n in re.split(r"[,\s]+", names.strip()) if n]
    if not requested:
        return "ERROR: no variable names given."
    resolved = []
    for n in requested:
        info = _symbols.lookup(n)
        if not info:
            sugg = _symbols.find(n)[:5]
            hint = f" Close matches: {', '.join(sugg)}" if sugg else ""
            return f"ERROR: variable '{n}' not found.{hint}"
        resolved.append((n, info[0], info[1]))

    samples = max(1, min(samples, 200))
    width_cmd = {1: "mdb", 2: "mdh", 4: "mdw"}
    rows = []
    t0 = time.time()
    for s in range(samples):
        values = []
        for _, addr, size in resolved:
            cmd = width_cmd.get(size, "mdw")
            words = _parse_words(_client.send_command(f"{cmd} {hex(addr)} 1"))
            values.append(words[0] if words else None)
        rows.append((int((time.time() - t0) * 1000), values))
        if s < samples - 1:
            time.sleep(interval_ms / 1000.0)

    header = "   ms | " + " | ".join(n for n, _, _ in resolved)
    out = [header, "-" * len(header)]
    for ms, values in rows:
        cells = ["n/a" if v is None else f"0x{v:X} ({v})" for v in values]
        out.append(f"{ms:5d} | " + " | ".join(cells))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Peripheral registers by name (from the CMSIS-SVD file)
# ---------------------------------------------------------------------------

@mcp.tool()
def load_svd(path: str = "") -> str:
    """
    Load peripheral register definitions from a CMSIS-SVD file.
    Loaded automatically on first use from the configured svd_file; call this
    only to use a different SVD. path defaults to the configured svd_file.
    """
    p = config.resolve_path(path or config.settings.get("svd_file"))
    if not p:
        return "ERROR: no SVD path given and no svd_file configured (see configure)."
    try:
        n = _periph.load(p)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: could not load SVD '{p}': {e}"
    return f"Loaded {n} registers across {len(_periph.peripherals())} peripherals from {_periph.device}."


@mcp.tool()
def read_peripheral_register(name: str) -> str:
    """
    Read a peripheral register by name (e.g. 'RCC.CR', 'GPIOA.MODER') and decode
    its named bitfields. The target should be halted for a stable read.
    """
    err = _ensure_svd()
    if err:
        return err
    reg = _periph.lookup(name)
    if not reg:
        return f"ERROR: register '{name}' not found. Try list_peripheral_registers('{name.split('.')[0]}')."
    resp = read_memory(hex(reg.address), 1, 32)
    words = _parse_words(resp)
    if not words:
        return resp
    val = words[0]
    lines = [f"{reg.name} @ 0x{reg.address:08X} = 0x{val:08X} ({val})"]
    for fname, bits, fval in _periph.decode(reg, val):
        lines.append(f"  [{bits}] {fname} = 0x{fval:X} ({fval})")
    return "\n".join(lines)


@mcp.tool()
def write_peripheral_register(name: str, value: str) -> str:
    """Write a 32-bit value to a peripheral register by name (e.g. 'GPIOA.ODR')."""
    err = _ensure_svd()
    if err:
        return err
    reg = _periph.lookup(name)
    if not reg:
        return f"ERROR: register '{name}' not found."
    return write_memory(hex(reg.address), value, 32)


@mcp.tool()
def list_peripheral_registers(peripheral: str = "") -> str:
    """
    With no argument: list all peripheral names.
    With a peripheral name (e.g. 'RCC'): list that peripheral's registers.
    """
    err = _ensure_svd()
    if err:
        return err
    if not peripheral:
        return "Peripherals:\n" + ", ".join(_periph.peripherals())
    regs = _periph.registers_of(peripheral)
    if not regs:
        return f"ERROR: peripheral '{peripheral}' not found. Call list_peripheral_registers() for the list."
    return f"{peripheral} registers:\n" + ", ".join(regs)


# ---------------------------------------------------------------------------
# Escape hatch
# ---------------------------------------------------------------------------

@mcp.tool()
def run_command(command: str) -> str:
    """
    Run any raw OpenOCD TCL command and return its output.
    Use this for anything not covered by the other tools.
    """
    return _client.send_command(command)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Console-script entry point (`openocd-mcp`).

    `openocd-mcp install-openocd` downloads OpenOCD and exits; with no args it
    runs the MCP server over stdio.
    """
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "install-openocd":
        from . import provision
        provision.install()
        return
    mcp.run()


if __name__ == "__main__":
    main()
