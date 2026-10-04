import struct
import time
import unittest
from unittest.mock import Mock

from tap_output import CrossingTracker
from test_tap_output import note
from touch_output import LaneTouchOutput, TouchScheduler, TouchSlideController


class TouchGestureTests(unittest.TestCase):
    def create(self, focus=True):
        sock = Mock()
        output = LaneTouchOutput(sock, (1600, 900),
                                 [243, 434, 619, 800, 983, 1169, 1353], 738, lambda _: focus)
        scheduler = TouchScheduler(1, enabled=True, output_backend=output,
                                   flick_duration_ms=60, flick_distance=120)
        self.addCleanup(scheduler.close)
        return sock, output, scheduler

    def wait(self, predicate):
        deadline = time.perf_counter() + 2
        while not predicate() and time.perf_counter() < deadline:
            time.sleep(.002)
        self.assertTrue(predicate())

    def packets(self, sock):
        return [struct.unpack('>BBQiiHHHII', c.args[0]) for c in sock.sendall.call_args_list]

    def test_tail_flick_reuses_pointer_without_blocking_other_finger(self):
        sock, output, scheduler = self.create()
        now = time.perf_counter()
        scheduler.schedule_hold('hold-down', 'TS1', 1, now)
        scheduler.schedule_hold('hold-down', 'TS2', 5, now)
        self.wait(lambda: len(output.tokens) == 2)
        pointer1, pointer2 = output.tokens['TS1'], output.tokens['TS2']
        due = time.perf_counter() + .04
        scheduler.schedule_hold('hold-move', 'TS1', 2.5, due - .02)
        scheduler.schedule_flick_tail(3, 3, due, {1: 'TS1'}, offset_bounds=None)
        # Another finger can release during the flick, not after its 60ms.
        scheduler.schedule_hold('hold-up', 'TS2', 5, due + .025)
        scheduler.schedule(4, 0, due + .03)
        self.wait(lambda: len([p for p in self.packets(sock) if p[1] == 1]) == 3)
        packets = self.packets(sock)
        self.assertEqual(len([p for p in packets if p[1] == 0 and p[2] == pointer1]), 1)
        moves = [p for p in packets if p[1] == 2 and p[2] == pointer1]
        self.assertGreater(len(moves), 2)
        self.assertEqual(moves[0][3], 710)  # interpolated lane 2.5
        self.assertEqual(moves[-1][4], 618)
        up1 = next(i for i, p in enumerate(packets) if p[1] == 1 and p[2] == pointer1)
        up2 = next(i for i, p in enumerate(packets) if p[1] == 1 and p[2] == pointer2)
        self.assertLess(up2, up1)
        self.assertIsNone(scheduler.error)

    def test_independent_flick_and_cancel_mid_gesture(self):
        sock, output, scheduler = self.create()
        scheduler.schedule_flick(1, 3, time.perf_counter())
        self.wait(lambda: len(output.tokens) == 1)
        scheduler.cancel()
        self.wait(lambda: not output.tokens)
        time.sleep(.08)
        packets = self.packets(sock)
        self.assertEqual(packets[-1][1], 1)
        self.assertEqual(len([p for p in packets if p[1] == 0]), 1)
        self.assertEqual(len([p for p in packets if p[1] == 1]), 1)

    def test_tail_without_head_does_not_create_finger(self):
        sock, _, scheduler = self.create()
        scheduler.schedule_flick_tail(1, 2, time.perf_counter(), {2: 'missing'})
        time.sleep(.05)
        self.assertFalse(sock.sendall.called)

    def test_simultaneous_flicks_keep_distinct_pointers(self):
        sock, output, scheduler = self.create()
        due = time.perf_counter() + .02
        scheduler.schedule_flick(1, 1, due)
        scheduler.schedule_flick(2, 5, due)
        self.wait(lambda: len([p for p in self.packets(sock) if p[1] == 1]) == 2)
        packets = self.packets(sock)
        pointers = {p[2] for p in packets if p[1] == 0}
        self.assertEqual(len(pointers), 2)
        for pointer in pointers:
            self.assertGreater(len([p for p in packets if p[1] == 2 and p[2] == pointer]), 1)
        self.assertFalse(output.tokens)

    def test_unfocused_does_not_press(self):
        sock, _, scheduler = self.create(focus=False)
        scheduler.schedule_flick(1, 1, time.perf_counter())
        time.sleep(.04)
        self.assertFalse(sock.sendall.called)

    def test_controller_continuous_motion_has_stable_token(self):
        controller = TouchSlideController()
        controller.update([note(1, 150)], [], 1600, 900, 160, 10, positions=[1])
        head = controller.update([note(1, 170)], [], 1600, 900, 160, 10.02, positions=[1])
        self.assertEqual(head, [('hold-down', 'TS1', 1, 10.01)])
        for index, position in enumerate((1.5, 2, 2.5, 3, 3.5, 4, 3.5, 3)):
            events = controller.update([], [], 1600, 900, 160, 10.04 + index * .02, positions=[position])
            self.assertEqual(events[0][:3], ('hold-move', 'TS1', position))
        controller.update([], [], 1600, 900, 160, 10.3, positions=[])
        tail = controller.update([], [], 1600, 900, 160, 10.4, positions=[])
        self.assertEqual(tail[0][:2], ('hold-up', 'TS1'))

    def test_merged_ribbons_do_not_swap_or_release(self):
        controller = TouchSlideController()
        for y, now in ((150, 10), (170, 10.02)):
            controller.update([note(2, y), note(3, y)], [], 1600, 900, 160, now, positions=[2, 3])
        for now in (10.04, 10.2, 10.3):
            self.assertEqual(controller.update([], [], 1600, 900, 160, now, positions=[2.5]), [])
        self.assertEqual(len(controller.active), 2)
        events = controller.update([], [], 1600, 900, 160, 10.4, positions=[2, 3])
        self.assertEqual([(e[1], e[2]) for e in events], [('TS1', 2), ('TS2', 3)])

    def test_flick_tail_association_keeps_slide_identity(self):
        controller = TouchSlideController()
        for y, now in ((150, 10), (170, 10.02)):
            controller.update([note(2, y)], [], 1600, 900, 160, now, positions=[2])
        controller.update([], [], 1600, 900, 160, 10.04, positions=[2.5])
        controller.update([], [], 1600, 900, 160, 10.06, positions=[3])
        tracker = CrossingTracker()
        for y, now in ((150, 10.08), (170, 10.1)):
            events = tracker.update([dict(note(3, y), green_below=True)], 1600, 900, 160, now)
            ordinary, tails = controller.flick_tails(tracker, events, now)
        self.assertEqual(ordinary, [])
        self.assertEqual(tails[0][3], {2: 'TS1'})
        self.assertFalse(controller.active)
