import time
import unittest

from live_preview import hold_offset_bounds
from tap_output import TapScheduler


class JudgmentWindowTests(unittest.TestCase):
    def test_routing(self):
        self.assertIsNone(hold_offset_bounds('hold-down', 'S1:0'))
        self.assertIsNone(hold_offset_bounds('hold-down', 'H1'))
        self.assertEqual(hold_offset_bounds('hold-down', 'S1:1'), (20, 60))
        self.assertEqual(hold_offset_bounds('handoff-up', 'S1:0'), (20, 60))
        self.assertEqual(hold_offset_bounds('hold-up', 'S1:1'), (0, 20))

    def test_negative_and_positive_jitter_clamped_without_resampling(self):
        for offset in (-83, 100):
            scheduler = TapScheduler(0)
            try:
                base = time.perf_counter() + 100
                scheduler.hold_offsets['S1'] = offset
                scheduler.schedule_hold('hold-down', 'S1:1', 4, base,
                                        offset_bounds=(20, 60))
                scheduler.schedule_hold('handoff-up', 'S1:0', 3, base + .03,
                                        offset_bounds=(20, 60))
                scheduler.schedule_flick_tail(5, 4, base + 1,
                                              {3: 'S1:0', 4: 'S1:1'},
                                              offset_bounds=(20, 50))
                events = sorted(scheduler.queue)
                self.assertAlmostEqual(events[0][0] - base, (.02 if offset < 0 else .06))
                self.assertAlmostEqual(events[1][0] - events[0][0], .03)
                self.assertAlmostEqual(events[2][0] - base - 1, (.02 if offset < 0 else .05))
                self.assertAlmostEqual(events[3][0] - events[2][0], .03)
                self.assertEqual(events[3][0], events[4][0])
            finally:
                scheduler.close()

    def test_disabled_jitter_still_has_late_margin(self):
        scheduler = TapScheduler(0)
        try:
            base = time.perf_counter() + 100
            scheduler.schedule_hold('hold-down', 'S1:1', 4, base, offset_bounds=(20, 60))
            self.assertAlmostEqual(scheduler.queue[0][0], base + .02)
        finally:
            scheduler.close()
