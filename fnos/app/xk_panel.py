#!/usr/bin/env python3
"""兮克 TS3004E 前面板驱动（飞牛 fnOS）

功能：
  - LCD 屏幕：CPU/系统/硬盘温度、存储容量、网口 IP/掩码/网关，刷新项与间隔可配
  - 状态灯：电源键三原色灯按「健康分」无极渐变；状态灯显示所选存储的容量健康度
  - 灯效：常亮 / 呼吸 / 心跳 / 闪烁 / 彩虹，亮度与速度可调；硬盘读写、网口流量可触发脉冲
  - 硬盘位绿/红灯、USB 灯、网口灯
  - 风扇：可选接管 IT8613E 的 PWM（默认关闭，含「交还 BIOS」按钮）
  - 设置页：http://<NAS>:47001（灯光校准、重映射、健康评分参数、夜间免打扰等）

说明：
  - LCD 走 /dev/hidraw 的 Feature 报告（9 字节，末字节校验：全包之和 = 0xFF）
  - LED 走 gpiochip（PCH 引脚），低电平点亮；三原色用软件 PWM 混色
  - 风扇走 /dev/port 读写 IT8613E 的 EC 寄存器（索引 0x295 / 数据 0x296，bank 0）
    IT8613E 的 PWM 控制寄存器 bit7 = 自动模式开关，手动模式需清 bit7，占空比写在 0x6B
  - 需要 root 运行
"""
import colorsys
import fcntl
import json
import math
import os
import re
import struct
import subprocess
import sys
import threading
import time

HID_SET_FEATURE_9 = 0xC0094806
HID_GET_FEATURE_9 = 0xC0094807
GPIO_GET_LINEHANDLE = 0xC16CB403
GPIO_GET_LINEVALUES = 0xC040B408
GPIO_SET_LINEVALUES = 0xC040B409
GPIO_REQ_OUTPUT = 0x2

HID_VID = 0x374A
HID_PID = 0xB101

EC_ADDR = 0x295
EC_DATA = 0x296
FAN_CTRL_REG = 0x16      # PWM2 控制寄存器（bit7=自动模式开关，清 bit7=手动占空比）
FAN_DUTY_REG = 0x6B      # PWM2 占空比寄存器（手动模式下生效）

# ---------------- 面板引脚角色：默认值为本机型实测结果，可在设置页重映射 ----------------
DEFAULT_PIN_MAP = {
    'power_red':   'GPP_S_3',     # 电源键灯 红
    'power_green': 'GPP_S_1',     # 电源键灯 绿
    'power_blue':  'GPPC_A_11',   # 电源键灯 蓝
    'store_red':   'GPP_S_4',     # 状态灯 红
    'store_green': 'GPP_S_2',     # 状态灯 绿
    'store_blue':  'GPP_S_6',     # 状态灯 蓝
    'usb':         'GPPC_A_13',   # USB 指示灯
    'lan1':        'GPP_R_7',     # 网口 1 指示灯
    'lan2':        'GPPC_B_7',    # 网口 2 指示灯
    'disk1_green': 'GPP_S_0',  'disk1_red': 'GPPC_D_19',
    'disk2_green': 'GPPC_H_4', 'disk2_red': 'GPPC_H_5',
    'disk3_green': 'GPPC_D_3', 'disk3_red': 'GPPC_H_7',
    'disk4_green': 'GPPC_D_0', 'disk4_red': 'GPPC_D_1',
    'btn_reset':   'GPPC_B_5',    # 复位按键
    'btn_usbcopy': 'GPPC_B_6',    # USB 复制按键
}

PIN_ROLES = [
    ('power_red', '电源键灯 · 红'), ('power_green', '电源键灯 · 绿'), ('power_blue', '电源键灯 · 蓝'),
    ('store_red', '状态灯 · 红'), ('store_green', '状态灯 · 绿'), ('store_blue', '状态灯 · 蓝'),
    ('usb', 'USB 指示灯'),
    ('lan1', '网口 1 指示灯'), ('lan2', '网口 2 指示灯'),
    ('disk1_green', '盘位 1 · 绿'), ('disk1_red', '盘位 1 · 红'),
    ('disk2_green', '盘位 2 · 绿'), ('disk2_red', '盘位 2 · 红'),
    ('disk3_green', '盘位 3 · 绿'), ('disk3_red', '盘位 3 · 红'),
    ('disk4_green', '盘位 4 · 绿'), ('disk4_red', '盘位 4 · 红'),
    ('btn_reset', '复位按键'), ('btn_usbcopy', 'USB 复制按键'),
]


def pin_map(settings):
    pm = dict(DEFAULT_PIN_MAP)
    custom = settings.data.get('pin_map') or {}
    for k, v in custom.items():
        if k in DEFAULT_PIN_MAP and isinstance(v, str) and v:
            pm[k] = v
    return pm


def led_structure(settings):
    """由映射表生成各类灯的分组结构"""
    pm = pin_map(settings)
    power = {'red': pm['power_red'], 'green': pm['power_green'], 'blue': pm['power_blue']}
    store = {'red': pm['store_red'], 'green': pm['store_green'], 'blue': pm['store_blue']}
    disks = [{'green': pm[f'disk{i}_green'], 'red': pm[f'disk{i}_red']} for i in (1, 2, 3, 4)]
    return power, store, pm['usb'], {'lan1': pm['lan1'], 'lan2': pm['lan2']}, disks


# 引脚名 -> gpiochip 线号兜底表（pinctrl 表不可读时使用）
FALLBACK_LINES = {
    'GPPC_B_7': 7, 'GPPC_A_11': 75, 'GPPC_A_13': 77,
    'GPP_S_0': 96, 'GPP_S_1': 97, 'GPP_S_2': 98, 'GPP_S_3': 99,
    'GPP_S_4': 100, 'GPP_S_5': 101, 'GPP_S_6': 102,
    'GPPC_H_4': 164, 'GPPC_H_5': 165, 'GPPC_H_7': 167,
    'GPPC_D_0': 192, 'GPPC_D_1': 193, 'GPPC_D_3': 195, 'GPPC_D_19': 211,
    'GPPC_F_7': 295, 'GPP_R_7': 359,
}

LAN_PORT_MAP = {
    'enx8ca682710c71': 1,
    'enx8ca682710c72': 2,
}

PWM_HZ = 100
PWM_STEPS = 20

EFFECTS = ['solid', 'breath', 'rainbow']   # blink 仅用于故障告警（内部行为）

# 风扇曲线预设：[[温度℃, 占空比%], ...]
FAN_PRESETS = {
    'quiet':       [[40, 20], [50, 32], [60, 50], [70, 70], [80, 90], [85, 100]],
    'standard':    [[40, 30], [50, 45], [60, 65], [70, 85], [80, 100]],
    'performance': [[30, 40], [40, 55], [50, 75], [60, 90], [70, 100]],
}

