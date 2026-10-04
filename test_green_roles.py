import unittest

import numpy as np

from detect_notes import classify_green_roles
from tap_output import SlidingHoldController
from test_tap_output import note


class GreenRoleTests(unittest.TestCase):
    def test_roles_and_roi_edge(self):
        for role in ('head', 'tail', 'middle', 'unknown'):
            mask = np.zeros((100, 100), np.uint8)
            if role in ('head', 'middle'):
                mask[:45, 35:65] = 255
            if role in ('tail', 'middle'):
                mask[55:, 35:65] = 255
            marker = dict(center_x=50, y=45, width=30, height=10)
            classify_green_roles([marker], mask)
            self.assertEqual(marker['green_role'], role)
        marker = dict(center_x=50, y=1, width=30, height=10)
        mask[:] = 255
        classify_green_roles([marker], mask)
        self.assertEqual(marker['green_role'], 'unknown')

    def test_parallel_terminal_faces_do_not_handoff(self):
        controller = SlidingHoldController(tail_release_ms=50)
        for y, now in ((150, 10), (170, 10.02)):
            controller.update([note(2, y), note(5, y)], [], 1600, 900, 160,
                              now, positions=[2, 5])
        # Continuous ribbon moves without a checkpoint. Only terminal faces
        # appear in neighbouring lanes; they must not create S1:1 / S2:1.
        controller.update([], [], 1600, 900, 160, 10.04, positions=[1.4, 4.4])
        for y, now in ((150, 10.06), (170, 10.08)):
            nodes = [dict(note(lane, y), green_role='tail') for lane in (1, 4)]
            events = controller.update(nodes, [], 1600, 900, 160, now, positions=[1, 4])
            self.assertEqual(events, [])
        self.assertEqual([s['generation'] for s in controller.active.values()], [0, 0])
        controller.update([], [], 1600, 900, 160, 10.10, positions=[])
        end = controller.update([], [], 1600, 900, 160, 10.18, positions=[])
        self.assertEqual([e[:3] for e in end], [('hold-up', 'S1:0', 2), ('hold-up', 'S2:0', 5)])
        self.assertAlmostEqual(end[0][3], 10.15)

    def test_orphan_tail_does_not_start_hold(self):
        controller = SlidingHoldController()
        for y, now in ((150, 10), (170, 10.02)):
            events = controller.update([dict(note(3, y), green_role='tail')], [],
                                       1600, 900, 160, now, positions=[3])
            self.assertEqual(events, [])
        self.assertFalse(controller.active)


if __name__ == '__main__':
    unittest.main()
