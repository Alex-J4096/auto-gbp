import threading
import time
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from detect_notes import analyze_frame, analyze_top_frame, read_image
from pathlib import Path
from tap_output import SlidingHoldController, TapScheduler
from test_tap_output import note


class SlideTests(unittest.TestCase):
    def test_tail_crossing_wins_over_expired_gap(self):
        controller = SlidingHoldController(tail_release_ms=30)
        self.start(controller)
        controller.update([], [], 1600, 900, 160, 10.20, positions=[])
        controller.update([note(3, 150)], [], 1600, 900, 160, 10.24, positions=[])
        events = controller.update([note(3, 170)], [], 1600, 900, 160, 10.28, positions=[])
        self.assertEqual(events, [])
        self.assertEqual(controller.sequence, 1)
        self.assertAlmostEqual(controller.active[1]['last_node'], 10.26)
        controller.update([], [], 1600, 900, 160, 10.30, positions=[])
        end = controller.update([], [], 1600, 900, 160, 10.38, positions=[])
        self.assertEqual(end[0][:3], ('hold-up', 'S1:0', 3))
        self.assertAlmostEqual(end[0][3], 10.33)

    def test_parallel_tails_release_independently_with_buffer(self):
        controller = SlidingHoldController(tail_release_ms=30)
        controller.update([note(1, 150), note(5, 150)], [], 1600, 900, 160, 10, positions=[1, 5])
        controller.update([note(1, 170), note(5, 170)], [], 1600, 900, 160, 10.02, positions=[1, 5])
        controller.update([], [], 1600, 900, 160, 10.04, positions=[5])
        first = controller.update([], [], 1600, 900, 160, 10.12, positions=[5])
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][2], 1)
        self.assertAlmostEqual(first[0][3], 10.07)
        self.assertEqual(len(controller.active), 1)
        controller.update([], [], 1600, 900, 160, 10.14, positions=[])
        second = controller.update([], [], 1600, 900, 160, 10.22, positions=[])
        self.assertEqual(second[0][2], 5)
        self.assertAlmostEqual(second[0][3], 10.17)

    def start(self, controller, lane=3):
        controller.update([note(lane, 150)], [], 1600, 900, 160, 10, positions=[lane])
        return controller.update([note(lane, 170)], [], 1600, 900, 160, 10.02, positions=[lane])

    def test_move_roundtrip_and_tail(self):
        controller = SlidingHoldController(overlap_ms=30)
        self.assertEqual(self.start(controller)[0][:3], ('hold-down', 'S1:0', 3))
        controller.update([], [], 1600, 900, 160, 10.04, positions=[3.4])
        self.assertEqual(controller.update([note(4, 150)], [], 1600, 900, 160, 10.05, positions=[3.7]), [])
        moved = controller.update([note(4, 170)], [], 1600, 900, 160, 10.06, positions=[4])
        self.assertEqual(moved[0][:3], ('hold-down', 'S1:1', 4))
        self.assertEqual(len(moved), 1)  # Original key stays pressed.
        # Do not chatter around the rounding boundary.
        self.assertEqual(controller.update([], [], 1600, 900, 160, 10.08, positions=[3.49]), [])
        controller.update([note(3, 150)], [], 1600, 900, 160, 10.20, positions=[3.2])
        back = controller.update([note(3, 170)], [], 1600, 900, 160, 10.22, positions=[3])
        self.assertEqual(back, [])  # Returning within the pair needs no input.
        controller.update([], [], 1600, 900, 160, 10.24, positions=[])
        end = controller.update([], [], 1600, 900, 160, 10.32, positions=[])
        self.assertEqual([event[:3] for event in end],
                         [('hold-up', 'S1:0', 3), ('hold-up', 'S1:1', 4)])
        self.assertEqual(end[0][3], end[1][3])
        self.assertFalse(controller.active)

    def test_gap_recovery_and_ambiguous_ribbons(self):
        controller = SlidingHoldController()
        self.start(controller)
        self.assertEqual(controller.update([], [], 1600, 900, 160, 10.04, positions=[]), [])
        self.assertEqual(controller.update([], [], 1600, 900, 160, 10.06, positions=[3.3]), [])
        self.assertEqual(controller.update([], [], 1600, 900, 160, 10.08, positions=[3.1, 3.8]), [])
        self.assertEqual(len(controller.active), 1)

    def test_parallel_holds_stay_separate(self):
        controller = SlidingHoldController()
        controller.update([note(1, 150), note(5, 150)], [], 1600, 900, 160, 10, positions=[1, 5])
        starts = controller.update([note(1, 170), note(5, 170)], [], 1600, 900, 160, 10.02, positions=[1, 5])
        self.assertEqual(len(starts), 2)
        controller.update([], [], 1600, 900, 160, 10.04, positions=[1.4, 4.6])
        self.assertEqual(controller.update([note(2, 150), note(4, 150)], [], 1600, 900, 160, 10.05, positions=[1.7, 4.3]), [])
        moves = controller.update([note(2, 170), note(4, 170)], [], 1600, 900, 160, 10.06, positions=[2, 4])
        self.assertEqual([event[2] for event in moves if event[0] == 'hold-down'], [2, 4])
        self.assertEqual(len(controller.active), 2)

    def test_observation_positions_scaled(self):
        frame = np.zeros((900, 1600, 3), np.uint8)
        cv2.rectangle(frame, (816, 140), (836, 180), (0, 210, 0), -1)
        for width in (1600, 800):
            result, _ = analyze_top_frame(cv2.resize(frame, (width, round(width * 900 / 1600))),
                                          render=False, green_holds=True)
            self.assertEqual(len(result['green_positions']), 1)
            self.assertAlmostEqual(result['green_positions'][0], 3 + 26 / 43, delta=.04)

    @unittest.skipUnless(Path('test_pic/current_environment.jpg').is_file(), 'optional local screenshot not available')
    def test_full_no_render_matches_rendered_detection(self):
        frame = read_image(Path('test_pic/current_environment.jpg'))
        for width in (1598, 800):
            resized = cv2.resize(frame, (width, round(width * frame.shape[0] / frame.shape[1])))
            expected, _ = analyze_frame(resized)
            actual, artifacts = analyze_frame(resized, render=False)
            self.assertEqual(actual, expected)
            self.assertEqual(artifacts, {})
            self.assertEqual(len(actual['blue_notes']), 2)
            self.assertEqual(len(actual['green_nodes']), 5)

    def test_scheduler_handoff_order_and_cancel(self):
        calls = []
        old_released = threading.Event()
        def send(key, up=False):
            calls.append((key, up))
            if key == 'f' and up:
                old_released.set()
        scheduler = TapScheduler(0, max_hold_ms=1000)
        scheduler.keyboard = SimpleNamespace(focused=lambda hwnd: True, send=send)
        scheduler.enabled = True
        try:
            now = time.perf_counter()
            scheduler.schedule_hold('hold-down', 'S1:0', 3, now + .01)
            scheduler.schedule_hold('hold-down', 'S1:1', 4, now + .03)
            scheduler.schedule_hold('handoff-up', 'S1:0', 3, now + .06)
            self.assertTrue(old_released.wait(1))
            self.assertEqual(calls[:3], [('f', False), ('j', False), ('f', True)])
            scheduler.cancel()
        finally:
            scheduler.close()
        self.assertEqual(calls[-1], ('j', True))

    def test_pair_tail_shares_jitter_and_releases_both(self):
        scheduler = TapScheduler(0, timing_jitter=True, jitter_seed=42)
        try:
            due = time.perf_counter() + 100
            scheduler.schedule_hold('hold-down', 'S1:0', 3, due)
            scheduler.schedule_hold('hold-down', 'S1:1', 4, due + 1)
            scheduler.schedule_hold('hold-up', 'S1:0', 3, due + 2, final=False)
            scheduler.schedule_hold('hold-up', 'S1:1', 4, due + 2)
            releases = [event for event in scheduler.queue if event[2] == 'hold-up']
            self.assertEqual(len(releases), 2)
            self.assertEqual(releases[0][0], releases[1][0])
            self.assertFalse(scheduler.hold_offsets)
        finally:
            scheduler.close()

    def test_pair_cancel_releases_every_key(self):
        calls = []
        both_down, both_up = threading.Event(), threading.Event()
        def send(key, up=False):
            calls.append((key, up))
            if sum(not released for _, released in calls) == 2:
                both_down.set()
            if sum(released for _, released in calls) == 2:
                both_up.set()
        scheduler = TapScheduler(0, max_hold_ms=1000)
        scheduler.keyboard = SimpleNamespace(focused=lambda hwnd: True, send=send)
        scheduler.enabled = True
        try:
            due = time.perf_counter() + .01
            scheduler.schedule_hold('hold-down', 'S1:0', 3, due)
            scheduler.schedule_hold('hold-down', 'S1:1', 4, due)
            self.assertTrue(both_down.wait(1))
            self.assertEqual(calls, [('f', False), ('j', False)])
            scheduler.cancel()
            self.assertTrue(both_up.wait(1))
        finally:
            scheduler.close()
        self.assertCountEqual(calls, [('f', False), ('j', False), ('f', True), ('j', True)])

    def test_old_release_cannot_release_revisited_lane(self):
        calls = []
        def send(key, up=False):
            calls.append((key, up))
        scheduler = TapScheduler(0, max_hold_ms=1000)
        scheduler.keyboard = SimpleNamespace(focused=lambda hwnd: True, send=send)
        scheduler.enabled = True
        try:
            now = time.perf_counter()
            scheduler.schedule_hold('hold-down', 'S1:0', 3, now + .01)
            scheduler.schedule_hold('hold-down', 'S1:2', 3, now + .03)
            scheduler.schedule_hold('handoff-up', 'S1:0', 3, now + .05)
            time.sleep(.09)
            self.assertEqual(scheduler.pressed.get('f'), 'S1:2')
            self.assertEqual(calls, [('f', False)])
        finally:
            scheduler.close()


if __name__ == '__main__':
    unittest.main()
