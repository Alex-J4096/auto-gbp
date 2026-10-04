import threading
import time
import unittest
from types import SimpleNamespace

from runtime_log import AsyncLog
from tap_output import CrossingTracker, GreenHoldController, SlidingHoldController, TapScheduler
from test_tap_output import note


class LatencyTests(unittest.TestCase):
    def test_blocked_console_does_not_block_keyboard_or_capture_producer(self):
        blocked, unblock, released = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def sink(message):
            blocked.set()
            unblock.wait(2)
        def send(key, up=False):
            calls.append((key, up))
            if key == 's' and up:
                released.set()
        scheduler = TapScheduler(0, hold_ms=5, key_log=True)
        scheduler.log.close()
        scheduler.log = AsyncLog(sink=sink)
        scheduler.keyboard = SimpleNamespace(send=send, focused=lambda _: True)
        scheduler.enabled = True
        try:
            scheduler.log('simulate a blocked terminal')
            self.assertTrue(blocked.wait(1))
            scheduler.schedule(1, 0, time.perf_counter() + .02)
            scheduler.schedule(2, 1, time.perf_counter() + .04)
            self.assertTrue(released.wait(.5))
            self.assertIn(('a', True), calls)
        finally:
            unblock.set()
            scheduler.close()

    def test_retained_old_lane_can_retrigger_without_releasing_current_lane(self):
        controller = SlidingHoldController()
        controller.active[1] = dict(lane=4, position=4., keys={3: 'S1:0', 4: 'S1:1'})
        self.assertIsNone(controller.detach_spare_key(4))
        spare = controller.detach_spare_key(3)
        self.assertEqual(spare, 'S1:0')
        self.assertEqual(controller.active[1]['keys'], {4: 'S1:1'})
        calls = []
        finished = threading.Event()
        def send(key, up=False):
            calls.append((key, up))
            if key == 'f' and up and calls.count(('f', True)) == 2:
                finished.set()
        scheduler = TapScheduler(0, hold_ms=5)
        scheduler.keyboard = SimpleNamespace(send=send, focused=lambda _: True)
        scheduler.enabled = True
        try:
            now = time.perf_counter()
            scheduler.schedule_hold('hold-down', 'S1:0', 3, now + .01)
            scheduler.schedule_hold('hold-down', 'S1:1', 4, now + .01)
            scheduler.schedule(9, 3, now + .04, release_token=spare)
            self.assertTrue(finished.wait(.5))
            self.assertEqual(calls, [('f', False), ('j', False), ('f', True), ('f', False), ('f', True)])
            self.assertEqual(scheduler.held, {'j': 'S1:1'})
        finally:
            scheduler.close()

    def test_disappeared_tail_reservation_has_a_fixed_expiry(self):
        controller = GreenHoldController()
        controller.update([note(3, 150)], [True]*7, 1600, 900, 160, 10)
        controller.update([note(3, 170)], [True]*7, 1600, 900, 160, 10.02)
        tracker = CrossingTracker()
        tracker.update([dict(note(3, 150), green_below=True)], 1600, 900, 160, 11)
        controller.flick_tails(tracker, [], 11)
        controller.flick_tails(tracker, [], 11.1)
        self.assertEqual(controller.active[3]['flick_pending_until'], 11.25)
        controller.update([], [False]*7, 1600, 900, 160, 11.26)
        end = controller.update([], [False]*7, 1600, 900, 160, 11.33)
        self.assertEqual(end[0][0], 'hold-up')

    def test_already_crossed_flick_cannot_reserve_a_new_hold(self):
        controller = GreenHoldController()
        controller.active[3] = dict(token='H2', last_node=10, gap_start=None)
        tracker = CrossingTracker()
        tracker.tracks = [dict(id=1, lane=3, y=180, time=10, fired=True, green_below=True)]
        controller.flick_tails(tracker, [], 10.02)
        self.assertNotIn('flick_pending_until', controller.active[3])