DEFAULT_SETTINGS = {
    # --- 状态灯 ---
    'power_led': 'gradient',        # gradient=无极渐变 / fixed=固定色 / off=关闭
    'power_fixed_color': '#2f7dff',
    'power_effect': 'breath',       # solid/breath/rainbow
    'store_led': 'capacity',        # capacity=容量健康度 / array=阵列状态 / off=关闭
    'store_effect': 'solid',        # solid/breath/rainbow
    'storage_target': 'all',        # 监控目标：all 或 vol1/vol2/...
    'cap_green_free': 35,           # 可用空间 ≥ 此值 = 绿
    'cap_red_free': 5,              # 可用空间 ≤ 此值 = 红
    'fault_blink': True,
    'power_effect_speed': 3.0,      # 电源键灯灯效周期（秒）
    'store_effect_speed': 3.0,      # 状态灯灯效周期（秒）
    'effect_speed': 3.0,            # 兼容旧配置的默认周期
    'disk_led': True,
    'disk_activity': True,          # 硬盘读写时盘位绿灯脉冲
    'disk_marquee': 'rebuild',      # 盘位灯跑马灯：off / boot / rebuild / always
    'marquee_period': 1.2,          # 跑马灯流转一周的时间（秒）
    'lan_activity': True,           # 网络流量时网口灯脉冲
    'usb_led': True,
    'lan_led': True,
    'brightness': 100,
    'boot_effect': True,            # 启动时 RGB 自检动画
    'status_light': 'gradient',
    # --- 健康评分 ---
    'cpu_temp_green': 50, 'cpu_temp_red': 85,
    'hdd_temp_green': 45, 'hdd_temp_red': 58,
    'load_cpu_ref': 0.9, 'load_gpu_ref': 0.9, 'mem_ok_ratio': 0.25,
    'w_cpu': 0.5, 'w_gpu': 0.2, 'w_mem': 0.3, 'w_temp': 0.6,
    'smooth': 0.15, 'throttle_cap': 0.5,
    # --- 屏幕 ---
    'lcd_enabled': True, 'lcd_interval': 1,
    'lcd_temp': True, 'lcd_storage': True, 'lcd_network': True,
    # --- 夜间免打扰 ---
    'night_enabled': False,
    'night_start': '23:00', 'night_end': '07:00',
    'night_leds': 'dim', 'night_fan_max': 35,
    'night_effect': 'solid',        # 夜间灯效（默认常亮，避免夜间闪烁）
    # --- 风扇 ---
    'fan_enabled': False,
    'fan_preset': 'standard', 'fan_curve': None, 'fan_min_duty': 15,
    'fan_reassert_sec': 10,
    # --- 引脚映射 ---
    'pin_map': {},
    'probe_seconds': 2.5,
    # --- 服务 ---
    'web_port': 47001,
}

settings_lock = threading.Lock()


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ------------------------------------------------------------ 配置（热加载）

class Settings:
    def __init__(self, path):
        self.path = path
        self.data = dict(DEFAULT_SETTINGS)
        self.mtime = 0
        self.load(force=True)

    def load(self, force=False):
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = 0
        if not force and mtime == self.mtime:
            return False
        self.mtime = mtime
        try:
            with open(self.path) as f:
                data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
            self.data = merged
        except (OSError, ValueError):
            pass
        return True

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.mtime = os.path.getmtime(self.path)
        except OSError as exc:
            log(f'保存配置失败: {exc!r}')

    def __getitem__(self, key):
        return self.data.get(key, DEFAULT_SETTINGS.get(key))


# ---------------------------------------------------------------- HID (LCD)

def find_hidraw():
    for name in sorted(os.listdir('/sys/class/hidraw')):
        try:
            uevent = open(f'/sys/class/hidraw/{name}/device/uevent').read()
        except OSError:
            continue
        m = re.search(r'HID_ID=0003:([0-9A-F]{8}):([0-9A-F]{8})', uevent)
        if m and int(m.group(1), 16) == HID_VID and int(m.group(2), 16) == HID_PID:
            return f'/dev/{name}'
    return None


def pkt(cmd, sub, data=b'\x00\x00\x00\x00'):
    body = bytes([0, 0, cmd, sub]) + bytes(data).ljust(4, b'\x00')[:4]
    return body + bytes([(255 - sum(body)) & 0xFF])


class Lcd:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def send(self, cmd, sub, data=b'\x00\x00\x00\x00'):
        fcntl.ioctl(self.fd, HID_SET_FEATURE_9, pkt(cmd, sub, data))

    def read(self):
        buf = bytearray(9)
        try:
            fcntl.ioctl(self.fd, HID_GET_FEATURE_9, buf, True)
        except OSError:
            return None
        return bytes(buf)

    def init(self):
        self.send(0x88, 0)
        time.sleep(0.05)
        self.send(0x88, 1)


# ------------------------------------------------------------- GPIO (LEDs)

class Gpio:
    def __init__(self):
        self.chip = os.open('/dev/gpiochip0', os.O_RDONLY)
        self.lines = self._parse_pinctrl()
        self.handles = {}

    @staticmethod
    def _parse_pinctrl():
        """返回 {引脚名: (线号, PADCFG 值)}；读不到调试表时用实测兜底表"""
        table = {}
        base = '/sys/kernel/debug/pinctrl'
        try:
            for d in os.listdir(base):
                path = os.path.join(base, d, 'pins')
                if not os.path.exists(path):
                    continue
                for line in open(path, errors='ignore'):
                    m = re.match(r'pin \d+ \((\S+)\) (\d+):\S+ \S+ (0x[0-9a-fA-F]+)', line)
                    if m:
                        table[m.group(1)] = (int(m.group(2)), int(m.group(3), 16))
        except OSError:
            pass
        if not table:
            return {k: (v, 0) for k, v in FALLBACK_LINES.items()}
        return table

    def line(self, pad):
        item = self.lines.get(pad)
        return item[0] if item else None

    def padcfg(self, pad):
        item = self.lines.get(pad)
        return item[1] if item else None

    def led_candidates(self):
        """LED 特征引脚（PADCFG 为 0x840002xx / 0x840001xx），校准时的安全候选集"""
        out = []
        for pad, (line, cfg) in sorted(self.lines.items(), key=lambda kv: kv[1][0]):
            if (cfg & 0xFFFFFF00) in (0x84000200, 0x84000100):
                out.append({'pad': pad, 'line': line, 'cfg': cfg})
        return out

    def all_pins(self):
        return [{'pad': p, 'line': l, 'cfg': c}
                for p, (l, c) in sorted(self.lines.items(), key=lambda kv: kv[1][0])]

    def _handle(self, pad):
        line = self.line(pad)
        if line is None:
            return None
        if line in self.handles:
            return self.handles[line]
        req = bytearray(364)
        struct.pack_into('<I', req, 0, line)
        struct.pack_into('<I', req, 256, GPIO_REQ_OUTPUT)
        req[260] = 1
        req[324:331] = b'xkpanel'
        struct.pack_into('<I', req, 356, 1)
        try:
            fcntl.ioctl(self.chip, GPIO_GET_LINEHANDLE, req, True)
        except OSError:
            return None
        h = struct.unpack_from('<i', req, 360)[0]
        self.handles[line] = h
        return h

    def set(self, pad, on):
        """低电平点亮"""
        h = self._handle(pad)
        if h is None:
            return False
        data = bytearray(64)
        data[0] = 0 if on else 1
        fcntl.ioctl(h, GPIO_SET_LINEVALUES, data)
        return True


class RgbPwm(threading.Thread):
    """三原色灯软件 PWM：duty 0..1；仅对非全亮/全灭的通道做脉冲"""

    def __init__(self, gpio):
        super().__init__(daemon=True)
        self.gpio = gpio
        self.duty = {}
        self.lock = threading.Lock()
        self.running = True

    def set_color(self, channels, color):
        with self.lock:
            for key, duty in zip(('red', 'green', 'blue'), color):
                pad = channels[key]
                duty = max(0.0, min(1.0, duty))
                if duty in (0.0, 1.0):
                    self.duty.pop(pad, None)
                    self.gpio.set(pad, bool(duty))
                else:
                    self.duty[pad] = duty

    def set_pad_level(self, pad, level):
        """单脚亮度（0..1）：0/1 直接写电平，中间值交给 PWM 平滑"""
        level = max(0.0, min(1.0, level))
        with self.lock:
            if level in (0.0, 1.0):
                self.duty.pop(pad, None)
                self.gpio.set(pad, bool(level))
            else:
                self.duty[pad] = level

    def run(self):
        step_time = 1.0 / (PWM_HZ * PWM_STEPS)
        n = 0
        while self.running:
            with self.lock:
                active = list(self.duty.items())
            if not active:
                time.sleep(0.2)
                continue
            n = (n + 1) % PWM_STEPS
            for pad, duty in active:
                self.gpio.set(pad, (n / PWM_STEPS) < duty)
            time.sleep(step_time)


