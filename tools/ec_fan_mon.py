"""IT8613 风扇寄存器只读监视（不改任何值）
运行：sudo python3 ~/nas-lcd/ec_fan_mon.py [秒数]
每 2 秒打印：PWM2 控制寄存器(0x16)、占空比(0x6B)、风扇转速寄存器(0x0D-0x12)
"""
import os, sys, time

ADDR, DATA = 0x295, 0x296
DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 40
fd = os.open('/dev/port', os.O_RDWR)

def rd(idx):
    os.pwrite(fd, bytes([idx]), ADDR)
    return os.pread(fd, 1, DATA)[0]

# bank 0
os.pwrite(fd, bytes([0x06]), ADDR)
os.pwrite(fd, bytes([0x00]), DATA)

print('时间      PWM2_ctrl(0x16) duty(0x6B)  tach(0x0D/0E 0x0F/10 0x11/12)  0x15/0x17')
t0 = time.time()
while time.time() - t0 < DURATION:
    ctrl = rd(0x16)
    duty = rd(0x6B)
    t1 = (rd(0x0d) << 8) | rd(0x0e)
    t2 = (rd(0x0f) << 8) | rd(0x10)
    t3 = (rd(0x11) << 8) | rd(0x12)
    r15, r17 = rd(0x15), rd(0x17)
    print(f'{time.strftime("%H:%M:%S")}  0x{ctrl:02x}         0x{duty:02x}       '
          f'{t1:5d} {t2:5d} {t3:5d}                0x{r15:02x}/0x{r17:02x}', flush=True)
    time.sleep(2)
os.close(fd)
print('结束（全程只读，未修改任何寄存器）')
