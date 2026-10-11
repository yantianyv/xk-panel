# 兮克面板驱动（xk-panel）

让安装了 [飞牛 fnOS](https://www.fnnas.com/) 的 **兮克 XikeStor TS3004E** NAS 前面板恢复完整功能：LCD 运行信息、全套指示灯、硬盘位灯、按键操作，并提供可视化设置页。

> **English:** A userspace panel driver for XikeStor TS3004E NAS running fnOS. It drives the Holtek USB-HID LCD, the front-panel RGB/status/disk LEDs over PCH GPIO, and optionally the IT8613E EC fan PWM. Ships as an fnOS `.fpk` app with a built-in web settings UI.

---

## 功能

**屏幕**
- 显示 CPU / 系统 / 硬盘温度、存储容量、网口 IP / 掩码 / 网关
- 刷新间隔与显示项可配置
- 屏幕上的关机 / 重启 / 弹出设备操作直接生效

**指示灯**
- **电源键灯**：三原色，按整机健康度做红 → 黄 → 绿无极渐变（健康分 = 温度分 × 性能分，温度取 CPU 与硬盘中最热的一个；性能分由 CPU / GPU / 内存占用加权）
- **状态灯**：三原色，显示所选存储的剩余容量健康度；阵列降级 / 重建橙闪、硬盘故障红闪
- **硬盘位灯**：硬盘在位绿灯、故障红灯；读写时绿色平滑呼吸（**呼吸节奏随实际读写速率变化**）
- **USB / 网口灯**：随设备与链路状态自动亮灭，网络流量时呼吸提示
- **灯效**：常亮 / 呼吸 / 彩虹，电源键灯与状态灯周期可独立设置
- **盘位灯流动效果**：仅开机自检 / 阵列重建时 / 常驻

**夜间免打扰**：指定时段内调暗或熄灭指示灯（可单独指定夜间灯效）

**风扇**（可选）：接管 IT8613E 的 PWM 通道按温度曲线调速，支持静音 / 标准 / 性能策略，可随时交还 BIOS 控制

**设置页**：`http://<NAS 地址>:47001`

---

## 适配机型

| 机型 | 状态 |
|---|---|
| 兮克 XikeStor TS3004E（4 盘位，IT8613E + Holtek USB-HID 面板） | ✅ 已实测 |

其他机型的引脚定义可能不同，可通过设置页的**灯位与引脚**校准功能重新映射（见 [docs/porting.md](docs/porting.md)）。

---

## 安装

1. 从 [Releases](../../releases) 下载 `xk-panel_<版本>_x86.fpk`
2. 飞牛 → **应用中心** → **手动安装** → 选择该 `.fpk`
3. 安装完成后应用显示「运行中」，桌面出现「兮克面板驱动」图标，点击进入设置页

命令行（安装后可用）：

```bash
xk-panel status      # 运行状态
xk-panel start|stop  # 启停
xk-panel log 200     # 查看日志
xk-panel url         # 打印设置页地址
```

---

## 工作原理

| 部分 | 方式 |
|---|---|
| LCD | `/dev/hidraw` 上的 **9 字节 Feature 报告**（末字节校验：全包之和 = 0xFF），协议细节见 [docs/protocol.md](docs/protocol.md) |
| 指示灯 | `/dev/gpiochip0`（Intel PCH GPIO），低电平点亮；三原色灯用软件 PWM 混色 |
| 风扇（可选） | `/dev/port` 直接读写 IT8613E 的 EC 寄存器（PWM 控制寄存器 bit7 = 自动模式开关），见 [docs/hardware.md](docs/hardware.md) |

引脚映射与实测数据见 [docs/leds.md](docs/leds.md)。

---

## 构建

```bash
bash build.sh          # 生成 xk-panel_<版本>_x86.fpk
```

打包脚本会校验 `fnos/` 下的 manifest、config、cmd、app、ui 等必要文件，并把应用文件打成 `app.tgz` 后组装为 `.fpk`。

---

## 目录结构

```
.
├── build.sh                 # 打包脚本
├── gen_icon.py              # 生成应用图标
├── fnos/                    # 应用包源
│   ├── manifest             # 应用元数据
│   ├── config/              # 权限与资源声明
│   ├── cmd/service-setup    # 生命周期脚本
│   ├── app/xk_panel.py      # 守护进程（屏幕 / 指示灯 / 风扇 / 设置页后端）
│   ├── app/ui.html          # 设置页
│   ├── bin/xk-panel         # 命令行工具
│   └── ui/                  # 桌面入口与图标
├── docs/                    # 协议、硬件、引脚、移植文档
└── tools/                   # 调试与移植辅助脚本
```

---

## 已知限制

- **LCD 背光亮度不可由主机控制**：背光由屏幕自身硬件决定，主机只能推送显示数值。详见 [docs/protocol.md](docs/protocol.md#关于背光)。
- **风扇接管为实验功能**：直接写 EC 寄存器会接管 BIOS 的智能风扇，可能出现转速异常；设置页提供「交还 BIOS 风扇控制」，异常时也可彻底断电（拔电源 10 秒）让 EC 复位。

---

## 许可

[MIT](LICENSE)

## 免责声明

本项目为第三方用户态驱动，与兮克 / XikeStor 官方无关。涉及硬件寄存器读写的功能（尤其是风扇控制）请自行评估风险；因使用本软件造成的任何损失，作者不承担责任。

## 致谢

- 引脚寄存器与 EC 语义参考了 lm-sensors 的 `it87` 驱动（Christophe Gauthron、Jean Delvare、Guenter Roeck 等）及社区维护的 [frankcrawford/it87](https://github.com/frankcrawford/it87)
- 飞牛 fnOS 应用框架与打包工具来自飞牛应用开放平台
