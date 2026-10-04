import struct
import time
import unittest
from unittest.mock import Mock

from launcher import defaults, to_argv
from live_preview import parse_args, runtime_settings
from tap_output import TapScheduler
from touch_output import LaneTouchOutput


class TouchOutputTests(unittest.TestCase):
    def backend(self):
        sock = Mock()
        return sock, LaneTouchOutput(sock, (1920, 1080),
                                    [243, 434, 619, 800, 983, 1169, 1353], 738.52, lambda _: True)

    def test_coordinates_and_independent_pointers(self):
        sock, backend = self.backend()
        backend.send('d')
        backend.send('k')
        backend.send('d', up=True)
        self.assertEqual(set(backend.touch.active), {5})
        packet = struct.unpack('>BBQiiHHHII', sock.sendall.call_args_list[0].args[0])
        self.assertEqual(packet[2:7], (2, 743, 886, 1920, 1080))
        backend.release_all()
        self.assertFalse(backend.touch.active)
        with self.assertRaises(ValueError):
            backend.send('q')

    def test_runtime_does_not_mutate_keyboard_config(self):
        values = defaults()
        values.update(output_backend='touch', touch_delay_ms=321, region='bottom')
        args, _ = parse_args(to_argv(values))
        resolved = runtime_settings(args)
        self.assertEqual(resolved.delay_ms, 321)
        self.assertTrue(resolved.flicks)
        self.assertTrue(resolved.green_slides)
        self.assertTrue(args.flicks)
        self.assertTrue(args.green_slides)

    def test_scheduler_cancel_releases_both(self):
        sock, backend = self.backend()
        scheduler = TapScheduler(1, enabled=True, output_backend=backend)
        try:
            now = time.perf_counter()
            scheduler.schedule_hold('hold-down', 'H1', 2, now)
            scheduler.schedule_hold('hold-down', 'H2', 5, now)
            deadline = now + 1
            while len(backend.touch.active) != 2 and time.perf_counter() < deadline:
                time.sleep(.005)
            self.assertEqual(set(backend.touch.active), {2, 5})
            scheduler.cancel()
            deadline = time.perf_counter() + 1
            while backend.touch.active and time.perf_counter() < deadline:
                time.sleep(.005)
            self.assertFalse(backend.touch.active)
            self.assertIsNone(scheduler.error)
        finally:
            scheduler.close()

    def test_scheduler_close_releases_hold(self):
        _, backend = self.backend()
        scheduler = TapScheduler(1, enabled=True, output_backend=backend)
        try:
            scheduler.schedule_hold('hold-down', 'H1', 2, time.perf_counter())
            deadline = time.perf_counter() + 1
            while not backend.touch.active and time.perf_counter() < deadline:
                time.sleep(.005)
            self.assertTrue(backend.touch.active)
        finally:
            scheduler.close()
        self.assertFalse(backend.touch.active)
