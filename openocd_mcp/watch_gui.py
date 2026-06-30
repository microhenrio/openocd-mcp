"""
Live-watch window: a small Tkinter GUI that shows selected variables updating in
real time, read from a running target WITHOUT halting it.

Structured variables (structs/unions/arrays) appear as expandable rows whose
members are auto-typed from the ELF's DWARF info. Entries can also be raw hex
addresses. It is a standalone app (not an MCP tool) and opens its own OpenOCD
connection, so it runs alongside an MCP/Claude session on the same target.

Usage:
    openocd-watch tick_count my_struct --elf path/to/firmware.elf
    openocd-watch foo --autostart --target target/stm32g0x.cfg
    openocd-watch foo --samples 5            # headless: print 5 samples and exit
"""
import argparse
import atexit
import re
import struct

from . import config
from .dwarf_types import TypeNode, TypeResolver, decode_leaf
from .launcher import OpenOCDLauncher
from .openocd import OpenOCDClient, OpenOCDError

_WIDTH_CMD = {1: "mdb", 2: "mdh", 4: "mdw"}
_READ_CAP = 4096  # max bytes read per aggregate per refresh

# A hex address, optionally with a byte-size suffix: 0x20000000 or 0x20000000:2
_HEX_RE = re.compile(r"^(0x[0-9a-fA-F]+)(?::([124]))?$")

# Display formats: key -> label (order drives the dropdown). "auto" = by C type.
FORMATS = {
    "auto": "Auto (type)",
    "hex": "Hex",
    "dec": "Decimal",
    "int": "Signed",
    "float": "Float (f32)",
    "bin": "Binary",
}


def _as_address(token):
    m = _HEX_RE.match(token.strip())
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2)) if m.group(2) else 4


