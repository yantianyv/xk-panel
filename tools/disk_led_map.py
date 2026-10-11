"""硬盘位 LED 交互式映射工具
运行：sudo python3 ~/nas-lcd/disk_led_map.py

命令：
  回车/n  熄灭当前 -> 跳到下一个并点亮（观察用）
  p       回到上一个
  l / o   当前引脚点亮 / 熄灭
  m       标注当前引脚（会提示输入，如「盘位1 绿」），写入映射文件
  i       显示当前引脚信息
  a       全部点亮 4 秒（对照用）
  r       全部熄灭
  v       查看已标注的记录
  q       全部熄灭并退出
"""
import os
import fcntl, os, struct, time

GPIO_GET_LINEHANDLE = 0xC16CB403
GPIO_GET_LINEVALUES = 0xC040B408
GPIO_SET_LINEVALUES = 0xC040B409
GPIO_REQ_OUTPUT = 0x2

MARKS = os.path.expanduser('~/disk_led_marks.txt')

PADS = [
    (96, 'GPP_S_0'),
    (164, 'GPPC_H_4'),
    (165, 'GPPC_H_5'),
    (167, 'GPPC_H_7'),
    (192, 'GPPC_D_0'),
    (193, 'GPPC_D_1'),
    (195, 'GPPC_D_3'),
    (211, 'GPPC_D_19'),
]

chip = os.open('/dev/gpiochip0', os.O_RDONLY)

def request(line):
    req = bytearray(364)
    struct.pack_into('<I', req, 0, line)
    struct.pack_into('<I', req, 256, GPIO_REQ_OUTPUT)
    req[260] = 1
    req[324:331] = b'xkdisk\0'
    struct.pack_into('<I', req, 356, 1)
    fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, req, True)
    return struct.unpack_from('<i', req, 360)[0]

def setv(h, v):
    d = bytearray(64)
    d[0] = v
    fcntl.ioctl(h, GPIO_SET_LINEVALUES, d)

handles = {line: request(line) for line, _ in PADS}

def all_off():
    for line, _ in PADS:
        setv(handles[line], 1)

def show(idx):
    line, name = PADS[idx]
    print(f'\n>>> [{idx+1}/{len(PADS)}] {name}  (gpio 线 {line})  —— 已点亮，观察是哪个盘位/什么颜色', flush=True)

all_off()
idx = 0
setv(handles[PADS[0][0]], 0)
show(0)

while True:
    try:
        cmd = input('命令> ').strip().lower()
    except (EOFError, KeyboardInterrupt):
        cmd = 'q'
    line, name = PADS[idx]
    if cmd in ('', 'n'):
        setv(handles[line], 1)
        if idx < len(PADS) - 1:
            idx += 1
            setv(handles[PADS[idx][0]], 0)
            show(idx)
        else:
            print('已是最后一个引脚')
    elif cmd == 'p':
        setv(handles[line], 1)
        if idx > 0:
            idx -= 1
            setv(handles[PADS[idx][0]], 0)
            show(idx)
    elif cmd == 'l':
        setv(handles[line], 0); print('  已点亮', flush=True)
    elif cmd == 'o':
        setv(handles[line], 1); print('  已熄灭', flush=True)
    elif cmd == 'm':
        note = input('  标注（例：盘位1 绿）> ').strip()
        if note:
            with open(MARKS, 'a') as f:
                f.write(f'{PADS[idx][1]}\t{line}\t{note}\n')
            os.chmod(MARKS, 0o644)
            print(f'  已记录: {PADS[idx][1]} = {note}', flush=True)
    elif cmd == 'i':
        print(f'  {PADS[idx][1]} 线={line}', flush=True)
    elif cmd == 'a':
        for l, _ in PADS:
            setv(handles[l], 0)
        print('  全部点亮 4 秒', flush=True)
        time.sleep(4)
        all_off()
    elif cmd == 'r':
        all_off(); print('  全部熄灭', flush=True)
    elif cmd == 'v':
        if os.path.exists(MARKS):
            print(open(MARKS).read())
        else:
            print('  还没有标注')
    elif cmd == 'q':
        all_off(); print('  已全部熄灭，退出'); break
    else:
        print('未知命令')
os.close(chip)
