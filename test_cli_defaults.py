"""Argument parsing only: never capture a window or inject keyboard input."""
import contextlib
import io
import unittest

from live_preview import parse_args


class CliDefaultsTests(unittest.TestCase):
    def test_safe_common_defaults(self):
        args, _ = parse_args([])
        self.assertEqual(args.title, "MuMu")
        self.assertEqual(args.region, "top")
        self.assertEqual(args.delay_ms, 350)
        self.assertEqual(args.input_mode, "vk")
        self.assertEqual(args.flick_tail_release_ms, 30)
        self.assertEqual(args.green_tail_release_ms, 30)
        for name in ("no_preview", "green_holds", "green_slides", "flicks"):
            self.assertTrue(getattr(args, name), name)
        for name in ("send_keys", "key_log", "timing_jitter"):
            self.assertFalse(getattr(args, name), name)

    def test_old_command_matches_short_command(self):
        old, _ = parse_args('--title MuMu --region top --no-preview --send-keys '
                            '--delay-ms 350 --green-holds --green-slides --flicks '
                            '--flick-tail-release-ms 30 --key-log'.split())
        new, _ = parse_args(['--send-keys', '--key-log'])
        self.assertEqual(vars(old), vars(new))

    def test_preview_and_overrides(self):
        args, _ = parse_args(['--preview', '--delay-ms', '365', '--hwnd', '0x123'])
        self.assertFalse(args.no_preview)
        self.assertEqual(args.delay_ms, 365)
        self.assertEqual(args.hwnd, 0x123)

    def test_disable_features(self):
        args, _ = parse_args(['--no-green-holds', '--no-flicks'])
        self.assertFalse(args.green_holds)
        self.assertFalse(args.green_slides)
        self.assertFalse(args.flicks)
        args, _ = parse_args(['--no-green-slides'])
        self.assertTrue(args.green_holds)
        self.assertFalse(args.green_slides)

    def test_test_key_requires_explicit_consent(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(['--test-key', 'f'])

    def test_tail_buffer_validation(self):
        for value in ('0', '50', '200'):
            args, _ = parse_args(['--green-tail-release-ms', value])
            self.assertEqual(args.green_tail_release_ms, float(value))
        for value in ('-1', '201', 'nan', 'inf'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parse_args(['--green-tail-release-ms', value])


if __name__ == '__main__':
    unittest.main()