def format_value(value, size, fmt="hex") -> str:
    """Render a raw integer (or float) value in an explicit format."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    size = size or 4
    bits = size * 8
    if fmt == "dec":
        return str(value)
    if fmt == "int":
        return str(value - (1 << bits) if value >= (1 << (bits - 1)) else value)
    if fmt == "float":
        return f"{struct.unpack('<f', value.to_bytes(4, 'little'))[0]:.6g}" if size == 4 else "(needs 4 bytes)"
    if fmt == "bin":
        return f"0b{value:0{bits}b}"
    return f"0x{value:0{size * 2}X}"


def present(node, value, fmt):
    """Format a leaf value, honoring its C type when fmt == 'auto'."""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.6g}"
    size = node.size or 4
    if fmt != "auto":
        return format_value(value, size, fmt)
    if node.kind == "pointer" or node.encoding == "address":
        return f"0x{value:0{size * 2}X}"
    if node.encoding == "signed":
        bits = size * 8
        return str(value - (1 << bits) if value >= (1 << (bits - 1)) else value)
    if node.encoding == "bool":
        return "true" if value else "false"
    return f"0x{value:0{size * 2}X}"


class VariableSampler:
    """Resolves variable names / hex addresses to addresses + DWARF types and
    reads them live (no halt)."""

    def __init__(self, host, port):
        self.client = OpenOCDClient(host, port)
        self.resolver = None
        self._symtab = {}  # name -> (addr, size) from .symtab

    def connect(self):
        self.client.connect()

    def load_elf(self, path):
        from .symbols import SymbolTable
        st = SymbolTable()
        n = st.load(path)
        self._symtab = dict(st._syms)  # name -> (addr, size)
        try:
            self.resolver = TypeResolver(path)
        except Exception:
            self.resolver = None
        return n

    def resolve(self, entry):
        """Return (address, TypeNode) for an entry, or (None, None) if unknown."""
        a = _as_address(entry)
        if a:
            addr, size = a
            return addr, TypeNode(entry, 0, size, "base", "unsigned", f"{size * 8}-bit @addr")
        info = self._symtab.get(entry)
        if not info:
            return None, None
        addr, size = info
        node = self.resolver.resolve(entry) if self.resolver else None
        if node is None:  # no DWARF type — treat as a scalar of its symbol size
            sz = size if size in (1, 2, 4) else 4
            node = TypeNode(entry, 0, sz, "base", "unsigned", f"u{sz * 8}")
        return addr, node

    def read_block(self, addr, size):
        size = max(1, min(size or 4, _READ_CAP))
        out = bytearray()
        for line in self.client.send_command(f"mdb {hex(addr)} {size}").splitlines():
            if ":" in line:
                for tok in line.split(":", 1)[1].split():
                    try:
                        out.append(int(tok, 16))
                    except ValueError:
                        pass
        return bytes(out)

    def read(self, entry):
        """Single scalar read (for headless mode). Returns (addr, size, value)."""
        addr, node = self.resolve(entry)
        if addr is None:
            return None, None, None
        block = self.read_block(addr, node.size)
        return addr, node.size, decode_leaf(node, block)


def _run_headless(sampler, names, interval_ms, samples, fmt="auto"):
    import time
    print("ms    | " + " | ".join(names))
    t0 = time.time()
    for i in range(samples):
        cells = []
        for n in names:
            addr, node = sampler.resolve(n)
            if addr is None:
                cells.append("not found")
                continue
            v = decode_leaf(node, sampler.read_block(addr, node.size))
            cells.append(present(node, v, fmt))
        print(f"{int((time.time()-t0)*1000):5d} | " + " | ".join(cells))
        if i < samples - 1:
            time.sleep(interval_ms / 1000.0)


def _run_gui(sampler, names, interval_ms, host, port, fmt="auto"):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"OpenOCD Live Watch — {host}:{port}")
    root.geometry("640x420")

    top = tk.Frame(root)
    top.pack(fill="x", padx=8, pady=(8, 4))
    tk.Label(top, text="Format:").pack(side="left")
    keys = list(FORMATS)
    fmt_combo = ttk.Combobox(top, values=list(FORMATS.values()), state="readonly", width=14)
    fmt_combo.current(keys.index(fmt) if fmt in keys else 0)
    fmt_combo.pack(side="left", padx=6)
    current = {"fmt": fmt}

    tree = ttk.Treeview(root, columns=("address", "value"), show="tree headings")
    tree.heading("#0", text="Variable")
    tree.heading("address", text="Address")
    tree.heading("value", text="Value")
    tree.column("#0", width=300, anchor="w")
    tree.column("address", width=110, anchor="w")
    tree.column("value", width=200, anchor="w")
    tree.tag_configure("changed", background="#fff4c2")
    tree.pack(fill="both", expand=True, padx=8, pady=4)

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

    def remove_selected(_=None):
        for sel in tree.selection():
            top = sel
            while tree.parent(top):          # a struct member -> remove its whole variable
                top = tree.parent(top)
            if top in watched:
                watched.remove(top)
            if tree.exists(top):
                tree.delete(top)
            for d in (prev, last):           # purge cached state for the subtree
                for k in [k for k in d if k == top or k.startswith(top + "/")]:
                    d.pop(k, None)

    tk.Button(bar, text="Add", command=add_var).pack(side="left", padx=4)
    tk.Button(bar, text="Remove", command=remove_selected).pack(side="left")
    entry.bind("<Return>", add_var)
    tree.bind("<Delete>", remove_selected)

    status = tk.Label(root, anchor="w", relief="sunken")
    status.pack(fill="x", side="bottom")

    prev = {}      # iid -> last value
    last = {}      # iid -> (node, base_addr) for re-rendering on format change
    counter = {"n": 0}

    def sync(parent_iid, node, base_addr, block):
        iid = (parent_iid + "/" + node.name) if parent_iid else node.name
        if not tree.exists(iid):
            tree.insert(parent_iid, "end", iid=iid, text=node.name, open=False)
        addr_s = f"0x{base_addr + node.offset:08X}"
        if node.children:
            tree.item(iid, values=(addr_s, node.type_name))
            for c in node.children:
                sync(iid, c, base_addr, block)
        else:
            value = decode_leaf(node, block)
            last[iid] = (node, base_addr)
            changed = iid in prev and prev[iid] != value
            prev[iid] = value
            tree.item(iid, values=(addr_s, present(node, value, current["fmt"])),
                      tags=("changed",) if changed else ())

    def refresh():
        for ent in watched:
            try:
                addr, node = sampler.resolve(ent)
            except OpenOCDError as e:
                status.config(text=f"connection lost: {e}")
                return
            if node is None:
                if not tree.exists(ent):
                    tree.insert("", "end", iid=ent, text=ent, values=("not found", ""))
                continue
            block = sampler.read_block(addr, node.size)
            sync("", node, addr, block)
        counter["n"] += 1
        status.config(text=f"sample #{counter['n']}  ·  {len(watched)} entr{'y' if len(watched)==1 else 'ies'}  ·  every {interval_ms} ms")
        root.after(interval_ms, refresh)

    def on_format(_=None):
        current["fmt"] = keys[fmt_combo.current()]
        for iid, (node, base) in last.items():       # re-render leaves immediately
            if tree.exists(iid):
                tree.set(iid, "value", present(node, prev.get(iid), current["fmt"]))

    fmt_combo.bind("<<ComboboxSelected>>", on_format)

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
    p.add_argument("names", nargs="*", help="variable names, struct names, or hex addresses (0x...[:size])")
    p.add_argument("--vars", default="", help="comma/space-separated entries (added to positional)")
    p.add_argument("--elf", default="", help="firmware .elf (default: configured elf_file)")
    p.add_argument("--interval", type=int, default=200, help="poll interval in ms")
    p.add_argument("--host", default=config.HOST)
    p.add_argument("--port", type=int, default=config.PORT)
    p.add_argument("--samples", type=int, default=0, help=">0: headless, print N samples and exit")
    p.add_argument("--autostart", action="store_true",
                   help="start OpenOCD if it isn't already running (uses the configured target)")
    p.add_argument("--target", default="", help="target cfg for --autostart, e.g. target/stm32g0x.cfg")
    p.add_argument("--interface", default="", help="interface cfg for --autostart, e.g. interface/stlink.cfg")
    p.add_argument("--format", choices=list(FORMATS), default="auto",
                   help="initial display format: " + ", ".join(FORMATS))
    args = p.parse_args()

    names = list(args.names) + [n for n in re.split(r"[,\s]+", args.vars.strip()) if n]
    if not names:
        p.error("give at least one variable name or address (positional or --vars)")

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
        atexit.register(launcher.stop)
        sampler.connect()

    n = sampler.load_elf(elf)
    print(f"Loaded {n} symbols from {elf}")

    if args.samples > 0:
        _run_headless(sampler, names, args.interval, args.samples, args.format)
        sampler.client.close()
    else:
        _run_gui(sampler, names, args.interval, args.host, args.port, args.format)


if __name__ == "__main__":
    main()