class LedGroups(threading.Thread):
    """灯效引擎：颜色 + 效果（常亮/呼吸/彩虹）+ 跑马灯 + 活动呼吸（正弦包络）"""

    TICK = 0.05

    def __init__(self, pwm, gpio, activity=None):
        super().__init__(daemon=True)
        self.pwm = pwm
        self.gpio = gpio
        self.activity = activity
        self.activity_pads = {}   # pad -> {'kind','key','base','last','start','active'}
        self._act_poll = 0.0
        self._disk_rate = {}
        self._net_rate = {}
        self.groups = {}          # name -> {channels, color, effect, speed}
        self.pulses = {}          # pad -> 过期时间（读写活动脉冲）
        self.marquee = None       # {'pads': [...], 'period': 秒}
        self.last = {}            # name -> 上次实际下发的颜色
        self.running = True
        self._t0 = time.time()

    def set(self, name, channels, color, effect='solid', speed=None):
        if effect not in EFFECTS and effect != 'blink':
            effect = 'solid'
        self.groups[name] = {'channels': channels, 'color': tuple(color),
                             'effect': effect, 'speed': speed or 3.0}

    def pulse(self, pad, seconds=0.35):
        if pad:
            self.pulses[pad] = time.time() + seconds

    def update_activity(self, specs):
        """主循环每秒给出当前需要呼吸提示的灯清单；不在清单内的立即交回常态控制"""
        wanted = {s['pad']: s for s in specs if s.get('pad')}
        for pad, spec in list(self.activity_pads.items()):
            if pad not in wanted:
                self.pwm.set_pad_level(pad, 1.0 if spec['base'] else 0.0)
                self.activity_pads.pop(pad, None)
        for pad, wanted_spec in wanted.items():
            spec = self.activity_pads.get(pad) or {'last': 0.0, 'start': time.time(), 'active': False}
            spec.update({'kind': wanted_spec['kind'], 'key': wanted_spec['key'],
                         'base': bool(wanted_spec['base'])})
            self.activity_pads[pad] = spec

    @staticmethod
    def rate_shape(rate_kb):
        """速率 -> (呼吸周期秒, 幅度)：约 0.5MB/s 舒缓小幅度，50MB/s 急促满幅"""
        if rate_kb <= 0:
            return None
        k = ((math.log10(max(rate_kb, 1.0)) - math.log10(128.0))
             / (math.log10(51200.0) - math.log10(128.0)))
        k = max(0.0, min(1.0, k))
        return 2.2 - 1.85 * k, 0.55 + 0.45 * k

    def _poll_activity(self, now):
        if not self.activity or not self.activity_pads or now - self._act_poll < 0.2:
            return
        self._act_poll = now
        kinds = {s['kind'] for s in self.activity_pads.values()}
        if 'disk' in kinds:
            self._disk_rate = self.activity.poll_disks()
        if 'net' in kinds:
            ifaces = sorted({s['key'] for s in self.activity_pads.values() if s['kind'] == 'net'})
            self._net_rate = self.activity.poll_net(ifaces)

    def _apply_activity(self, now):
        for pad, spec in list(self.activity_pads.items()):
            rate = (self._disk_rate.get(spec['key'], 0.0) if spec['kind'] == 'disk'
                    else self._net_rate.get(spec['key'], 0.0))
            shape = self.rate_shape(rate)
            if shape:
                spec['period'], spec['amp'] = shape
                if not spec.get('active'):
                    spec['active'] = True
                    spec['start'] = now          # 从 0 起脉，保证完整一个呼吸周期
                spec['last'] = now
            active = spec.get('active') and (now - spec['last'] <= spec.get('period', 1.6) * 0.6 + 0.25)
            if active:
                period = spec.get('period', 1.6)
                amp = spec.get('amp', 1.0)
                phase = ((now - spec['start']) / period) % 1.0
                level = amp * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
            else:
                spec['active'] = False
                level = 1.0 if spec['base'] else 0.0
            self.pwm.set_pad_level(pad, level)

    def set_marquee(self, pads, period=1.2):
        """跑马灯：pads 依次点亮循环流动；传空表示关闭"""
        pads = [p for p in (pads or []) if p]
        self.marquee = {'pads': pads, 'period': max(0.4, float(period))} if pads else None

    def clear_pulses(self):
        self.pulses.clear()

    def _effect_color(self, g, now):
        color = g['color']
        speed = max(0.4, g['speed'])
        phase = ((now - self._t0) / speed) % 1.0
        eff = g['effect']
        if eff == 'rainbow':
            return colorsys.hsv_to_rgb(phase, 1.0, 1.0)
        if eff == 'breath':
            k = 0.12 + 0.88 * (0.5 - 0.5 * math.cos(2 * math.pi * phase))
            return tuple(c * k for c in color)
        if eff == 'heartbeat':
            k = 1.0 if (phase < 0.10 or 0.20 < phase < 0.30) else 0.05
            return tuple(c * k for c in color)
        if eff == 'blink':
            return color if phase < 0.5 else (0.0, 0.0, 0.0)
        return color

    def run(self):
        while self.running:
            now = time.time()
            self._poll_activity(now)
            self._apply_activity(now)
            for name, g in list(self.groups.items()):
                color = self._effect_color(g, now)
                prev = self.last.get(name)
                if prev is None or max(abs(a - b) for a, b in zip(prev, color)) > 0.02:
                    self.pwm.set_color(g['channels'], color)
                    self.last[name] = color
            # 跑马灯：依次点亮
            if self.marquee:
                pads = self.marquee['pads']
                phase = ((now - self._t0) / self.marquee['period']) % 1.0
                idx = min(len(pads) - 1, int(phase * len(pads)))
                for i, pad in enumerate(pads):
                    self.gpio.set(pad, i == idx)
            # 读写活动脉冲：以 5Hz 闪烁
            if self.pulses:
                on = (int(now * 5) % 2) == 0
                for pad in [p for p, exp in self.pulses.items() if exp < now]:
                    self.pulses.pop(pad, None)
                    self.gpio.set(pad, False)
                for pad in list(self.pulses):
                    self.gpio.set(pad, on)
            time.sleep(self.TICK)


class Activity:
    """硬盘读写与网口流量速率检测（差分，单位 KB/s）"""

    def __init__(self):
        self.disk_prev = {}
        self.net_prev = {}
        self._t = time.monotonic()

    def _elapsed(self):
        now = time.monotonic()
        dt = max(0.05, now - self._t)
        self._t = now
        return dt

    def poll_disks(self):
        """返回 {设备名: KB/s}"""
        rates = {}
        dt = self._elapsed()
        try:
            for line in open('/proc/diskstats'):
                f = line.split()
                if len(f) < 14:
                    continue
                name = f[2]
                if not re.fullmatch(r'sd[a-z]', name):
                    continue
                sectors = int(f[5]) + int(f[9])
                prev = self.disk_prev.get(name)
                self.disk_prev[name] = sectors
                if prev is not None:
                    rates[name] = max(0.0, (sectors - prev) * 512 / 1024 / dt)
        except (OSError, ValueError, IndexError):
            pass
        return rates

    def poll_net(self, ifaces):
        """返回 {网口名: KB/s}"""
        rates = {}
        dt = self._elapsed()
        for name in ifaces:
            try:
                rx = int(open(f'/sys/class/net/{name}/statistics/rx_bytes').read())
                tx = int(open(f'/sys/class/net/{name}/statistics/tx_bytes').read())
            except (OSError, ValueError):
                continue
            prev = self.net_prev.get(name)
            self.net_prev[name] = (rx, tx)
            if prev is not None:
                rates[name] = max(0.0, ((rx - prev[0]) + (tx - prev[1])) / 1024 / dt)
        return rates


