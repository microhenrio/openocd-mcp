"""
Resolve C types of variables from an ELF's DWARF debug info, so structured
variables (structs/unions/arrays) can be expanded into their members and each
leaf shown in its natural type (signed/unsigned/float/pointer/enum/char).

Requires a debug build (-g). Used by the live-watch window for expandable rows.
"""
import struct

from elftools.elf.elffile import ELFFile

_PASSTHRU = (
    "DW_TAG_typedef",
    "DW_TAG_const_type",
    "DW_TAG_volatile_type",
    "DW_TAG_restrict_type",
)
# DWARF DW_AT_encoding -> our leaf kind
_ENC = {1: "address", 2: "bool", 4: "float", 5: "signed", 6: "signed",
        7: "unsigned", 8: "unsigned"}


class TypeNode:
    """A node in a variable's type tree. Leaves have kind base/pointer/enum."""
    __slots__ = ("name", "offset", "size", "kind", "encoding", "type_name", "children",
                 "bit_size", "bit_offset")

    def __init__(self, name, offset, size, kind, encoding="", type_name="", children=None,
                 bit_size=None, bit_offset=None):
        self.name = name            # field/element/variable name
        self.offset = offset        # byte offset from the root variable's address
        self.size = size            # byte size of the storage unit read from the target
        self.kind = kind            # base|pointer|enum|struct|union|array|unknown
        self.encoding = encoding    # for leaves: signed|unsigned|float|bool|address|char
        self.type_name = type_name  # e.g. "uint32_t", "struct foo", "int[4]"
        self.children = children or []
        self.bit_size = bit_size    # C bitfield width in bits, or None if not a bitfield
        self.bit_offset = bit_offset  # bit position (from LSB) within the storage unit

    @property
    def is_aggregate(self):
        return self.kind in ("struct", "union", "array")


