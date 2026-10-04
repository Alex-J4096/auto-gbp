"""ADB GBP Touch validated multi-finger timelines; standard library only."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import math
import threading
import time

from .probe import Adb, Touch, connection, touch_packet


@contextmanager
def open_device(serial, server, size=(1920, 1080), adb='adb', log=False):
    """Connect once; attempt UPs before closing the socket."""
    host, _, port = serial.rpartition(':')
    if host not in ('localhost', '127.0.0.1') or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError('serial 必须是 localhost:端口 或 127.0.0.1:端口')
    server = Path(server)
    if not server.is_file() or server.suffix.lower() == '.exe':
        raise ValueError('server 需要 scrcpy 3.3.3 Android 服务端文件，不是 scrcpy.exe')
    width, height = size
    touch_packet(0, 0, 0, 0, width, height)
    bridge = Adb(adb, serial)
    bridge.run('connect', serial, device=False)
    if bridge.run('get-state') != 'device':
        raise RuntimeError('ADB 设备未就绪')
    with connection(bridge, server) as sock:
        touch = Touch(sock, width, height, log=log)
        try:
            yield touch
        finally:
            touch.release_all()


@dataclass(frozen=True)
class Event:
    at: float
    action: int  # 0 DOWN, 1 UP, 2 MOVE
    pointer: int
    point: tuple[int, int] | None = None


class Macro:
    """One timeline for all fingers; seconds relative to play()."""
    def __init__(self):
        self.events = []

    def down(self, at, pointer, x, y):
        self.events.append(Event(at, 0, pointer, (x, y)))
        return self

    def move(self, at, pointer, x, y):
        self.events.append(Event(at, 2, pointer, (x, y)))
        return self

    def up(self, at, pointer):
        self.events.append(Event(at, 1, pointer))
        return self

    @staticmethod
    def _duration(duration):
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('duration 必须是正有限秒数')

    def tap(self, at, pointer, x, y, duration=.03):
        self._duration(duration)
        return self.down(at, pointer, x, y).up(at + duration, pointer)

    def slide(self, at, pointer, start, end, duration=.06, hz=120):
        """Move an existing finger; no DOWN/UP inserted."""
        self._duration(duration)
        if not math.isfinite(hz) or not 1 <= hz <= 1000 or duration * hz > 100000:
            raise ValueError('hz 需在 1..1000，单段采样数不超过 100000')
        if len(start) != 2 or len(end) != 2:
            raise ValueError('start/end 必须是二维坐标')
        steps = max(1, round(duration * hz))
        for step in range(1, steps + 1):
            x, y = (round(a + (b - a) * step / steps) for a, b in zip(start, end))
            self.move(at + duration * step / steps, pointer, x, y)
        return self

    def flick(self, at, pointer, start, end, duration=.06):
        """Standalone flick; use slide()+up() for a held finger."""
        self._duration(duration)
        return (self.down(at, pointer, *start).slide(at, pointer, start, end, duration)
                .up(at + duration, pointer))

    def validate(self, width, height):
        active = {}
        ordered = sorted(self.events, key=lambda event: event.at)
        for event in ordered:
            if not math.isfinite(event.at) or event.at < 0:
                raise ValueError('事件时间必须是非负有限秒数')
            if event.action not in (0, 1, 2):
                raise ValueError('未知动作')
            if (event.action == 0) == (event.pointer in active):
                raise ValueError(f'触点 {event.pointer} 重复按下或尚未按下')
            point = active[event.pointer] if event.action == 1 else event.point
            if point is None:
                raise ValueError('DOWN/MOVE 缺少坐标')
            touch_packet(event.action, event.pointer, *point, width, height)
            if event.action == 1:
                del active[event.pointer]
            else:
                active[event.pointer] = point
        if active:
            raise ValueError('宏结束前必须显式释放全部触点')
        return ordered

    def play(self, touch, cancel=None):
        """Blocks the caller, but interleaves fingers. Not thread-safe.

        Pass threading.Event for cancellation; False means cancelled.
        Validate the complete timeline before any input. Errors propagate.
        """
        if touch.active:
            raise ValueError('播放宏前必须没有已按下触点')
        ordered = self.validate(touch.width, touch.height)
        cancel = cancel if cancel is not None else threading.Event()
        began = time.perf_counter()
        try:
            for event in ordered:
                if cancel.wait(max(0, began + event.at - time.perf_counter())):
                    return False
                point = touch.active[event.pointer] if event.action == 1 else event.point
                touch.event(event.action, event.pointer, *point)
            return True
        finally:
            touch.release_all()
