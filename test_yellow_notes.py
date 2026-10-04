import unittest
from pathlib import Path

import cv2
import numpy as np

from detect_notes import analyze_frame, analyze_top_frame, read_image


class YellowTests(unittest.TestCase):
    def test_flick_headless_opt_in(self):
        frame = np.zeros((900, 1600, 3), np.uint8)
        cv2.rectangle(frame, (772, 150), (828, 166), (180, 30, 255), -1)
        result, _ = analyze_top_frame(frame, render=False)
        self.assertEqual(result['flick_notes'], [])
        for width in (1600, 800):
            image = cv2.resize(frame, (width, round(width * 900 / 1600)))
            result, artifacts = analyze_top_frame(image, render=False, flicks=True)
            self.assertEqual(len(result['flick_notes']), 1)
            self.assertEqual(result['blue_notes'], [])
            self.assertEqual(result['yellow_notes'], [])
            self.assertEqual(artifacts, {})

    @unittest.skipUnless(Path('test_pic/yellow_note.png').is_file(), 'optional local screenshot not available')
    def test_user_screenshot(self):
        result, _ = analyze_frame(read_image(Path('test_pic/yellow_note.png')))
        self.assertEqual(len(result['yellow_notes']), 1)
        note = result['yellow_notes'][0]
        self.assertAlmostEqual(note['center_x'], 1065, delta=8)
        self.assertFalse(note['hold_connected'])

    def test_top_tap_head_tail_and_scaled_headless(self):
        # Above ribbon = head; below ribbon = tail in the scrolling screen.
        for side in ('none', 'above', 'below'):
            frame = np.zeros((900, 1600, 3), np.uint8)
            if side == 'above':
                cv2.rectangle(frame, (787, 90), (813, 148), (0, 210, 0), -1)
            if side == 'below':
                cv2.rectangle(frame, (787, 168), (813, 198), (0, 210, 0), -1)
            cv2.rectangle(frame, (772, 150), (828, 166), (0, 220, 255), -1)
            for width in (1600, 800):
                image = cv2.resize(frame, (width, round(width * 900 / 1600)))
                result, artifacts = analyze_top_frame(image, render=False, green_holds=True)
                self.assertEqual(artifacts, {})
                self.assertEqual(len(result['yellow_notes']), 1)
                note = result['yellow_notes'][0]
                self.assertEqual(note['hold_connected'], side != 'none')
                self.assertEqual(note['green_above'], side == 'above')
                self.assertEqual(note['green_below'], side == 'below')


if __name__ == '__main__':
    unittest.main()