class Ec:
    def __init__(self):
        try:
            self.fd = os.open('/dev/port', os.O_RDWR)
        except OSError:
            self.fd = None
        self.ctrl_set = False
        self.ctrl_time = 0.0
        self.reassert_sec = 10.0      # EC 会周期性夺回自动模式，需定期重申手动模式
        self.orig = None
        if self.fd is not None:
            self._bank0()
            try:
                self.orig = (self.rd(FAN_CTRL_REG), self.rd(FAN_DUTY_REG))
            except OSError:
                self.orig = None

    @property
    def available(self):
        return self.fd is not None

    def handback(self):
        """交还给 BIOS/EC 的自动风扇控制：恢复原值并置回自动模式位"""
        if self.fd is None:
            return False
        try:
            if self.orig:
                self.wr(FAN_DUTY_REG, self.orig[1])
                self.wr(FAN_CTRL_REG, self.orig[0])
            cur = self.rd(FAN_CTRL_REG)
            self.wr(FAN_CTRL_REG, cur | 0x80)      # 置 bit7 = 交还自动模式
            self.ctrl_set = False
            log('风扇已交还 BIOS 自动控制')
            return True
        except OSError:
            return False

    def restore(self):
        """恢复接管前的 EC 风扇控制状态（避免把风扇留在手动模式）"""
        if self.fd is None or self.orig is None or not self.ctrl_set:
            return
        try:
            self.wr(FAN_DUTY_REG, self.orig[1])
            self.wr(FAN_CTRL_REG, self.orig[0])
            self.ctrl_set = False
            log('已恢复 EC 风扇原始控制状态')
        except OSError:
            pass

    def _bank0(self):
        try:
            os.pwrite(self.fd, bytes([0x06]), EC_ADDR)
            os.pwrite(self.fd, bytes([0x00]), EC_DATA)
        except OSError:
            pass

    def rd(self, idx):
        os.pwrite(self.fd, bytes([idx]), EC_ADDR)
        return os.pread(self.fd, 1, EC_DATA)[0]

    def wr(self, idx, val):
        os.pwrite(self.fd, bytes([idx]), EC_ADDR)
        os.pwrite(self.fd, bytes([val]), EC_DATA)

    def set_fan_duty(self, percent):
        """写占空比；EC 会周期性回到自动模式，因此按 reassert_sec 定期重申手动模式"""
        now = time.monotonic()
        if not self.ctrl_set or now - self.ctrl_time >= self.reassert_sec:
            if not self._ensure_manual():
                return None
            self.ctrl_time = now
        raw = max(0, min(100, int(percent))) * 255 // 100
        cur = self.rd(FAN_DUTY_REG)
        if abs(cur - raw) >= 5:
            self.wr(FAN_DUTY_REG, raw)
            return raw
        return cur

    def _ensure_manual(self):
        """切到手动模式：IT8613E 的 PWM 控制寄存器 bit7=自动模式开关，手动需清 bit7
        （依据 lm-sensors it87 驱动的 set_pwm_enable：manual = temp_map & 0x7f）"""
        if self.ctrl_set:
            return True
        try:
            cur = self.rd(FAN_CTRL_REG)
            self.wr(FAN_CTRL_REG, cur & 0x7F)     # 清 bit7 = 手动占空比模式
            self.ctrl_set = True
            self.ctrl_time = time.monotonic()
            return True
        except OSError:
            return False


# --------------------------------------------------------------- 数据采集

def read_temp(pattern):
    try:
        for h in sorted(os.listdir('/sys/class/hwmon')):
            try:
                if pattern not in open(f'/sys/class/hwmon/{h}/name').read():
                    continue
            except OSError:
                continue
            best = None
            for f in sorted(os.listdir(f'/sys/class/hwmon/{h}')):
                if re.fullmatch(r'temp\d+_input', f):
                    v = int(open(f'/sys/class/hwmon/{h}/{f}').read()) / 1000
                    best = v if best is None else max(best, v)
            if best is not None:
                return int(round(best))
    except OSError:
        pass
    return None


