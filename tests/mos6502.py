"""Minimal cycle-counting NMOS 6502 emulator for host-side tests.

Covers the documented (legal) instruction set only, with the standard
cycle counts including the page-crossing and taken-branch penalties.
Undocumented opcodes and decimal mode raise, so a test cannot pass by
silently executing something this emulator does not model.

Timing model: each instruction's memory access to an I/O address is
performed at the END of the instruction (after all its cycles), which
matches the last-cycle read/write of every absolute and indexed load
and store this project's timing-critical code uses. The emulator does
not own time itself: after each instruction it calls
`bus.tick(cycles, vic_reads)` and the bus decides when each of those
cycles finishes in real time (see tests/machine.py). `vic_reads` lists
the cycles (0-based) that read VIC-II/colour-RAM space ($D000-$DFFF):
the final data read of a load, and the dummy read an indexed access
makes one cycle earlier. A real 6502 performs that dummy read on every
indexed store, and on an indexed load only when the index crosses a
page; it reads the base address's page at the indexed low byte, so
`sta $d020,x` with X = 0 reads $D020 before writing it. That read costs
a wait state on the U64 like any other VIC register read (measured on
an Ultimate 64 Elite: see docs/UPIC_VIEWER.md). The dummy read's data
is discarded and has no side effect on the registers modelled here, so
only its timing is modelled.
"""


class UnsupportedOpcode(Exception):
    pass


# mode names: imp acc imm zp zpx zpy abs abx aby izx izy ind rel
_OPS = {}


def _op(code, name, mode, cycles, page_penalty=False):
    _OPS[code] = (name, mode, cycles, page_penalty)


for _c, _m, _n, _p in [
    (0xA9, 'imm', 2, 0), (0xA5, 'zp', 3, 0), (0xB5, 'zpx', 4, 0),
    (0xAD, 'abs', 4, 0), (0xBD, 'abx', 4, 1), (0xB9, 'aby', 4, 1),
    (0xA1, 'izx', 6, 0), (0xB1, 'izy', 5, 1)]:
    _op(_c, 'LDA', _m, _n, _p)
for _c, _m, _n, _p in [
    (0xA2, 'imm', 2, 0), (0xA6, 'zp', 3, 0), (0xB6, 'zpy', 4, 0),
    (0xAE, 'abs', 4, 0), (0xBE, 'aby', 4, 1)]:
    _op(_c, 'LDX', _m, _n, _p)
for _c, _m, _n, _p in [
    (0xA0, 'imm', 2, 0), (0xA4, 'zp', 3, 0), (0xB4, 'zpx', 4, 0),
    (0xAC, 'abs', 4, 0), (0xBC, 'abx', 4, 1)]:
    _op(_c, 'LDY', _m, _n, _p)
for _c, _m, _n in [
    (0x85, 'zp', 3), (0x95, 'zpx', 4), (0x8D, 'abs', 4), (0x9D, 'abx', 5),
    (0x99, 'aby', 5), (0x81, 'izx', 6), (0x91, 'izy', 6)]:
    _op(_c, 'STA', _m, _n)
for _c, _m, _n in [(0x86, 'zp', 3), (0x96, 'zpy', 4), (0x8E, 'abs', 4)]:
    _op(_c, 'STX', _m, _n)
for _c, _m, _n in [(0x84, 'zp', 3), (0x94, 'zpx', 4), (0x8C, 'abs', 4)]:
    _op(_c, 'STY', _m, _n)

for _name, _base in [('ORA', 0x00), ('AND', 0x20), ('EOR', 0x40),
                     ('ADC', 0x60), ('CMP', 0xC0), ('SBC', 0xE0)]:
    for _off, _m, _n, _p in [
        (0x09, 'imm', 2, 0), (0x05, 'zp', 3, 0), (0x15, 'zpx', 4, 0),
        (0x0D, 'abs', 4, 0), (0x1D, 'abx', 4, 1), (0x19, 'aby', 4, 1),
        (0x01, 'izx', 6, 0), (0x11, 'izy', 5, 1)]:
        _op(_base + _off, _name, _m, _n, _p)

for _c, _m, _n in [(0xE0, 'imm', 2), (0xE4, 'zp', 3), (0xEC, 'abs', 4)]:
    _op(_c, 'CPX', _m, _n)
