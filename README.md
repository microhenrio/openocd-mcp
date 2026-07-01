<!-- mcp-name: io.github.microhenrio/openocd-mcp -->
<p align="center">
  <!-- Estado del repositorio -->
  <img src="https://img.shields.io/github/last-commit/microhenrio/openocd-mcp" alt="Last Commit" />
  <img src="https://img.shields.io/github/license/microhenrio/openocd-mcp" alt="License" />

  <!-- Compatibilidad MCP -->
  <img src="https://img.shields.io/badge/MCP-Server-blue" alt="MCP Server" />
  <img src="https://img.shields.io/badge/AI-Ready-purple" alt="AI Ready" />

  <!-- Compatibilidad con uvx -->
  <img src="https://img.shields.io/badge/uvx-compatible-green" alt="uvx compatible" />

  <!-- Badges que funcionarán cuando publiques en PyPI -->
  <img src="https://img.shields.io/pypi/v/openocd-mcp" alt="PyPI Version" />
  <img src="https://img.shields.io/pypi/dm/openocd-mcp" alt="PyPI Downloads" />
  <img src="https://img.shields.io/pypi/pyversions/openocd-mcp" alt="Python Versions" />
</p>


# OpenOCD MCP Server

Debug microcontrollers directly from your AI assistant. This is an [MCP](https://modelcontextprotocol.io)
server that drives [OpenOCD](https://openocd.org/), letting any MCP-compatible AI
flash firmware, control execution, and inspect a running target — and read your
variables and peripheral registers **by name** instead of raw addresses.

## Description

Once connected to a target through a debug probe (ST-Link, J-Link, CMSIS-DAP, …),
your AI assistant can:

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

## Supported AI clients

Any MCP-compatible client works. Tested and known to work:

| Client | Platform |
|---|---|
| [Claude Code](https://claude.com/claude-code) | CLI / IDE |
| [Claude Desktop](https://claude.ai/download) | macOS / Windows |
| [Cursor](https://www.cursor.com/) | IDE |
| [Windsurf](https://windsurf.ai/) | IDE |
| [Cline](https://github.com/clinebot/cline) | VS Code extension |
| [Continue](https://continue.dev/) | VS Code / JetBrains |
| [Zed](https://zed.dev/) | Editor |
| [VS Code + GitHub Copilot](https://code.visualstudio.com/) | IDE (agent mode) |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | CLI |

## Installation

**Prerequisites:** Python 3.10+ and a debug probe connected to your target.

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

> **Windows shortcut:** run `setup.bat` — it creates the environment, installs
> the package, and registers it with Claude Code automatically.

**OpenOCD** is obtained automatically: a build for your OS/architecture is
downloaded and cached on first connect (checksum-verified). You can also fetch it
ahead of time with `openocd-mcp install-openocd`, or use an existing install by
setting the `OPENOCD_BIN` environment variable.

### Registering with your AI client

The server executable is:

```
# Windows
<repo>\.venv\Scripts\openocd-mcp.exe

# macOS / Linux
<repo>/.venv/bin/openocd-mcp
```

<details>
<summary><strong>Claude Code</strong></summary>

```bash
# Windows
claude mcp add --scope user openocd -- "%CD%\.venv\Scripts\openocd-mcp.exe"

# macOS / Linux
claude mcp add --scope user openocd -- "$PWD/.venv/bin/openocd-mcp"
```

Restart Claude Code, then verify with `claude mcp list`.
</details>

<details>
<summary><strong>Claude Desktop</strong></summary>

Add to `claude_desktop_config.json` (Edit → Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "openocd": {
      "command": "/path/to/.venv/bin/openocd-mcp"
    }
  }
}
```

Restart Claude Desktop.
</details>

<details>
<summary><strong>Cursor</strong></summary>

Add to `~/.cursor/mcp.json` (or `.cursor/mcp.json` in your project):

```json
{
  "mcpServers": {
    "openocd": {
      "command": "/path/to/.venv/bin/openocd-mcp"
    }
  }
}
```

Restart Cursor.
</details>

<details>
<summary><strong>Windsurf</strong></summary>

Add to `~/.codeium/windsurf/mcp_config.json`:

```json
{
  "mcpServers": {
    "openocd": {
      "command": "/path/to/.venv/bin/openocd-mcp"
    }
  }
}
```

Restart Windsurf.
</details>

<details>
<summary><strong>Cline (VS Code)</strong></summary>

Open the Cline panel → MCP Servers → Add Server → Manual, then enter:

```json
{
  "openocd": {
    "command": "/path/to/.venv/bin/openocd-mcp"
  }
}
```

</details>

<details>
<summary><strong>Continue (VS Code / JetBrains)</strong></summary>

Add to `~/.continue/config.json`:

```json
{
  "mcpServers": [
    {
      "name": "openocd",
      "command": "/path/to/.venv/bin/openocd-mcp"
    }
  ]
}
```

</details>

<details>
<summary><strong>VS Code + GitHub Copilot</strong></summary>

Add to `.vscode/mcp.json` in your workspace (or user `settings.json`):

```json
{
  "servers": {
    "openocd": {
      "type": "stdio",
      "command": "/path/to/.venv/bin/openocd-mcp"
    }
  }
}
```

Enable via **Chat → Agent mode** in VS Code.
</details>

### Updating

There's no self-update tool exposed over MCP — updating means running commands in
a terminal, either yourself or by asking an AI that has shell access (e.g. Claude
Code). **After updating, restart your AI client** so it loads the new server;
if it was mid-session, it may need to stop the old `openocd-mcp` process first
(a file lock can block the reinstall while it's running).

**PyPI install** (`pip install openocd-mcp` or `uvx`):

```bash
pip install -U openocd-mcp

# uvx caches by default — force a refresh:
uvx --refresh openocd-mcp
```

**Editable git-clone install** (what `setup.bat` / this repo's instructions set up):

```bash
git pull
# only needed if dependencies changed:
.venv/bin/python -m pip install -e .   # or .venv\Scripts\python on Windows
```

Check the installed version with `pip show openocd-mcp`.

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
- `transport` — `"swd"` or `"jtag"`. Set `"swd"` for a **J-Link** on Cortex-M
  (with `interface_cfg: "interface/jlink.cfg"`); leave empty for ST-Link.
- `svd_file` — CMSIS-SVD file for the chip (enables peripheral registers by name).
- `elf_file` — your firmware build output (enables variables by name).

> **J-Link on Windows:** OpenOCD reaches J-Links via libusb, so bind the J-Link's
> debug interface to **libusbK** (or WinUSB) with [Zadig](https://zadig.akeo.ie/)
> once (Interface 2 / MI_02). Newer SEGGER software (v7.x+) is compatible with
> libusbK, so both OpenOCD and SEGGER tools can coexist. ST-Link works without
> that step.

Or simply tell the AI the chip you're using and it will configure the session for
you. `show_config` reports the active settings at any time.

### 2. Describe what you want

With the board plugged in, describe what you want — the AI picks the right tools:

| You say… | What happens |
|---|---|
| "connect and halt the target" | Starts OpenOCD if needed, attaches, halts the CPU |
| "what's the status?" | Reports running/halted and the current program counter |
| "read the variable `sensor_value`" | Looks it up in the `.elf` and reads it off the chip |
| "set `motor_enabled` to 1" | Writes the variable by name |
| "watch `tick_count` live for 2 seconds" | Calls `watch_variables` — samples it repeatedly *without halting* and returns a table of values over time, right in the chat |
| "read `GPIOA.MODER`" | Reads the register and decodes its named bitfields |
| "list the `RCC` registers" | Lists registers from the SVD |
| "break at `0x08001234`, then reset and run" | Sets a breakpoint and resets |
| "break at `0x08001234` when `r0` is 42" | Sets a conditional breakpoint (using `[get_reg r0] == 42`) |
| "break at `0x08001234` after 5 hits" | Sets a hit-count breakpoint |
| "watch for writes to `0x20000000`" | Sets a hardware data watchpoint |
| "flash `build/firmware.elf` and run it" | Programs, verifies, and restarts |
| "dump 64 bytes of RAM at `0x20000000`" | Reads memory |

You don't call tools by name — describe the goal and the AI maps it to the
underlying tools.

### 3. Conditional breakpoints & watchpoints

**Conditional breakpoints** halt only when a condition holds — useful for catching
one specific case in code that runs constantly. Conditions are TCL expressions and
can use two helpers:

- `get_reg <name>` — a CPU register value (e.g. `get_reg r0`, `get_reg pc`)
- `get_mem <addr> [width]` — a memory value (e.g. `get_mem 0x20000000`)

Just describe the intent; the AI builds the condition:

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

### Live-watching variables

There are **two separate ways** to watch a variable without halting the CPU —
one the AI can trigger, one you run yourself:

| | `watch_variables` (MCP tool) | `openocd-watch` (standalone GUI) |
|---|---|---|
| Triggered by | Asking the AI in chat | Running the command yourself in a terminal |
| Output | A text table of samples returned to the chat | A live-updating window with a tree view |
| Duration | One-shot: N samples, then it returns | Keeps running until you close it |
| Structs/arrays | Flat values only | Expandable, DWARF-typed |

**Ask the AI** for a quick, bounded look at a value over time — "watch `tick_count`
for 10 samples", "sample `sensor_value` every 200ms for 2 seconds". This calls the
`watch_variables` tool directly; no extra setup needed beyond an ELF loaded.

**Run the GUI yourself** for an open-ended live view, especially of structs/arrays.
It's a standalone app that opens its own connection to OpenOCD, so it works fine
alongside any AI session driving the same target — but it is *not* an MCP tool, so
the AI cannot open it for you; run it directly:

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
for you (and stops it on close) — fully standalone, no AI client or `.bat` needed:

```bash
openocd-watch uwTick xTickCount --elf path/to/firmware.elf --autostart --target target/stm32g0x.cfg
```

> **CPU core registers** (`r0`, `pc`, `sp`, …) require the target to be halted —
> the AI halts first when needed. Memory, variables, and peripheral registers are
> memory-mapped and can be read while the CPU is running (as the live-watch window
> does). The first `connect` of a session starts OpenOCD automatically.

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
2. The **`set_permissions`** tool at runtime — e.g. ask the AI to "make the target
   read-only" or "allow flash erase for this session".
3. The **`OPENOCD_MCP_READONLY=1`** environment variable (forces read-only).

A blocked call returns a clear `BLOCKED: …` message explaining which permission to
enable. `show_config` lists the active permissions.
