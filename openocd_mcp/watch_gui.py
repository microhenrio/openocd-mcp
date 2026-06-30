"""
Live-watch window: a small Tkinter GUI that shows selected variables updating in
real time, read from a running target WITHOUT halting it.

It is a standalone app (not an MCP tool — Claude can't draw a window). It opens
its own connection to OpenOCD's TCL-RPC port, so it runs happily alongside the
MCP server / a Claude session that's driving the same OpenOCD.

Usage:
    openocd-watch tick_count uart_rx_count --elf path/to/firmware.elf
    openocd-watch --vars "a,b,c" --interval 100
    openocd-watch foo --samples 5            # headless: print 5 samples and exit

Connects to an OpenOCD already running on localhost:6666 (start it via the MCP
server, `openocd-mcp install-openocd` + your usual flow, or start_openocd.bat).
"""
import argparse
import atexit
import re

from . import config
from .launcher import OpenOCDLauncher
from .openocd import OpenOCDClient, OpenOCDError
from .symbols import SymbolTable

_WIDTH_CMD = {1: "mdb", 2: "mdh", 4: "mdw"}


class VariableSampler:
    """Resolves variable names to addresses and reads them live (no halt)."""

    def __init__(self, host: str, port: int):
        self.client = OpenOCDClient(host, port)
        self.symbols = SymbolTable()

    def connect(self) -> None:
        self.client.connect()

    def load_elf(self, path: str) -> int:
        return self.symbols.load(path)

    def read(self, name: str) -> tuple[int | None, int | None, int | None]:
        """Return (address, size, value); value/None. address None if unknown."""
        info = self.symbols.lookup(name)
        if not info:
            return None, None, None
        addr, size = info
        cmd = _WIDTH_CMD.get(size, "mdw")
        resp = self.client.send_command(f"{cmd} {hex(addr)} 1")
        for line in resp.splitlines():
            if ":" in line:
                toks = line.split(":", 1)[1].split()
                if toks:
                    try:
                        return addr, size, int(toks[0], 16)
                    except ValueError:
                        pass
        return addr, size, None


def _fmt(size, value):
    if value is None:
        return "-"
    digits = (size or 4) * 2
    return f"0x{value:0{digits}X} ({value})"


def _run_headless(sampler, names, interval_ms, samples):
    import time
    print("ms    | " + " | ".join(names))
    t0 = time.time()
    for i in range(samples):
        cells = []
        for n in names:
            _, size, v = sampler.read(n)
            cells.append(_fmt(size, v))
        print(f"{int((time.time()-t0)*1000):5d} | " + " | ".join(cells))
        if i < samples - 1:
            time.sleep(interval_ms / 1000.0)


def _run_gui(sampler, names, interval_ms, host, port):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"OpenOCD Live Watch — {host}:{port}")
    root.geometry("560x360")

    cols = ("variable", "address", "value")
    tree = ttk.Treeview(root, columns=cols, show="headings")
    for c, w in (("variable", 200), ("address", 110), ("value", 230)):
        tree.heading(c, text=c.capitalize())
        tree.column(c, width=w, anchor="w")
    tree.tag_configure("changed", background="#fff4c2")
    tree.pack(fill="both", expand=True, padx=8, pady=(8, 4))

    # Add-variable bar
    bar = tk.Frame(root)
    bar.pack(fill="x", padx=8, pady=(0, 4))
    entry = tk.Entry(bar)
    entry.pack(side="left", fill="x", expand=True)
    watched = list(names)

    def add_var(_=None):
        name = entry.get().strip()
        if name and name not in watched:
            watched.append(name)
            entry.delete(0, "end")

    tk.Button(bar, text="Add", command=add_var).pack(side="left", padx=4)
    entry.bind("<Return>", add_var)

    status = tk.Label(root, anchor="w", relief="sunken")
    status.pack(fill="x", side="bottom")

    items: dict[str, str] = {}
    prev: dict[str, int | None] = {}
    counter = {"n": 0}

    def refresh():
        for name in watched:
            try:
                addr, size, value = sampler.read(name)
            except OpenOCDError as e:
                status.config(text=f"connection lost: {e}")
                return
            addr_s = "not found" if addr is None else f"0x{addr:08X}"
            val_s = "—" if addr is None else _fmt(size, value)
            changed = name in prev and prev[name] != value
            prev[name] = value
            tags = ("changed",) if changed else ()
            if name in items:
                tree.item(items[name], values=(name, addr_s, val_s), tags=tags)
            else:
                items[name] = tree.insert("", "end", values=(name, addr_s, val_s), tags=tags)
        counter["n"] += 1
        status.config(text=f"sample #{counter['n']}  ·  {len(watched)} variable(s)  ·  every {interval_ms} ms")
        root.after(interval_ms, refresh)

    def on_close():
        try:
            sampler.client.close()
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    refresh()
    root.mainloop()


def main() -> None:
    p = argparse.ArgumentParser(description="Live-watch target variables in a window.")
    p.add_argument("names", nargs="*", help="variable names to watch")
    p.add_argument("--vars", default="", help="comma/space-separated names (added to positional)")
    p.add_argument("--elf", default="", help="firmware .elf (default: configured elf_file)")
    p.add_argument("--interval", type=int, default=200, help="poll interval in ms")
    p.add_argument("--host", default=config.HOST)
    p.add_argument("--port", type=int, default=config.PORT)
    p.add_argument("--samples", type=int, default=0, help=">0: headless, print N samples and exit")
    p.add_argument("--autostart", action="store_true",
                   help="start OpenOCD if it isn't already running (uses the configured target)")
    p.add_argument("--target", default="", help="target cfg for --autostart, e.g. target/stm32g0x.cfg")
    p.add_argument("--interface", default="", help="interface cfg for --autostart, e.g. interface/stlink.cfg")
    args = p.parse_args()

    names = list(args.names) + [n for n in re.split(r"[,\s]+", args.vars.strip()) if n]
    if not names:
        p.error("give at least one variable name (positional or --vars)")

    elf = args.elf or config.resolve_path(config.settings.get("elf_file"))
    if not elf:
        p.error("no ELF given; pass --elf or set elf_file in openocd-mcp.json")

    sampler = VariableSampler(args.host, args.port)
    try:
        sampler.connect()
    except OpenOCDError as e:
        if not args.autostart:
            raise SystemExit(
                f"Cannot reach OpenOCD at {args.host}:{args.port}: {e}\n"
                f"Start OpenOCD first (via the MCP server or start_openocd.bat), "
                f"or pass --autostart to have this launch it."
            )
        launcher = OpenOCDLauncher(interface_cfg=args.interface, target_cfg=args.target)
        try:
            print(launcher.start().splitlines()[0])
        except RuntimeError as le:
            raise SystemExit(f"--autostart failed: {le}")
        atexit.register(launcher.stop)  # stop it when the window closes (if we started it)
        sampler.connect()

    n = sampler.load_elf(elf)
    print(f"Loaded {n} symbols from {elf}")

    if args.samples > 0:
        _run_headless(sampler, names, args.interval, args.samples)
        sampler.client.close()
    else:
        _run_gui(sampler, names, args.interval, args.host, args.port)


if __name__ == "__main__":
    main()
