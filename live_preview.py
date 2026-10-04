"""Capture Windows gameplay, optionally preview, and schedule opt-in tap output."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import sys
import time

import cv2
import mss
import numpy as np

from detect_notes import analyze_frame, analyze_top_frame, detect_bottom_blue, draw_bottom_keys
from tap_output import CrossingTracker, TapScheduler, DelayEstimator, Keyboard, GreenHoldController, SlidingHoldController


class GlobalHotkeys:
    """Register function keys on the capture thread without a preview window."""
    ACTIONS = {1: -50, 2: 50, 3: -10, 4: 10, 5: "pause", 6: "exit"}

    def __init__(self, user):
        self.user, self.registered = user, []
        user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                     wintypes.UINT, wintypes.UINT, wintypes.UINT]
        try:
            for hotkey_id in self.ACTIONS:
                # F6..F11; MOD_NOREPEAT prevents held keys making large jumps.
                if not user.RegisterHotKey(None, hotkey_id, 0x4000, 0x74 + hotkey_id):
                    raise RuntimeError(f"F{hotkey_id + 5} 快捷键被占用；可加 --no-hotkeys 禁用全局快捷键")
                self.registered.append(hotkey_id)
        except Exception:
            self.close()
            raise

    def poll(self):
        message = wintypes.MSG()
        actions = []
        while self.user.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            action = self.ACTIONS.get(message.wParam)
            if action is not None:
                actions.append(action)
        return actions

    def close(self):
        for hotkey_id in self.registered:
            self.user.UnregisterHotKey(None, hotkey_id)
        self.registered.clear()


def windows_api():
    if sys.platform != "win32":
        raise RuntimeError("实时窗口捕捉目前只支持 Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    # Physical pixels, including systems with Windows display scaling enabled.
    try:
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user.SetProcessDPIAware()
    user.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    return user


def list_windows(user):
    items = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def callback(hwnd, _):
        if user.IsWindowVisible(hwnd):
            length = user.GetWindowTextLengthW(hwnd)
            title = ctypes.create_unicode_buffer(length + 1)
            user.GetWindowTextW(hwnd, title, length + 1)
            if title.value:
                items.append((int(hwnd), title.value))
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows(callback, 0)
    return items


def capture_region(user, hwnd, crop):
    if not user.IsWindow(hwnd):
        raise RuntimeError("目标窗口已关闭")
    if user.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user.GetClientRect(hwnd, ctypes.byref(rect)) or not user.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise RuntimeError("无法取得目标窗口客户区")
    width, height = rect.right, rect.bottom
    x, y, w, h = crop if crop else (0, 0, width, height)
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
        raise RuntimeError(f"裁剪区域超出客户区 {width}x{height}，请重新设置 --crop")
    return {"left": origin.x + x, "top": origin.y + y, "width": w, "height": h}


def capture_top_only(capture, region, process_width, band):
    """Grab only the central top band, retaining full-game coordinate geometry."""
    width, height = region["width"], region["height"]
    sx, sy = width / 1600, height / 900
    half = 40 + 1.03 * band[1]
    x1, x2 = max(0, round((800 - half) * sx)), min(width, round((800 + half) * sx) + 1)
    y1, y2 = round(band[0] * sy), min(height, round(band[1] * sy) + 1)
    small_region = {"left": region["left"] + x1, "top": region["top"] + y1,
                    "width": x2 - x1, "height": y2 - y1}
    pixels = np.asarray(capture.grab(small_region))[:, :, :3].copy()
    captured = time.perf_counter()
    ratio = min(1, process_width / width)
    out_width, out_height = round(width * ratio), round(height * ratio)
    frame = np.zeros((out_height, out_width, 3), np.uint8)
    left, right = round(x1 * ratio), min(out_width, round(x2 * ratio))
    top, bottom = round(y1 * ratio), min(out_height, round(y2 * ratio))
    if right <= left or bottom <= top:
        raise RuntimeError("捕捉区域过小，请增大 process-width 或顶部范围")
    frame[top:bottom, left:right] = cv2.resize(pixels, (right - left, bottom - top), interpolation=cv2.INTER_AREA)
    return frame, captured


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="列出可见窗口及 HWND")
    select = parser.add_mutually_exclusive_group()
    select.add_argument("--title", default="MuMu", help="目标窗口标题子串，默认 MuMu（大小写不敏感）")
    select.add_argument("--hwnd", type=lambda value: int(value, 0), help="窗口句柄，支持十六进制")
    parser.add_argument("--crop", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                        help="客户区内游戏画面的物理像素矩形，排除工具栏及黑边")
    parser.add_argument("--fps", type=float, default=60, help="采集频率上限，默认 60")
    parser.add_argument("--process-width", type=int, default=800,
                        help="识别宽度上限，默认 800；坐标基于缩小后的图像")
    parser.add_argument("--cv-threads", type=int, default=2, help="OpenCV 线程数，默认 2")
    parser.add_argument("--preview-width", type=int, default=1280)
    preview = parser.add_mutually_exclusive_group()
    preview.add_argument("--preview", dest="no_preview", action="store_false", help="开启捕捉预览及底部测量；默认关闭")
    preview.add_argument("--no-preview", dest="no_preview", action="store_true", help="关闭预览及底部测量（默认，兼容旧命令）")
    parser.set_defaults(no_preview=True)
    parser.add_argument("--no-hotkeys", action="store_true", help="不注册全局 F6～F11 快捷键")
    parser.add_argument("--region", choices=("top", "full"), default="top", help="默认只识别顶部")
    parser.add_argument("--top-band", nargs=2, type=float, default=(34, 200), metavar=("Y1", "Y2"))
    parser.add_argument("--observation-y", type=float, default=160, help="顶部虚拟观察线，900 高基准")
    parser.add_argument("--judgment-y", type=float, default=738.52, help="固定底部判定线，900 高基准")
    parser.add_argument("--send-keys", action="store_true", help="启用真实按键；默认仅输出日志")
    parser.add_argument("--key-log", action="store_true", help="打印成功发送的按下/释放日志，默认关闭")
    parser.add_argument("--input-mode", choices=("scan", "vk"), default="vk",
                        help="键盘输入方式，默认 vk（附带有效扫描码）；scan 用于对照")
    parser.add_argument("--test-key", choices=list("asdfjklqweruio"),
                        help="仅测试指定按键，不捕捉、不识别；需显式 send-keys")
    parser.add_argument("--delay-ms", type=float, default=350, help="从顶部观察线到按键的人工延迟，默认 350ms；需按游戏速度校准")
    parser.add_argument("--hold-ms", type=float, default=30, help="普通音符按键持续时间，默认 30ms")
    parser.add_argument("--flicks", action=argparse.BooleanOptionalAction, default=True,
                        help="粉色 flick 的 qweruio 手势宏处理，默认开启；--no-flicks 关闭")
    parser.add_argument("--flick-delay-ms", type=float, default=None,
                        help="flick 初始延迟，默认沿用 delay-ms；热键同步调整两种延迟")
    parser.add_argument("--flick-hold-ms", type=float, default=30, help="手势宏按键持续时间，默认 30ms")
    parser.add_argument("--flick-tail-release-ms", type=float, default=30,
                        help="长押尾 flick 宏触发后释放长押的间隔，默认 30ms")
    parser.add_argument("--timing-jitter", action="store_true", help="启用截断在 [-83,+100]ms 的正态时间偏移")
    parser.add_argument("--jitter-mean-ms", type=float, default=5, help="正态偏移均值 μ，默认 +5ms")
    parser.add_argument("--jitter-sigma-ms", type=float, default=21.5, help="正态偏移标准差 σ，默认 21.5ms；0 为固定偏移")
    parser.add_argument("--jitter-seed", type=int, default=None, help="随机偏移种子，用于复现调试")
    parser.add_argument("--green-holds", action=argparse.BooleanOptionalAction, default=True,
                        help="绿色长押处理，默认开启；关闭时同时禁用换轨")
    parser.add_argument("--green-slides", action=argparse.BooleanOptionalAction, default=True,
                        help="实验性相邻变轨双键保持，默认开启；--no-green-slides 切回同轨长押")
    parser.add_argument("--slide-overlap-ms", type=float, default=30, help="换轨新旧键重叠时间，默认 30ms")
    parser.add_argument("--green-gap-ms", type=float, default=60, help="绿色连接带消失确认时间，默认 60ms")
    parser.add_argument("--green-tail-release-ms", type=float, default=30,
                        help="普通长押尾额外释放缓冲，默认 30ms，范围 [0,200]；不影响 flick 尾")
    parser.add_argument("--max-hold-ms", type=float, default=15000, help="长押强制释放保护，默认 15000ms")
    parser.add_argument("--lane-spacing", type=float, default=43,
                        help="观察线处相邻轨道间距，1600 宽基准；默认 43，需校准")
    parser.add_argument("--bottom-band", nargs=2, type=float, default=(650, 775), metavar=("Y1", "Y2"))
    parser.add_argument("--bottom-offset", type=float, default=24,
                        help="底部检测线在实际判定线上方的距离，900 高基准；用末端速度外推剩余时间")
    parser.add_argument("--measure-tolerance-ms", type=float, default=250,
                        help="同轨延迟匹配相对人工 delay 的容差，默认 +/-250ms")
    parser.add_argument("--key-x", nargs=7, type=float, default=(243, 434, 619, 800, 983, 1169, 1353),
                        help="底部七个键标记的 x，1600 宽基准")
    args = parser.parse_args(argv)
    if not args.green_holds:
        args.green_slides = False
    if not 0 < args.flick_tail_release_ms <= 200:
        parser.error("flick-tail-release-ms 必须位于 (0,200] 内")
    if args.test_key and not args.send_keys:
        parser.error("test-key 会发送真实按键，请同时指定 --send-keys")
    if args.fps <= 0 or min(args.preview_width, args.process_width, args.cv_threads) <= 0:
        parser.error("fps、宽度和线程数必须大于 0")
    if args.delay_ms < 0 or args.hold_ms <= 0 or args.lane_spacing <= 0:
        parser.error("delay-ms 必须非负，hold-ms 和 lane-spacing 必须大于 0")
    if args.flick_hold_ms <= 0 or (args.flick_delay_ms is not None and args.flick_delay_ms < 0):
        parser.error("flick-hold-ms 必须为正，flick-delay-ms 必须非负")
    if not 0 < args.slide_overlap_ms <= 200:
        parser.error("slide-overlap-ms 必须位于 (0,200] 内")
    if not 0 < args.green_gap_ms < args.max_hold_ms:
        parser.error("需满足 0 < green-gap-ms < max-hold-ms")
    if not 0 <= args.green_tail_release_ms <= 200:
        parser.error("green-tail-release-ms 必须位于 [0,200] 内")
    bottom_y = args.judgment_y - args.bottom_offset
    if not (0 <= args.bottom_band[0] < bottom_y < args.bottom_band[1] <= 900
            and args.bottom_offset >= 0 and args.measure_tolerance_ms > 0
            and all(0 <= x < 1600 for x in args.key_x)
            and all(a < b for a, b in zip(args.key_x, args.key_x[1:]))):
        parser.error("底部检测线必须在 bottom-band 内，offset 非负、容差正数、key-x 递增且位于 [0,1600)")
    if not (0 <= args.top_band[0] <= args.observation_y < args.top_band[1] <= 900
            and 0 <= args.judgment_y < 900):
        parser.error("需满足 0 <= Y1 <= observation-y < Y2 <= 900，且 judgment-y 位于 [0,900)")
    return args, parser


def hold_offset_bounds(action, token):
    """Conservative windows relative to calibrated arrival, in milliseconds.

    Initial heads retain tap jitter. Migration presses and paired handoffs use
    the same clamp, preserving overlap. Ordinary tails already include the
    controller's release buffer; flick tails have their own atomic schedule.
    """
    if action == 'hold-up':
        return (0, 20)
    if action == 'handoff-up' or (action == 'hold-down' and ':' in str(token)
                                and str(token).split(':')[1] != '0'):
        return (20, 60)
    return None


def main():
    args, parser = parse_args()
    bottom_y = args.judgment_y - args.bottom_offset
    cv2.setNumThreads(args.cv_threads)
    user = windows_api()
    items = list_windows(user)
    if args.list:
        for hwnd, title in items:
            print(f"0x{hwnd:X}  {title}")
        return
    hwnd = args.hwnd
    if hwnd is None:
        if not args.title:
            parser.error("请使用 --title 或 --hwnd 指定窗口；--list 可查看窗口列表")
        matches = [(handle, title) for handle, title in items if args.title.casefold() in title.casefold()]
        if len(matches) != 1:
            parser.error(f"找到 {len(matches)} 个匹配窗口，请用 --list 查看并使用 --hwnd 精确选择")
        hwnd = matches[0][0]
    if args.test_key:
        keyboard = Keyboard(args.input_mode)
        print(f"单键测试 mode={args.input_mode} key={args.test_key} hold={args.hold_ms}ms；"
              "3 秒内切回 MuMu 游戏画面。", flush=True)
        time.sleep(3)
        if not keyboard.focused(hwnd):
            raise RuntimeError("目标 MuMu 不在前台，取消单键测试")
        pressed = False
        try:
            keyboard.send(args.test_key)
            pressed = True
            print(f"[test-down] key={args.test_key} mode={args.input_mode}", flush=True)
            time.sleep(args.hold_ms / 1000)
        finally:
            if pressed:
                keyboard.send(args.test_key, up=True)
                print(f"[test-up] key={args.test_key} mode={args.input_mode}", flush=True)
        return
    name = "Rhythm vision | 1-4 view | [ ] delay | SPACE pause | Q exit"
    if not args.no_preview:
        cv2.namedWindow(name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(name, args.preview_width, round(args.preview_width * 9 / 16))
    mode = ord("1")
    previous = time.perf_counter()
    fps = 0.0
    tracker = CrossingTracker(args.lane_spacing)
    flick_tracker = CrossingTracker(args.lane_spacing)
    bottom_spacing = (args.key_x[-1] - args.key_x[0]) / 6 * (5 + .36 * bottom_y) / (5 + .36 * args.judgment_y)
    bottom_tracker = CrossingTracker(bottom_spacing, match_distance=180)
    estimator = DelayEstimator(args.delay_ms, args.measure_tolerance_ms)
    scheduler = TapScheduler(hwnd, args.send_keys, args.hold_ms, key_log=args.key_log,
                             input_mode=args.input_mode, max_hold_ms=args.max_hold_ms,
                             flick_hold_ms=args.flick_hold_ms, timing_jitter=args.timing_jitter,
                             jitter_seed=args.jitter_seed, jitter_mean_ms=args.jitter_mean_ms,
                             jitter_sigma_ms=args.jitter_sigma_ms)
    if scheduler.jitter:
        print(f"正态偏移 μ={args.jitter_mean_ms:+g}ms σ={args.jitter_sigma_ms:g}ms；"
              f"普通点击/独立 flick 理论 Perfect={scheduler.jitter.perfect_probability():.2%}（假设基础误差为零）")
    def new_green_controller():
        if args.green_slides:
            return SlidingHoldController(args.lane_spacing, args.green_gap_ms, args.slide_overlap_ms,
                                         tail_release_ms=args.green_tail_release_ms)
        return GreenHoldController(args.lane_spacing, args.green_gap_ms,
                                   tail_release_ms=args.green_tail_release_ms)

    green_controller = new_green_controller()
    paused = False
    delay_ms = args.delay_ms
    flick_offset_ms = 0 if args.flick_delay_ms is None else args.flick_delay_ms - args.delay_ms
    last_report = time.perf_counter()
    hotkeys = None
    print("无预览模式：Ctrl+C 退出。" if args.no_preview else
          "预览已启动：1 特征图 / 2 标注图 / 3 过滤画面 / 4 原图；Q 或 Esc 退出。")
    print("这是屏幕区域捕捉：目标需保持可见且未被遮挡；最小化时暂停。")
    print(f"普通音符输出：{'真实按键（仅目标窗口聚焦时）' if args.send_keys else 'dry-run 日志'}；延迟 {delay_ms}ms")
    if args.flicks:
        print(f"flick 输出：qweruio；延迟 {delay_ms + flick_offset_ms}ms；"
              f"宏按键持续 {args.flick_hold_ms}ms；热键同步调整 tap/flick 延迟")
    if args.green_slides:
        print(f"绿色换轨：相邻双键保留；旧轨出现新 tap 时让出；跨多轨重叠 {args.slide_overlap_ms}ms")
    print(f"键盘输入方式：{args.input_mode}")
    if not args.no_preview:
        print("预览获得焦点后：[ / ] 调整延迟 -/+10ms，空格暂停/恢复。")
    try:
        if not args.no_hotkeys:
            hotkeys = GlobalHotkeys(user)
            print("全局快捷键：F6/F7 -/+50ms；F8/F9 -/+10ms；F10 暂停/恢复；F11 退出。", flush=True)
        with mss.mss() as capture:
            while True:
                start = time.perf_counter()
                exit_requested = False
                for action in hotkeys.poll() if hotkeys else ():
                    if action == "exit":
                        exit_requested = True
                    elif action == "pause":
                        paused = not paused
                        scheduler.cancel()
                        green_controller = new_green_controller()
                        tracker = CrossingTracker(args.lane_spacing)
                        flick_tracker = CrossingTracker(args.lane_spacing)
                        bottom_tracker = CrossingTracker(bottom_spacing, match_distance=180)
                        estimator = DelayEstimator(delay_ms, args.measure_tolerance_ms)
                        print(f"[control] {'PAUSED' if paused else 'RESUMED'} delay={delay_ms:.0f}ms", flush=True)
                    else:
                        updated = max(0, -flick_offset_ms if args.flicks else 0, delay_ms + action)
                        scheduler.adjust_delay(updated - delay_ms)
                        delay_ms = updated
                        estimator = DelayEstimator(delay_ms, args.measure_tolerance_ms)
                        print(f"[delay] {delay_ms:.0f}ms (pending taps updated)", flush=True)
                if exit_requested:
                    break
                region = capture_region(user, hwnd, args.crop)
                if region is None:
                    scheduler.cancel()
                    green_controller = new_green_controller()
                    tracker = CrossingTracker(args.lane_spacing)
                    flick_tracker = CrossingTracker(args.lane_spacing)
                    bottom_tracker = CrossingTracker(bottom_spacing, match_distance=180)
                    estimator = DelayEstimator(delay_ms, args.measure_tolerance_ms)
                    if args.no_preview:
                        time.sleep(min(0.05, 1 / args.fps))
                        continue
                    frame = np.zeros((450, 800, 3), np.uint8)
                    cv2.putText(frame, "Target minimized: restore to resume", (25, 220),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
                else:
                    if args.no_preview and args.region == "top":
                        processing, captured = capture_top_only(capture, region, args.process_width, args.top_band)
                    else:
                        original = np.asarray(capture.grab(region))[:, :, :3].copy()
                        captured = time.perf_counter()
                        processing = original
                        if original.shape[1] > args.process_width:
                            ratio = args.process_width / original.shape[1]
                            processing = cv2.resize(original, None, fx=ratio, fy=ratio,
                                                    interpolation=cv2.INTER_AREA)
                    if args.region == "top":
                        result, artifacts = analyze_top_frame(processing, *args.top_band,
                                                              args.observation_y, args.judgment_y,
                                                              render=not args.no_preview, green_holds=args.green_holds,
                                                              lane_spacing=args.lane_spacing, flicks=args.flicks)
                    else:
                        result, artifacts = analyze_frame(processing, features_only=mode in (ord("1"), ord("4")),
                                                          render=not args.no_preview)
                        if args.green_holds:
                            support, _ = analyze_top_frame(processing, *args.top_band,
                                                          args.observation_y, args.judgment_y,
                                                          render=False, green_holds=True,
                                                          lane_spacing=args.lane_spacing)
                            result.update({key: support[key] for key in ("green_positions", "green_ribbon")})
                    bottom_notes = [] if args.no_preview else detect_bottom_blue(processing, *args.bottom_band)
                    active = not paused and (not args.send_keys or scheduler.keyboard.focused(hwnd))
                    if active:
                        observation = args.observation_y * processing.shape[0] / 900
                        yellow_holds = [n for n in result["yellow_notes"] if n["hold_connected"]]
                        yellow_taps = [n for n in result["yellow_notes"] if not n["hold_connected"]]
                        flick_events = []
                        if args.flicks:
                            flick_events = flick_tracker.update(result["flick_notes"], processing.shape[1],
                                                                processing.shape[0], observation, captured)
                            if args.green_holds:
                                flick_events, tails = green_controller.flick_tails(flick_tracker, flick_events, captured)
                                for note_id, lane, crossed, keys in tails:
                                    scheduler.schedule_flick_tail(note_id, lane,
                                        crossed + (delay_ms + flick_offset_ms) / 1000, keys,
                                        release_ms=args.flick_tail_release_ms, offset_bounds=(20, 50))
                        if args.green_holds:
                            slide_options = {"positions": result["green_positions"]} if args.green_slides else {}
                            transitions = green_controller.update(result["green_nodes"] + yellow_holds, result["green_ribbon"],
                                                                  processing.shape[1], processing.shape[0], observation, captured,
                                                                  **slide_options)
                            for index, (action, token, lane, crossed) in enumerate(transitions):
                                final = not any(a == 'hold-up' and t.split(':')[0] == token.split(':')[0]
                                                for a, t, _, _ in transitions[index + 1:])
                                bounds = hold_offset_bounds(action, token)
                                scheduler.schedule_hold(action, token, lane, crossed + delay_ms / 1000,
                                                        final=final, offset_bounds=bounds)
                        events = tracker.update(result["blue_notes"] + yellow_taps, processing.shape[1],
                                                processing.shape[0], observation, captured)
                        for note_id, lane, crossing in events:
                            spare = green_controller.detach_spare_key(lane) if args.green_slides else None
                            scheduler.schedule(note_id, lane, crossing + delay_ms / 1000, release_token=spare)
                            if not args.no_preview:
                                scheduler.log(f"[cross] note={note_id} key={'asdfjkl'[lane]} delay={delay_ms:.0f}ms")
                        estimator.add_top(events)
                        if args.flicks:
                            for note_id, lane, crossing in flick_events:
                                scheduler.schedule_flick(note_id, lane, crossing + (delay_ms + flick_offset_ms) / 1000)
                                if not args.no_preview:
                                    scheduler.log(f"[flick-cross] note=F{note_id} key={'qweruio'[lane]} "
                                          f"delay={delay_ms + flick_offset_ms:.0f}ms", flush=True)
                        bottom_events = bottom_tracker.update(bottom_notes, processing.shape[1], processing.shape[0],
                                                              bottom_y * processing.shape[0] / 900, captured,
                                                              arrival_y=args.judgment_y * processing.shape[0] / 900)
                        for lane, measured in estimator.add_bottom(bottom_events):
                            scheduler.log(f"[travel-estimate] key={'asdfjkl'[lane]} {measured:.1f}ms; {estimator.summary()}")
                        estimator.expire(captured)
                    else:
                        scheduler.cancel()
                        green_controller = new_green_controller()
                        tracker = CrossingTracker(args.lane_spacing)
                        flick_tracker = CrossingTracker(args.lane_spacing)
                        bottom_tracker = CrossingTracker(bottom_spacing, match_distance=180)
                        estimator = DelayEstimator(delay_ms, args.measure_tolerance_ms)
                    if scheduler.error:
                        raise RuntimeError(scheduler.error)
                    now = time.perf_counter()
                    fps = 0.9 * fps + 0.1 / max(now - previous, 1e-6)
                    previous = now
                    if args.no_preview:
                        if now - last_report >= 2:
                            scheduler.log(f"[status] {fps:.1f} FPS cap:{(captured-start)*1000:.1f}ms "
                                  f"cv:{(now-captured)*1000:.1f}ms delay:{delay_ms:.0f}ms "
                                  f"{'ACTIVE' if active else 'PAUSED (focus MuMu)'} {scheduler.timing_summary()}")
                            last_report = now
                        remaining = 1 / args.fps - (time.perf_counter() - start)
                        if remaining > 0:
                            time.sleep(remaining)
                        continue
                    key = {ord("1"): "08_features.png", ord("2"): "04_overlay.png",
                           ord("3"): "06_relevant_only.png"}.get(mode)
                    frame = artifacts[key] if key else original
                    line = result["judgment_line"]
                    status = (f"{fps:.1f} FPS | B:{len(result['blue_notes'])} G:{len(result['green_nodes'])} "
                              f"F:{len(result['flick_notes'])} | {args.region} line:{line['y'] if line else 'missing'} "
                              f"| cap:{(captured-start)*1000:.1f}ms cv:{(now-captured)*1000:.1f}ms")
                    cv2.putText(frame, status, (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
                    cv2.putText(frame, f"{'SEND' if args.send_keys else 'DRY'} {'ACTIVE' if active else 'PAUSED'} "
                                f"delay:{delay_ms:.0f}ms hold:{args.hold_ms:.0f}ms", (15, 52),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1)
                    cv2.putText(frame, estimator.summary(), (15, 74), cv2.FONT_HERSHEY_SIMPLEX,
                                .55, (40, 255, 90), 1)
                    fsx, fsy = frame.shape[1] / processing.shape[1], frame.shape[0] / processing.shape[0]
                    for note in bottom_notes:
                        x, y = round(note['x'] * fsx), round(note['y'] * fsy)
                        w, h = round(note['width'] * fsx), round(note['height'] * fsy)
                        cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 210, 0), 1)
                    by = round(bottom_y * frame.shape[0] / 900)
                    cv2.line(frame, (0, by), (frame.shape[1] - 1, by), (120, 180, 120), 1)
                    draw_bottom_keys(frame, args.judgment_y, args.key_x)
                    # Seven calibration ticks on the fixed observation line.
                    canvas_sx, canvas_sy = frame.shape[1] / 1600, frame.shape[0] / 900
                    for lane, letter in enumerate("asdfjkl"):
                        x = round((800 + (lane - 3) * args.lane_spacing) * canvas_sx)
                        y = round(args.observation_y * canvas_sy)
                        cv2.line(frame, (x, y - 5), (x, y + 5), (255, 255, 255), 1)
                        cv2.putText(frame, letter, (x - 4, y + 18), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.4, (255, 255, 255), 1)
                scale = min(1.0, args.preview_width / frame.shape[1])
                if scale < 1:
                    frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                cv2.imshow(name, frame)
                delay = max(1, round((1 / args.fps - (time.perf_counter() - start)) * 1000))
                key = cv2.waitKey(delay) & 0xFF
                if key in (27, ord("q"), ord("Q")) or cv2.getWindowProperty(name, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if key in map(ord, "1234"):
                    mode = key
                if key in (ord("["), ord("]"), ord(" ")):
                    if key == ord(" "):
                        paused = not paused
                        scheduler.cancel()
                        green_controller = new_green_controller()
                        tracker = CrossingTracker(args.lane_spacing)
                        flick_tracker = CrossingTracker(args.lane_spacing)
                        bottom_tracker = CrossingTracker(bottom_spacing, match_distance=180)
                    else:
                        updated = max(0, -flick_offset_ms if args.flicks else 0,
                                      delay_ms + (10 if key == ord("]") else -10))
                        scheduler.adjust_delay(updated - delay_ms)
                        delay_ms = updated
                    estimator = DelayEstimator(delay_ms, args.measure_tolerance_ms)
                    print(f"delay={delay_ms:.0f}ms paused={paused}", flush=True)
    finally:
        if hotkeys:
            hotkeys.close()
        scheduler.close()
        if not args.no_preview:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("已停止，取消待输出按键并释放已按下的键。")
    except (RuntimeError, mss.exception.ScreenShotError) as exc:
        print(f"捕捉失败：{exc}", file=sys.stderr)
        sys.exit(1)
