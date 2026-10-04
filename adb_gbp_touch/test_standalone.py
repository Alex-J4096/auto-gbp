"""Can run with this folder alone; never connects to a device."""
import importlib
from pathlib import Path
import struct
import sys
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
package = importlib.import_module(Path(__file__).resolve().parent.name)
Macro, Touch = package.Macro, package.Touch


class StandaloneTests(unittest.TestCase):
    def test_timeline_and_same_pointer_tail(self):
        macro = (Macro().down(0, 0, 50, 80).down(0, 1, 150, 80)
                 .slide(.001, 0, (50, 80), (70, 60), .002)
                 .up(.003, 0).up(.004, 1))
        sock = Mock()
        touch = Touch(sock, 200, 100, log=False)
        self.assertTrue(macro.play(touch))
        packets = [struct.unpack('>BBQiiHHHII', c.args[0]) for c in sock.sendall.call_args_list]
        self.assertEqual([(p[1], p[2]) for p in packets], [(0, 0), (0, 1), (2, 0), (1, 0), (1, 1)])
        self.assertFalse(touch.active)

    def test_validation_before_any_input(self):
        invalid = [Macro().down(0, 0, 10, 10), Macro().up(0, 0),
                   Macro().tap(0, 0, 200, 10), Macro().tap(-1, 0, 10, 10),
                   Macro().down(0, 0, 10, 10).down(.1, 0, 20, 20).up(.2, 0)]
        for macro in invalid:
            sock = Mock()
            with self.assertRaises(ValueError):
                macro.play(Touch(sock, 200, 100, log=False))
            self.assertFalse(sock.sendall.called)

    def test_pre_cancelled(self):
        cancel = threading.Event()
        cancel.set()
        sock = Mock()
        self.assertFalse(Macro().tap(0, 0, 10, 10).play(Touch(sock, 200, 100), cancel))
        self.assertFalse(sock.sendall.called)

    def test_cancel_after_down_releases(self):
        cancel = threading.Event()
        sock = Mock()
        sock.sendall.side_effect = lambda _: cancel.set()
        touch = Touch(sock, 200, 100, log=False)
        self.assertFalse(Macro().tap(0, 0, 10, 10).play(touch, cancel))
        self.assertEqual(sock.sendall.call_count, 2)
        self.assertFalse(touch.active)

    def test_error_attempts_release(self):
        sock = Mock()
        sock.sendall.side_effect = [OSError('test'), None]
        touch = Touch(sock, 200, 100, log=False)
        with self.assertRaises(OSError):
            Macro().tap(0, 0, 10, 10).play(touch)
        self.assertEqual(sock.sendall.call_count, 2)
        self.assertFalse(touch.active)

    def test_flick_and_packet(self):
        events = Macro().flick(0, 0, (50, 80), (50, 60)).validate(200, 100)
        self.assertEqual(events[0].action, 0)
        self.assertEqual(events[-1].action, 1)
        self.assertEqual(events[-2].point, (50, 60))
        self.assertEqual(len(package.touch_packet(0, 0, 50, 80, 200, 100)), 32)


if __name__ == '__main__':
    unittest.main()
