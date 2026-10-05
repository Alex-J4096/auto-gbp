"""Persistent multi-touch transport and non-blocking gesture scheduling."""
from contextlib import contextmanager
from pathlib import Path
import heapq
import time

from .tap_output import TapScheduler, FlickTailMixin, CrossingTracker

from adb_gbp_touch import Adb, Touch, connection


class LaneTouchOutput:
    def __init__(self, sock, size, key_x, judgment_y, focus):
        self.touch = Touch(sock, *size, log=False)
        self.focus = focus
        self.tokens = {}
        self.points = [(min(size[0] - 1, round(x * size[0] / 1600)),
                        min(size[1] - 1, round(judgment_y * size[1] / 900))) for x in key_x]

    def focused(self, hwnd):
        return self.focus(hwnd)

    def send(self, key, up=False):
        if key not in 'asdfjkl':
            raise ValueError('send 仅用于兼容轨道按键；触摸手势必须使用 TouchScheduler')
        pointer = 'asdfjkl'.index(key)
        self.touch.event(1 if up else 0, pointer, *self.points[pointer])

    def release_all(self):
        self.touch.release_all()
        self.tokens.clear()

    def point(self, lane):
        lane = max(0., min(6., lane))
        left = min(5, int(lane))
        fraction = lane - left
        return (round(self.points[left][0] * (1 - fraction) + self.points[left + 1][0] * fraction),
                self.points[left][1])

    def down(self, token, lane):
        if token in self.tokens:
            raise ValueError('重复触点身份')
        pointer = next((i for i in range(10) if i not in self.touch.active), None)
        if pointer is None:
            raise RuntimeError('触点超过 10 个，停止输出')
        self.tokens[token] = pointer
        self.touch.event(0, pointer, *self.point(lane))

    def move(self, token, point):
        self.touch.event(2, self.tokens[token], *point)

    def up(self, token):
        pointer = self.tokens[token]
        self.touch.event(1, pointer, *self.touch.active[pointer])
        del self.tokens[token]


class TouchSlideController(FlickTailMixin):
    """Follow uniquely matched ribbons with a stable identity, never key handoff."""
    def __init__(self, spacing=43, gap_ms=60, tail_release_ms=0, flick_guard_ms=250):
        self.tracker = CrossingTracker(spacing)
        self.gap, self.tail_release, self.flick_guard = gap_ms / 1000, tail_release_ms / 1000, flick_guard_ms / 1000
        self.active, self.sequence = {}, 0

    def detach_spare_key(self, lane):
        return None

    def update(self, nodes, ribbon, width, height, line_y, now, positions=None):
        positions = list(positions or [])
        transitions = []
        candidates = {i: [p for p in positions if abs(p - s['position']) <= .9]
                      for i, s in self.active.items()}
        for identity, state in self.active.items():
            matches = candidates[identity]
            if len(matches) == 1 and sum(matches[0] in c for c in candidates.values()) == 1:
                position = max(0., min(6., matches[0]))
                state['position'], state['gap_start'] = position, None
                transitions.append(('hold-move', state['token'], position, now))
            elif matches or now < state.get('flick_pending_until', 0):
                state['gap_start'] = None  # ambiguous evidence must not release or swap identities
            elif state['gap_start'] is None:
                state['gap_start'] = now
        crossings = self.tracker.update(nodes, width, height, line_y, now)
        tracks = {t['id']: t for t in self.tracker.tracks}
        for note_id, lane, crossing in crossings:
            related = [s for s in self.active.values() if abs(s['position'] - lane) <= .8]
            if related:
                if len(related) == 1:
                    related[0]['last_node'] = crossing
                    related[0]['gap_start'] = None
                continue
            track = tracks[note_id]
            if track.get('green_role') == 'tail' and track.get('role_frames', 0) >= 2:
                continue
            self.sequence += 1
            token = f'TS{self.sequence}'
            self.active[self.sequence] = dict(token=token, position=float(lane), lane=lane,
                keys={lane: token}, last_node=crossing, gap_start=None)
            transitions.append(('hold-down', token, lane, crossing))
        for identity, state in list(self.active.items()):
            gap = state['gap_start']
            if gap is not None and now - gap >= self.gap:
                transitions.append(('hold-up', state['token'], state['position'],
                                    max(gap, state['last_node']) + self.tail_release))
                del self.active[identity]
        return transitions