class TypeResolver:
    def __init__(self, elf_path, max_depth=5, max_array=64):
        self._fh = open(elf_path, "rb")
        self._elf = ELFFile(self._fh)
        self._dw = self._elf.get_dwarf_info() if self._elf.has_dwarf_info() else None
        self.max_depth = max_depth
        self.max_array = max_array
        self._index = None  # name -> type DIE (built lazily)

    def has_dwarf(self):
        return self._dw is not None

    # -- DIE helpers --------------------------------------------------------
    def _strip(self, die):
        while die is not None and die.tag in _PASSTHRU:
            if "DW_AT_type" not in die.attributes:
                return None
            die = die.get_DIE_from_attribute("DW_AT_type")
        return die

    def _name(self, die):
        if die is None:
            return "void"
        a = die.attributes.get("DW_AT_name")
        return a.value.decode("utf-8", "replace") if a else die.tag.replace("DW_TAG_", "")

    def _bitfield_info(self, m, underlying_size):
        """Return (byte_offset_in_struct, bit_offset_from_lsb, bit_len, unit_size)
        for a bitfield member DIE `m`, or None if it isn't a bitfield."""
        bsz = m.attributes.get("DW_AT_bit_size")
        if bsz is None:
            return None
        bit_len = bsz.value
        unit_size = underlying_size or 4

        # DWARF 4/5: total bit offset from the start of the containing struct.
        dbo = m.attributes.get("DW_AT_data_bit_offset")
        if dbo is not None:
            total_bit = dbo.value
            unit_index = total_bit // (unit_size * 8)
            byte_off = unit_index * unit_size
            bit_off = total_bit - byte_off * 8
            return byte_off, bit_off, bit_len, unit_size

        # DWARF <=3: DW_AT_bit_offset counts from the storage unit's MSB
        # (little-endian target, e.g. Cortex-M).
        legacy_bo = m.attributes.get("DW_AT_bit_offset")
        byte_size_attr = m.attributes.get("DW_AT_byte_size")
        if legacy_bo is not None and byte_size_attr is not None:
            storage_size = byte_size_attr.value
            mloc = m.attributes.get("DW_AT_data_member_location")
            byte_off = mloc.value if (mloc and isinstance(mloc.value, int)) else 0
            bit_off = storage_size * 8 - legacy_bo.value - bit_len
            return byte_off, bit_off, bit_len, storage_size

        return None

    def _build_index(self):
        self._index = {}
        for cu in self._dw.iter_CUs():
            for die in cu.iter_DIEs():
                if die.tag == "DW_TAG_variable" and "DW_AT_type" in die.attributes:
                    na = die.attributes.get("DW_AT_name")
                    if na:
                        nm = na.value.decode("utf-8", "replace")
                        self._index.setdefault(nm, die)

    # -- public -------------------------------------------------------------
    def resolve(self, name):
        """Return the TypeNode tree for a variable, or None if unknown."""
        if not self._dw:
            return None
        if self._index is None:
            self._build_index()
        die = self._index.get(name)
        if die is None:
            return None
        try:
            return self._build(self._strip(die.get_DIE_from_attribute("DW_AT_type")), name, 0, 0)
        except Exception:
            return None

    def _build(self, die, name, offset, depth):
        die = self._strip(die)
        if die is None:
            return TypeNode(name, offset, 0, "unknown")
        tag = die.tag
        size = die.attributes["DW_AT_byte_size"].value if "DW_AT_byte_size" in die.attributes else 0
        tn = self._name(die)

        if tag == "DW_TAG_base_type":
            enc = die.attributes.get("DW_AT_encoding")
            encoding = _ENC.get(enc.value, "unsigned") if enc else "unsigned"
            if encoding in ("signed", "unsigned") and ("char" in tn):
                encoding = "char"
            return TypeNode(name, offset, size or 1, "base", encoding, tn)
        if tag == "DW_TAG_pointer_type":
            return TypeNode(name, offset, size or 4, "pointer", "address", tn)
        if tag == "DW_TAG_enumeration_type":
            return TypeNode(name, offset, size or 4, "enum", "unsigned", tn)
        if tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            kind = "struct" if tag.endswith("structure_type") else "union"
            node = TypeNode(name, offset, size, kind, "", tn)
            if depth < self.max_depth:
                for m in die.iter_children():
                    if m.tag != "DW_TAG_member" or "DW_AT_type" not in m.attributes:
                        continue
                    mloc = m.attributes.get("DW_AT_data_member_location")
                    moff = mloc.value if (mloc and isinstance(mloc.value, int)) else 0
                    mname = self._name(m)
                    child = self._build(m.get_DIE_from_attribute("DW_AT_type"),
                                         mname, offset + moff, depth + 1)
                    bf = self._bitfield_info(m, child.size)
                    if bf:
                        byte_off, bit_off, bit_len, unit_size = bf
                        child.offset = offset + byte_off
                        child.size = unit_size
                        child.bit_offset = bit_off
                        child.bit_size = bit_len
                    node.children.append(child)
            return node
        if tag == "DW_TAG_array_type":
            elem = die.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in die.attributes else None
            elem_s = self._strip(elem)
            count = 0
            for sub in die.iter_children():
                if sub.tag == "DW_TAG_subrange_type":
                    if "DW_AT_count" in sub.attributes:
                        count = sub.attributes["DW_AT_count"].value
                    elif "DW_AT_upper_bound" in sub.attributes:
                        ub = sub.attributes["DW_AT_upper_bound"].value
                        if isinstance(ub, int):
                            count = ub + 1
            probe = self._build(elem_s, "[0]", 0, depth + 1)
            stride = probe.size or 1
            node = TypeNode(name, offset, count * stride if count else size, "array",
                            "", f"{self._name(elem_s)}[{count}]")
            if depth < self.max_depth and count:
                for i in range(min(count, self.max_array)):
                    node.children.append(
                        self._build(elem_s, f"[{i}]", offset + i * stride, depth + 1))
            return node
        return TypeNode(name, offset, size, "unknown", "", tn)


def decode_leaf(node, data: bytes):
    """Decode a leaf node's bytes from `data` (which starts at the root address).
    Returns a float for float types, otherwise the RAW unsigned int (sign/format
    is applied at presentation time). None if the bytes aren't available."""
    size = node.size or 4
    raw = data[node.offset:node.offset + size]
    if len(raw) < size:
        return None
    if node.kind == "base" and node.encoding == "float":
        if size == 4:
            return struct.unpack("<f", raw)[0]
        if size == 8:
            return struct.unpack("<d", raw)[0]
    value = int.from_bytes(raw, "little")
    if node.bit_size:
        value = (value >> (node.bit_offset or 0)) & ((1 << node.bit_size) - 1)
    return value
