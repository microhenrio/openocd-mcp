# OpenOCD MCP Server

An MCP server that lets Claude debug microcontrollers through OpenOCD: flash,
halt/step, read/write memory and registers, set breakpoints, read variables by
name (from your `.elf`) and peripheral registers by name (from a CMSIS-SVD file).

It is **project-agnostic** — one installed server works with any chip/board/project.

OpenOCD itself is **bundled** in `./openocd`, so there is nothing extra to
download — the project is self-contained.

## Install (each machine, once)

**Prerequisites:** [Python 3.10+](https://www.python.org/downloads/) (tick
"Add python.exe to PATH" during install) and [Claude Code](https://claude.com/claude-code).

Then just **double-click `setup.bat`** (or run it from a terminal). It:

1. creates the Python virtual environment (`.venv`)
2. installs the dependencies
3. registers the server with Claude Code at **user scope** (available in every project)

When it finishes, **restart Claude Code** so it loads the server. Done — OpenOCD
is bundled, so there's nothing else to download.

<details>
<summary>Manual install (if you'd rather not use setup.bat)</summary>

```
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
claude mcp add --scope user openocd -- "<PROJECT>\.venv\Scripts\openocd-mcp.exe"
```

`pip install -e .` installs the `openocd_mcp` package (and its dependencies) in
editable mode and creates the `openocd-mcp` console script that Claude launches.
</details>

To confirm it registered: `claude mcp list` should show `openocd: ... ✓ Connected`.

## Distributing to your team

The project is a self-contained folder:

1. Zip it **without** `.venv` and `scratchpad/` (a virtualenv is machine-specific
   and not portable).
2. A teammate unzips it anywhere and runs `setup.bat`.

The OpenOCD binaries, scripts, and bundled SVD travel with the folder. Only the
venv and the Claude registration are per-machine (both handled by `setup.bat`).

> OpenOCD binary resolution order: `OPENOCD_BIN` env var → bundled `./openocd` →
> `openocd` on PATH. The bundle is used by default; a teammate can point at their
> own install via the env var if they prefer.

## Tell it which chip you're debugging (per project)

Chip-specific settings (`target_cfg`, `svd_file`, `elf_file`) are **not** baked in
— you provide them per project. Easiest: drop an **`openocd-mcp.json`** in your
firmware project's root (copy `openocd-mcp.example.json`):

```json
{
  "target_cfg": "target/stm32g0x.cfg",
  "svd_file":   "svd/STM32G0B0.svd",
  "elf_file":   "C:\\path\\to\\your\\build\\firmware.elf"
}
```

The server loads it automatically. (`target_cfg` is relative to OpenOCD's scripts
dir; `svd_file` may be relative to this MCP project; `elf_file` is the path to
your firmware build output.) Alternatively just **tell Claude the chip** and it
will call the `configure` tool for you. `show_config` shows what's in effect.

## Working with it from Claude

Once installed and your chip is configured, talk to Claude in plain language from
your firmware project — it picks the right tools. With the board plugged in:

| You say… | What happens |
|---|---|
| "connect and halt the target" | Auto-starts OpenOCD, attaches, halts the CPU |
| "what's the status?" | Reports running/halted + current PC |
| "read the variable `uart_rx_count`" | Looks it up in your `.elf`, reads it off the chip |
| "set `motor_enabled` to 1" | Writes the variable by name |
| "read GPIOA.MODER" | Reads + decodes the register's named bitfields |
| "list the RCC registers" | Lists registers from the SVD |
| "break at 0x08001234, then reset and run" | Sets a breakpoint, resets |
| "flash `build/firmware.elf` and run it" | Programs and verifies the firmware |
| "dump 64 bytes of RAM at 0x20000000" | Reads memory |

You don't call tools by name — describe what you want and Claude maps it to the
tools below. The raw tool names are just there if you want to be explicit.

> First time in a session, "connect" auto-starts OpenOCD. The target must be
> **halted** to read registers/variables — Claude will halt first when needed.

## Tools (32)

- **Config:** `configure`, `show_config`
- **Process:** `start_openocd`, `stop_openocd`, `status`, `connect`
- **CPU:** `halt`, `resume`, `reset`, `step`
- **Registers:** `read_registers`, `read_register`, `write_register`
- **Memory:** `read_memory`, `write_memory`, `read_peripheral`
- **Variables (by name):** `load_elf`, `read_variable`, `write_variable`, `list_variables`
- **Peripherals (by name):** `load_svd`, `read_peripheral_register`, `write_peripheral_register`, `list_peripheral_registers`
- **Breakpoints:** `add_breakpoint`, `remove_breakpoint`, `list_breakpoints`, `remove_all_breakpoints`
- **Flash:** `flash_write`, `flash_info`, `flash_erase_sector`
- **Escape hatch:** `run_command` (any raw OpenOCD command)

## Files

| File | Role |
|---|---|
| `pyproject.toml` | Package metadata + `openocd-mcp` entry point |
| `openocd_mcp/server.py` | MCP tools (`main()` entry point) |
| `openocd_mcp/openocd.py` | OpenOCD TCL-RPC client (port 6666) |
| `openocd_mcp/launcher.py` | Starts/stops OpenOCD as a subprocess |
| `openocd_mcp/symbols.py` | ELF symbol reader (variables by name) |
| `openocd_mcp/svd.py` | CMSIS-SVD parser (peripheral registers by name) |
| `openocd_mcp/config.py` | OpenOCD resolution + layered project settings |
| `setup.bat` | One-click install (venv + package + Claude registration) |
| `openocd-mcp.example.json` | Template project config |
| `openocd/` | **Bundled** OpenOCD 0.12 (binary, DLLs, scripts) |
| `svd/` | Bundled CMSIS-SVD files (add your chip's `.svd` here) |

## Publishing (official distribution routes)

This is a standard Python package, so the official channels are open if you want
to go beyond the internal zip:

- **Private PyPI / Artifactory / Azure Artifacts** — `pip install build && python -m build`,
  upload the wheel, then teammates `pip install openocd-mcp` from your index and
  register `openocd-mcp` with Claude. (OpenOCD then comes from `OPENOCD_BIN`/PATH,
  since the wheel doesn't vendor the GPL binary.)
- **Public PyPI + the [official MCP Registry](https://registry.modelcontextprotocol.io/)** —
  publish the wheel to PyPI, then add a `server.json` and submit to the registry
  under a reverse-DNS namespace tied to your GitHub/domain.
- **Claude Connectors Directory** — for a one-click *Claude Desktop* install you
  can package as an `.mcpb` bundle and submit for review. (Aimed at Desktop, not
  Claude Code, and needs a privacy policy + review — heavier than needed for an
  internal tool.)

Bump `version` in `pyproject.toml` (and `openocd_mcp/__init__.py`) for each release.

## Adding another chip

- **Target/probe:** any `interface/*.cfg` and `target/*.cfg` shipped with OpenOCD
  are in `openocd/openocd/scripts/`. Set them via `configure` or the project file.
- **Peripheral names:** drop the chip's CMSIS-SVD into `svd/` and point
  `svd_file` at it (e.g. `"svd/STM32F407.svd"`). ST's SVDs ship with
  STM32CubeProgrammer (`...\STM32CubeProgrammer\SVD\`) and STM32CubeIDE.

## Licensing

OpenOCD is GPL v2 (full license texts under `openocd/distro-info/licenses/`).
Redistributing the binaries is permitted under the GPL. Bundled SVD files are
provided by their respective silicon vendors under their own terms.
