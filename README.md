<!-- mcp-name: io.github.microhenrio/openocd-mcp -->

# OpenOCD MCP Server

Debug microcontrollers directly from Claude. This is an [MCP](https://modelcontextprotocol.io)
server that drives [OpenOCD](https://openocd.org/), letting Claude flash firmware,
control execution, and inspect a running target — and read your variables and
peripheral registers **by name** instead of raw addresses.

## Description

Once connected to a target through a debug probe (ST-Link, J-Link, CMSIS-DAP, …),
Claude can:

- **Flash firmware** — program and verify `.elf` / `.bin` / `.hex` images
- **Control execution** — halt, resume, single-step, reset
- **Inspect state** — read/write CPU registers and memory
- **Set breakpoints** — hardware & software, including **conditional** breakpoints
  (halt only when an expression is true) and hit-count breakpoints
- **Watch memory** — hardware **watchpoints** that halt on read/write/access to an address
- **Read variables by name** — from your firmware's `.elf` symbols (e.g. `read_variable uart_rx_count`)
- **Live-watch variables** — a window that samples variables over time *without halting* the CPU, with **expandable structs/arrays** auto-typed from DWARF (signed/float/pointer/enum)
- **Read peripheral registers by name** — from a CMSIS-SVD file, decoded into named bitfields (e.g. `RCC.CR`, `GPIOA.MODER`)
- **Safety gates** — permission layer (read-only mode, gated flash-erase, flash path/size limits) so the agent can't damage a target unexpectedly

The server is **chip-agnostic** — it works with any target OpenOCD supports; you
point it at your chip's config and (optionally) SVD/ELF. It can also **start
OpenOCD for you** and **download OpenOCD automatically** for your platform, so
there's nothing else to install by hand.

## Installation

**Prerequisites:** Python 3.10+, [Claude Code](https://claude.com/claude-code),
and a debug probe connected to your target.

Clone and install the package into a virtual environment:

```bash
git clone https://github.com/microhenrio/openocd-mcp
cd openocd-mcp
python -m venv .venv
```

Install it (creates the `openocd-mcp` command):

```bash
# Windows
.venv\Scripts\python -m pip install -e .

# macOS / Linux
.venv/bin/python -m pip install -e .
```

Register the server with Claude Code (user scope makes it available in every
project):

```bash
# Windows
claude mcp add --scope user openocd -- "%CD%\.venv\Scripts\openocd-mcp.exe"

# macOS / Linux
claude mcp add --scope user openocd -- "$PWD/.venv/bin/openocd-mcp"
```

> **Windows shortcut:** instead of the steps above you can just run `setup.bat`,
> which creates the environment, installs the package, and registers it.

Then **restart Claude Code** so it loads the server. Verify with:

```bash
claude mcp list      # openocd: ... ✓ Connected
```

**OpenOCD** is obtained automatically: a build for your OS/architecture is
downloaded and cached on first connect (checksum-verified). You can also fetch it
ahead of time with `openocd-mcp install-openocd`, or use an existing install by
setting the `OPENOCD_BIN` environment variable.

## How to work with it

### 1. Point it at your chip

Each firmware project tells the server which target it's debugging. Create an
`openocd-mcp.json` in your project root (a template is in
[`openocd-mcp.example.json`](openocd-mcp.example.json)):

```json
{
  "target_cfg": "target/stm32g0x.cfg",
  "svd_file": "path/to/STM32G0B0.svd",
  "elf_file": "path/to/build/firmware.elf"
}
```

- `target_cfg` / `interface_cfg` — OpenOCD configs (relative to its scripts dir).
  Defaults to an ST-Link probe; set `target_cfg` for your chip.
- `svd_file` — CMSIS-SVD file for the chip (enables peripheral registers by name).
- `elf_file` — your firmware build output (enables variables by name).

Or simply tell Claude the chip you're using and it will configure the session for
you. `show_config` reports the active settings at any time.

### 2. Talk to Claude

With the board plugged in, describe what you want — Claude picks the right tools:

| You say… | What happens |
|---|---|
| "connect and halt the target" | Starts OpenOCD if needed, attaches, halts the CPU |
| "what's the status?" | Reports running/halted and the current program counter |
| "read the variable `sensor_value`" | Looks it up in the `.elf` and reads it off the chip |
| "set `motor_enabled` to 1" | Writes the variable by name |
| "watch `tick_count` live for 2 seconds" | Samples it repeatedly *without halting* and shows the values over time |
| "read `GPIOA.MODER`" | Reads the register and decodes its named bitfields |
| "list the `RCC` registers" | Lists registers from the SVD |
| "break at `0x08001234`, then reset and run" | Sets a breakpoint and resets |
| "break at `0x08001234` when `r0` is 42" | Sets a conditional breakpoint (using `[get_reg r0] == 42`) |
| "break at `0x08001234` after 5 hits" | Sets a hit-count breakpoint |
| "watch for writes to `0x20000000`" | Sets a hardware data watchpoint |
| "flash `build/firmware.elf` and run it" | Programs, verifies, and restarts |
| "dump 64 bytes of RAM at `0x20000000`" | Reads memory |

You don't call tools by name — describe the goal and Claude maps it to the
underlying tools.

### 3. Conditional breakpoints & watchpoints

**Conditional breakpoints** halt only when a condition holds — useful for catching
one specific case in code that runs constantly. Conditions are TCL expressions and
can use two helpers:

- `get_reg <name>` — a CPU register value (e.g. `get_reg r0`, `get_reg pc`)
- `get_mem <addr> [width]` — a memory value (e.g. `get_mem 0x20000000`)

Just describe the intent to Claude; it builds the condition:

| You say… | Condition used |
|---|---|
| "break at `0x08001234` only when `r0` > 100" | `expr {[get_reg r0] > 100}` |
| "break at `parse_packet` when the byte at `0x20000005` is `0xFF`" | `expr {[get_mem 0x20000005 8] == 0xFF}` |
| "stop at `0x08001234` on the 10th time it's hit" | `incr ::hits; expr {$::hits >= 10}` |

When the condition is false the server resumes automatically and keeps going until
it's true (or you stop it).

**Watchpoints** halt the CPU when it accesses a memory location — ideal for finding
what corrupts a variable:

- "watch for writes to `0x20000000`"
- "watch address `0x20000010` for any read or write"

### A live-watch window

For a continuously-updating view of variable values, run the bundled GUI. It's a
standalone app (separate from Claude) that opens its own connection to OpenOCD,
so it works fine while a Claude session is driving the same target:

```bash
openocd-watch tick_count sensor_value --elf path/to/firmware.elf
# or, if elf_file is set in openocd-mcp.json:
openocd-watch tick_count sensor_value
```

It samples the variables **without halting** the CPU and refreshes a tree in a
window. Add entries with the box (press Enter or **Add**) and remove a selected
row with **Remove** (or the Delete key). Each entry is resolved automatically and
can be:

- a **variable name** (`uwTick`, `commsService`) → looked up in the ELF; if it's a
  **struct, union, or array** it gets an expand triangle, and its members/elements
  are shown **auto-typed from DWARF** (signed, float, pointer, enum, nested structs);
- a **hex address** with optional size (`0x20000000`, `0x20000000:2`) → read directly.

A **Format** dropdown switches how values are shown — **Auto (by C type) / Hex /
Decimal / Signed / Float (f32) / Binary** — and re-renders instantly. Use
`--interval` to change the poll rate, `--format` to set the initial format, or
`--samples N` for a headless printout. Requires Tkinter (ships with standard Python)
and a debug build (`-g`) for the type info.

If OpenOCD isn't already running, add `--autostart` and the window launches it
for you (and stops it on close) — fully standalone, no Claude or `.bat` needed:

```bash
openocd-watch uwTick xTickCount --elf path/to/firmware.elf --autostart --target target/stm32g0x.cfg
```

> The target must be **halted** to read registers, memory, or variables — Claude
> halts first when needed. The first `connect` of a session starts OpenOCD
> automatically.

## Safety / permissions

Mutating operations are gated so the agent can't damage a target unexpectedly.
Reads are always allowed; the gates apply to writes, flashing, erasing, and the
raw-command escape hatch.

| Permission | Default | Gates |
|---|---|---|
| `read_only` | `false` | master switch — blocks **all** writes/flash/erase/raw |
| `allow_memory_write` | `true` | `write_memory`, `write_variable`, `write_register`, `write_peripheral_register` |
| `allow_flash` | `true` | `flash_write` (program) |
| `allow_flash_erase` | **`false`** | `flash_erase_sector` (destructive — opt in) |
| `allow_raw_command` | `true` | `run_command` (can bypass other limits) |
| `flash_allowed_paths` | `[]` (any) | restrict `flash_write` to files under these dirs |
| `flash_max_bytes` | `0` (no limit) | reject flashing files larger than this |

Set them three ways (later wins):

1. A **`permissions`** object in `openocd-mcp.json` (see `openocd-mcp.example.json`).
2. The **`set_permissions`** tool at runtime — e.g. ask Claude to "make the target
   read-only" or "allow flash erase for this session".
3. The **`OPENOCD_MCP_READONLY=1`** environment variable (forces read-only).

A blocked call returns a clear `BLOCKED: …` message explaining which permission to
enable. `show_config` lists the active permissions.
