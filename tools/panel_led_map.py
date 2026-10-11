"""面板其余 LED 交互式标定（电源 / 状态 / 网口 / USB）
运行：sudo python3 ~/nas-lcd/panel_led_map.py

命令：
  回车/n  熄灭当前 -> 跳到下一个并点亮（低电平）
  p       回到上一个
  l / o   当前引脚点亮(低) / 熄灭(高)
  m       标注当前引脚（如「左侧上灯条 / 低电平亮 / 常亮」）
  i       显示当前引脚信息
  r       全部熄灭
  v       查看已标注
  q       全部熄灭并退出
"""
import os
import fcntl, os, struct, time

GPIO_GET_LINEHANDLE = 0xC16CB403
GPIO_GET_LINEVALUES = 0xC040B408
GPIO_SET_LINEVALUES = 0xC040B409
GPIO_REQ_OUTPUT = 0x2
MARKS = os.path.expanduser('~/panel_led_marks.txt')

PADS = [
    (75,  'GPPC_A_11', '厂商名 power（原厂程序常写低）'),
    (77,  'GPPC_A_13', '厂商名 USB 灯'),
    (7,   'GPPC_B_7',  '厂商名 LAN1 灯'),
    (295, 'GPPC_F_7',  '厂商名 LAN2 灯'),
    (98,  'GPP_S_2',   '厂商名 状态灯 1'),
    (100, 'GPP_S_4',   '厂商名 状态灯 2'),
]

chip = os.open('/dev/gpiochip0', os.O_RDONLY)

def request(line):
    req = bytearray(364)
    struct.pack_into('<I', req, 0, line)
    struct.pack_into('<I', req, 256, GPIO_REQ_OUTPUT)
    req[260] = 1
    req[324:331] = b'xkleds\0'
    struct.pack_into('<I', req, 356, 1)
    fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, req, True)
    return struct.unpack_from('<i', req, 360)[0]

def setv(h, v):
    d = bytearray(64)
    d[0] = v
    fcntl.ioctl(h, GPIO_SET_LINEVALUES, d)

handles = {line: request(line) for line, _, _ in PADS}

def all_off():
    for line, _, _ in PADS:
        setv(handles[line], 1)

def show(i):
    line, name, desc = PADS[i]
    print(f'\n>>> [{i+1}/{len(PADS)}] {name} (线 {line}) — {desc}  —— 已点亮(低电平)', flush=True)

all_off()
i = 0
setv(handles[PADS[0][0]], 0)
show(0)

while True:
    try:
        cmd = input('命令> ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        cmd = 'q'
    line, name, desc = PADS[i]
    if cmd in ('', 'n'):
        setv(handles[line], 1)
        if i < len(PADS) - 1:
            i += 1
            setv(handles[PADS[i][0]], 0)
            show(i)
        else:
            print('已是最后一个')
    elif cmd == 'p':
        setv(handles[line], 1)
        if i > 0:
            i -= 1
            setv(handles[PADS[i][0]], 0)
            show(i)
    elif cmd == 'l':
        setv(handles[line], 0); print('  点亮(低电平)', flush=True)
    elif cmd == 'o':
        setv(handles[line], 1); print('  熄灭(高电平)', flush=True)
    elif cmd == 'm':
        note = input('  标注（例：左侧下灯条 / 亮=低电平 / 常亮）> ').strip()
        if note:
            with open(MARKS, 'a') as f:
                f.write(f'{name}\t{line}\t{note}\n')
            os.chmod(MARKS, 0o644)
            print('  已记录', flush=True)
    elif cmd == 'i':
        print(f'  {name} 线={line} {desc}', flush=True)
    elif cmd == 'r':
        all_off(); print('  全部熄灭', flush=True)
    elif cmd == 'v':
        print(open(MARKS).read() if os.path.exists(MARKS) else '  还没有标注')
    elif cmd == 'q':
        all_off(); print('  已全部熄灭，退出'); break
    else:
        print('未知命令')
os.close(chip)