def hdd_temps():
    temps = []
    try:
        for h in sorted(os.listdir('/sys/class/hwmon')):
            try:
                if 'drivetemp' not in open(f'/sys/class/hwmon/{h}/name').read():
                    continue
            except OSError:
                continue
            try:
                temps.append(int(open(f'/sys/class/hwmon/{h}/temp1_input').read()) // 1000)
            except OSError:
                temps.append(0)
    except OSError:
        pass
    return temps[:4]


def storage_usage(target='all'):
    """(总容量GB, 已用GB, 可用GB)；target=all 时统计所有数据卷，否则只看指定卷"""
    vols = []
    for vol in sorted(os.listdir('/')):
        if not re.fullmatch(r'vol\d+', vol):
            continue
        if target and target != 'all' and target != vol:
            continue
        vols.append(vol)
    total = used = free = 0
    for vol in vols:
        try:
            st = os.statvfs(f'/{vol}')
        except OSError:
            continue
        bs = st.f_frsize
        total += st.f_blocks * bs
        free += st.f_bavail * bs
        used += (st.f_blocks - st.f_bfree) * bs
    return total // 2**30, used // 2**30, free // 2**30


def list_storage():
    """所有数据卷及容量，供设置页选择"""
    out = []
    for vol in sorted(os.listdir('/')):
        if not re.fullmatch(r'vol\d+', vol):
            continue
        try:
            st = os.statvfs(f'/{vol}')
        except OSError:
            continue
        bs = st.f_frsize
        total = st.f_blocks * bs
        free = st.f_bavail * bs
        out.append({'name': vol, 'total_gb': total // 2**30,
                    'free_gb': free // 2**30,
                    'free_pct': round(free * 100 / total) if total else 0})
    out.append({'name': 'all', 'label': '全部合计'})
    return out


def net_info():
    out = []
    try:
        route = subprocess.run(['ip', '-4', 'route', 'show', 'default'],
                               capture_output=True, text=True, timeout=3).stdout
        m = re.search(r'default via (\d+\.\d+\.\d+\.\d+)', route)
        gw = [int(x) for x in m.group(1).split('.')] if m else [0, 0, 0, 0]
        addrs = subprocess.run(['ip', '-4', '-o', 'addr', 'show'],
                               capture_output=True, text=True, timeout=3).stdout
        found = []
        for line in addrs.splitlines():
            m = re.match(r'\d+: (\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/(\d+)', line)
            if not m or m.group(1) == 'lo':
                continue
            plen = int(m.group(3))
            mask = [0, 0, 0, 0]
            for i in range(4):
                bits = max(0, min(8, plen - i * 8))
                mask[i] = (0xFF << (8 - bits)) & 0xFF
            found.append(([int(x) for x in m.group(2).split('.')], mask))
        for ip, mask in found[:2]:
            out.append((ip, mask, gw))
    except Exception:
        pass
    return out


def physical_ifaces():
    skip = ('lo', 'docker', 'veth', 'br-', 'virbr', 'tun', 'tap', 'wg', 'tailscale', 'zt')
    names = []
    try:
        for name in os.listdir('/sys/class/net'):
            if name.startswith(skip):
                continue
            if not os.path.exists(f'/sys/class/net/{name}/device') and not name.startswith(('eth', 'en')):
                continue
            names.append(name)
    except OSError:
        pass
    return sorted(names)[:2]


def lan_link_states():
    states = {}
    for i, name in enumerate(physical_ifaces()):
        port = LAN_PORT_MAP.get(name, i + 1)
        try:
            states[port] = open(f'/sys/class/net/{name}/carrier').read().strip() == '1'
        except OSError:
            states[port] = False
    return states.get(1, False), states.get(2, False)


def disk_states():
    """返回 (每个盘位的 (在位, 故障), 是否重建中, 重建百分比, 是否降级)"""
    present = set()
    try:
        for name in os.listdir('/sys/block'):
            if re.fullmatch(r'sd[a-z]', name):
                present.add(name)
    except OSError:
        pass
    fault = set()
    rebuild, rebuild_pct, degraded = False, None, False
    try:
        mdstat = open('/proc/mdstat').read()
        for m in re.finditer(r'\b(sd[a-z])\d*\[\d+\]\*?\(F\)', mdstat):
            fault.add(m.group(1))
        rebuild = bool(re.search(r'recovery|resync|reshape|reconstruct', mdstat))
        m = re.search(r'(?:recovery|resync)\s*=\s*([\d.]+)%', mdstat)
        if m:
            rebuild_pct = float(m.group(1))
        # 降级：[n/m] 中 m<n，或状态串里出现 _
        degraded = bool(re.search(r'\[(\d+)/(\d+)\]\s*\[[U_]*_[U_]*\]', mdstat)) or \
            bool(re.search(r'\[\d+/\d+\]\s*\[[^\]]*_[^\]]*\]', mdstat))
        if rebuild:
            degraded = True
    except OSError:
        pass
    # 降级且缺盘时，把缺失的盘位判为故障，便于红灯提示
    if degraded and not rebuild and present and len(present) < 4:
        for dev in ('sda', 'sdb', 'sdc', 'sdd'):
            if dev not in present:
                fault.add(dev)
    return [(dev in present, dev in fault) for dev in ('sda', 'sdb', 'sdc', 'sdd')], rebuild, rebuild_pct, degraded


def usb_disk_present():
    try:
        for name in os.listdir('/sys/block'):
            if re.fullmatch(r'sd[a-z]', name) and '/usb' in os.path.realpath(f'/sys/block/{name}'):
                return True
    except OSError:
        pass
    return False


# ------------------------------------------------------- 状态评分与配色

class LoadMonitor:
    """CPU / GPU 占用采集（差分 + EMA 平滑），内存取 MemAvailable 比例"""

    def __init__(self, alpha=0.15):
        self.alpha = alpha
        self.cpu = None
        self.gpu = None
        self._prev_cpu = None
        self._prev_rc6 = None

    def _ema(self, old, new):
        if new is None:
            return old
        return new if old is None else old * (1 - self.alpha) + new * self.alpha

    def poll(self, alpha=None):
        if alpha:
            self.alpha = alpha
        return self.cpu_util(), self.gpu_busy(), mem_available_ratio()

    def cpu_util(self):
        try:
            vals = [int(x) for x in open('/proc/stat').readline().split()[1:]]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
        except (OSError, ValueError, IndexError):
            return self.cpu
        if self._prev_cpu:
            di = idle - self._prev_cpu[0]
            dt = total - self._prev_cpu[1]
            if dt > 0:
                self.cpu = self._ema(self.cpu, max(0.0, min(1.0, 1 - di / dt)))
        self._prev_cpu = (idle, total)
        return self.cpu

    def gpu_busy(self):
        """优先用 rc6 驻留时间算占用，取不到时用频率比例兜底"""
        path = rc6_path()
        if path:
            try:
                rc6 = int(open(path).read())
                now = time.monotonic()
                if self._prev_rc6:
                    d_rc6 = rc6 - self._prev_rc6[0]
                    dt = (now - self._prev_rc6[1]) * 1000
                    if dt > 0:
                        self.gpu = self._ema(self.gpu, max(0.0, min(1.0, 1 - d_rc6 / dt)))
                self._prev_rc6 = (rc6, now)
                return self.gpu
            except (OSError, ValueError):
                pass
        try:
            cur = int(open('/sys/class/drm/card1/gt_act_freq_mhz').read())
            lo = int(open('/sys/class/drm/card1/gt_min_freq_mhz').read())
            hi = int(open('/sys/class/drm/card1/gt_max_freq_mhz').read())
            if hi > lo:
                self.gpu = self._ema(self.gpu, max(0.0, min(1.0, (cur - lo) / (hi - lo))))
        except (OSError, ValueError):
            pass
        return self.gpu


def _drm_cards():
    try:
        return [d for d in os.listdir('/sys/class/drm') if re.fullmatch(r'card\d+', d)]
    except OSError:
        return []


def rc6_path():
    for c in _drm_cards():
        p = f'/sys/class/drm/{c}/gt/gt0/rc6_residency_ms'
        if os.path.exists(p):
            return p
    for c in _drm_cards():
        p = f'/sys/class/drm/{c}/power/rc6_residency_ms'
        if os.path.exists(p):
            return p
    return None


def thermal_throttled():
    """GPU 是否因温度而节流"""
    for c in _drm_cards():
        p = f'/sys/class/drm/{c}/gt/gt0/throttle_reason_thermal'
        try:
            if int(open(p).read().strip()) != 0:
                return True
        except (OSError, ValueError):
            continue
    return False


def mem_available_ratio():
    try:
        info = {}
        for line in open('/proc/meminfo'):
            k, _, v = line.partition(':')
            info[k] = int(v.split()[0])
        total = info.get('MemTotal', 0)
        return (info.get('MemAvailable', total) / total) if total else 1.0
    except (OSError, ValueError, IndexError):
        return 1.0


def temp_score(temp, green, red):
    """温度 -> 0..1（越低越健康）"""
    if temp is None:
        return 1.0
    if temp <= green:
        return 1.0
    if temp >= red:
        return 0.0
    return 1.0 - (temp - green) / (red - green)


def gradient_color(score):
    """红(0) -> 黄(0.5) -> 绿(1) 连续渐变，返回 (r, g, b)"""
    score = max(0.0, min(1.0, score))
    if score < 0.5:
        k = score / 0.5
        r, g = 1.0, k
    else:
        k = (score - 0.5) / 0.5
        r, g = 1.0 - k, 1.0
    return (r, g, 0.0)


def combine_score(temp_s, load_s, settings):
    """加权几何平均：保留木桶效应（相乘），但不让单项轻微变化就把总分拉到 0"""
    wt = max(0.05, min(0.95, settings['w_temp']))
    return (max(temp_s, 1e-6) ** wt) * (max(load_s, 1e-6) ** (1 - wt))


def health_components(cpu, hdd_max, cpu_util, gpu_busy, mem_ratio, slots,
                      rebuild, settings, throttled=False, degraded=False):
    """整机健康分 = 温度分^w × 性能分^(1-w)；不含阵列/硬盘状态（那由状态灯表达）"""
    """返回 (健康分, 温度分, 性能分, 明细 dict)"""
    t_cpu = temp_score(cpu, settings['cpu_temp_green'], settings['cpu_temp_red'])
    t_hdd = temp_score(hdd_max, settings['hdd_temp_green'], settings['hdd_temp_red'])
    temp_s = min(t_cpu, t_hdd)                    # 最热点决定（木桶）

    parts, weights = [], []
    detail = {}
    if cpu_util is not None:
        s = 1.0 - min(1.0, cpu_util / max(0.1, settings['load_cpu_ref']))
        parts.append(s); weights.append(settings['w_cpu']); detail['cpu_util'] = cpu_util
    if gpu_busy is not None:
        s = 1.0 - min(1.0, gpu_busy / max(0.1, settings['load_gpu_ref']))
        parts.append(s); weights.append(settings['w_gpu']); detail['gpu_busy'] = gpu_busy
    if mem_ratio is not None:
        ok = max(0.05, settings['mem_ok_ratio'])
        s = 1.0 if mem_ratio >= ok else mem_ratio / ok
        parts.append(s); weights.append(settings['w_mem']); detail['mem_ratio'] = mem_ratio
    load_s = (sum(p * w for p, w in zip(parts, weights)) / sum(weights)) if parts else 1.0

    score = combine_score(temp_s, load_s, settings)

    # 阵列 / 硬盘状态只由状态灯表达，不影响电源键灯
    if throttled:
        score = min(score, settings['throttle_cap'])
    detail['temp_score'] = temp_s
    detail['load_score'] = load_s
    return score, temp_s, load_s, detail


def status_color(cpu, hdd_max, cpu_util, gpu_busy, mem_ratio, slots, rebuild, settings,
                 throttled, degraded=False):
    """电源键颜色 = 健康分（温度分 × 性能分）的渐变"""
    score, temp_s, load_s, detail = health_components(
        cpu, hdd_max, cpu_util, gpu_busy, mem_ratio, slots, rebuild, settings, throttled, degraded)
    if settings['status_light'] == 'gradient':
        color = gradient_color(score)
    else:
        color = gradient_color(round(score * 2) / 2)
    return color, score, temp_s, load_s, detail


def storage_color(total, free, rebuild, any_fault, settings, degraded=False):
    """存储灯：容量健康度渐变（绿↔黄↔红），故障/重建/降级闪烁
    返回 (颜色, 是否闪烁)
    """
    blink = settings['fault_blink']
    if any_fault:
        return (1.0, 0.0, 0.0), blink             # 红闪：硬盘故障或缺失
    if rebuild:
        return (1.0, 0.75, 0.0), blink            # 橙闪：阵列重建中
    if degraded:
        return (1.0, 0.55, 0.0), blink            # 橙闪：阵列降级
    if not total:
        return (0.0, 0.0, 0.2), False
    ratio = free / total * 100
    green = max(1, settings['cap_green_free'])
    red = max(0, min(settings['cap_red_free'], green - 1))
    score = (ratio - red) / max(1, green - red)
    return gradient_color(score), False


def apply_fan(temp, settings, ec, night, state):
    """按温度曲线调风扇：温度 EMA 平滑 + 5% 量化 + 15 秒冷却（设置变化立即生效）"""
    if not settings['fan_enabled'] or not ec.available:
        return None
    if temp is not None:
        prev = state.get('temp_ema')
        state['temp_ema'] = temp if prev is None else prev * 0.7 + temp * 0.3
    temp_s = state.get('temp_ema')

    preset = settings['fan_preset']
    curve = settings['fan_curve'] if preset == 'custom' and settings['fan_curve'] \
        else FAN_PRESETS.get(preset, FAN_PRESETS['standard'])
    duty = curve[0][1]
    if temp_s is not None:
        for i in range(len(curve) - 1):
            t0, d0 = curve[i]
            t1, d1 = curve[i + 1]
            if t0 <= temp_s <= t1:
                k = (temp_s - t0) / (t1 - t0) if t1 > t0 else 0
                duty = d0 + (d1 - d0) * k
                break
            if temp_s > t1:
                duty = d1
    duty = max(settings['fan_min_duty'], duty)
    if night:
        duty = min(duty, settings['night_fan_max'])
    duty = int(round(duty / 5.0) * 5)          # 量化到 5% 档

    now = time.time()
    cfg_sig = (settings['fan_preset'], settings['fan_min_duty'],
               settings['night_fan_max'], bool(night), int(settings['fan_enabled']))
    settings_changed = state.get('cfg_sig') != cfg_sig
    if not settings_changed and (state.get('duty_target') == duty
                                 or now - state.get('duty_time', 0) < 15):
        # 即使占空比未变，也按 EC 的重申周期写一次，避免被夺回自动模式
        state['duty_raw'] = ec.set_fan_duty(state.get('duty_target', duty))
        return state['duty_raw']
    state['cfg_sig'] = cfg_sig
    state['duty_target'] = duty
    state['duty_time'] = now
    state['duty_raw'] = ec.set_fan_duty(duty)
    return state['duty_raw']


class Latch:
    """异常状态锁存：置位后保持一段时间，避免阵列过渡期状态抖动导致灯色乱跳"""

    def __init__(self, hold=5.0):
        self.hold = hold
        self.until = 0.0

    def update(self, flag, now=None):
        now = now or time.time()
        if flag:
            self.until = now + self.hold
        return now < self.until

    @property
    def active(self):
        return time.time() < self.until


def in_night(start, end, now=None):
    now = now or time.localtime()
    cur = now.tm_hour * 60 + now.tm_min
    h0, m0 = (int(x) for x in start.split(':'))
    h1, m1 = (int(x) for x in end.split(':'))
    a, b = h0 * 60 + m0, h1 * 60 + m1
    if a == b:
        return False
    return (a <= cur < b) if a < b else (cur >= a or cur < b)


# ------------------------------------------------------------------- Web UI

import http.server
import socketserver


class State:
    """主循环与 Web UI 的共享状态"""

    def __init__(self):
        self.lock = threading.Lock()
        self.status = {}
        self.overrides = {}      # 目标名 -> (颜色, 过期时间)
        self.pins = {}
        self.led_candidates = []
        self.all_pins = []
        self.probe = None        # {'list': [...], 'index': n, 'next': ts, 'seconds': s}
        self.request_fan_handback = False

    def start_probe(self, pads, seconds):
        with self.lock:
            self.probe = {'list': pads, 'index': 0, 'next': 0.0, 'seconds': seconds}

    def stop_probe(self):
        with self.lock:
            self.probe = None

    def current_probe(self):
        with self.lock:
            return dict(self.probe) if self.probe else None

    def set_status(self, **kw):
        with self.lock:
            self.status.update(kw)

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def set_override(self, target, color, seconds):
        with self.lock:
            self.overrides[target] = (tuple(color), time.time() + seconds)

    def active_overrides(self):
        now = time.time()
        with self.lock:
            for pad in [p for p, (_, exp) in self.overrides.items() if exp < now]:
                self.overrides.pop(pad, None)
            return dict(self.overrides)


class WebUi(threading.Thread):
    def __init__(self, state, settings, port, app_dir):
        super().__init__(daemon=True)
        self.state = state
        self.settings = settings
        self.port = port
        self.app_dir = app_dir

    def run(self):
        state, settings, app_dir = self.state, self.settings, self.app_dir

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = 'xk-panel'

            def log_message(self, *args):
                pass

            def _send(self, code, body, ctype='application/json; charset=utf-8'):
                data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                try:
                    n = int(self.headers.get('Content-Length', 0))
                    return json.loads(self.rfile.read(n) or b'{}')
                except (ValueError, OSError):
                    return {}

            def do_GET(self):
                if self.path in ('/', '/index.html'):
                    try:
                        html = open(os.path.join(app_dir, 'ui.html'), 'rb').read()
                    except OSError:
                        self._send(500, {'error': 'ui.html 缺失'})
                        return
                    self._send(200, html, 'text/html; charset=utf-8')
                elif self.path == '/api/status':
                    self._send(200, state.get_status())
                elif self.path == '/api/settings':
                    self._send(200, {'settings': settings.data, 'defaults': DEFAULT_SETTINGS})
                elif self.path == '/api/storage':
                    self._send(200, {'volumes': list_storage()})
                elif self.path == '/api/pins':
                    self._send(200, {'pins': state.pins})
                elif self.path == '/api/pads':
                    self._send(200, {
                        'led_candidates': state.led_candidates,
                        'all_pins': state.all_pins,
                        'roles': PIN_ROLES,
                        'current': pin_map(settings),
                        'defaults': DEFAULT_PIN_MAP,
                    })
                else:
                    self._send(404, {'error': 'not found'})

            def do_POST(self):
                body = self._body()
                if self.path == '/api/settings':
                    with settings_lock:
                        settings.load(force=True)
                        for k, v in body.items():
                            if k in DEFAULT_SETTINGS:
                                settings.data[k] = v
                        settings.save()
                    self._send(200, {'ok': True, 'settings': settings.data})
                elif self.path == '/api/defaults':
                    with settings_lock:
                        settings.data = dict(DEFAULT_SETTINGS)
                        settings.save()
                    self._send(200, {'ok': True, 'settings': settings.data})
                elif self.path == '/api/test':
                    target = body.get('target')
                    color = body.get('color')
                    seconds = float(body.get('seconds', 3))
                    if body.get('off'):
                        color = [0, 0, 0]
                    if isinstance(color, str):
                        color = hex_to_rgb(color)
                    if target and color:
                        state.set_override(target, color, seconds)
                        self._send(200, {'ok': True})
                    else:
                        self._send(400, {'error': 'need target & color'})
                elif self.path == '/api/test_all':
                    seconds = float(body.get('seconds', 4))
                    state.set_override('power', (1.0, 1.0, 1.0), seconds)
                    state.set_override('store', (1.0, 1.0, 1.0), seconds)
                    for i in range(4):
                        state.set_override(f'disk{i}g', (0.0, 1.0, 0.0), seconds)
                        state.set_override(f'disk{i}r', (1.0, 0.0, 0.0), seconds)
                    state.set_override('usb', (1.0, 1.0, 1.0), seconds)
                    state.set_override('lan1', (1.0, 1.0, 1.0), seconds)
                    state.set_override('lan2', (1.0, 1.0, 1.0), seconds)
                    self._send(200, {'ok': True})
                elif self.path == '/api/clear_test':
                    with state.lock:
                        state.overrides.clear()
                    self._send(200, {'ok': True})
                elif self.path == '/api/pin_map':
                    with settings_lock:
                        settings.load(force=True)
                        pm = dict(settings.data.get('pin_map') or {})
                        for k, v in (body.get('pin_map') or {}).items():
                            if k in DEFAULT_PIN_MAP and isinstance(v, str) and v:
                                pm[k] = v
                        settings.data['pin_map'] = pm
                        settings.save()
                    self._send(200, {'ok': True, 'pin_map': pin_map(settings)})
                elif self.path == '/api/pin_map_reset':
                    with settings_lock:
                        settings.load(force=True)
                        settings.data['pin_map'] = {}
                        settings.save()
                    self._send(200, {'ok': True, 'pin_map': pin_map(settings)})
                elif self.path == '/api/probe':
                    scope = body.get('scope', 'leds')
                    pads = [x['pad'] for x in (state.led_candidates if scope == 'leds'
                                               else state.all_pins)]
                    seconds = float(body.get('seconds') or settings['probe_seconds'])
                    state.start_probe(pads, seconds)
                    self._send(200, {'ok': True, 'count': len(pads)})
                elif self.path == '/api/probe_stop':
                    state.stop_probe()
                    with state.lock:
                        state.overrides.clear()
                    self._send(200, {'ok': True})
                elif self.path == '/api/fan_restore':
                    with settings_lock:
                        settings.load(force=True)
                        settings.data['fan_enabled'] = False
                        settings.save()
                    state.request_fan_handback = True
                    self._send(200, {'ok': True})
                else:
                    self._send(404, {'error': 'not found'})

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        try:
            with Server(('0.0.0.0', self.port), Handler) as httpd:
                log(f'设置页: http://<NAS 地址>:{self.port}/')
                httpd.serve_forever()
        except OSError as exc:
            log(f'设置页启动失败: {exc!r}')


def hex_to_rgb(text):
    text = text.strip().lstrip('#')
    if len(text) == 3:
        text = ''.join(c * 2 for c in text)
    try:
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.0)


