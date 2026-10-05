"""Modern settings UI; exits before the capture/input loop starts."""
import argparse

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, Switch, TabbedContent, TabPane

from .launcher import edit_value, fields, save_config, validate


GROUPS = [
    ('常用', 'general', ['title', 'hwnd', 'send_keys', 'key_log', 'no_preview', 'delay_ms', 'input_mode', 'region']),
    ('触摸输入', 'touch', ['output_backend', 'adb_path', 'adb_serial', 'touch_server', 'touch_size', 'touch_delay_ms',
                         'touch_flick_delay_ms', 'touch_flick_duration_ms', 'touch_flick_distance']),
    ('底部识别', 'lower', ['bottom_delay_ms', 'bottom_flick_delay_ms', 'lower_band', 'lower_observation_y']),
    ('长押', 'holds', ['green_holds', 'green_slides', 'green_tail_release_ms', 'green_gap_ms', 'slide_overlap_ms', 'max_hold_ms']),
    ('Flick', 'flick', ['flicks', 'flick_delay_ms', 'flick_hold_ms', 'flick_tail_guard_ms', 'flick_tail_release_ms']),
    ('随机偏移', 'jitter', ['timing_jitter', 'jitter_mean_ms', 'jitter_sigma_ms', 'jitter_seed']),
]
LABELS = {'title': '窗口标题', 'hwnd': '窗口句柄（可选）', 'send_keys': '发送真实按键',
          'key_log': '按键日志', 'no_preview': '显示预览', 'delay_ms': '基础延迟 / ms',
          'input_mode': '输入模式', 'green_holds': '绿色长押', 'green_slides': '实验性换轨',
          'green_tail_release_ms': '普通尾释放缓冲 / ms', 'green_gap_ms': '轨迹消失确认 / ms',
          'slide_overlap_ms': '换轨重叠 / ms', 'max_hold_ms': '最长保持 / ms',
          'flicks': 'Flick 宏输入', 'flick_delay_ms': '独立 Flick 延迟 / ms',
          'flick_hold_ms': '宏键保持 / ms', 'flick_tail_guard_ms': '尾 Flick 保护 / ms',
          'flick_tail_release_ms': '宏触发后释放 / ms', 'timing_jitter': '启用随机偏移',
          'jitter_mean_ms': '均值 μ / ms', 'jitter_sigma_ms': '标准差 σ / ms',
          'jitter_seed': '随机种子（可选）', 'no_hotkeys': '启用全局快捷键'}
LABELS.update({'region': '识别模式', 'delay_ms': '顶部 / 全图延迟 / ms',
               'output_backend': '输入后端', 'adb_path': 'ADB 路径', 'adb_serial': '本地 ADB 地址',
               'touch_server': 'scrcpy 3.3.3 服务端路径', 'touch_size': 'Android 当前宽 高',
               'touch_delay_ms': '触摸独立延迟 / ms', 'send_keys': '发送真实输入（键盘/触摸）',
               'touch_flick_delay_ms': '触摸 Flick 延迟 / ms（可选）',
               'touch_flick_duration_ms': '触摸 Flick 滑动时长 / ms',
               'touch_flick_distance': '触摸 Flick 向上距离 / 900 高基准',
               'bottom_delay_ms': '底部延迟 / ms（需校准）',
               'bottom_flick_delay_ms': '底部 Flick 延迟 / ms（可选）',
               'lower_band': '底部识别范围 Y1 Y2', 'lower_observation_y': '底部观察线 Y'})
INVERT = {'no_preview', 'no_hotkeys'}


class ConfirmInput(ModalScreen[bool]):
    BINDINGS = [('escape', 'cancel', '返回')]
    CSS = '''
    ConfirmInput { align: center middle; background: $background 70%; }
    #confirm-box { width: 60; max-width: 95%; height: auto; border: thick $warning; padding: 1 2; }
    #confirm-box Horizontal { height: auto; align-horizontal: right; margin-top: 1; }
    #confirm-box Button { margin-left: 1; }
    '''

    def compose(self) -> ComposeResult:
        with Vertical(id='confirm-box'):
            yield Label('确认发送真实输入', markup=False)
            yield Static('请确认窗口与输入后端。触摸模式需确认 ADB 实例和 Android 分辨率，Flick 及换轨开关也适用于触摸。', markup=False)
            with Horizontal():
                yield Button('返回设置', id='back')
                yield Button('确认启动', id='confirm', variant='warning')

    def on_mount(self):
        self.query_one('#back', Button).focus()

    def action_cancel(self):
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == 'confirm')


