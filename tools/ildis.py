"""Minimal .NET IL disassembler using dnfile. Usage: ildis.py HidCon.dll [methodFilter]"""
import sys, struct
import dnfile
from dnfile.enums import MetadataTables

# IL opcode table: opcode -> (name, operand_type)
# operand types: none, i8, i32, i64, r32, r64, brtarget(1/4), string, field, method, type, tok, sig, var16, var8, switch, calli
OPS = {}
def _op(code, name, ot):
    OPS[code] = (name, ot)

# one-byte opcodes
_ops1 = [
 (0x00,'nop','none'),(0x01,'break','none'),(0x02,'ldarg.0','none'),(0x03,'ldarg.1','none'),
 (0x04,'ldarg.2','none'),(0x05,'ldarg.3','none'),(0x06,'ldloc.0','none'),(0x07,'ldloc.1','none'),
 (0x08,'ldloc.2','none'),(0x09,'ldloc.3','none'),(0x0a,'stloc.0','none'),(0x0b,'stloc.1','none'),
 (0x0c,'stloc.2','none'),(0x0d,'stloc.3','none'),(0x0e,'ldarg.s','var8'),(0x0f,'ldarga.s','var8'),
 (0x10,'starg.s','var8'),(0x11,'ldloc.s','var8'),(0x12,'ldloca.s','var8'),(0x13,'stloc.s','var8'),
 (0x14,'ldnull','none'),(0x15,'ldc.i4.m1','none'),(0x16,'ldc.i4.0','none'),(0x17,'ldc.i4.1','none'),
 (0x18,'ldc.i4.2','none'),(0x19,'ldc.i4.3','none'),(0x1a,'ldc.i4.4','none'),(0x1b,'ldc.i4.5','none'),
 (0x1c,'ldc.i4.6','none'),(0x1d,'ldc.i4.7','none'),(0x1e,'ldc.i4.8','none'),(0x1f,'ldc.i4.s','i8'),
 (0x20,'ldc.i4','i32'),(0x21,'ldc.i8','i64'),(0x22,'ldc.r4','r32'),(0x23,'ldc.r8','r64'),
 (0x25,'dup','none'),(0x26,'pop','none'),(0x27,'jmp','method'),(0x28,'call','method'),
 (0x29,'calli','calli'),(0x2a,'ret','none'),(0x2b,'br.s','br8'),(0x2c,'brfalse.s','br8'),
 (0x2d,'brtrue.s','br8'),(0x2e,'beq.s','br8'),(0x2f,'bge.s','br8'),(0x30,'bgt.s','br8'),
 (0x31,'ble.s','br8'),(0x32,'blt.s','br8'),(0x33,'bne.un.s','br8'),(0x34,'bge.un.s','br8'),
 (0x35,'bgt.un.s','br8'),(0x36,'ble.un.s','br8'),(0x37,'blt.un.s','br8'),(0x38,'br','br32'),
 (0x39,'brfalse','br32'),(0x3a,'brtrue','br32'),(0x3b,'beq','br32'),(0x3c,'bge','br32'),
 (0x3d,'bgt','br32'),(0x3e,'ble','br32'),(0x3f,'blt','br32'),(0x40,'bne.un','br32'),
 (0x41,'bge.un','br32'),(0x42,'bgt.un','br32'),(0x43,'ble.un','br32'),(0x44,'blt.un','br32'),
 (0x45,'switch','switch'),(0x46,'ldind.i1','none'),(0x47,'ldind.u1','none'),(0x48,'ldind.i2','none'),
 (0x49,'ldind.u2','none'),(0x4a,'ldind.i4','none'),(0x4b,'ldind.u4','none'),(0x4c,'ldind.i8','none'),
 (0x4d,'ldind.i','none'),(0x4e,'ldind.r4','none'),(0x4f,'ldind.r8','none'),(0x50,'ldind.ref','none'),
 (0x51,'stind.ref','none'),(0x52,'stind.i1','none'),(0x53,'stind.i2','none'),(0x54,'stind.i4','none'),
 (0x55,'stind.i8','none'),(0x56,'stind.r4','none'),(0x57,'stind.r8','none'),(0x58,'add','none'),
 (0x59,'sub','none'),(0x5a,'mul','none'),(0x5b,'div','none'),(0x5c,'div.un','none'),
 (0x5d,'rem','none'),(0x5e,'rem.un','none'),(0x5f,'and','none'),(0x60,'or','none'),
 (0x61,'xor','none'),(0x62,'shl','none'),(0x63,'shr','none'),(0x64,'shr.un','none'),
 (0x65,'neg','none'),(0x66,'not','none'),(0x67,'conv.i1','none'),(0x68,'conv.i2','none'),
 (0x69,'conv.i4','none'),(0x6a,'conv.i8','none'),(0x6b,'conv.r4','none'),(0x6c,'conv.r8','none'),
 (0x6d,'conv.u4','none'),(0x6e,'conv.u8','none'),(0x6f,'callvirt','method'),(0x70,'cpobj','type'),
 (0x71,'ldobj','type'),(0x72,'ldstr','string'),(0x73,'newobj','method'),(0x74,'castclass','type'),
 (0x75,'isinst','type'),(0x76,'conv.r.un','none'),(0x79,'unbox','type'),(0x7a,'throw','none'),
 (0x7b,'ldfld','field'),(0x7c,'ldflda','field'),(0x7d,'stfld','field'),(0x7e,'ldsfld','field'),
 (0x7f,'ldsflda','field'),(0x80,'stsfld','field'),(0x81,'stobj','type'),(0x82,'conv.ovf.i1.un','none'),
 (0x83,'conv.ovf.i2.un','none'),(0x84,'conv.ovf.i4.un','none'),(0x85,'conv.ovf.i8.un','none'),
 (0x86,'conv.ovf.u1.un','none'),(0x87,'conv.ovf.u2.un','none'),(0x88,'conv.ovf.u4.un','none'),
 (0x89,'conv.ovf.u8.un','none'),(0x8a,'conv.ovf.i.un','none'),(0x8b,'conv.ovf.u.un','none'),
 (0x8c,'box','type'),(0x8d,'newarr','type'),(0x8e,'ldlen','none'),(0x8f,'ldelema','type'),
 (0x90,'ldelem.i1','none'),(0x91,'ldelem.u1','none'),(0x92,'ldelem.i2','none'),(0x93,'ldelem.u2','none'),
 (0x94,'ldelem.i4','none'),(0x95,'ldelem.u4','none'),(0x96,'ldelem.i8','none'),(0x97,'ldelem.i','none'),
 (0x98,'ldelem.r4','none'),(0x99,'ldelem.r8','none'),(0x9a,'ldelem.ref','none'),(0x9b,'stelem.i','none'),
 (0x9c,'stelem.i1','none'),(0x9d,'stelem.i2','none'),(0x9e,'stelem.i4','none'),(0x9f,'stelem.i8','none'),
 (0xa0,'stelem.r4','none'),(0xa1,'stelem.r8','none'),(0xa2,'stelem.ref','none'),(0xa3,'ldelem','type'),
 (0xa4,'stelem','type'),(0xa5,'unbox.any','type'),(0xb3,'conv.ovf.i1','none'),(0xb4,'conv.ovf.u1','none'),
 (0xb5,'conv.ovf.i2','none'),(0xb6,'conv.ovf.u2','none'),(0xb7,'conv.ovf.i4','none'),(0xb8,'conv.ovf.u4','none'),
 (0xb9,'conv.ovf.i8','none'),(0xba,'conv.ovf.u8','none'),(0xc2,'refanyval','type'),(0xc3,'ckfinite','none'),
 (0xc6,'mkrefany','type'),(0xd0,'ldtoken','tok'),(0xd1,'conv.u2','none'),(0xd2,'conv.u1','none'),
 (0xd3,'conv.i','none'),(0xd4,'conv.ovf.i','none'),(0xd5,'conv.ovf.u','none'),(0xd6,'add.ovf','none'),
 (0xd7,'add.ovf.un','none'),(0xd8,'mul.ovf','none'),(0xd9,'mul.ovf.un','none'),(0xda,'sub.ovf','none'),
 (0xdb,'sub.ovf.un','none'),(0xdc,'endfinally','none'),(0xdd,'leave','br32'),(0xde,'leave.s','br8'),
 (0xdf,'stind.i','none'),(0xe0,'conv.u','none'),
]
for c,n,t in _ops1: _op(c,n,t)
_ops2 = [
 (0x00,'arglist','none'),(0x01,'ceq','none'),(0x02,'cgt','none'),(0x03,'cgt.un','none'),
 (0x04,'clt','none'),(0x05,'clt.un','none'),(0x06,'ldftn','method'),(0x07,'ldvirtftn','method'),
 (0x09,'ldarg','var16'),(0x0a,'ldarga','var16'),(0x0b,'starg','var16'),(0x0c,'ldloc','var16'),
 (0x0d,'ldloca','var16'),(0x0e,'stloc','var16'),(0x0f,'localloc','none'),(0x11,'endfilter','none'),
 (0x12,'unaligned.','var8'),(0x13,'volatile.','none'),(0x14,'tail.','none'),(0x15,'initobj','type'),
 (0x16,'constrained.','type'),(0x17,'cpblk','none'),(0x18,'initblk','none'),(0x19,'no.','var8'),
 (0x1a,'rethrow','none'),(0x1c,'sizeof','type'),(0x1d,'refanytype','none'),(0x1e,'readonly.','none'),
]
for c,n,t in _ops2: _op(0xfe00|c,n,t)

