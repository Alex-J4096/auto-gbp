"""ADB GBP Touch: scrcpy 3.3.3 probe and standard-library transport."""
import argparse
from contextlib import contextmanager
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time

VERSION = '3.3.3'


def touch_packet(action, pointer, x, y, width, height):
    if action not in (0, 1, 2) or not 0 <= pointer < 10:
        raise ValueError('invalid action / pointer')
    if not (0 < width <= 65535 and 0 < height <= 65535 and
            0 <= x < width and 0 <= y < height):
        raise ValueError('触点超出 Android 屏幕范围')
    # INJECT_TOUCH_EVENT; pressure is unsigned 16-bit fixed point.
    return struct.pack('>BBQiiHHHII', 2, action, pointer, x, y, width, height,
                       0 if action == 1 else 65535, 0, 0)


class Touch:
    def __init__(self, connection, width, height, log=True):
        self.connection, self.width, self.height = connection, width, height
        self.active = {}
        self.log = log

    def event(self, action, pointer, x, y):
        if (action == 0) == (pointer in self.active):
            raise ValueError('重复按下或操作未按下的触点')
        packet = touch_packet(action, pointer, x, y, self.width, self.height)
        # Track attempted downs too, so cleanup attempts an UP on partial failure.
        self.active[pointer] = (x, y)
        self.connection.sendall(packet)
        if action == 1:
            del self.active[pointer]
        if self.log and action != 2:
            print(f'[touch-sent] {"DOWN" if action == 0 else "UP"} id={pointer} x={x} y={y}', flush=True)

    def release_all(self):
        for pointer, (x, y) in list(self.active.items()):
            try:
                self.event(1, pointer, x, y)
            except OSError as exc:
                print(f'[warning] 无法确认触点 {pointer} 已释放: {exc}')

    def move(self, pointer, target, duration):
        start = self.active[pointer]
        count = max(1, round(duration * 120))
        began = time.perf_counter()
        for step in range(1, count + 1):
            time.sleep(max(0, began + duration * step / count - time.perf_counter()))
            point = tuple(round(a + (b - a) * step / count) for a, b in zip(start, target))
            self.event(2, pointer, *point)


class Adb:
    def __init__(self, executable, serial):
        self.executable, self.serial = executable, serial

    def command(self, *args, device=True):
        return [self.executable] + (['-s', self.serial] if device else []) + list(args)

    def run(self, *args, device=True):
        result = subprocess.run(self.command(*args, device=device), capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=20)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip()


@contextmanager
def connection(adb, server):
    scid = f'{secrets.randbits(31):08x}'
    remote = f'/data/local/tmp/auto-gbp-touch-{scid}.jar'
    port, process, sock = None, None, None
    pushed = False
    try:
        adb.run('push', str(server.resolve()), remote)
        pushed = True
        port = int(adb.run('forward', 'tcp:0', f'localabstract:scrcpy_{scid}'))
        process = subprocess.Popen(adb.command('shell', f'CLASSPATH={remote}', 'app_process', '/',
            'com.genymobile.scrcpy.Server', VERSION, f'scid={scid}', 'video=false', 'audio=false',
            'control=true', 'tunnel_forward=true', 'send_device_meta=false',
            'clipboard_autosync=false', 'power_on=false', 'cleanup=false'),
            # Inherit console: preserve server errors without a blocked PIPE.
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('scrcpy 服务退出；检查上方错误、版本和注入权限')
            candidate = None
            try:
                candidate = socket.create_connection(('127.0.0.1', port), timeout=.5)
                if candidate.recv(1) == b'\x00':
                    sock = candidate
                    break
            except OSError:
                pass
            if candidate:
                candidate.close()
            time.sleep(.1)
        if sock is None:
            raise RuntimeError('scrcpy 控制连接超时')
        sock.settimeout(2)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        yield sock
    finally:
        if sock:
            sock.close()
        if process:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
        for args in ([('forward', '--remove', f'tcp:{port}')] if port else []) + (
                [('shell', 'rm', '-f', remote)] if pushed else []):
            try:
                adb.run(*args)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f'[warning] 清理失败: {exc}')


def demo(touch, mode, hold):
    w, h = touch.width, touch.height
    a, b = (round(w * .3), round(h * .7)), (round(w * .7), round(h * .7))
    touch.event(0, 0, *a)
    touch.event(0, 1, *b)
    print('[stage] 两指保持（依次发送 DOWN，并非原子同时按下）')
    time.sleep(hold)
    if mode == 'slide':
        print('[stage] 指 0 横移，指 1 不动')
        touch.move(0, (round(w * .5), a[1]), 1)
    elif mode == 'flick':
        print('[stage] 指 0 长押后向上 flick，指 1 不动')
        touch.move(0, (a[0], round(h * .5)), .06)
    touch.event(1, 0, *touch.active[0])
    print('[stage] 指 0 已抬起，指 1 应继续保持')
    time.sleep(hold)
    touch.event(1, 1, *touch.active[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default='adb', help='adb.exe 路径（默认 PATH）')
    parser.add_argument('--serial', required=True, help='本地模拟器 ADB 地址，如 127.0.0.1:实际端口')
    parser.add_argument('--server', type=Path, help='官方 scrcpy-server-v3.3.3 文件')
    parser.add_argument('--size', nargs=2, type=int, default=(1600, 900), metavar=('W', 'H'))
    parser.add_argument('--mode', choices=('check', 'dual', 'slide', 'flick'), default='check')
    parser.add_argument('--hold-seconds', type=float, default=2)
    parser.add_argument('--send-touch', action='store_true', help='明确启用真实触摸')
    args = parser.parse_args(argv)
    if not all(10 <= v <= 65535 for v in args.size) or not .1 <= args.hold_seconds <= 30:
        parser.error('size 必须在 10..65535；hold-seconds 必须在 0.1..30')
    if args.mode != 'check' and (not args.send_touch or not args.server or not args.server.is_file()):
        parser.error('触摸测试需要 --send-touch 和有效的 --server 文件')
    # Do not silently connect to a remote device or select an arbitrary emulator.
    host, separator, port = args.serial.rpartition(':')
    if host not in ('127.0.0.1', 'localhost') or not separator or not port.isdigit() or not 1 <= int(port) <= 65535:
        parser.error('--serial 必须是 localhost:端口 或 127.0.0.1:端口')
    adb = Adb(args.adb, args.serial)
    print(adb.run('connect', args.serial, device=False))
    if adb.run('get-state') != 'device':
        raise RuntimeError('设备未就绪')
    print('[device]', adb.run('shell', 'getprop', 'ro.product.model'))
    print('[wm size]', adb.run('shell', 'wm', 'size'))
    print('坐标按当前 Android 横屏像素计算；wm size 可能是自然方向，测试时禁止旋转。')
    if args.mode == 'check':
        print('ADB 检查完成，未启动触摸服务、未发送触摸。')
        return
    print('请切到多点触摸测试画面或练习模式，关闭其他自动输入。')
    if input(f'将以 {args.size[0]}x{args.size[1]} 发送 {args.mode} 测试；输入 TOUCH 确认: ') != 'TOUCH':
        return
    with connection(adb, args.server) as sock:
        touch = Touch(sock, *args.size)
        try:
            print('3 秒后开始；Ctrl+C 尝试释放全部触点。')
            time.sleep(3)
            demo(touch, args.mode, args.hold_seconds)
        finally:
            touch.release_all()
    print('发送完成；sent 仅表示写入连接，需在模拟器里确认接收效果。')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n测试中止。')
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f'触摸测试失败: {exc}')