class SettingsApp(App[list[str] | None]):
    TITLE = 'Auto GBP · 启动设置'
    BINDINGS = [('ctrl+r', 'launch', '运行'), ('ctrl+s', 'save_launch', '保存并运行'),
                ('ctrl+q', 'cancel', '取消')]
    CSS = '''
    Screen { background: $background; }
    #path { height: auto; max-height: 3; padding: 0 2; color: $text-muted; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 0 1; }
    .field { height: auto; min-height: 5; padding: 0 1; margin-bottom: 1; border-bottom: solid $panel; }
    .field-row { height: 3; align-vertical: middle; }
    .field-row Label { width: 2fr; content-align: left middle; height: 3; }
    .field-row Input, .field-row Select { width: 3fr; }
    .field-row Switch { width: auto; }
    .hint { height: auto; color: $text-muted; }
    #status { height: auto; max-height: 4; padding: 0 2; color: $warning; }
    #actions { height: 3; align-horizontal: right; padding-right: 1; }
    #actions Button { min-width: 12; margin-left: 1; }
    '''

    def __init__(self, values, path):
        super().__init__()
        self.values = dict(values)
        self.path = path
        self.schema = fields()
        known = {key for _, _, keys in GROUPS for key in keys}
        self.groups = GROUPS + [('高级', 'advanced', [key for key in self.schema if key not in known])]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f'配置：{self.path}  |  Tab 切换 · 空格切换开关 · 鼠标点击', id='path', markup=False)
        with TabbedContent(id='tabs'):
            for title, group_id, keys in self.groups:
                with TabPane(title, id=group_id):
                    with VerticalScroll():
                        for key in keys:
                            action, value = self.schema[key], self.values[key]
                            with Vertical(classes='field'):
                                with Horizontal(classes='field-row'):
                                    yield Label(LABELS.get(key, key.replace('_', '-')), markup=False)
                                    widget_id = 'setting-' + key
                                    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                                                           argparse.BooleanOptionalAction)):
                                        yield Switch(not value if key in INVERT else value, id=widget_id)
                                    elif action.choices:
                                        yield Select([(str(item), item) for item in action.choices],
                                                     value=value, allow_blank=False, id=widget_id)
                                    else:
                                        text = '' if value is None else (' '.join(map(str, value)) if isinstance(value, (list, tuple)) else str(value))
                                        yield Input(value=text, placeholder='留空使用自动值' if action.default is None else '必填', id=widget_id)
                                hint = action.help or key
                                if key in INVERT:
                                    hint = '打开即启用此功能；保存时自动转换为 CLI 的 no-* 参数。'
                                yield Static(hint, classes='hint', markup=False)
        yield Static('修改只在运行后生效；“运行”不写配置。', id='status', markup=False)
        with Horizontal(id='actions'):
            yield Button('取消', id='cancel')
            yield Button('运行', id='run', variant='primary')
            yield Button('保存并运行', id='save-run', variant='success')
        yield Footer()

    def collect(self):
        result = dict(self.values)
        for key, action in self.schema.items():
            widget = self.query_one('#setting-' + key)
            try:
                if isinstance(widget, Switch):
                    result[key] = not widget.value if key in INVERT else widget.value
                elif isinstance(widget, Select):
                    result[key] = widget.value
                else:
                    text = widget.value
                    if not text.strip():
                        if action.default is not None:
                            raise ValueError('此项不能为空')
                        result[key] = None
                    else:
                        result[key] = edit_value(action, self.values[key], text)
            except ValueError as exc:
                for _, group_id, keys in self.groups:
                    if key in keys:
                        self.query_one('#tabs', TabbedContent).active = group_id
                widget.focus()
                raise ValueError(f'{LABELS.get(key, key)}：{exc}') from exc
        validate(result)
        return result

    def launch(self, save=False):
        try:
            values = self.collect()
        except ValueError as exc:
            self.query_one('#status', Static).update(f'无法启动：{exc}')
            return

        def finish(confirmed):
            if not confirmed:
                return
            try:
                if save:
                    save_config(self.path, values)
                self.exit(validate(values))
            except (OSError, ValueError) as exc:
                self.query_one('#status', Static).update(f'保存 / 启动失败：{exc}')

        if values['send_keys']:
            self.push_screen(ConfirmInput(), finish)
        else:
            finish(True)

    def action_launch(self):
        self.launch()

    def action_save_launch(self):
        self.launch(save=True)

    def action_cancel(self):
        self.exit(None)

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == 'cancel':
            self.action_cancel()
        elif event.button.id in {'run', 'save-run'}:
            self.launch(save=event.button.id == 'save-run')
