# Auto GBP

ADB 多点触摸模块通过 Git submodule 引用独立仓库 [adb_gbp_touch](https://github.com/Alex-J4096/adb_gbp_touch)。主项目固定其提交版本，不再单独维护一份库源码；库的独立用法见 [模块说明](adb_gbp_touch/README.md)。

基于 Python、OpenCV 和 Windows 窗口捕捉的音游自动输入原型，面向 MuMu 模拟器中的七轨游戏画面。支持顶部或底部区域识别，按人工校准的延迟调度键盘或多点触摸输入，并提供实时预览和离线截图分析。

支持普通音符、黄色音符、绿色长押及粉色 flick。默认键盘模式的绿色换轨使用实验性双键交接，flick 使用模拟器预设手势宏；触摸模式则通过 ADB 控制通道注入多点触摸。

主程序另提供实验性 `touch` 输入后端，支持普通点击、长押、连续滑条和向上 flick。接入前可先运行 [ADB 双指触摸测试](TOUCH_TEST.md)。

### 实验性触摸输入

TUI 的“触摸输入”页可选择 `touch`，设置 ADB 路径、实例地址、scrcpy 3.3.3 服务端路径、Android 当前横屏尺寸及独立延迟。配置可正常保存到 TOML；切回 `keyboard` 后仍使用原来的键盘参数。已有配置不会自动改成触摸模式。

```powershell
uv run python live_preview.py --title "MuMu安卓设备" --output-backend touch --adb-serial 127.0.0.1:5555 --touch-server tools/scrcpy-win64-v3.3.3/scrcpy-server --touch-size 1920 1080 --touch-delay-ms 350 --send-keys --key-log
```

地址、路径和尺寸请按已通过的独立测试填写。`350` 仅为延迟试验起点，不能保证与键盘模式相同；默认触摸延迟为 0，尚未校准。触摸延迟覆盖 top/bottom 的键盘延迟，F6～F9 调节当前运行值。关闭随机偏移后先在简单谱面校准。

- `--send-keys` 为兼容旧配置保留的真实输入开关，也控制触摸。不加时只 dry-run，不连接 ADB。
- 触点位置由 `key-x` / 1600、`judgment-y` / 900 比例映射到 `touch-size`，因此捕捉裁剪必须对应完整 Android 游戏画面，无工具栏及黑边。运行中不要旋转设备。
- 触摸模式不会发送 Windows 按键；`touch-sent` 日志记录音符身份和动作。每条长押动态分配独立触点，换轨保持身份不变，允许经过同一轨道，不按轨道绑定手指。日志不是 Android 接收确认。
- “长押”页的绿色换轨开关在 touch 模式控制连续跟随：观察线上的绿条位置经过基础延迟后映射到判定线；每帧匹配明确时移动原触点。双条合并、关联不明确时保持位置，不猜测交换身份；交叉和遮挡仍需实测。
- “Flick”页的 Flick 开关也控制 touch 模式。独立 flick 新建触点；长押尾 flick 复用原触点向上移动后抬起，另一根手指可同时运动或释放。已丢失触点的尾 flick 不补造一根手指。
- “触摸输入”页新增：`touch-flick-duration-ms`（默认 60ms）、`touch-flick-distance`（默认 120，900 高基准）和 `touch-flick-delay-ms`（空值跟随触摸基础延迟）。可先保持当前已校准 tap 延迟不变。
- 触摸 flick 不使用 qweruio 宏，也不使用宏的保持/释放时长；它在滑动结束时抬起。尾 flick 仍沿用尾端关联保护期及原有 20..50ms 时间偏移约束。触摸滑条不使用键盘的换轨重叠时长。
- 仍要求目标窗口在前台且可见；暂停、失焦、最小化会取消待发送动作并释放触点，退出时先释放再关闭连接。连接中断时无法保证 Android 收到释放，需观察残留触点，必要时重启游戏/模拟器。
- ADB 仅在启动时准备服务，音符通过持久连接发送，不逐音符调用 shell。仅测试默认 Android 显示，MuMu 窗口与 ADB 实例必须由使用者确认一致。

滑条跟随为实验性决策；普通收尾仍依赖绿条消失确认及尾端缓冲。输入层验证通过不代表所有原有 Miss 已修复。默认沿用配置中的 Flick/换轨开关，可用 `--no-flicks` / `--no-green-slides` 分别关闭。

## 环境与准备

- Windows，Python 3.11 或更新版本，使用 uv 管理依赖。
- MuMu 模拟器，游戏参考分辨率为 **1600×900**。
- 保持窗口可见、未最小化且不被遮挡；发送按键时 MuMu 必须在前台。
- 使用键盘模式时，在模拟器内配置以下键位，从左到右对应七条轨道；触摸模式不需要键盘映射。

| 轨道 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 普通点击 / 长押 | a | s | d | f | j | k | l |
| flick 手势宏 | q | w | e | r | u | i | o |

键盘模式 flick 的方向、距离和速度由 MuMu 手势宏决定。Android 的 240 DPI 不影响截图像素坐标；Windows 客户区可能包含工具栏和黑边，需要单独确认裁剪。

## 快速开始

### 获取项目和依赖

```powershell
git clone --recurse-submodules https://github.com/Alex-J4096/auto-gbp.git
cd auto-gbp
uv sync --locked
```

已有仓库拉取更新后，运行：

```powershell
git submodule update --init --recursive
uv sync --locked
```

`uv sync` 不会下载 Git 子模块；GitHub 的主仓库 ZIP 通常不含子模块内容，推荐使用上述克隆命令。Python 导入仍是 `from adb_gbp_touch import ...`，无需另外 pip 安装。

仓库分工：

```text
auto-gbp/
├── .gitmodules          # 独立库地址与目录
├── adb_gbp_touch/       # 子模块：主仓库只记录固定提交
├── touch_output.py      # 音游专用触点调度与滑条决策
├── live_preview.py      # 捕捉、识别和主循环
├── launcher*.py         # 配置和 TUI
├── config.example.toml  # 可提交的配置示例
└── test_*.py            # 主项目回归测试
```

个人 `config.toml`、外部二进制 `tools/`、截图 `test_pic/` 和产物 `output/` 保留在本地并忽略。

### 开发与升级子模块

修改库代码时，在 `adb_gbp_touch` 仓库单独提交并推送；然后在主仓库执行 `git add adb_gbp_touch` 并提交版本指针。必须先确保库提交已推送，否则其他人无法获取对应版本。

仅在明确升级库版本时使用 `git submodule update --remote adb_gbp_touch`；测试通过后提交新指针。日常安装使用不带 `--remote` 的命令，才能保持版本可复现。主项目不使用 uv Git 依赖，也不自动跟随库的最新版本。

### 回归测试

```powershell
uv run python -m unittest discover
```

测试使用模拟输入，不会控制模拟器。缺少未公开的 `test_pic/` 截图时，3 项截图测试会跳过，其余测试仍执行。

### 终端设置界面（推荐）

安装依赖后运行：

```powershell
uv run python launcher.py
```

界面使用 Textual，从脚本所在目录的 `config.toml` 读取并填入设置，按“常用、长押、Flick、随机偏移、高级”分组。支持鼠标点击、滚轮、Tab / Shift+Tab 切换控件、空格切换开关和下拉选择。可选输入框清空表示恢复自动值，必填项为空会阻止启动。“显示预览”和“启用全局快捷键”均为正向开关。

底部“运行”仅本次生效，“保存并运行”写入配置，“取消”退出；快捷键分别为 Ctrl+R、Ctrl+S、Ctrl+Q。真实输入开启时会弹出二次确认框，可按 Esc 返回修改。TUI 退出并恢复终端后才进入游戏主程序，不在表单打开时采集或发送按键。

更新依赖请运行 `uv sync`。推荐在 Windows Terminal 中使用；若终端不支持交互控件，可用 `uv run python launcher.py --plain` 回退到编号菜单。原有 TOML 配置无需迁移。

配置文件使用 `[settings]` 表，键名为命令行参数名将连字符替换为下划线；未填写的项目使用程序默认值。可复制 `config.example.toml` 为 `config.toml`，或直接在 TUI 保存配置。示例默认关闭真实输入，参数只是调试起点。个人 `config.toml`、外部工具 `tools/` 和测试截图 `test_pic/` 不提交到仓库。保存会重写配置（不保留手写注释）；运行中的热键调整不会自动保存。

可用 `uv run python launcher.py --config my_config.toml` 选择其他配置。文件不存在时使用程序默认值，选择保存才创建；格式错误或未知配置项会报错，不会静默忽略。

`flick-tail-guard-ms` 也已加入设置，默认 250ms，范围 0～2000ms。它控制尾 flick 候选的固定保护期，不是宏输入延迟；不随每帧续期。`flick-tail-release-ms` 则是宏触发后松开长押键的间隔，两者不同。

### 底部识别模式（实验性）

在 TUI「常用」将识别模式设为 `bottom`，在「底部识别」调整独立延迟和观察范围。已有配置仍默认使用顶部模式，不会自动覆盖你的校准值。

先预览，不发送按键：

```powershell
uv run python live_preview.py --title "MuMu安卓设备" --region bottom --preview
```

底部默认识别 `y=480～700`，观察线 `y=600`（1600×900 基准）。无预览时只抓取该区域。轨道间距由 `key-x` 的平均间距及 `judgment-y` 按透视换算，不使用顶部的 43px；请先用预览刻度确认对齐。原有 `bottom-band` 是延迟测量区域，不是底部模式识别区域。

`--bottom-delay-ms` 默认 **0ms，仅为未校准起点**；不沿用 `--delay-ms` 的顶部值。下面的 80ms 只是命令示例，请按实测修改：

```powershell
uv run python live_preview.py --title "MuMu安卓设备" --region bottom --bottom-delay-ms 80 --send-keys --key-log
```

F6/F7、F8/F9 继续调整当前模式延迟，运行中调整不会自动保存。底部 flick 默认沿用底部延迟，另行校准使用 `--bottom-flick-delay-ms`；顶部 `--flick-delay-ms` 在底部模式下不生效。配置字段为 `region = "bottom"`、`bottom_delay_ms`、`bottom_flick_delay_ms`（可省略）、`lower_band = [480, 700]`、`lower_observation_y = 600`。

底部节点更大，但提前量更少，也可能被 AUTO、判定文字和击打光效遮挡。现有长押消失确认仍需 60ms，低延迟时可能来不及按原定时刻收尾；换轨、尾 flick 的原有偏移规则保持不变。本模式尚需实机验证，并不等同于已经修复绿条漏检；校准时建议先关闭随机偏移。

### 直接命令行运行

原有 `live_preview.py` 用法保持不变，**不会读取启动器的配置文件**，默认值仍见下表。

在项目目录安装依赖：

```powershell
uv sync
```

先打开预览检查画面与识别结果，不发送真实按键：

```powershell
uv run python live_preview.py --preview
```

确认后启动真实输出，可选开启按键日志：

```powershell
uv run python live_preview.py --send-keys
uv run python live_preview.py --send-keys --key-log
```

默认配置无需逐项填写：

| 配置 | 默认值 |
| --- | --- |
| 窗口标题匹配 | MuMu |
| 检测范围 / 预览 | 顶部 / 关闭 |
| 人工下落延迟 / 输入模式 | 350ms / vk |
| 长押 / 实验性换轨 / flick | 开启 |
| 普通键 / flick 宏键保持时间 | 30ms |
| 长押尾 flick 触发后的释放间隔 | 30ms |
| 采集频率上限 / 识别宽度 | 60 FPS / 800 像素 |
| 真实按键 / 按键日志 / 随机偏移 | 关闭，需显式开启 |

**350ms 是当前游戏设置的手工校准值，不是自动测量结果。** 改变游戏速度、观察线或运行环境后应重新校准。不带 `--send-keys` 时为 dry-run：只识别、调度和打印模拟输出，不按键。

旧的显式参数仍可使用，例如 `--green-holds --green-slides --flicks --no-preview`，但在当前默认配置下可以省略。

## 常用命令

### 选择窗口与裁剪

```powershell
uv run python live_preview.py --list
uv run python live_preview.py --title "MuMu"
uv run python live_preview.py --hwnd 0x123456 --preview
```

句柄是示例，请替换为窗口列表中的值。标题匹配多个窗口时，使用 `--hwnd` 精确选择。

预览中按 `4` 查看原始捕捉；如包含工具栏或黑边，用 `--crop X Y W H` 指定客户区内的游戏区域：

```powershell
uv run python live_preview.py --preview --crop 0 0 1600 900
```

裁剪采用 Windows 客户区物理像素。以上只是示例，不一定等于模拟器的 Android 分辨率。

### 延迟与功能开关

```powershell
# 指定延迟
uv run python live_preview.py --send-keys --delay-ms 360

# 仅普通音符（含黄色普通音符）
uv run python live_preview.py --send-keys --no-green-holds --no-flicks

# 保留同轨长押，关闭实验性换轨
uv run python live_preview.py --send-keys --no-green-slides

# 单独设置 flick 延迟
uv run python live_preview.py --send-keys --flick-delay-ms 330
```

`--no-green-holds` 同时禁用换轨。关闭长押后，识别为长押连接节点的黄色音符不会退化为普通点击。

### 单键测试

```powershell
uv run python live_preview.py --send-keys --test-key f
uv run python live_preview.py --send-keys --test-key r
```

程序等待三秒，期间切回 MuMu；目标窗口未聚焦时取消测试。`f` 测试中间轨普通键，`r` 测试对应 flick 宏键。测试不识别画面，保持时长由 `--hold-ms` 控制。

## 预览与快捷键

`--preview` 开启捕捉预览和底部延迟测量。默认无预览模式省去显示及底部检测开销；顶部无预览模式只采集顶部小区域。

全局快捷键无需切出 MuMu：

| 按键 | 功能 |
| --- | --- |
| F6 / F7 | 延迟 −50 / +50ms |
| F8 / F9 | 延迟 −10 / +10ms |
| F10 | 暂停 / 恢复 |
| F11 | 退出 |

如快捷键被占用，可加 `--no-hotkeys` 禁用注册。终端中也可用 `Ctrl+C` 退出。

预览窗口获得焦点时：

- `1` 纯色特征图，`2` 原图标注，`3` 过滤后的像素，`4` 原始捕捉。
- `[` / `]` 调整延迟 −/+10ms；空格暂停；`Q` / `Esc` 退出。

真实输出只在目标窗口聚焦时执行。切到预览会暂停真实输入，切回游戏后等待新音符进入。暂停、失焦、最小化或退出会取消待执行事件并释放程序按住的键。

## 校准与工作方式

### 顶部观察线与下落延迟

顶部检测默认使用参考画面 y=34～200 的中央梯形区域，虚拟观察线为 y=160。音符中心跨过观察线后，按 `--delay-ms` 安排输入；这条线不是游戏底部判定线。

轨道位置由固定透视模型投影。以下坐标均以 1600×900 为基准，随画面尺寸缩放，要求正确裁剪完整游戏画面：

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--top-band Y1 Y2` | `34 200` | 顶部检测范围 |
| `--observation-y` | `160` | 顶部观察线高度 |
| `--lane-spacing` | `43` | 观察线处轨道间距 |
| `--judgment-y` | `738.52` | 配置的底部判定线高度 |
| `--key-x` | `243 434 619 800 983 1169 1353` | 底部七键预览标记位置 |

修改观察线高度后需重新校准间距和延迟；底部字母标记不会修改 MuMu 键位映射。

先用低密度普通音符校准：整体偏 Fast 时增大延迟，整体偏 Late 时减小延迟。热键同步调整待执行事件，tap 与 flick 保留初始延迟差，不改变已按下按键的保持时长。

### 底部辅助测量

开启预览后，程序尝试匹配同轨的顶部与底部跨线事件，估计下落时间。默认底部范围为 `--bottom-band 650 775`，检测线位于配置判定线上方 `--bottom-offset 24` 像素，并外推剩余时间。

只有位于人工延迟 ±`--measure-tolerance-ms`（默认 250ms）且能唯一匹配的事件才被采纳。`waiting` 表示尚无有效匹配，不代表顶部输入没有运行。可在稀疏谱面、dry-run 预览中检查候选框、轨道映射和延迟范围。

测量不会自动修改输入延迟，也不包含可靠的模拟器输入响应补偿；底部特效、音符消失和速度变化会影响结果。顶部模式的判定线是固定配置；`--region full` 才使用全谱面检测和自动判定线识别。

### 长押与 flick

- 绿色长押通过节点跨线及连接带消失判断按下和释放；黄色长押节点沿用相同机制。
- 换轨采用相邻双键保持。后续普通音符需要旧轨时，满足轨迹已离开的条件才释放旧键并点击；非相邻交接重叠时间由 `--slide-overlap-ms` 控制，默认 30ms。
- 绿色消失防抖为 `--green-gap-ms 60`，长押保护超时为 `--max-hold-ms 15000`。
- 普通长押尾额外延后 `--green-tail-release-ms 30` 再松键，可在 0～200ms 内调整；适用于同轨和换轨长押，不改变普通点击或 flick 尾释放间隔。仍提前松键时可试 40～50ms；过大会影响后续同轨音符。此缓冲不替代绿色消失防抖，也不能补救持续漏检。
- 粉色节点下方连接绿色轨迹且唯一关联当前长押时，按长押尾 flick 处理，触发宏后等待 `--flick-tail-release-ms 30` 再释放长押。
- 普通点击和宏键保持时间分别为 `--hold-ms`、`--flick-hold-ms`，均默认 30ms。宏键保持时间不等于模拟器实际滑动时长。

## 可选：随机时间偏移

```powershell
uv run python live_preview.py --send-keys --timing-jitter --jitter-mean-ms 5 --jitter-sigma-ms 21.5
```

默认关闭。开启后，从截断于 [−83,+100]ms 的正态分布采样并叠加到基础延迟。`--jitter-mean-ms` 设置均值 μ，`--jitter-sigma-ms` 设置标准差 σ；σ=0 时为固定偏移。`--jitter-seed 42` 可复现相同事件调用顺序下的采样。

按 Perfect [−33,+50]ms 计算，默认 μ=5、σ=21.5 的理论 Perfect 比例约 94.33%。该比例假设基础延迟已校准，不保证实测结果；均值 +5ms 也不代表每次偏移都为正或 Great 主要偏 Late。

普通音符和独立 flick 各采样一次，同一长押共用原始采样值，但不同动作按下表限制最终偏移。热键改变基础延迟，不重新采样。密集音符可能因独立偏移改变输入顺序，排查漏键时建议关闭随机偏移。

### 按音符判定窗口限制偏移

依据用户提供的判定表，滑条节点的 PERFECT 为 [0,+200]ms，滑条尾 flick 为 [0,+100]ms，不允许提前。当前策略如下，均相对于人工校准后的计划时间：

| 动作 | 偏移策略 |
| --- | --- |
| 普通点击、独立 flick、长押头 | 原有配置不变；随机偏移关闭时为 0 |
| 换轨节点按下、配对旧键交接 | 将共享偏移限制为 +20～+60ms；关闭随机偏移时为 +20ms，原交接重叠间隔不变 |
| 普通长押尾 | 共享偏移限制为 0～+20ms，另加释放缓冲；默认缓冲 30ms 时额外延后合计 30～50ms |
| 长押尾 flick | 共享偏移限制为 +20～+50ms；关闭随机偏移时为 +20ms，触发后按原释放间隔松键 |

当前不能可靠区分直条与斜条的尾 flick，因此选择两类 PERFECT 窗口交集 [0,+67]ms 内的保守范围，并留出部分误差空间。普通长押尾的时间基准仍是视觉消失估计，不是已知的真实谱面尾时刻；这些限制只约束计划偏移，不能保证游戏判定。flick 宏自身的响应和滑动时长仍需在 MuMu 中校准，程序的宏键保持时长不等同于滑动时长。

上述安全偏移在关闭随机偏移时也生效。94.33% 理论比例仅对应普通点击/独立 flick 的原始分布，不代表整曲或其他音符类型的 PERFECT 比例。

## 排错与性能

### 日志含义

`--key-log` 才会记录成功按键发送及随机偏移。日志异步输出，队列满时丢弃日志，不阻塞按键调度。

| 日志 / 指标 | 含义 |
| --- | --- |
| `[cross]` / `[flick-cross]` | 检测到跨线事件并安排输入 |
| `[dry-run]` | 模拟执行，没有发送真实输入 |
| `[send-down]` / `[send-up]` | 已提交按下 / 释放，不保证游戏命中 |
| `[skip]` / `[send-error]` | 跳过输入 / 发送失败 |
| `enqueue-late-p95` | 最近事件入队时已超过计划时间的第 95 百分位 |
| `send-late-p95` | 最近事件执行晚于计划时间的第 95 百分位；dry-run 为模拟执行 |
| `skip-held` / `expired` | 因长押占用而跳过 / 因过期跳过的累计次数 |
| `log-dropped` | 日志拥堵或写入失败导致的丢弃数 |

无预览模式约每两秒打印状态。lateness 不是游戏的 Fast/Late 判定，也不包含随机偏移本身。

### 常见问题

- **手动按键有效，程序无效：** 确认带有 `--send-keys`、MuMu 聚焦且映射启用。默认 `vk` 是当前实测可用方式，`--input-mode scan` 仅用于对照；还需检查模拟器与脚本权限等级是否一致。
- **漏键：** 先关闭随机偏移，保持已校准延迟，检查 `skip-held`、`expired` 和发送迟到指标。指标低但仍漏判时，应检查视觉漏检或模拟器宏，不应直接增大延迟。
- **预览帧率低：** 去掉 `--preview`。实际输出优先用默认顶部模式；`--fps` 只是上限，不保证处理或游戏达到该帧率。
- **仍有性能瓶颈：** 可试 `--process-width 640`，但远处小音符识别可能变差；预览可调 `--preview-width 800`。全谱面 `--region full` 会增加开销并改变候选集合。
- **换轨或尾 flick miss：** 双键交接不等同于同一触点连续拖动，宏也未必能接管原触点；增加重叠时间不一定有效。

## 离线截图分析

```powershell
uv run python detect_notes.py path/to/screenshots --output output
uv run python detect_notes.py path/to/green.jpg --output output/green
```

截图需自行准备，将上述路径替换为本地截图目录或文件。目录输入按文件名分别保存结果，主要输出：

| 输出 | 内容 |
| --- | --- |
| `04_overlay.png` | 原图候选框与判定线标注 |
| `06_relevant_only.png` | 过滤后的原始像素 |
| `08_features.png` | 纯色重建的音符与长押路径 |
| `09_judgment_mask.png` | 判定线检测掩码 |
| `notes.json` | 候选音符、连接关系和判定线数据 |

其余编号图片用于查看谱面区域、颜色掩码及中间结果。离线判定线检测失败时返回 `null`；候选与路径连接不等同于完整谱面语义。

## 项目结构

| 文件 | 职责 |
| --- | --- |
| `live_preview.py` | 窗口捕捉、运行参数、预览与热键 |
| `detect_notes.py` | OpenCV 候选筛选、轨迹特征与离线分析 |
| `tap_output.py` | 跨帧跟踪、长押状态、输入调度与时间偏移 |
| `runtime_log.py` | 有界异步日志 |

测试代码（`test_*.py`）、测试截图（`test_pic/`）、分析输出（`output/`）、`cmd.txt`、虚拟环境、缓存和日志由 `.gitignore` 排除，仅留在本地，不随仓库分发。

```powershell
uv run python live_preview.py --help
uv run python detect_notes.py --help
```

## 已知限制

这是依赖固定布局和人工延迟的原型，不是硬实时输入系统。背景特效、漏帧、高密度同轨音符、交叉长押和下落速度变化均可能导致误判。

不支持遮挡或最小化后的后台捕捉。绿色换轨、长押尾 flick 及并行宏仍需实际曲目验证；暂停或退出可以释放程序按键，但不能保证中止 MuMu 已启动的内部宏。

请仅在比赛规则和游戏使用规则允许的环境中使用。
