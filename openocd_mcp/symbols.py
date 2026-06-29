"""
Read variable names -> addresses from a firmware .elf file.

Uses the ELF symbol table (.symtab), so the firmware must be compiled with
symbols (the default for debug builds; '-g' also adds DWARF type info which a
future version can use to decode signed/float/struct values).
"""
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection


class SymbolTable:
    def __init__(self):
        self.elf_path: str | None = None
        self._syms: dict[str, tuple[int, int]] = {}  # name -> (address, size_bytes)

    def load(self, path: str) -> int:
        """Parse the ELF and index all data-object symbols. Returns the count."""
        syms: dict[str, tuple[int, int]] = {}
        with open(path, "rb") as f:
            elf = ELFFile(f)
            for section in elf.iter_sections():
                if not isinstance(section, SymbolTableSection):
                    continue
                for sym in section.iter_symbols():
                    # STT_OBJECT = a data object (a variable), as opposed to a
                    # function (STT_FUNC) or other symbol kinds.
                    if (
                        sym["st_info"]["type"] == "STT_OBJECT"
                        and sym.name
                        and sym["st_value"] != 0
                    ):
                        syms[sym.name] = (sym["st_value"], sym["st_size"])
        self._syms = syms
        self.elf_path = path
        return len(syms)

    def lookup(self, name: str) -> tuple[int, int] | None:
        """Return (address, size_bytes) for a variable, or None if unknown."""
        return self._syms.get(name)

    def find(self, pattern: str = "") -> list[str]:
        """Names containing `pattern` (case-insensitive), sorted."""
        p = pattern.lower()
        return sorted(n for n in self._syms if p in n.lower())

    def count(self) -> int:
        return len(self._syms)
