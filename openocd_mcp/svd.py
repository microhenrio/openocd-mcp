"""
Map peripheral register names -> addresses and bitfields from a CMSIS-SVD file.

Lets you refer to registers as 'RCC.CR' or 'GPIOA.MODER' instead of raw
addresses, and decode a register value into its named bitfields.
"""
from cmsis_svd import SVDParser


class Field:
    __slots__ = ("name", "offset", "width")

    def __init__(self, name: str, offset: int, width: int):
        self.name = name
        self.offset = offset
        self.width = width


class Register:
    __slots__ = ("name", "address", "size", "fields")

    def __init__(self, name: str, address: int, size: int, fields: list[Field]):
        self.name = name
        self.address = address
        self.size = size
        self.fields = fields


class PeripheralMap:
    def __init__(self):
        self.device: str | None = None
        self.svd_path: str | None = None
        self._regs: dict[str, Register] = {}      # "PERIPH.REG" -> Register
        self._periph_regs: dict[str, list[str]] = {}  # "PERIPH" -> [reg names]

    def load(self, path: str) -> int:
        """Parse the SVD and index every register. Returns the register count."""
        dev = SVDParser.for_xml_file(path).get_device()
        regs: dict[str, Register] = {}
        periph_regs: dict[str, list[str]] = {}
        for p in dev.peripherals:
            names = []
            for r in p.registers:
                key = f"{p.name}.{r.name}"
                fields = [
                    Field(fld.name, fld.bit_offset, fld.bit_width)
                    for fld in (r.fields or [])
                ]
                regs[key] = Register(
                    name=key,
                    address=p.base_address + r.address_offset,
                    size=(r.size or 32),  # STM32 registers are 32-bit unless stated
                    fields=fields,
                )
                names.append(r.name)
            periph_regs[p.name] = names
        self.device = dev.name
        self.svd_path = path
        self._regs = regs
        self._periph_regs = periph_regs
        return len(regs)

    def lookup(self, name: str) -> Register | None:
        """Case-insensitive lookup of 'PERIPH.REG'."""
        reg = self._regs.get(name)
        if reg:
            return reg
        target = name.lower()
        for key, r in self._regs.items():
            if key.lower() == target:
                return r
        return None

    def decode(self, reg: Register, value: int) -> list[tuple[str, str, int]]:
        """Split a register value into its fields.
        Returns [(field_name, bit_range_str, field_value), ...]."""
        out = []
        for f in reg.fields:
            fval = (value >> f.offset) & ((1 << f.width) - 1)
            hi = f.offset + f.width - 1
            bits = f"{hi}" if f.width == 1 else f"{hi}:{f.offset}"
            out.append((f.name, bits, fval))
        return out

    def registers_of(self, peripheral: str) -> list[str]:
        for key, names in self._periph_regs.items():
            if key.lower() == peripheral.lower():
                return sorted(names)
        return []

    def peripherals(self) -> list[str]:
        return sorted(self._periph_regs)

    def count(self) -> int:
        return len(self._regs)
