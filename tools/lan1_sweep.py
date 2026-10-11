"""针对 LAN1 口灯定标：逐个点亮剩余候选引脚，观察上面 LAN1 口的指示灯
运行：sudo python3 ~/nas-lcd/lan1_sweep.py
看到 LAN1 口灯亮 -> 按 Ctrl+C，并记下屏幕最后打印的引脚名
"""
import fcntl, os, struct, time

GPIO_GET_LINEHANDLE = 0xC16CB403
GPIO_SET_LINEVALUES = 0xC040B409
GPIO_REQ_OUTPUT = 0x2

# 厂商 LED 特征值 (0x840002xx) 中尚未定标的引脚
CANDIDATES = [
    (8,   'GPPC_B_8'), (16, 'GPPC_B_16'), (17, 'GPPC_B_17'), (18, 'GPPC_B_18'),
    (23,  'GPPC_B_23'), (71, 'GPPC_A_7'), (72, 'GPPC_A_8'), (81, 'GPPC_A_17'),
    (85,  'GPPC_A_21'), (97, 'GPP_S_1'), (99, 'GPP_S_3'), (101, 'GPP_S_5'),
    (102, 'GPP_S_6'), (160, 'GPPC_H_0'), (161, 'GPPC_H_1'), (205, 'GPPC_D_13'),
    (206, 'GPPC_D_14'), (208, 'GPPC_D_16'), (210, 'GPPC_D_18'), (298, 'GPPC_F_10'),
    (320, 'GPPC_E_0'), (324, 'GPPC_E_4'), (327, 'GPPC_E_7'), (358, 'GPP_R_6'),
    (359, 'GPP_R_7'),
]

chip = os.open('/dev/gpiochip0', os.O_RDONLY)

def request(line):
    req = bytearray(364)
    struct.pack_into('<I', req, 0, line)
    struct.pack_into('<I', req, 256, GPIO_REQ_OUTPUT)
    req[260] = 1
    req[324:331] = b'xklan1\0'
    struct.pack_into('<I', req, 356, 1)
    fcntl.ioctl(chip, GPIO_GET_LINEHANDLE, req, True)
    return struct.unpack_from('<i', req, 360)[0]

def setv(h, v):
    d = bytearray(64)
    d[0] = v
    fcntl.ioctl(h, GPIO_SET_LINEVALUES, d)

print('=== 逐个点亮候选引脚（每个 3 秒）===\n  盯住上面 LAN1 口的指示灯，亮了就按 Ctrl+C', flush=True)
hs = {}
try:
    for line, name in CANDIDATES:
        h = request(line)
        hs[line] = h
        setv(h, 1)
        for state, tag in ((0, '点亮(低)'), (1, '点亮(高)')):
            setv(h, state)
            print(f'[{time.strftime("%H:%M:%S")}] {name} (线 {line}) {tag}', flush=True)
            time.sleep(1.5)
        setv(h, 1)
except KeyboardInterrupt:
    print('\n已中止', flush=True)
finally:
    for h in hs.values():
        setv(h, 1)
print('结束')
