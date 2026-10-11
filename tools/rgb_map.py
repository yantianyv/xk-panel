"""电源键区域 LED 颜色标定（三原色验证）
运行：sudo python3 ~/nas-lcd/rgb_map.py

命令：
  回车/n  熄灭当前 -> 下一个并点亮
  p       上一个
  l / o   当前点亮 / 熄灭
  m       标注颜色（如 红 / 绿 / 蓝 / 白 / 黄 / 看不出）
  1-6     切到第 N 个引脚
  a       全部点亮（看混合效果）
  r       全部熄灭
  q       退出
"""
import os
import fcntl, os, struct, time

GPIO_GET_LINEHANDLE = 0xC16CB403
GPIO_SET_LINEVALUES = 0xC040B409
GPIO_REQ_OUTPUT = 0x2
MARKS = os.path.expanduser('~/rgb_marks.txt')

PADS = [
    (97,  'GPP_S_1'),
    (98,  'GPP_S_2'),
    (99,  'GPP_S_3'),
    (100, 'GPP_S_4'),
    (101, 'GPP_S_5'),
    (102, 'GPP_S_6'),
    (75,  'GPPC_A_11'),   # 已知：电源灯
    (77,  'GPPC_A_13'),   # 已知：USB 灯
]

chip = os.open('/dev/gpiochip0', os.O_RDONLY)

def request(line):
    req = bytearray(364)
    struct.pack_into('<I', req, 0, line)
    struct.pack_into('<I', req, 256, GPIO_REQ_OUTPUT)
    req[260] = 1
    req[324:331] = b'xkrgb\0\0'
    struct.pack_into('<I', req, 356, 1)
    fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, req, True)
    return struct.unpack_from('<i', req, 360)[0]

def setv(h, v):
    d = bytearray(64)
    d[0] = v
    fcntl.ioctl(h, GPIO_SET_LINEVALUES, d)

hs = {line: request(line) for line, _ in PADS}
for h in hs.values():
    setv(h, 1)

def show(i):
    line, name = PADS[i]
    print(f'\n>>> [{i+1}/{len(PADS)}] {name} (线 {line}) — 已点亮(低电平)，请判断发什么颜色', flush=True)

i = 0
setv(hs[PADS[0][0]], 0)
show(0)

while True:
    try:
        cmd = input('命令> ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        cmd = 'q'
    line, name = PADS[i]
    if cmd in ('', 'n'):
        setv(hs[line], 1)
        i = (i + 1) % len(PADS)
        setv(hs[PADS[i][0]], 0)
        show(i)
    elif cmd == 'p':
        setv(hs[line], 1)
        i = (i - 1) % len(PADS)
        setv(hs[PADS[i][0]], 0)
        show(i)
    elif cmd == 'l':
        setv(hs[line], 0); print('  点亮', flush=True)
    elif cmd == 'o':
        setv(hs[line], 1); print('  熄灭', flush=True)
    elif len(cmd) == 1 and cmd in '12345678':
        setv(hs[line], 1)
        i = int(cmd) - 1
        setv(hs[PADS[i][0]], 0)
        show(i)
    elif cmd == 'm':
        note = input('  颜色（红/绿/蓝/白/黄/看不出）> ').strip()
        if note:
            with open(MARKS, 'a') as f:
                f.write(f'{name}\t{line}\t{note}\n')
            os.chmod(MARKS, 0o644)
            print('  已记录', flush=True)
    elif cmd == 'a':
        for l, _ in PADS:
            setv(hs[l], 0)
        print('  全部点亮 4 秒', flush=True)
        time.sleep(4)
        for l, _ in PADS:
            setv(hs[l], 1)
    elif cmd == 'r':
        for l, _ in PADS:
            setv(hs[l], 1)
        print('  全部熄灭', flush=True)
    elif cmd == 'q':
        for l, _ in PADS:
            setv(hs[l], 1)
        print('  已熄灭，退出'); break
    else:
        print('未知命令')
os.close(chip)
