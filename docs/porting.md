# 移植到其他机型

同一系列或相近结构的面板（很多 NAS 前面板都使用 USB-HID 屏幕 + 主板 GPIO 指示灯）可以按下面的流程适配。

## 1. 确认屏幕设备

```bash
# 查看面板 HID 设备（本机型为 374a:b101）
for f in /sys/class/hidraw/*/device/uevent; do grep -H "HID_ID\|HID_NAME" $f; done
```

若 VID/PID 不同，修改 `fnos/app/xk_panel.py` 中的 `HID_VID` / `HID_PID`；若报文格式不同（例如报告长度不是 8 字节 Feature），需要按实际报告描述符调整 `pkt()` 与 ioctl 长度。

```bash
# 查看报告描述符
od -A d -t x1 /sys/class/hidraw/hidrawN/device/report_descriptor
```

## 2. 枚举指示灯引脚

```bash
# 需要 root：读取 pinctrl 引脚表（引脚名、线号、PADCFG 当前值）
sudo cat /sys/kernel/debug/pinctrl/*/pins
```

重点看 `PADCFG` 为 `0x840002xx`（输出，指示灯）与 `0x840001xx`（输入，按键）的引脚。

## 3. 逐脚探测确定灯位

本仓库 `tools/` 提供交互式脚本（需 root，`/dev/gpiochip0` 可读写）：

| 脚本 | 用途 |
|---|---|
| `tools/panel_led_map.py` | 逐个点亮候选引脚，标注电源/状态/USB/网口灯 |
| `tools/disk_led_map.py` | 逐个点亮硬盘位灯，标注「盘位 + 颜色」 |
| `tools/rgb_map.py` | 逐通道点亮，判断哪些组成同一个三原色灯 |

也可直接在设置页「灯位与引脚」里用逐脚探测 + 单灯测试完成，无需命令行。

## 4. 写入映射

在设置页把探测结果填入对应功能位并保存（或直接编辑应用数据目录中的 `settings.json` 的 `pin_map`）。若引脚数量/命名差异较大，可修改 `fnos/app/xk_panel.py` 中的 `DEFAULT_PIN_MAP` 与 `FALLBACK_LINES`，使其成为该机型的默认值。

## 5. 风扇（可选）

```bash
# 探测 Super I/O 芯片型号与 GPIO 逻辑设备寄存器
sudo python3 tools/ec_probe.py

# 监视 EC 风扇控制/占空比/转速寄存器（只读，安全）
sudo python3 tools/ec_fan_mon.py 30
```

确认 PWM 通道与占空比寄存器后，修改 `fnos/app/xk_panel.py` 中的 `FAN_CTRL_REG` / `FAN_DUTY_REG`。

> 不同 EC（ITE / Nuvoton 等）的寄存器语义可能不同，务必先只读确认，再谨慎写入；写入前建议记录原始值以便恢复。

## 6. 分析原厂驱动（可选）

若厂商提供了 Windows/Linux 驱动或原厂系统镜像，可从中提取协议细节：

- **.NET 程序集**：`tools/ildis.py` 可反汇编 IL（依赖 `dnfile`），用于提取命令字、报文构造与校验逻辑
- **原生 ELF**：`objdump -d`（带调试信息时更易读）可定位 MMIO 地址与写值

分析原厂程序时，注意其**引脚命名可能与实物灯位不一致**，最终仍以逐脚探测结果为准。