def scale_color(color, percent):
    k = max(0, min(100, percent)) / 100.0
    return tuple(c * k for c in color)


# ------------------------------------------------------------------- 主循环

ALLOW_POWER_ACTIONS = os.environ.get('XK_PANEL_ALLOW_POWER', '1') == '1'


def run_cmd(argv):
    try:
        subprocess.Popen(argv)
    except Exception as exc:
        log(f'执行 {argv} 失败: {exc!r}')


def handle_report(report):
    """上行动作：按厂商定义 [3]=命令，[4]=子动作"""
    cmd, act = report[3], report[4]
    log(f'设备上报: {report.hex(" ")} (cmd={cmd} act={act})')
    if not ALLOW_POWER_ACTIONS:
        return
    if cmd == 1 and act == 0:
        log('屏幕请求关机')
        run_cmd(['/sbin/shutdown', '-h', 'now'])
    elif cmd == 1 and act == 1:
        log('屏幕请求重启')
        run_cmd(['/sbin/reboot'])
    elif cmd == 4 and act == 1:
        log('屏幕请求弹出 USB')
        run_cmd(['/bin/bash', '-c', 'eject $(ls /dev/sd* 2>/dev/null | head -1) 2>/dev/null || true'])


def boot_animation(leds, colors=((1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)), dwell=0.35):
    """开机自检动画：电源键与状态灯依次亮起红绿蓝白"""
    groups = leds.groups
    for color in colors:
        for name, g in list(groups.items()):
            leds.pwm.set_color(g['channels'], color)
        time.sleep(dwell)
    for name, g in list(groups.items()):
        leds.pwm.set_color(g['channels'], (0, 0, 0))
    time.sleep(0.15)


