# 调试与移植工具

这些脚本用于**探测硬件与确定灯位映射**，在移植到其他机型或排查问题时使用。除特别说明外都需要 root 权限（访问 `/dev/gpiochip0`、`/dev/port` 等）。

| 脚本 | 用途 |
|---|---|
| `panel_led_map.py` | 交互式：逐个点亮电源/状态/USB/网口候选引脚，可标注每个引脚的实际用途 |
| `disk_led_map.py` | 交互式：逐个点亮 8 个硬盘位灯引脚，标注「盘位 + 绿/红」 |
| `rgb_map.py` | 交互式：逐通道点亮，判断哪些引脚组成同一个三原色灯（含电源灯、USB 灯） |
| `lan1_sweep.py` | 扫描剩余候选引脚，用于定位尚未确定的灯（例如某个网口指示灯） |
| `ec_probe.py` | 探测 ITE Super I/O（EC）芯片型号与 GPIO 逻辑设备寄存器（只读） |
| `ec_fan_mon.py` | 监视 EC 的风扇控制、占空比、转速寄存器（只读，安全） |
| `ildis.py` | 极简 .NET IL 反汇编器（依赖 `dnfile`），用于分析厂商的 .NET 驱动程序集 |

## 常用命令

```bash
# 交互式确定灯位（按提示回车逐个点亮）
sudo python3 panel_led_map.py
sudo python3 disk_led_map.py

# 探测 EC 与监视风扇（只读）
sudo python3 ec_probe.py
sudo python3 ec_fan_mon.py 30

# 反汇编 .NET 程序集
pip install dnfile
python3 ildis.py path/to/Driver.dll MethodName
```

标注与测量结果会写入当前用户主目录下的 `*_marks.txt`。

> 日常使用不需要这些脚本：设置页已内置逐脚探测与灯位映射功能。
