"""ITE Super I/O (IT87 EC) 探测：读芯片 ID + GPIO 逻辑设备寄存器（只读操作）
需要 root 运行（/dev/port）。结果 tee 到 /tmp/ec_probe.log
"""
import os, sys, time

def write_port(fd, off, val):
    os.pwrite(fd, bytes([val]), off)

def read_port(fd, off):
    return os.pread(fd, 1, off)[0]

def probe(fd, base):
    """返回 chip_id 或 None"""
    # 进入配置模式（ITE 标准序列）
    write_port(fd, base, 0x87)
    write_port(fd, base, 0x01)
    write_port(fd, base, 0x55)
    write_port(fd, base, 0x55)
    write_port(fd, base, 0x20)
    hi = read_port(fd, base + 1)
    write_port(fd, base, 0x21)
    lo = read_port(fd, base + 1)
    # 退出配置模式
    write_port(fd, base, 0x02)
    write_port(fd, base, 0x02)
    chip = (hi << 8) | lo
    return chip if chip not in (0x0000, 0xFFFF) else None

def dump_ldn(fd, base, ldn, start=0x00, end=0x100):
    write_port(fd, base, 0x87); write_port(fd, base, 0x01)
    write_port(fd, base, 0x55); write_port(fd, base, 0x55)
    write_port(fd, base, 0x07)
    write_port(fd, base + 1, ldn)
    out = []
    for r in range(start, end):
        write_port(fd, base, r)
        out.append(read_port(fd, base + 1))
    write_port(fd, base, 0x02); write_port(fd, base, 0x02)
    return out

def main():
    fd = os.open('/dev/port', os.O_RDWR)
    try:
        for base, name in ((0x2E, '0x2E'), (0x4E, '0x4E')):
            chip = probe(fd, base)
            print(f'端口 {name}: chip_id = {chip and hex(chip)}')
            if chip:
                print(f'  => 发现 ITE Super I/O: 0x{chip:04X}')
                for ldn, lname in ((0x07, 'GPIO'), (0x04, 'EC?'), (0x0A, 'ACPI')):
                    try:
                        regs = dump_ldn(fd, base, ldn)
                        hexs = ' '.join(f'{v:02x}' for v in regs)
                        print(f'  LDN 0x{ldn:02x} ({lname}):')
                        for i in range(0, 256, 16):
                            print(f'    {i:02x}: ' + ' '.join(f'{regs[i+j]:02x}' for j in range(16)))
                    except Exception as e:
                        print(f'  LDN 0x{ldn:02x} 读取失败: {e}')
    finally:
        os.close(fd)

main()