def main():
    settings_path = os.environ.get('XK_PANEL_CONFIG', '/vol1/@appdata/xk-panel/settings.json')
    settings = Settings(settings_path)
    log(f'配置: {settings_path}')

    state = State()
    app_dir = os.path.dirname(os.path.abspath(__file__))

    hid_path = find_hidraw()
    if not hid_path:
        log('未找到面板 HID 设备（VID 374a PID b101）')
        return 1
    lcd = Lcd(hid_path)
    lcd.init()
    log(f'LCD 设备 {hid_path} 已初始化')

    gpio = Gpio()
    log(f'引脚表: {len(gpio.lines)} 项')
    state.pins = {k: v[0] for k, v in gpio.lines.items()}
    state.led_candidates = gpio.led_candidates()
    state.all_pins = gpio.all_pins()
    pwm = RgbPwm(gpio)
    pwm.start()
    leds = LedGroups(pwm, gpio, Activity())
    leds.start()

    ec = Ec()
    ec.reassert_sec = max(2.0, float(settings['fan_reassert_sec']))
    log(f'EC 风扇: {"可用" if ec.available else "不可用"}（默认不接管）')

    WebUi(state, settings, int(settings['web_port']), app_dir).start()

    import atexit
    import signal

    def cleanup(*_a):
        ec.restore()
        pwm.running = False
        leds.running = False
        os._exit(0)

    atexit.register(ec.restore)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    mon = LoadMonitor()
    activity = Activity()
    latch_fault, latch_degraded, latch_rebuild = Latch(5.0), Latch(5.0), Latch(5.0)
    if settings['boot_effect']:
        power_rgb, store_rgb, _, _, disk_pads = led_structure(settings)
        leds.set('power', power_rgb, (0, 0, 0))
        leds.set('store', store_rgb, (0, 0, 0))
        boot_animation(leds)
        if settings['disk_marquee'] in ('boot', 'rebuild', 'always'):
            leds.set_marquee([d['green'] for d in disk_pads], 1.0)
            time.sleep(1.3)
            leds.set_marquee(None)
            log('盘位灯跑马灯自检完成')
        log('开机动画完成')

    last_up = None
    tick = 0
    fan_state = {}
    lcd_last = 0.0
    while True:
        try:
            if tick % 5 == 0:
                settings.load()

            cpu = read_temp('coretemp') or read_temp('k10temp') or 0
            sys_t = read_temp('acpitz') or cpu
            hdds = hdd_temps()
            hdd_max = max([t for t in hdds if t] or [0]) or None
            cpu_util, gpu_busy, mem_ratio = mon.poll(alpha=settings['smooth'])

            slots, rebuild, rebuild_pct, degraded_raw = disk_states()
            any_fault_raw = any(bad for _, bad in slots)
            any_fault = latch_fault.update(any_fault_raw)
            degraded = latch_degraded.update(degraded_raw or rebuild)
            rebuild = latch_rebuild.update(rebuild)
            total, used, free = storage_usage(settings['storage_target'])
            free_pct = (free * 100 // total) if total else 0

            # ---- 屏幕刷新 ----
            now = time.time()
            if settings['lcd_enabled'] and now - lcd_last >= max(1, int(settings['lcd_interval'])):
                lcd_last = now
                if settings['lcd_temp']:
                    lcd.send(0x03, 2, bytes([sys_t & 0xFF]))
                    lcd.send(0x03, 1, bytes([cpu & 0xFF]))
                    for i in range(4):
                        lcd.send(0x03, 3 + i, bytes([(hdds[i] if i < len(hdds) else 0) & 0xFF]))
                if settings['lcd_storage']:
                    lcd.send(0x02, 1, total.to_bytes(4, 'big'))
                    lcd.send(0x02, 3, used.to_bytes(4, 'big'))
                    lcd.send(0x02, 2, free.to_bytes(4, 'big'))
                if settings['lcd_network']:
                    nets = net_info()
                    for idx, (ip, mask, gw) in enumerate(nets[:2]):
                        ones = [1, 2][idx]
                        lcd.send(0x00, ones, bytes(ip))
                        lcd.send(0x00, 0x80 | ones, bytes(mask))
                        lcd.send(0x00, 0x40 | ones, bytes(gw))
                    if nets:
                        lcd.send(0x08, 1, bytes(nets[0][0]))

            # ---- 逐脚探测模式（校准用）：只点亮当前引脚 ----
            probe = state.current_probe()
            probing = bool(probe and probe['list'])
            if probing and time.time() >= probe['next']:
                idx = probe['index']
                if idx >= len(probe['list']):
                    state.stop_probe()
                    probing = False
                    with state.lock:
                        state.overrides.clear()
                else:
                    for pad in probe['list']:
                        gpio.set(pad, False)
                    gpio.set(probe['list'][idx], True)
                    state.set_status(probe={'pad': probe['list'][idx],
                                            'index': idx + 1, 'total': len(probe['list'])})
                    with state.lock:
                        if state.probe:
                            state.probe['index'] = idx + 1
                            state.probe['next'] = time.time() + probe['seconds']

            power_rgb, store_rgb, usb_pad, lan_pads, disk_pads = led_structure(settings)

            # ---- 状态灯 ----
            throttled = thermal_throttled()
            power_color, score, temp_s, load_s, detail = status_color(
                cpu, hdd_max, cpu_util, gpu_busy, mem_ratio, slots, rebuild, settings,
                throttled, degraded)
            if settings['power_led'] == 'off':
                power_color = (0.0, 0.0, 0.0)
            elif settings['power_led'] == 'fixed':
                power_color = hex_to_rgb(settings['power_fixed_color'])
            power_color = scale_color(power_color, settings['brightness'])

            if settings['store_led'] == 'off':
                store_color, store_blink = (0.0, 0.0, 0.0), False
            else:
                store_color, store_blink = storage_color(total, free, rebuild, any_fault,
                                                        settings, degraded)
                store_color = scale_color(store_color, settings['brightness'])

            night = settings['night_enabled'] and in_night(settings['night_start'], settings['night_end'])
            power_speed = float(settings['power_effect_speed'])
            store_speed = float(settings['store_effect_speed'])
            power_effect = settings['power_effect']
            store_effect = settings['store_effect']
            if store_blink:                      # 故障/重建：内部改用闪烁告警
                store_effect = 'blink'
                store_speed = min(store_speed, 1.0)
            if night:
                mode = settings['night_leds']
                if mode == 'off':
                    power_color = store_color = (0.0, 0.0, 0.0)
                elif mode == 'dim':
                    power_color = scale_color(power_color, 15)
                    store_color = scale_color(store_color, 15)
                power_effect = store_effect = settings['night_effect']

            ov0 = state.active_overrides()
            test_mode = probing or any(k.startswith('pin:') for k in ov0)
            if test_mode:                # 校准/单脚测试：屏蔽常态灯效，只亮显式测试目标
                power_color = store_color = (0.0, 0.0, 0.0)
                power_effect = store_effect = 'solid'
            if 'power' in ov0:
                power_color, power_effect = ov0['power'][0], 'solid'
            if 'store' in ov0:
                store_color, store_effect = ov0['store'][0], 'solid'
            leds.set('power', power_rgb, power_color, power_effect, power_speed)
            leds.set('store', store_rgb, store_color, store_effect, store_speed)

            # ---- 其他 LED ----
            if test_mode:
                leds.set_marquee(None)
                leds.update_activity([])
                probe_pad = None
                if probing and probe['list'] and probe['index'] > 0:
                    probe_pad = probe['list'][probe['index'] - 1]
                for pad in [usb_pad, lan_pads['lan1'], lan_pads['lan2']] + \
                           [d[c] for d in disk_pads for c in ('green', 'red')]:
                    if pad and pad != probe_pad:
                        gpio.set(pad, False)
                for key, (color, _exp) in ov0.items():
                    v = color[0] > 0.5
                    if key.startswith('pin:'):
                        gpio.set(key[4:], v)
                    elif key == 'usb':
                        gpio.set(usb_pad, v)
                    elif key == 'lan1':
                        gpio.set(lan_pads['lan1'], v)
                    elif key == 'lan2':
                        gpio.set(lan_pads['lan2'], v)
                    elif re.fullmatch(r'disk[0-3][gr]', key):
                        gpio.set(disk_pads[int(key[4])]['green' if key[5] == 'g' else 'red'], v)
            else:
                ov = ov0
                if settings['usb_led'] or 'usb' in ov:
                    gpio.set(usb_pad, ov['usb'][0][0] > 0.5 if 'usb' in ov else usb_disk_present())
                lan1_up, lan2_up = lan_link_states()
                if settings['lan_led'] and settings['lan_activity'] and not ov:
                    pass   # 网口灯由活动呼吸接管（下方 register_activity 处理）
                elif settings['lan_led'] or ov:
                    gpio.set(lan_pads['lan1'], ov['lan1'][0][0] > 0.5 if 'lan1' in ov else lan1_up)
                    gpio.set(lan_pads['lan2'], ov['lan2'][0][0] > 0.5 if 'lan2' in ov else lan2_up)
                marquee_on = (settings['disk_marquee'] == 'always'
                              or (settings['disk_marquee'] == 'rebuild' and rebuild))
                leds.set_marquee([disk_pads[i]['green'] for i in range(4)]
                                 if (marquee_on and settings['disk_led'] and not ov) else None,
                                 settings['marquee_period'])
                if settings['disk_led'] or ov:
                    for i, (up, bad) in enumerate(slots):
                        g = ov.get(f'disk{i}g')
                        r = ov.get(f'disk{i}r')
                        if marquee_on and not ov:
                            # 跑马灯接管绿灯，这里只处理红灯
                            gpio.set(disk_pads[i]['red'], bad)
                        else:
                            gv = g[0][0] > 0.5 if g else (up and not bad)
                            if not (settings['disk_activity'] and settings['disk_led']
                                    and not marquee_on and not ov):
                                gpio.set(disk_pads[i]['green'], gv)
                            gpio.set(disk_pads[i]['red'], r[0][0] > 0.5 if r else bad)
                for key, (color, _exp) in ov.items():
                    if key.startswith('pin:'):
                        gpio.set(key[4:], color[0] > 0.5)

                # ---- 读写 / 流量活动：交给灯效引擎做平滑呼吸 ----
                specs = []
                if settings['disk_activity'] and settings['disk_led'] and not marquee_on and not ov:
                    for i, dev in enumerate(('sda', 'sdb', 'sdc', 'sdd')):
                        specs.append({'pad': disk_pads[i]['green'], 'kind': 'disk', 'key': dev,
                                      'base': slots[i][0]})
                if settings['lan_activity'] and settings['lan_led'] and not ov:
                    link = {1: lan1_up, 2: lan2_up}
                    for i, name in enumerate(physical_ifaces()):
                        port = LAN_PORT_MAP.get(name, i + 1)
                        specs.append({'pad': lan_pads['lan1' if port == 1 else 'lan2'],
                                      'kind': 'net', 'key': name,
                                      'base': link.get(port, False)})
                leds.update_activity(specs)

            if state.request_fan_handback:
                state.request_fan_handback = False
                ec.handback()
                fan_state.clear()
            duty = apply_fan(hdd_max or cpu, settings, ec, night, fan_state)

            report = lcd.read()
            if report and report != last_up:
                last_up = report
                handle_report(report)

            state.set_status(
                cpu=cpu, sys=sys_t, hdd_temps=hdds, hdd_max=hdd_max,
                cpu_util=cpu_util, gpu_busy=gpu_busy, mem_ratio=mem_ratio,
                temp_score=temp_s, load_score=load_s, health=score, detail=detail,
                throttled=throttled, night=night,
                total_gb=total, used_gb=used, free_gb=free, free_pct=free_pct,
                storage_target=settings['storage_target'],
                disks=[{'present': up, 'fault': bad} for up, bad in slots],
                rebuild=rebuild, rebuild_pct=rebuild_pct, degraded=degraded,
                degraded_raw=degraded_raw, fault_raw=any_fault_raw,
                fan_duty=duty, fan_enabled=bool(settings['fan_enabled']),
                power_color=power_color, store_color=store_color,
                power_effect=power_effect, store_effect=store_effect,
                power_led=settings['power_led'], store_led=settings['store_led'],
                ec_available=ec.available, hid=hid_path,
            )

            if tick % 15 == 0:
                log(f'{cpu}°C/{hdd_max}°C 温度分{temp_s:.2f} 性能分{load_s:.2f} 健康分{score:.2f} '
                    f'| CPU{(cpu_util or 0)*100:.0f}% GPU{(gpu_busy or 0)*100:.0f}% '
                    f'内存可用{mem_ratio*100:.0f}% 可用空间{free_pct}% '
                    f'风扇{duty if duty is not None else "未接管"}{" 热节流" if throttled else ""}')

            tick += 1
            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            log(f'循环异常: {exc!r}')
            time.sleep(2)

    ec.restore()
    pwm.running = False
    leds.running = False
    return 0


if __name__ == '__main__':
    sys.exit(main())
