import time
import unittest
from pathlib import Path

import cv2

from detect_notes import read_image, analyze_top_frame
from tap_output import CrossingTracker, GreenHoldController, SlidingHoldController, TapScheduler
from test_tap_output import note


class FlickTailTests(unittest.TestCase):
    @unittest.skipUnless(Path('test_pic/flick_tails.png').is_file(), 'optional local screenshot not available')
    def test_screenshot_connections(self):
        frame = read_image(Path('test_pic/flick_tails.png'))
        for width in (1598, 800):
            image = cv2.resize(frame, (width, round(width * frame.shape[0] / frame.shape[1])))
            result, _ = analyze_top_frame(image, render=False, green_holds=True, flicks=True)
            self.assertEqual(len(result['flick_notes']), 2)
            self.assertTrue(all(n['green_below'] for n in result['flick_notes']))

    def test_parallel_tails_preempt_gap_and_consume_once(self):
        for cls in (GreenHoldController, SlidingHoldController):
            controller = cls()
            extra = {'positions': [2, 4]} if cls is SlidingHoldController else {}
            controller.update([note(2, 150), note(4, 150)], [True]*7, 1600, 900, 160, 10, **extra)
            controller.update([note(2, 170), note(4, 170)], [True]*7, 1600, 900, 160, 10.02, **extra)
            tracker = CrossingTracker()
            faces = [dict(note(lane, 150), green_below=True) for lane in (2, 4)]
            events = tracker.update(faces, 1600, 900, 160, 11)
            self.assertEqual(controller.flick_tails(tracker, events, 11), ([], []))
            empty = {'positions': []} if cls is SlidingHoldController else {}
            self.assertEqual(controller.update([], [False]*7, 1600, 900, 160, 11.01, **empty), [])
            self.assertEqual(controller.update([], [False]*7, 1600, 900, 160, 11.08, **empty), [])
            # Connection evidence survives its disappearance on crossing frame.
            events = tracker.update([note(2, 170), note(4, 170)], 1600, 900, 160, 11.1)
            ordinary, tails = controller.flick_tails(tracker, events, 11.1)
            self.assertEqual(ordinary, [])
            self.assertEqual(len(tails), 2)
            self.assertFalse(controller.active)
            self.assertEqual(controller.update([], [False]*7, 1600, 900, 160, 11.2, **empty), [])
            self.assertEqual(tracker.update([note(2, 175), note(4, 175)], 1600, 900, 160, 11.11), [])

    def test_unconnected_flick_remains_ordinary(self):
        controller = GreenHoldController()
        tracker = CrossingTracker()
        tracker.update([note(3, 150)], 1600, 900, 160, 10)
        events = tracker.update([note(3, 170)], 1600, 900, 160, 10.02)
        self.assertEqual(controller.flick_tails(tracker, events, 10.02), (events, []))

    def test_atomic_pair_release_shared_jitter_and_delay_adjustment(self):
        scheduler = TapScheduler(0, timing_jitter=True, jitter_seed=42)
        try:
            now = time.perf_counter() + 100
            scheduler.schedule_hold('hold-down', 'S1:0', 2, now)
            scheduler.schedule_hold('hold-down', 'S1:1', 3, now + .2)
            offset = scheduler.hold_offsets['S1'] / 1000
            scheduler.schedule_flick_tail(7, 3, now + 1, {2: 'S1:0', 3: 'S1:1'}, 40)
            events = sorted(scheduler.queue)
            self.assertEqual([e[2] for e in events], ['hold-down', 'hold-down', 'flick-down', 'hold-up', 'hold-up'])
            self.assertAlmostEqual(events[2][0], now + 1 + offset)
            self.assertAlmostEqual(events[3][0] - events[2][0], .04)
            self.assertEqual(events[3][0], events[4][0])
            self.assertFalse(scheduler.hold_offsets)
            scheduler.adjust_delay(20)
            self.assertAlmostEqual(sorted(scheduler.queue)[2][0], events[2][0] + .02)
            scheduler.cancel()
            self.assertFalse(scheduler.queue)
        finally:
            scheduler.close()
