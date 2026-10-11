# AGENTS.md — xk-panel（兮克面板驱动）

## 项目概述

飞牛 fnOS 的用户态前面板驱动，适配兮克 XikeStor TS3004E NAS：驱动 Holtek USB-HID LCD、PCH GPIO 指示灯/按键，可选接管 IT8613E EC 风扇 PWM。打包为 fnOS `.fpk` 应用，内置 Web 设置页（端口 47001）。实现全部在一个守护进程文件 `fnos/app/xk_panel.py`（约 1650 行，**仅用 Python 标准库，禁止引入第三方依赖**——目标机上没有 pip 环境）。

## 常用命令

| 命令 | 用途 | 出处 |
|---|---|---|
| `bash build.sh` | 校验并打包生成 `xk-panel_<版本>_x86.fpk`（版本取自 `fnos/manifest`） | build.sh |
| `bash build.sh test` | 打测试包：版本自动追加构建号（见下方版本号纪律） | build.sh |
| `python3 gen_icon.py` | 重新生成应用图标 | gen_icon.py |
| `xk-panel status\|start\|stop\|log [N]\|url` | NAS 上安装后的运行管理 CLI | fnos/bin/xk-panel |

- **无测试、无 lint、无 CI**。守护进程依赖真实硬件（`/dev/hidraw`、`/dev/gpiochip0`、`/dev/port`），本开发机无法运行验证，改动只能靠 NAS 实测。
- `tools/*.py` 是需在 NAS 上以 root 运行的硬件探测脚本（灯位映射、EC 只读探测等），用法见 `tools/README.md`。

## 目录结构

- `fnos/` — fpk 应用包源：`manifest`（元数据与版本）、`app/`（守护进程 + ui.html）、`cmd/service-setup`（生命周期钩子）、`bin/`（CLI）、`config/`（权限声明）、`ui/`（桌面入口）
- `docs/` — 协议与硬件的**唯一权威出处**：`protocol.md`（LCD 协议）、`leds.md`（引脚映射实测）、`hardware.md`（EC/风扇）、`porting.md`（移植流程）
- `tools/` — 硬件探测/移植辅助脚本（需 root，在 NAS 上运行）
- 根目录 `*.fpk` 是打包产物（已 gitignore）；`build.sh` 中间产物在 `.build_tmp/`

## 架构要点

- `xk_panel.py` 单进程多线程：主循环（采集+LCD）、`RgbPwm`（软件 PWM 混色）、`LedGroups`（灯效）、`WebUi`（设置页后端）；`Settings` 类热加载配置。
- 硬件接口三条路：LCD 走 hidraw ioctl Feature 报告（9 字节，校验=全包之和 0xFF）；LED 走 gpiochip 字符设备 ioctl（**低电平点亮**）；风扇走 `/dev/port` 读写 EC 寄存器（索引 0x295/数据 0x296）。
- 引脚映射默认值 `DEFAULT_PIN_MAP` 是本机型实测结果，与 `docs/leds.md` 保持一致；用户可在设置页重映射，不要把设置页配置当默认值写死。
- fpk 的生命周期框架（`cmd/common`、`main`、`installer`）由 build.sh 内联生成，应用专属逻辑只写在 `fnos/cmd/service-setup`。

## 注意事项

- **风扇控制是危险路径**：直写 EC 寄存器会接管 BIOS 智能风扇，改动 `Ec` 类或 `apply_fan` 前先读 `docs/hardware.md`；必须保留「交还 BIOS 控制」的退路。
- LCD 背光亮度不可由主机控制（硬件限制，见 docs/protocol.md），不要尝试实现。
- 改硬件协议、引脚、EC 语义时同步更新 `docs/` 对应文档——代码默认值与文档不一致是历史 bug 来源。
- **版本号纪律**：`fnos/manifest` 的 version 只写**基准版本**（如 `1.0.0`），仅在发版时向前推进并同步 `CHANGELOG.md`。开发测试用 `bash build.sh test`，自动追加构建号（git commit 数 + 时间戳，如 `1.0.0.12.10122215`），保证每次打包版本号都递增，规避同版本不能覆盖安装的问题；测试包的多段号小于任何推进后的基准版本，因此正式版能自然覆盖测试版。（fnOS 的版本比较算法**待确认**，若实测不认 4 段号，再调整后缀格式）
- git 提交信息风格：中文，`feat: ...` 前缀。

## 关于 AGENTS.md 自身

- **一个项目只有这一份 AGENTS.md**：子系统细节写进 `.pi/skills/`，不在子目录另建 AGENTS.md。
- **只写每轮都要用的**：相关时才用的进 skill；在办的进度与待办写进对应目录的 `STATUS.md`。
- **每条规则要能回答「不写会导致什么错误」**；口径类标注出处，拿不准标「待确认」，禁止凭推断写死。
- **会过期的不留在这里**：不写变更史、不写自我说明、不把临时状态留成本文件的一部分。