class Disasm:
    def __init__(self, path):
        self.pe = dnfile.dnPE(path)
        self.md = self.pe.net.mdtables

    def resolve(self, token):
        table = (token >> 24) & 0xff
        rid = token & 0xffffff
        try:
            if table == 0x70:
                us = self.pe.net.user_strings.get(token & 0xffffff)
                return 'str:' + repr(us.value if us else '?')
            tmap = {0x01:'TypeRef',0x02:'TypeDef',0x04:'Field',0x06:'MethodDef',0x0a:'MemberRef',0x1b:'TypeSpec'}
            tname = tmap.get(table)
            if not tname: return f'tok:{token:08x}'
            tbl = getattr(self.md, tname)
            row = tbl.rows[rid-1]
            if tname == 'MemberRef':
                cls = ''
                try:
                    c = row.Class.row
                    cls = getattr(c,'TypeName',getattr(c,'Name',''))
                except Exception: pass
                return f'{cls}::{row.Name}'
            if tname in ('TypeRef','TypeDef'):
                return str(row.TypeName)
            if tname == 'Field':
                return 'fld:' + str(row.Name)
            if tname == 'MethodDef':
                return 'md:' + str(row.Name)
            if tname == 'TypeSpec':
                return 'typespec'
        except Exception as e:
            return f'tok:{token:08x}?'
        return f'tok:{token:08x}'

    def read_us_init(self, il, i):
        pass

    def dis(self, rva, name):
        pe = self.pe
        off = pe.get_offset_from_rva(rva)
        data = pe.__data__[off:off+16]
        b0 = data[0]
        if b0 & 3 == 2:  # tiny
            code_size = b0 >> 2
            code_off = off + 1
        else:  # fat
            flags_size = struct.unpack_from('<H', data, 0)[0]
            code_size = struct.unpack_from('<I', data, 4)[0]
            code_off = off + (flags_size >> 12) * 4
        il = pe.__data__[code_off:code_off+code_size]
        print(f'\n===== {name} (rva {rva:x}, {code_size}B IL) =====')
        i = 0
        while i < len(il):
            pos = i
            op = il[i]; i += 1
            if op == 0xfe:
                op = 0xfe00 | il[i]; i += 1
            info = OPS.get(op)
            if not info:
                print(f'{pos:04x}: <db {op:02x}>'); continue
            nm, ot = info
            arg = ''
            if ot == 'none': pass
            elif ot == 'var8': arg = str(il[i]); i += 1
            elif ot == 'var16': arg = str(struct.unpack_from('<H', il, i)[0]); i += 2
            elif ot == 'i8': arg = str(struct.unpack_from('<b', il, i)[0]); i += 1
            elif ot == 'i32': arg = str(struct.unpack_from('<i', il, i)[0]); i += 4
            elif ot == 'i64': arg = str(struct.unpack_from('<q', il, i)[0]); i += 8
            elif ot == 'r32': arg = str(struct.unpack_from('<f', il, i)[0]); i += 4
            elif ot == 'r64': arg = str(struct.unpack_from('<d', il, i)[0]); i += 8
            elif ot == 'br8':
                d = struct.unpack_from('<b', il, i)[0]; i += 1
                arg = f'IL_{i+d:04x}'
            elif ot == 'br32':
                d = struct.unpack_from('<i', il, i)[0]; i += 4
                arg = f'IL_{i+d:04x}'
            elif ot == 'switch':
                n = struct.unpack_from('<I', il, i)[0]; i += 4
                base = i + 4*n
                tg = []
                for k in range(n):
                    d = struct.unpack_from('<i', il, i)[0]; i += 4
                    tg.append(f'IL_{base+d:04x}')
                arg = '(' + ', '.join(tg) + ')'
            elif ot == 'string':
                t = struct.unpack_from('<I', il, i)[0]; i += 4
                arg = self.resolve(t)
            elif ot in ('method','field','type','tok','calli','sig'):
                t = struct.unpack_from('<I', il, i)[0]; i += 4
                arg = self.resolve(t)
            print(f'{pos:04x}: {nm} {arg}')

def main():
    path = sys.argv[1]
    filt = sys.argv[2] if len(sys.argv) > 2 else ''
    d = Disasm(path)
    for row in d.md.MethodDef.rows:
        nm = str(row.Name)
        if filt and filt.lower() not in nm.lower(): continue
        if nm.startswith(('get_','set_')) and filt != '': continue
        d.dis(row.Rva, nm)

main()
