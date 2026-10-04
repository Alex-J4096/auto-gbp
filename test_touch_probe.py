import struct
import unittest
from unittest.mock import Mock, patch

from touch_probe import Touch, demo, touch_packet


class TouchProbeTests(unittest.TestCase):
    def test_wire_packet(self):
        packet = touch_packet(0, 1, 400, 700, 1600, 900)
        self.assertEqual(len(packet), 32)
        self.assertEqual(struct.unpack('>BBQiiHHHII', packet),
                         (2, 0, 1, 400, 700, 1600, 900, 65535, 0, 0))
        self.assertEqual(struct.unpack('>BBQiiHHHII', touch_packet(1, 1, 400, 700, 1600, 900))[7], 0)

    def test_independent_release(self):
        touch = Touch(Mock(), 1600, 900)
        touch.event(0, 0, 400, 700)
        touch.event(0, 1, 1000, 700)
        touch.event(1, 0, 400, 700)
        self.assertEqual(touch.active, {1: (1000, 700)})
        touch.release_all()
        self.assertFalse(touch.active)

    def test_invalid_transition_and_coordinates(self):
        touch = Touch(Mock(), 1600, 900)
        with self.assertRaises(ValueError):
            touch.event(2, 0, 10, 10)
        with self.assertRaises(ValueError):
            touch.event(0, 0, 1600, 10)
        touch.event(0, 0, 10, 10)
        with self.assertRaises(ValueError):
            touch.event(0, 0, 10, 10)

    @patch('touch_probe.time.sleep')
    def test_demo_sequence(self, _):
        for mode in ('dual', 'slide', 'flick'):
            sock = Mock()
            touch = Touch(sock, 1600, 900)
            demo(touch, mode, .1)
            packets = [struct.unpack('>BBQiiHHHII', c.args[0]) for c in sock.sendall.call_args_list]
            self.assertEqual([(p[1], p[2]) for p in packets if p[1] != 2],
                             [(0, 0), (0, 1), (1, 0), (1, 1)])
            self.assertFalse(touch.active)


if __name__ == '__main__':
    unittest.main()
