import tempfile
import unittest
from pathlib import Path

from textual.widgets import Input, Switch, TabbedContent, Select
from launcher import defaults, load_config
from launcher_tui import SettingsApp, ConfirmInput
from live_preview import parse_args, runtime_settings


class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_touch_gesture_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            app = SettingsApp(defaults(), path)
            async with app.run_test(size=(100, 36)) as pilot:
                app.query_one('#tabs', TabbedContent).active = 'touch'
                app.query_one('#setting-output_backend', Select).value = 'touch'
                app.query_one('#setting-touch_flick_duration_ms', Input).value = '45'
                app.query_one('#setting-touch_flick_distance', Input).value = '95'
                app.query_one('#setting-touch_flick_delay_ms', Input).value = '365'
                await pilot.click('#save-run')
            args = runtime_settings(parse_args(app.return_value)[0])
            self.assertTrue(args.flicks)
            self.assertTrue(args.green_slides)
            self.assertEqual(args.touch_flick_duration_ms, 45)
            self.assertEqual(args.flick_delay_ms, 365)
            self.assertEqual(load_config(path)['touch_flick_distance'], 95)

    async def test_bottom_mode_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            app = SettingsApp(defaults(), Path(directory) / 'config.toml')
            async with app.run_test(size=(100, 36)) as pilot:
                app.query_one('#setting-region', Select).value = 'bottom'
                app.query_one('#tabs', TabbedContent).active = 'lower'
                app.query_one('#setting-bottom_delay_ms', Input).value = '85'
                await pilot.click('#run')
            args = runtime_settings(parse_args(app.return_value)[0])
            self.assertEqual(args.region, 'bottom')
            self.assertEqual(args.delay_ms, 85)
            self.assertEqual(args.observation_y, 600)

    async def test_prefill_edit_and_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            app = SettingsApp(defaults(), path)
            async with app.run_test(size=(100, 36)) as pilot:
                self.assertEqual(app.query_one('#setting-delay_ms', Input).value, '350')
                self.assertFalse(app.query_one('#setting-no_preview', Switch).value)
                app.query_one('#setting-delay_ms', Input).value = '355'
                app.query_one('#setting-no_preview', Switch).value = True
                await pilot.click('#run')
            args, _ = parse_args(app.return_value)
            self.assertEqual(args.delay_ms, 355)
            self.assertFalse(args.no_preview)
            self.assertFalse(path.exists())

    async def test_invalid_then_cancel_no_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            app = SettingsApp(defaults(), path)
            async with app.run_test(size=(80, 24)) as pilot:
                app.query_one('#setting-delay_ms', Input).value = 'nan'
                await pilot.click('#save-run')
                self.assertIsNone(app.return_value)
                self.assertFalse(path.exists())
                await pilot.click('#cancel')
            self.assertIsNone(app.return_value)

    async def test_confirm_save_and_guard_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.toml'
            app = SettingsApp(defaults(), path)
            async with app.run_test(size=(100, 36)) as pilot:
                app.query_one('#setting-send_keys', Switch).value = True
                app.query_one('#tabs', TabbedContent).active = 'flick'
                await pilot.pause()
                app.query_one('#setting-flick_tail_guard_ms', Input).value = '180'
                await pilot.click('#save-run')
                self.assertIsInstance(app.screen, ConfirmInput)
                await pilot.click('#back')
                self.assertFalse(path.exists())
                await pilot.click('#save-run')
                await pilot.click('#confirm')
            saved = load_config(path)
            self.assertEqual(saved['flick_tail_guard_ms'], 180)
            self.assertTrue(saved['send_keys'])

    async def test_optional_blank_clears_value(self):
        values = {**defaults(), 'flick_delay_ms': 375}
        app = SettingsApp(values, Path('unused.toml'))
        async with app.run_test(size=(100, 36)) as pilot:
            app.query_one('#setting-flick_delay_ms', Input).value = ''
            self.assertIsNone(app.collect()['flick_delay_ms'])
            await pilot.press('ctrl+q')
