# 独立多点触摸测试

`touch_probe.py` 不调用 OpenCV，不读写游戏配置，也不接入键盘调度器。
只使用 Python 标准库，通过 ADB 转发的持久连接发送触摸。

## 准备

1. 在 MuMu 中确认本实例的 ADB 端口，不要照抄其他版本的默认端口。
2. 准备 `adb.exe`，可使用模拟器自带版本或 scrcpy 官方 Windows 包中的版本。
3. 从 [scrcpy 官方 v3.3.3 发布页](https://github.com/Genymobile/scrcpy/releases/tag/v3.3.3)
   下载 `scrcpy-server-v3.3.3`，例如保存为 `tools/scrcpy-server-v3.3.3`。
   必须使用该版本；不需要安装 Android APK，也不需要启动 scrcpy 电脑客户端。
4. Android 保持横屏。坐标尺寸是 Android 显示尺寸，不是带工具栏的 Windows 窗口尺寸。

## 检查连接

下面 `16384` 仅为端口示例，请替换。adb 不在 PATH 时，补充 `--adb "实际路径/adb.exe"`。

```powershell
uv run python touch_probe.py --serial 127.0.0.1:16384
```

默认只运行 ADB 连接和设备检查，不上传服务、不发送触摸。

## 真实触摸测试

先关闭原来的自动输入程序，进入多点触摸测试画面或游戏练习模式。
建议手动开启 Android 开发者选项的“指针位置”，观察触点数量和轨迹。

```powershell
uv run python touch_probe.py --serial 127.0.0.1:16384 --server tools/scrcpy-server-v3.3.3 --size 1600 900 --mode dual --send-touch
```

输入 `TOUCH` 确认后倒数 3 秒。测试点为屏幕的 `(30%,70%)` 和 `(70%,70%)`，不是游戏轨道校准点。

- `--mode dual`：两指按下，保持 2 秒；抬起指 0，指 1 再保持 2 秒后抬起。
- `--mode slide`：双指保持后，指 0 横移至屏幕中央，指 1 不动，然后分别抬起。
- `--mode flick`：双指保持后，指 0 在约 60ms 内向上移动屏幕高度的 20%，然后分别抬起。
- `--hold-seconds 3`：修改两个保持阶段的时长。

重点验收第二根手指是否在第一根手指移动/抬起时持续存在。两次 DOWN 依次发送，并非原子同时事件。
`touch-sent` 只表示写入控制连接，不是 Android 注入成功确认；实际时序、flick 是否被游戏接受需要实测。

Ctrl+C 和普通异常退出会尝试释放所有触点并清理本次转发和上传的临时文件。
强制终止、连接中断无法保证释放；若仍有残留触点，停止测试并重启目标应用，必要时重启模拟器。
不会停止其他 ADB 连接或修改系统触摸显示设置。

## 排错

- 服务版本不匹配：使用 3.3.3 文件，查看终端服务端错误。
- 连接成功但触摸无效：检查注入权限、当前显示尺寸和方向；MuMu 兼容性尚需本机验证。
- 坐标偏移：确保 `--size` 是当前横屏 Android 像素尺寸，测试中不要旋转屏幕。
- 端口连接失败：确认 ADB 已开启和实例端口正确；不要使用模拟器窗口标题作为设备地址。

实现依据：[固定版本协议说明](https://github.com/Genymobile/scrcpy/blob/v3.3.3/doc/develop.md)、
[触摸消息编码](https://github.com/Genymobile/scrcpy/blob/v3.3.3/app/src/control_msg.c)。
这是内部协议，后续升级服务端时必须同步核对客户端，不能只更改版本字符串。
