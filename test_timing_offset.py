import time
import unittest

from tap_output import NormalTimingOffset, TapScheduler


class TimingTests(unittest.TestCase):
    def test_bounds_signs_and_reproducibility(self):
        a, b = NormalTimingOffset(42), NormalTimingOffset(42)
        values = [a.sample_ms() for _ in range(10000)]
        self.assertEqual(values, [b.sample_ms() for _ in values])
        self.assertTrue(all(-83 <= value <= 100 for value in values))
        self.assertLess(min(values), 0)
        self.assertGreater(max(values), 0)
        rate = sum(-33 <= value <= 50 for value in values) / len(values)
        self.assertAlmostEqual(rate, a.perfect_probability(), delta=.006)
        self.assertTrue(.94 <= a.perfect_probability() <= .95)
        self.assertAlmostEqual(sum(values) / len(values), 5, delta=.6)

    def test_sigma_and_validation(self):
        self.assertEqual(NormalTimingOffset(mean_ms=8, sigma_ms=0).sample_ms(), 8)
        self.assertGreater(NormalTimingOffset(sigma_ms=15).perfect_probability(),
                           NormalTimingOffset(sigma_ms=25).perfect_probability())
        for sigma in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                NormalTimingOffset(sigma_ms=sigma)

    def test_hold_timing_relationships_and_cancel(self):
        scheduler = TapScheduler(0, timing_jitter=True, jitter_seed=42)
        try:
            base = time.perf_counter() + 100
            scheduler.schedule_hold('hold-down', 'S1:0', 3, base)
            scheduler.schedule_hold('hold-down', 'S1:1', 4, base + 1)
            scheduler.schedule_hold('handoff-up', 'S1:0', 3, base + 1.03)
            scheduler.schedule_hold('hold-up', 'S1:1', 4, base + 2)
            deadlines = [e[0] for e in sorted(scheduler.queue)]
            self.assertAlmostEqual(deadlines[1] - deadlines[0], 1)
            self.assertAlmostEqual(deadlines[2] - deadlines[1], .03)
            self.assertAlmostEqual(deadlines[3] - deadlines[0], 2)
            self.assertFalse(scheduler.hold_offsets)
            scheduler.cancel()
            self.assertFalse(scheduler.queue)
        finally:
            scheduler.close()

    def test_tap_and_flick_shift_and_disabled_default(self):
        for enabled in (False, True):
            scheduler = TapScheduler(0, timing_jitter=enabled, jitter_seed=42)
            expected = NormalTimingOffset(42)
            try:
                base = time.perf_counter() + 100
                scheduler.schedule(1, 0, base)
                scheduler.schedule_flick(2, 1, base + 1)
                for event, original in zip(sorted(scheduler.queue), (base, base + 1)):
                    offset = expected.sample_ms() if enabled else 0
                    self.assertAlmostEqual(event[0], original + offset / 1000)
            finally:
                scheduler.close()
