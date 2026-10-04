"""Bottom mode checks: synthetic frames and fake capture, no real input."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from detect_notes import analyze_top_frame
from live_preview import parse_args, runtime_settings, capture_top_only
from tap_output import CrossingTracker
from launcher import defaults, save_config, load_config, to_argv


class BottomModeTests(unittest.TestCase):
    def test_independent_settings(self):
        args, _ = parse_args(['--region', 'bottom', '--delay-ms', '355',
                              '--flick-delay-ms', '365', '--bottom-delay-ms', '75'])
        actual = runtime_settings(args)
        self.assertEqual(actual.delay_ms, 75)
        self.assertIsNone(actual.flick_delay_ms)
        self.assertEqual(actual.top_band, (480, 700))
        self.assertEqual(actual.observation_y, 600)
        self.assertAlmostEqual(actual.lane_spacing, 185 * 221 / (5 + .36 * 738.52))
        self.assertEqual(args.delay_ms, 355)
        self.assertEqual(args.observation_y, 160)
        top, _ = parse_args([])
        self.assertEqual(vars(runtime_settings(top)), vars(top))

    def test_validation(self):
        for options in (['--bottom-delay-ms', '-1'], ['--bottom-delay-ms', 'nan'],
                        ['--bottom-flick-delay-ms', '-1'],
                        ['--lower-band', '620', '700'], ['--lower-observation-y', '750']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(options)

    def test_bottom_seven_lane_detection_and_crossing(self):
        args = runtime_settings(parse_args(['--region', 'bottom'])[0])
        for width in (1600, 800):
            tracker = CrossingTracker(args.lane_spacing, match_distance=195)
            for y, timestamp in ((580, 10), (620, 10.02)):
                frame = np.zeros((900, 1600, 3), np.uint8)
                for lane in range(7):
                    x = round(800 + (lane - 3) * args.lane_spacing * (5 + .36*y) / 221)
                    w = round(.65 * (5 + .36*y))
                    cv2.rectangle(frame, (x-w//2, y-7), (x+w//2, y+7), (255, 200, 0), -1)
                frame = cv2.resize(frame, (width, round(width*900/1600)))
                result, _ = analyze_top_frame(frame, *args.top_band, args.observation_y,
                                             render=False, green_holds=True, lane_spacing=args.lane_spacing)
                self.assertEqual(len(result['blue_notes']), 7)
                events = tracker.update(result['blue_notes'], width, frame.shape[0],
                                        600*width/1600, timestamp)
            self.assertEqual(sorted(e[1] for e in events), list(range(7)))

    def test_roi_capture_preserves_coordinates(self):
        regions = []
        def grab(region):
            regions.append(region)
            return np.full((region['height'], region['width'], 4), 100, np.uint8)
        frame, _ = capture_top_only(SimpleNamespace(grab=grab),
                                   dict(left=0, top=0, width=1600, height=900), 800, (480, 700))
        self.assertEqual(frame.shape, (450, 800, 3))
        self.assertEqual(regions[0]['top'], 480)
        self.assertLess(regions[0]['height'], 225)
        self.assertFalse(frame[:240].any())
        self.assertTrue(frame[300, 400].any())

    def test_config_roundtrip(self):
        values = defaults()
        values.update(region='bottom', bottom_delay_ms=85, bottom_flick_delay_ms=90)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            save_config(path, values)
            loaded = load_config(path)
            args, _ = parse_args(to_argv(loaded))
        resolved = runtime_settings(args)
        self.assertEqual(resolved.delay_ms, 85)
        self.assertEqual(resolved.flick_delay_ms, 90)
