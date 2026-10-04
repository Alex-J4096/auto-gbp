import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import launcher
from live_preview import parse_args
from tap_output import GreenHoldController, SlidingHoldController
from types import SimpleNamespace


class LauncherTests(unittest.TestCase):
    def test_roundtrip_all_fields(self):
        values = launcher.defaults()
        values.update(title='MuMu 安卓 "设备"', no_preview=False, send_keys=True,
                      green_holds=False, green_slides=False, flicks=False,
                      crop=[0, 0, 1600, 900], flick_delay_ms=365, jitter_seed=42)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.toml'
            launcher.save_config(path, values)
            loaded = launcher.load_config(path)
            self.assertEqual(launcher.validate(values), launcher.validate(loaded))
            self.assertEqual(loaded['title'], values['title'])

    def test_missing_config_and_hwnd(self):
        with tempfile.TemporaryDirectory() as directory:
            values = launcher.load_config(Path(directory) / 'missing.toml')
        self.assertFalse(values['send_keys'])
        values['hwnd'] = 0x123
        args, _ = parse_args(launcher.validate(values))
        self.assertEqual(args.hwnd, 0x123)

    def test_invalid_settings(self):
        for key, value in [('unknown', 3), ('send_keys', 'false'), ('fps', float('nan')),
                           ('crop', [1, 2]), ('flick_tail_guard_ms', -1), ('jitter_sigma_ms', -2)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                launcher.validate({**launcher.defaults(), key: value})

    def test_edit_run_does_not_save(self):
        values = launcher.defaults()
        number = list(launcher.fields()).index('delay_ms') + 1
        inputs = iter([str(number), '355', 'r'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.toml'
            argv = launcher.plain_menu(values, path, lambda _: next(inputs), lambda _: None)
            self.assertFalse(path.exists())
        self.assertEqual(parse_args(argv)[0].delay_ms, 355)

    def test_confirmation_and_cancel(self):
        inputs = iter(['s', 'n', 'q'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.toml'
            result = launcher.plain_menu({**launcher.defaults(), 'send_keys': True}, path,
                                   lambda _: next(inputs), lambda _: None)
            self.assertIsNone(result)
            self.assertFalse(path.exists())

    def test_save_confirmed(self):
        inputs = iter(['s', 'y'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.toml'
            result = launcher.plain_menu({**launcher.defaults(), 'send_keys': True}, path,
                                   lambda _: next(inputs), lambda _: None)
            self.assertTrue(launcher.load_config(path)['send_keys'])
            self.assertTrue(parse_args(result)[0].send_keys)

    def test_main_passes_args_without_launching_on_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.toml'
            with patch.object(launcher, 'menu', return_value=None), patch.object(launcher, 'run_game') as run:
                launcher.main(['--config', str(path)])
                run.assert_not_called()
            with patch.object(launcher, 'menu', return_value=['--delay-ms', '355']), patch.object(launcher, 'run_game') as run:
                launcher.main(['--config', str(path)])
                run.assert_called_once_with(['--delay-ms', '355'])

    def test_guard_duration_not_renewed(self):
        for cls in (GreenHoldController, SlidingHoldController):
            controller = cls(flick_guard_ms=100)
            controller.active[3] = {'token': 'H1'}
            track = {'id': 1, 'lane': 3, 'time': 10, 'fired': False, 'green_below': True}
            controller.flick_tails(SimpleNamespace(tracks=[track]), [], 10)
            self.assertAlmostEqual(controller.active[3]['flick_pending_until'], 10.1)
            track['time'] = 10.05
            controller.flick_tails(SimpleNamespace(tracks=[track]), [], 10.05)
            self.assertAlmostEqual(controller.active[3]['flick_pending_until'], 10.1)


if __name__ == '__main__':
    unittest.main()
