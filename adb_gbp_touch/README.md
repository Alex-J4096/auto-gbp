# ADB GBP Touch

通过本地模拟器的 ADB 连接注入 Android 多点触摸。支持独立触点、点击、长押、滑动、flick 和多指时间轴宏，适用于 BanG Dream! Girls Band Party! 手游的模拟输入。

仅依赖 **Python 3.11+ 标准库**，ADB 负责上传/启动服务及端口转发，触摸消息通过持久 TCP 连接发送，不为每个动作启动 shell。

## 使用方式

直接复制整个 `adb_gbp_touch` 文件夹即可。

```text
adb_gbp_touch/
  __init__.py       # 导出的 Python API
  __main__.py       # python -m adb_gbp_touch
  probe.py          # ADB 传输 + 独立双指测试
  macros.py         # 多指时间轴宏
  test_standalone.py
  pyproject.toml    # 可选 uv 环境，无第三方依赖
  README.md
```

## 环境准备

1. 开启模拟器 ADB，确认**当前实例**的本地端口。下面的 `5555` 是示例，不是所有安卓模拟器的默认端口。
2. 准备 `adb.exe`，加入到 PATH 环境变量中，或在命令中提供 `--adb "D:/tools/adb.exe"`。
3. 从 [scrcpy 官方 3.3.3 发布页](https://github.com/Genymobile/scrcpy/releases/tag/v3.3.3) 准备 `scrcpy-server-v3.3.3`，或该版本 Windows 包里的 `scrcpy-server`。
   **不是 `scrcpy.exe`！** 服务端版本固定为 3.3.3，不能随意替换其他版本。
4. 确认当前 Android 显示方向与分辨率。使用 Android 屏幕坐标，不是 Windows 窗口坐标；例如横屏 1920×1080，左上角 `(0,0)`。`adb shell wm size` 可能报告竖屏自然尺寸，测试中不要旋转。

已在原项目的 MuMu 模拟器环境验证过基本触摸；其他模拟器/Android 版本仍需验证注入权限与兼容性。仅支持本地 TCP ADB 地址，不会自动选择设备。

## 独立测试

进入本文件夹后运行（也可以将 `python` 换为 `uv run python`, 根据项目的环境管理方案而定）：

默认只检查连接：

```powershell
python probe.py --serial 127.0.0.1:5555
```

开启测试：

```powershell
python probe.py --serial 127.0.0.1:5555 --server "D:/tools/scrcpy-server" --size 1920 1080 --mode dual --send-touch
```

输入 `TOUCH` 确认后倒数 3 秒，再执行测试：

`--mode` 长选项参数：

| mode | 动作 |
|---|---|
| dual | 两指保持，先释放指 0，指 1 继续保持后释放 |
| slide | 两指保持，指 0 横移，指 1 不动，再分别释放 |
| flick | 两指保持，指 0 快速上滑后释放，指 1 继续保持 |

`--hold-seconds 3` 修改保持时间。测试点按屏幕比例生成，建议使用多点触摸测试画面，或手动开启开发者选项“指针位置”。不要在购买、删除、发送消息等有副作用的界面测试。

从本文件夹的**父目录**也可运行 `python -m adb_gbp_touch --help`。无需 pip 安装；当前 pyproject 仅用于 uv 管理环境，不提供发布到 PyPI 的构建配置。

## Python 接口

将 `adb_gbp_touch/` 放到自己脚本旁边，然后导入。调用 API 会真实发送输入，**不会出现 CLI 的二次确认**。

### 按下、移动、释放

```python
import time
from adb_gbp_touch import open_device

with open_device(
    serial="127.0.0.1:5555",
    server="D:/tools/scrcpy-server",
    size=(1920, 1080),
    adb="D:/tools/adb.exe",
) as touch:
    touch.event(0, 0, 500, 850)   # DOWN，触点 0
    touch.event(0, 1, 1400, 850)  # DOWN，触点 1
    time.sleep(1)
    touch.event(2, 0, 650, 850)   # MOVE，仍是触点 0
    touch.event(1, 0, 650, 850)   # UP，只释放触点 0
    time.sleep(1)
    touch.event(1, 1, 1400, 850)
```

动作编码：`0=DOWN`、`1=UP`、`2=MOVE`。触点 ID 为 `0..9`，按下到抬起之间必须保持不变。不要为已经按下的触点再次发送 DOWN。同一 Touch 不应被多个线程同时调用。

### 双指长押，其中一指尾端 flick

```python
from adb_gbp_touch import Macro, open_device

macro = (Macro()
    .down(0.0, 0, 500, 850)
    .down(0.0, 1, 1400, 850)
    .slide(1.0, 0, (500, 850), (900, 850), duration=.5)
    .slide(2.0, 0, (900, 850), (900, 700), duration=.06)
    .up(2.06, 0)
    .up(2.5, 1))

with open_device("127.0.0.1:5555", "D:/tools/scrcpy-server", (1920, 1080)) as touch:
    macro.play(touch)
```

时间参数全部为**相对播放开始的秒数**，坐标为整数像素。`.slide()` 仅生成 MOVE，不创建/释放触点；其 `start` 应与该时刻实际位置一致。`.flick()` 自动生成 DOWN → MOVE → UP，适用于独立 flick，不可用于已经按下的手指。

```python
macro = Macro().tap(0, 0, 500, 850)
macro.flick(.5, 0, (500, 850), (500, 700), duration=.06)
macro.flick(.5, 1, (1400, 850), (1400, 700), duration=.06)
```

`play()` 会先校验完整时间轴：坐标范围、重复 DOWN、没有 DOWN 的 MOVE/UP、结束未释放等错误，在发送前拒绝。同一时间的事件按加入顺序执行，多指并非硬件级原子同时按下。

播放会阻塞调用线程，但所有触点在同一时间轴中交错执行，不会先完整滑完第一指才处理第二指。若需要其他工作并行，将整次播放放入一个工作线程，传入 `threading.Event`：

```python
import threading
cancel = threading.Event()
# 由其他线程调用 cancel.set()；也可 Ctrl+C 中止前台播放。
completed = macro.play(touch, cancel=cancel)  # 取消返回 False，正常结束返回 True
```

库不提供硬实时保证、应用焦点保护、视觉识别、谱面决策或在线无限事件队列。120Hz 是默认滑动采样目标，不代表实际设备收到事件的精确频率。原项目的实时识别调度器仍留在原项目中，不属于此分享模块。

## 退出与排错

- 正常退出、Ctrl+C、播放异常：尝试释放触点，再关闭连接；移除本次专用转发和上传的临时服务文件，不停止其他 ADB 连接。
- 断网、强制杀进程、Android 拒绝注入时无法保证释放。发现残留触点时停止测试，必要时重启应用/模拟器。
- `WinError 2`：通常找不到 adb，指定 `--adb` 完整路径。
- 服务立即退出：确认 server 不是 exe，版本为 3.3.3，并检查终端服务端错误。
- 连接成功但无触摸：检查注入权限、Android 尺寸与方向、目标实例是否正确。
- `sent` 只意味着写入连接，不是应用接收确认；默认不改系统设置、不自动启动目标应用。

## 离线测试

在本文件夹运行，无需 启动ADB 或模拟器：

```powershell
python -m unittest discover -s . -p "test_*.py"
```

## 协议与第三方组件

协议实现参考 [scrcpy 3.3.3 开发说明](https://github.com/Genymobile/scrcpy/blob/v3.3.3/doc/develop.md)
及 [触摸消息格式](https://github.com/Genymobile/scrcpy/blob/v3.3.3/app/src/control_msg.c)。它是内部协议，版本升级需同步核对，不能只修改版本字符串。

第三方 ADB/scrcpy 组件需自行取得，并遵守其各自许可证。本目录未捆绑第三方二进制；也未替项目所有者选择本模块的开源许可证，公开发布前请自行补充许可说明。