for _c, _m, _n in [(0xC0, 'imm', 2), (0xC4, 'zp', 3), (0xCC, 'abs', 4)]:
    _op(_c, 'CPY', _m, _n)
_op(0x24, 'BIT', 'zp', 3)
_op(0x2C, 'BIT', 'abs', 4)

for _name, _base in [('ASL', 0x00), ('ROL', 0x20), ('LSR', 0x40),
                     ('ROR', 0x60)]:
    _op(_base + 0x0A, _name, 'acc', 2)
    _op(_base + 0x06, _name, 'zp', 5)
    _op(_base + 0x16, _name, 'zpx', 6)
    _op(_base + 0x0E, _name, 'abs', 6)
    _op(_base + 0x1E, _name, 'abx', 7)
for _name, _base in [('DEC', 0xC0), ('INC', 0xE0)]:
    _op(_base + 0x06, _name, 'zp', 5)
    _op(_base + 0x16, _name, 'zpx', 6)
    _op(_base + 0x0E, _name, 'abs', 6)
    _op(_base + 0x1E, _name, 'abx', 7)

for _c, _name in [(0x10, 'BPL'), (0x30, 'BMI'), (0x50, 'BVC'), (0x70, 'BVS'),
                  (0x90, 'BCC'), (0xB0, 'BCS'), (0xD0, 'BNE'), (0xF0, 'BEQ')]:
    _op(_c, _name, 'rel', 2)

_op(0x4C, 'JMP', 'abs', 3)
_op(0x6C, 'JMP', 'ind', 5)
_op(0x20, 'JSR', 'abs', 6)
_op(0x60, 'RTS', 'imp', 6)
_op(0x48, 'PHA', 'imp', 3)
_op(0x68, 'PLA', 'imp', 4)
_op(0x08, 'PHP', 'imp', 3)
_op(0x28, 'PLP', 'imp', 4)
for _c, _name in [(0xAA, 'TAX'), (0x8A, 'TXA'), (0xA8, 'TAY'), (0x98, 'TYA'),
                  (0xBA, 'TSX'), (0x9A, 'TXS'), (0xE8, 'INX'), (0xC8, 'INY'),
                  (0xCA, 'DEX'), (0x88, 'DEY'), (0x18, 'CLC'), (0x38, 'SEC'),
                  (0x58, 'CLI'), (0x78, 'SEI'), (0xB8, 'CLV'), (0xD8, 'CLD'),
                  (0xEA, 'NOP')]:
    _op(_c, _name, 'imp', 2)

_READS = {'LDA', 'LDX', 'LDY', 'ORA', 'AND', 'EOR', 'ADC', 'SBC', 'CMP',
          'CPX', 'CPY', 'BIT'}
# Stores whose indexed forms always make a dummy read. Read-modify-write
# instructions do too, but at a different cycle, and none of the
# timing-critical code uses them indexed, so they are not modelled.
_WRITES = {'STA', 'STX', 'STY'}

_LEN = {'imp': 1, 'acc': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'zpy': 2, 'izx': 2,
        'izy': 2, 'rel': 2, 'abs': 3, 'abx': 3, 'aby': 3, 'ind': 3}