class TouchScheduler(TapScheduler):
    """One timed queue for all fingers; gestures never sleep on the sender thread."""
    def __init__(self, *args, flick_duration_ms=60, flick_distance=120, **kwargs):
        self.flick_duration = flick_duration_ms / 1000
        self.flick_distance = flick_distance
        self.flicking = set()
        super().__init__(*args, **kwargs)

    def push(self, due, action, payload, token):
        self.sequence += 1
        heapq.heappush(self.queue, (due, self.sequence, action, payload, token))
        self.condition.notify()

    def schedule(self, note_id, lane, deadline, release_token=None):
        with self.condition:
            token = f'T{note_id}'
            self.push(self.offset_deadline(deadline, token), 'tap', lane, token)

    def schedule_hold(self, action, token, lane, deadline, final=True, offset_bounds=None):
        if action not in ('hold-down', 'hold-move', 'hold-up'):
            raise ValueError('触摸模式不允许键盘换轨动作')
        with self.condition:
            if token not in self.hold_offsets:
                self.hold_offsets[token] = self.jitter.sample_ms() if self.jitter else 0
            due = self.offset_deadline(deadline, token, self.hold_offsets[token], offset_bounds)
            self.push(due, action, lane, token)
            if action == 'hold-up':
                self.hold_offsets.pop(token, None)

    def schedule_flick(self, note_id, lane, deadline):
        with self.condition:
            token = f'F{note_id}'
            self.push(self.offset_deadline(deadline, token), 'flick', lane, token)

    def schedule_flick_tail(self, note_id, lane, deadline, keys, release_ms=30, offset_bounds=None):
        if len(keys) != 1:
            raise ValueError('触摸长押尾必须对应唯一触点')
        token = next(iter(keys.values()))
        with self.condition:
            due = self.offset_deadline(deadline, token, self.hold_offsets.pop(token, 0), offset_bounds)
            self.push(due, 'tail-flick', lane, token)

    def cancel(self):
        with self.condition:
            self.queue.clear()
            self.hold_offsets.clear()
            for token in self.pressed:
                self.push(time.perf_counter(), 'up', None, token)

    def adjust_delay(self, delta_ms):
        with self.condition:
            self.queue = [(due + delta_ms / 1000 if action in
                           ('tap', 'hold-down', 'hold-move', 'hold-up', 'flick', 'tail-flick') else due,
                           seq, action, payload, token) for due, seq, action, payload, token in self.queue]
            heapq.heapify(self.queue)
            self.condition.notify()

    def start_flick(self, token, now):
        self.flicking.add(token)
        # Any earlier slide samples must not overwrite the flick movement.
        point = self.keyboard.touch.active[self.keyboard.tokens[token]] if self.enabled else (0, 200)
        distance = self.flick_distance * (self.keyboard.touch.height / 900 if self.enabled else 1)
        target_y = max(0, round(point[1] - distance))
        steps = max(2, round(self.flick_duration * 120))
        for step in range(1, steps + 1):
            target = (point[0], round(point[1] + (target_y - point[1]) * step / steps))
            self.push(now + self.flick_duration * step / steps, 'flick-move', target, token)
        self.push(now + self.flick_duration, 'up', None, token)

    def run(self):
        try:
            while True:
                with self.condition:
                    if self.stopping:
                        break
                    if not self.queue:
                        self.condition.wait()
                        continue
                    wait = self.queue[0][0] - time.perf_counter()
                    if wait > 0:
                        self.condition.wait(wait)
                        continue
                    due, _, action, payload, token = heapq.heappop(self.queue)
                    now = time.perf_counter()
                    if action in ('up', 'hold-up', 'safety-up'):
                        if token not in self.pressed:
                            continue
                        if self.enabled:
                            self.keyboard.up(token)
                        del self.pressed[token]
                        self.flicking.discard(token)
                    else:
                        if self.enabled and not self.keyboard.focused(self.hwnd):
                            self.cancel()
                            continue
                        if action in ('tap', 'hold-down', 'flick'):
                            if now - due > .1:
                                self.skipped_expired += 1
                                continue
                            if self.enabled:
                                self.keyboard.down(token, payload)
                            self.pressed[token] = payload
                            if action == 'tap':
                                self.push(now + self.hold, 'up', None, token)
                            elif action == 'hold-down':
                                self.push(now + self.max_hold, 'safety-up', None, token)
                            else:
                                self.start_flick(token, now)
                        elif token not in self.pressed:
                            if action == 'tail-flick':
                                self.log(f'[touch-skip] note={token} action=tail-flick reason=missing-contact')
                            continue  # missing/expired head: never create a replacement finger
                        elif action == 'tail-flick':
                            self.start_flick(token, now)
                        elif action == 'hold-move':
                            if token in self.flicking:
                                continue
                            if self.enabled:
                                self.keyboard.move(token, self.keyboard.point(payload))
                        elif action == 'flick-move' and self.enabled:
                            self.keyboard.move(token, payload)
                    self.send_late.append(max(0, (time.perf_counter() - due) * 1000))
                    if (self.key_log or not self.enabled) and action not in ('hold-move', 'flick-move'):
                        self.log(f'[{"touch-sent" if self.enabled else "dry-run"}] note={token} action={action} late={max(0, (now-due)*1000):.1f}ms')
        except Exception as exc:
            self.error = str(exc)
            self.log(f'[touch-error] {exc}')
        finally:
            if self.enabled:
                self.keyboard.release_all()
            self.pressed.clear()


@contextmanager
def open_touch_output(args, focus):
    server = Path(args.touch_server)
    if not server.is_file():
        raise RuntimeError(f'找不到 scrcpy 3.3.3 服务端: {server}')
    if server.suffix.lower() == '.exe':
        raise RuntimeError('touch-server 需要 scrcpy-server，不是 scrcpy.exe')
    adb = Adb(args.adb_path, args.adb_serial)
    print(adb.run('connect', args.adb_serial, device=False))
    if adb.run('get-state') != 'device':
        raise RuntimeError('ADB 设备未就绪')
    print('[touch-device]', adb.run('shell', 'getprop', 'ro.product.model'))
    print('[touch-size]', args.touch_size, '；请确认与 Android 当前横屏尺寸一致，运行中不要旋转')
    with connection(adb, server) as sock:
        sock.settimeout(.25)
        backend = LaneTouchOutput(sock, args.touch_size, args.key_x, args.judgment_y, focus)
        try:
            yield backend
        finally:
            backend.release_all()
