import time
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
import io
import threading
import ctypes
import sys

from tap_output import CrossingTracker, TapScheduler, DelayEstimator, Keyboard, GreenHoldController


def note(lane, y, scale=1):
    x = 800 + (lane - 3) * 43 * (5 + .36 * y) / (5 + .36 * 160)
    return {"center_x": x * scale, "center_y": y * scale}


class TapTests(unittest.TestCase):
    def test_all_flick_keys_and_release_with_fake_keyboard(self):
        calls = []
        finished = threading.Event()
        def send(key, up=False):
            calls.append((key, up))
            if sum(up for _, up in calls) == 7:
                finished.set()
        scheduler = TapScheduler(0, flick_hold_ms=5)
        scheduler.keyboard = SimpleNamespace(focused=lambda hwnd: True, send=send)
        scheduler.enabled = True
        try:
            for lane in range(7):
                scheduler.schedule_flick(lane, lane, time.perf_counter() + .02)
            self.assertTrue(finished.wait(1))
        finally:
            scheduler.close()
        self.assertEqual([k for k, up in calls if not up], list('qweruio'))
        self.assertEqual([k for k, up in calls if up], list('qweruio'))

    def test_flick_delay_shift_preserves_release(self):
        scheduler = TapScheduler(0)
        try:
            deadline = time.perf_counter() + 100
            scheduler.schedule_flick(1, 0, deadline)
            scheduler.adjust_delay(10)
            self.assertAlmostEqual(scheduler.queue[0][0], deadline + .01)
            self.assertEqual(scheduler.queue[0][2:4], ('flick-down', 'q'))
            scheduler.cancel()
            self.assertFalse(scheduler.queue)
        finally:
            scheduler.close()

    def test_green_hold_gap_debounce_and_node_release(self):
        controller = GreenHoldController(gap_ms=60, tail_release_ms=30)
        ribbon = [False] * 7
        ribbon[3] = True
        self.assertEqual(controller.update([note(3,150)], ribbon,1600,900,160,10), [])
        start = controller.update([note(3,170)], ribbon,1600,900,160,10.02)
        self.assertEqual(start[0][:3], ('hold-down','H1',3))
        self.assertEqual(controller.update([], [False]*7,1600,900,160,10.03), [])
        self.assertEqual(controller.update([], ribbon,1600,900,160,10.05), [])
        controller.update([note(3,150)], ribbon,1600,900,160,10.2)
        self.assertEqual(controller.update([note(3,170)], ribbon,1600,900,160,10.22), [])
        controller.update([], [False]*7,1600,900,160,10.23)
        end = controller.update([], [False]*7,1600,900,160,10.30)
        self.assertEqual(end[0][:3], ('hold-up','H1',3))
        # First missing frame + tail buffer, never backdated to an earlier node.
        self.assertAlmostEqual(end[0][3], 10.26)
        self.assertFalse(controller.active)

    def test_hold_has_no_tap_auto_release_and_suppresses_same_key_tap(self):
        released, pressed = threading.Event(), threading.Event()
        calls=[]
        def send(key,up=False):
            calls.append((key,up))
            (released if up else pressed).set()
        scheduler=TapScheduler(0,hold_ms=5,max_hold_ms=1000)
        scheduler.keyboard=SimpleNamespace(focused=lambda hwnd:True,send=send)
        scheduler.enabled=True
        try:
            scheduler.schedule_hold('hold-down','H1',3,time.perf_counter()+.01)
            self.assertTrue(pressed.wait(1))
            self.assertFalse(released.wait(.03))
            scheduler.schedule(99,3,time.perf_counter())
            scheduler.schedule_hold('hold-up','H1',3,time.perf_counter()+.03)
            self.assertTrue(released.wait(1))
        finally:
            scheduler.close()
        self.assertEqual(calls,[('f',False),('f',True)])

    def test_hold_watchdog_releases_without_detected_tail(self):
        released=threading.Event()
        calls=[]
        def send(key,up=False):
            calls.append((key,up))
            if up: released.set()
        scheduler=TapScheduler(0,max_hold_ms=20)
        scheduler.keyboard=SimpleNamespace(focused=lambda hwnd:True,send=send)
        scheduler.enabled=True
        try:
            scheduler.schedule_hold('hold-down','H1',0,time.perf_counter()+.01)
            self.assertTrue(released.wait(1))
        finally:
            scheduler.close()
        self.assertEqual(calls,[('a',False),('a',True)])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows input structures')
    def test_scan_and_vk_packets_without_sending_real_input(self):
        for mode in ('scan', 'vk'):
            keyboard = Keyboard(mode)
            packets = []
            def fake_send(count, pointer, size):
                event = ctypes.cast(pointer, ctypes.POINTER(keyboard.INPUT)).contents
                packets.append((event.type, event.data.ki.wVk, event.data.ki.wScan, event.data.ki.dwFlags))
                self.assertEqual(size, ctypes.sizeof(keyboard.INPUT))
                return count
            keyboard.user = SimpleNamespace(MapVirtualKeyW=lambda vk, kind: 0x21, SendInput=fake_send)
            keyboard.send('f')
            keyboard.send('f', up=True)
            self.assertEqual(packets, [(1, 0 if mode == 'scan' else ord('F'), 0x21, 8 if mode == 'scan' else 0),
                                       (1, 0 if mode == 'scan' else ord('F'), 0x21, 10 if mode == 'scan' else 2)])

    def test_successful_send_logs_with_fake_keyboard(self):
        released = threading.Event()
        calls = []
        def send(key, up=False):
            calls.append((key, up))
            if up:
                released.set()
        output = io.StringIO()
        with redirect_stdout(output):
            scheduler = TapScheduler(0, hold_ms=5, key_log=True)
            scheduler.keyboard = SimpleNamespace(focused=lambda hwnd: True, send=send)
            scheduler.enabled = True
            try:
                scheduler.schedule(42, 3, time.perf_counter() + .01)
                self.assertTrue(released.wait(1))
            finally:
                scheduler.close()
        self.assertEqual(calls, [('f', False), ('f', True)])
        self.assertIn('[send-down] note=42 key=f late=', output.getvalue())
        self.assertIn('[send-up] note=42 key=f late=', output.getvalue())

    def test_successful_send_logs_disabled_by_default(self):
        released = threading.Event()
        calls = []
        def send(key, up=False):
            calls.append((key, up))
            if up:
                released.set()
        output = io.StringIO()
        with redirect_stdout(output):
            scheduler = TapScheduler(0, hold_ms=5)
            scheduler.keyboard = SimpleNamespace(focused=lambda hwnd: True, send=send)
            scheduler.enabled = True
            try:
                scheduler.schedule(42, 3, time.perf_counter() + .01)
                self.assertTrue(released.wait(1))
            finally:
                scheduler.close()
        self.assertEqual(calls, [('f', False), ('f', True)])
        self.assertEqual(output.getvalue(), '')

    def test_all_lanes_and_no_repeated_crossing(self):
        for scale in (1, .5):
            tracker = CrossingTracker()
            self.assertEqual(tracker.update([note(i, 150, scale) for i in range(7)],
                                            1600 * scale, 900 * scale, 160 * scale, 10), [])
            events = tracker.update([note(i, 170, scale) for i in range(7)],
                                    1600 * scale, 900 * scale, 160 * scale, 10.02)
            self.assertEqual(sorted(e[1] for e in events), list(range(7)))
            self.assertTrue(all(abs(e[2] - 10.01) < 1e-6 for e in events))
            self.assertEqual(tracker.update([note(i, 180, scale) for i in range(7)],
                                            1600 * scale, 900 * scale, 160 * scale, 10.03), [])

    def test_startup_below_line_ignored(self):
        tracker = CrossingTracker()
        self.assertEqual(tracker.update([note(3, 170)], 1600, 900, 160, 10), [])
        self.assertEqual(tracker.update([note(3, 180)], 1600, 900, 160, 10.02), [])

    def test_same_lane_consecutive_notes(self):
        tracker = CrossingTracker()
        tracker.update([note(3, 150)], 1600, 900, 160, 10)
        self.assertEqual(len(tracker.update([note(3, 170), note(3, 130)], 1600, 900, 160, 10.02)), 1)
        self.assertEqual(len(tracker.update([note(3, 195), note(3, 170)], 1600, 900, 160, 10.04)), 1)

    def test_dry_scheduler_cancel_and_close(self):
        scheduler = TapScheduler(0)
        scheduler.schedule(1, 0, time.perf_counter() + 100)
        scheduler.cancel()
        with scheduler.condition:
            self.assertEqual(scheduler.queue, [])
        scheduler.close()
        self.assertFalse(scheduler.thread.is_alive())

    def test_delay_adjustment_preserves_release_and_pending_notes(self):
        scheduler = TapScheduler(0)
        try:
            due = time.perf_counter() + 100
            scheduler.schedule(1, 0, due)
            with scheduler.condition:
                scheduler.queue.append((due + 1, 99, 'up', 's', 2))
            scheduler.adjust_delay(-50)
            with scheduler.condition:
                down = next(item for item in scheduler.queue if item[2] == 'down')
                up = next(item for item in scheduler.queue if item[2] == 'up')
                self.assertAlmostEqual(down[0], due - .05)
                self.assertEqual(up[0], due + 1)
                self.assertEqual(down[4], 1)
        finally:
            scheduler.close()

    def test_hotkey_registration_and_failure_cleanup(self):
        from live_preview import GlobalHotkeys
        registered, released = [], []
        def register(hwnd, identifier, flags, key):
            registered.append((identifier, flags, key))
            return identifier != 3
        def unregister(hwnd, identifier):
            released.append(identifier)
            return 1
        user = SimpleNamespace(RegisterHotKey=register, UnregisterHotKey=unregister,
                               PeekMessageW=lambda *args: 0)
        with self.assertRaises(RuntimeError):
            GlobalHotkeys(user)
        self.assertEqual(registered[0], (1, 0x4000, 0x75))
        self.assertEqual(released, [1, 2])

    def test_bottom_arrival_extrapolation(self):
        tracker = CrossingTracker(spacing=185, match_distance=180)
        tracker.update([{"center_x": 800, "center_y": 690}], 1600, 900, 714, 10)
        events = tracker.update([{"center_x": 800, "center_y": 730}], 1600, 900, 714, 10.02, arrival_y=738)
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0][2], 10.024)

    def test_delay_estimator_unique_ambiguous_and_expiry(self):
        estimator = DelayEstimator(1000, 100)
        estimator.add_top([(1, 0, 10)])
        self.assertEqual(estimator.add_bottom([(9, 0, 11)]), [(0, 1000)])
        self.assertIn('1000ms', estimator.summary())
        estimator.add_top([(2, 1, 12), (3, 1, 12.05)])
        self.assertEqual(estimator.add_bottom([(10, 1, 13)]), [])
        estimator.expire(15)
        self.assertTrue(all(not items for items in estimator.pending))


if __name__ == "__main__":
    unittest.main()