class CPU:
    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.sp = 0xFF
        self.pc = 0
        self.n = self.v = self.z = self.c = False
        self.i = True
        self.d = False
        self.cycles = 0
        self.cur_pc = 0     # address of the instruction being executed

    # -- memory helpers -------------------------------------------------
    def rd(self, addr):
        return self.bus.read(addr & 0xFFFF)

    def wr(self, addr, val):
        self.bus.write(addr & 0xFFFF, val & 0xFF)

    def rd16(self, addr):
        return self.rd(addr) | (self.rd(addr + 1) << 8)

    def push(self, v):
        self.wr(0x100 + self.sp, v)
        self.sp = (self.sp - 1) & 0xFF

    def pull(self):
        self.sp = (self.sp + 1) & 0xFF
        return self.rd(0x100 + self.sp)

    def _nz(self, v):
        self.n = bool(v & 0x80)
        self.z = (v & 0xFF) == 0
        return v & 0xFF

    def _flags(self):
        return ((self.n << 7) | (self.v << 6) | 0x20 | (self.d << 3) |
                (self.i << 2) | (self.z << 1) | int(self.c))

    def _set_flags(self, p):
        self.n, self.v = bool(p & 0x80), bool(p & 0x40)
        self.d, self.i = bool(p & 0x08), bool(p & 0x04)
        self.z, self.c = bool(p & 0x02), bool(p & 0x01)

    # -- execution ------------------------------------------------------
    def call(self, addr, max_cycles=50_000_000):
        """JSR to addr and run until the matching RTS returns."""
        sentinel = 0xFFF0
        ret = sentinel - 1
        self.push(ret >> 8)
        self.push(ret & 0xFF)
        self.pc = addr
        start = self.cycles
        while self.pc != sentinel:
            self.step()
            if self.cycles - start > max_cycles:
                raise TimeoutError('no return from $%04X' % addr)

    def step(self):
        pc = self.pc
        self.cur_pc = pc
        opcode = self.bus.fetch(pc)
        if opcode not in _OPS:
            raise UnsupportedOpcode('$%02X at $%04X' % (opcode, pc))
        name, mode, cycles, page_penalty = _OPS[opcode]
        size = _LEN[mode]
        lo = self.bus.fetch(pc + 1) if size > 1 else 0
        hi = self.bus.fetch(pc + 2) if size > 2 else 0
        operand = lo | (hi << 8)
        self.pc = (pc + size) & 0xFFFF

        addr = None
        dummy = None      # address of an indexed access's dummy read
        if mode == 'zp':
            addr = lo
        elif mode == 'zpx':
            addr = (lo + self.x) & 0xFF
        elif mode == 'zpy':
            addr = (lo + self.y) & 0xFF
        elif mode == 'abs':
            addr = operand
        elif mode in ('abx', 'aby'):
            idx = self.x if mode == 'abx' else self.y
            addr = (operand + idx) & 0xFFFF
            crossed = (addr & 0xFF00) != (operand & 0xFF00)
            if page_penalty and crossed:
                cycles += 1
            if crossed or name in _WRITES:
                dummy = (operand & 0xFF00) | (addr & 0xFF)
        elif mode == 'izx':
            zp = (lo + self.x) & 0xFF
            addr = self.bus.fetch(zp) | (self.bus.fetch((zp + 1) & 0xFF) << 8)
        elif mode == 'izy':
            base = self.bus.fetch(lo) | (self.bus.fetch((lo + 1) & 0xFF) << 8)
            addr = (base + self.y) & 0xFFFF
            crossed = (addr & 0xFF00) != (base & 0xFF00)
            if page_penalty and crossed:
                cycles += 1
            if crossed or name in _WRITES:
                dummy = (base & 0xFF00) | (addr & 0xFF)
        elif mode == 'ind':
            # NMOS page-wrap bug on the pointer's high byte.
            addr = self.bus.fetch(operand) | (
                self.bus.fetch((operand & 0xFF00) | ((operand + 1) & 0xFF)) << 8)

        if mode == 'rel':
            taken = {
                'BPL': not self.n, 'BMI': self.n, 'BVC': not self.v,
                'BVS': self.v, 'BCC': not self.c, 'BCS': self.c,
                'BNE': not self.z, 'BEQ': self.z}[name]
            if taken:
                target = (self.pc + (lo - 256 if lo & 0x80 else lo)) & 0xFFFF
                cycles += 1
                if (target & 0xFF00) != (self.pc & 0xFF00):
                    cycles += 1
                self.pc = target
            self._finish(cycles)
            return

        # All cycles elapse before the instruction's data access, so an
        # I/O read or write is time-stamped at the instruction's end.
        # The bus is told which cycles read VIC-II/colour-RAM space
        # ($D000-$DFFF), which costs a wait period on the U64: the last
        # one for a load, the one before it for an indexed dummy read.
        vic_reads = []
        if dummy is not None and 0xD000 <= dummy < 0xE000:
            vic_reads.append(cycles - 2)
        if (name in _READS and mode not in ('imm', 'acc')
                and 0xD000 <= addr < 0xE000):
            vic_reads.append(cycles - 1)
        self._finish(cycles, tuple(vic_reads))

        def operand_value():
            return lo if mode == 'imm' else self.rd(addr)

        if name == 'LDA':
            self.a = self._nz(operand_value())
        elif name == 'LDX':
            self.x = self._nz(operand_value())
        elif name == 'LDY':
            self.y = self._nz(operand_value())
        elif name == 'STA':
            self.wr(addr, self.a)
        elif name == 'STX':
            self.wr(addr, self.x)
        elif name == 'STY':
            self.wr(addr, self.y)
        elif name == 'ORA':
            self.a = self._nz(self.a | operand_value())
        elif name == 'AND':
            self.a = self._nz(self.a & operand_value())
        elif name == 'EOR':
            self.a = self._nz(self.a ^ operand_value())
        elif name in ('ADC', 'SBC'):
            if self.d:
                raise UnsupportedOpcode('decimal mode at $%04X' % pc)
            m = operand_value()
            if name == 'SBC':
                m ^= 0xFF
            r = self.a + m + int(self.c)
            self.v = bool(~(self.a ^ m) & (self.a ^ r) & 0x80)
            self.c = r > 0xFF
            self.a = self._nz(r)
        elif name in ('CMP', 'CPX', 'CPY'):
            reg = {'CMP': self.a, 'CPX': self.x, 'CPY': self.y}[name]
            m = operand_value()
            self.c = reg >= m
            self._nz((reg - m) & 0xFF)
        elif name == 'BIT':
            m = self.rd(addr)
            self.n, self.v = bool(m & 0x80), bool(m & 0x40)
            self.z = (self.a & m) == 0
        elif name in ('ASL', 'ROL', 'LSR', 'ROR', 'INC', 'DEC'):
            m = self.a if mode == 'acc' else self.rd(addr)
            if name == 'ASL':
                self.c, m = bool(m & 0x80), (m << 1) & 0xFF
            elif name == 'ROL':
                m, self.c = ((m << 1) | int(self.c)) & 0xFF, bool(m & 0x80)
            elif name == 'LSR':
                self.c, m = bool(m & 1), m >> 1
            elif name == 'ROR':
                m, self.c = (m >> 1) | (int(self.c) << 7), bool(m & 1)
            elif name == 'INC':
                m = (m + 1) & 0xFF
            else:
                m = (m - 1) & 0xFF
            self._nz(m)
            if mode == 'acc':
                self.a = m
            else:
                self.wr(addr, m)
        elif name == 'JMP':
            self.pc = addr
        elif name == 'JSR':
            ret = (self.pc - 1) & 0xFFFF
            self.push(ret >> 8)
            self.push(ret & 0xFF)
            self.pc = addr
        elif name == 'RTS':
            self.pc = ((self.pull() | (self.pull() << 8)) + 1) & 0xFFFF
        elif name == 'PHA':
            self.push(self.a)
        elif name == 'PLA':
            self.a = self._nz(self.pull())
        elif name == 'PHP':
            self.push(self._flags() | 0x10)
        elif name == 'PLP':
            self._set_flags(self.pull())
        elif name == 'TAX':
            self.x = self._nz(self.a)
        elif name == 'TXA':
            self.a = self._nz(self.x)
        elif name == 'TAY':
            self.y = self._nz(self.a)
        elif name == 'TYA':
            self.a = self._nz(self.y)
        elif name == 'TSX':
            self.x = self._nz(self.sp)
        elif name == 'TXS':
            self.sp = self.x
        elif name == 'INX':
            self.x = self._nz(self.x + 1)
        elif name == 'INY':
            self.y = self._nz(self.y + 1)
        elif name == 'DEX':
            self.x = self._nz(self.x - 1)
        elif name == 'DEY':
            self.y = self._nz(self.y - 1)
        elif name == 'CLC':
            self.c = False
        elif name == 'SEC':
            self.c = True
        elif name == 'CLI':
            self.i = False
        elif name == 'SEI':
            self.i = True
        elif name == 'CLV':
            self.v = False
        elif name == 'CLD':
            self.d = False
        elif name == 'SED':
            raise UnsupportedOpcode('SED at $%04X' % pc)
        elif name == 'NOP':
            pass
        else:  # pragma: no cover - table and dispatch out of sync
            raise UnsupportedOpcode(name)

    def _finish(self, cycles, vic_reads=()):
        self.cycles += cycles
        self.bus.tick(cycles, vic_reads)
