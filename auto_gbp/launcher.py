"""Config-backed Textual launcher. No capture until confirmed."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import tomllib

if __package__:
    from .live_preview import build_parser, main as run_game, parse_args
    from .tap_output import NormalTimingOffset
else:
    # Keep direct execution working: ``python auto_gbp/launcher.py``.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from auto_gbp.live_preview import build_parser, main as run_game, parse_args
    from auto_gbp.tap_output import NormalTimingOffset


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / 'config.toml'
EXCLUDED = {'help', 'list', 'test_key'}


def fields():
    """Use the CLI schema so new settings automatically appear in the menu."""
    result = {}
    for action in build_parser()._actions:
        if action.dest not in EXCLUDED:
            result.setdefault(action.dest, action)
    return result


def defaults():
    args, _ = parse_args([])
    return {key: value for key, value in vars(args).items() if key not in EXCLUDED}


def to_argv(values):
    schema = fields()
    unknown = values.keys() - schema.keys()
    if unknown:
        raise ValueError('未知配置项：' + ', '.join(sorted(unknown)))
    argv = []
    for key, value in values.items():
        action = schema[key]
        if value is None:
            continue
        if key == 'title' and values.get('hwnd') is not None:
            continue  # Explicit HWND takes precedence over the default title.
        if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                               argparse.BooleanOptionalAction)):
            if type(value) is not bool:
                raise ValueError(f'{key} 必须为 true 或 false')
            if key == 'no_preview':
                argv.append('--no-preview' if value else '--preview')
            elif isinstance(action, argparse.BooleanOptionalAction):
                argv.append(action.option_strings[0] if value else action.option_strings[1])
            elif value:
                argv.append(action.option_strings[0])
            continue
        items = value if isinstance(value, (list, tuple)) else [value]
        if isinstance(action.nargs, int) and (not isinstance(value, (list, tuple)) or len(items) != action.nargs):
            raise ValueError(f'{key} 需要 {action.nargs} 个数值')
        if action.nargs is None and isinstance(value, (list, tuple, dict)):
            raise ValueError(f'{key} 只能填写一个值')
        if any(type(item) is bool or not isinstance(item, (str, int, float)) for item in items):
            raise ValueError(f'{key} 的类型不正确')
        if any(isinstance(item, float) and not math.isfinite(item) for item in items):
            raise ValueError(f'{key} 必须是有限数值')
        argv.extend([action.option_strings[0], *map(str, items)])
    return argv


def validate(values):
    argv = to_argv(values)
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors):
            args, _ = parse_args(argv)
    except SystemExit as exc:
        raise ValueError(errors.getvalue().strip().splitlines()[-1]) from exc
    # Validate jitter even when disabled, so saving cannot hide invalid values.
    NormalTimingOffset(args.jitter_seed, args.jitter_mean_ms, args.jitter_sigma_ms)
    return argv


def load_config(path):
    values = defaults()
    if path.exists():
        with path.open('rb') as stream:
            document = tomllib.load(stream)
        if set(document) - {'settings'} or not isinstance(document.get('settings', {}), dict):
            raise ValueError('配置文件只允许 [settings] 表')
        values.update(document.get('settings', {}))
    validate(values)
    return values


def save_config(path, values):
    validate(values)
    lines = ['# Auto GBP launcher settings; omitted values use CLI defaults.', '[settings]']
    for key, value in values.items():
        if value is not None:
            lines.append(f'{key} = {json.dumps(value, ensure_ascii=False, allow_nan=False)}')
    # Atomic replacement: an interrupted save must not truncate the old config.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write('\n'.join(lines) + '\n')
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def edit_value(action, current, text):
    if not text.strip():
        return current
    if text.strip() == '-':
        if action.default is None:
            return None
        raise ValueError('此项不能留空')
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction,
                           argparse.BooleanOptionalAction)):
        word = text.strip().lower()
        if word not in {'true', 'false', '1', '0', 'y', 'n', 'on', 'off'}:
            raise ValueError('开关请填写 true/false 或 1/0')
        return word in {'true', '1', 'y', 'on'}
    converter = action.type or str
    if isinstance(action.nargs, int):
        parts = text.replace(',', ' ').split()
        if len(parts) != action.nargs:
            raise ValueError(f'需要 {action.nargs} 个值，用空格分隔')
        value = [converter(part) for part in parts]
    else:
        value = converter(text)
    if action.choices and value not in action.choices:
        raise ValueError(f'可选值：{action.choices}')
    return value


def plain_menu(values, path, input_fn=input, output=print):
    schema = fields()
    keys = list(schema)
    while True:
        output(f'\n=== Auto GBP 启动设置 ===\n配置：{path}')
        for number, key in enumerate(keys, 1):
            heading = {'title': '窗口与采集', 'no_preview': '预览与观察线',
                       'send_keys': '输入与延迟', 'flicks': 'Flick 与尾部保护',
                       'timing_jitter': '随机偏移', 'green_holds': '长押与换轨',
                       'lane_spacing': '轨道与底部测量'}.get(key)
            if heading:
                output(f'\n--- {heading} ---')
            value = values[key]
            display = '自动 / 未设置' if value is None else str(value)
            output(f'{number:2}. {key.replace("_", "-"):26} {display}')
        output('\n输入编号修改；R 运行；S 保存并运行；Q 取消。空输入保留，- 清除可选项。')
        output('no-preview=True 表示关闭预览；send-keys=True 表示真实输入。')
        try:
            choice = input_fn('选择：').strip().lower()
            if choice == 'q':
                return None
            if choice in {'r', 's'}:
                argv = validate(values)
                if values['send_keys']:
                    if input_fn('即将发送真实按键，请确认 MuMu 映射已配置。输入 y 确认：').strip().lower() != 'y':
                        continue
                if choice == 's':
                    save_config(path, values)
                    output('配置已保存。')
                return argv
            index = int(choice) - 1
            if not 0 <= index < len(keys):
                raise ValueError('编号超出范围')
            key = keys[index]
            output(schema[key].help or key)
            new_value = edit_value(schema[key], values[key], input_fn(f'{key} [{values[key]}]：'))
            # Cross-field validation happens at run/save, allowing related edits.
            values[key] = new_value
        except (ValueError, OSError) as exc:
            output(f'设置错误：{exc}')
        except (EOFError, KeyboardInterrupt):
            output('\n已取消。')
            return None


def menu(values, path):
    if __package__:
        from .launcher_tui import SettingsApp
    else:
        from auto_gbp.launcher_tui import SettingsApp
    return SettingsApp(values, path).run()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Auto GBP 终端启动设置')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG, help='TOML 配置路径')
    parser.add_argument('--plain', action='store_true', help='使用兼容模式的编号菜单')
    options = parser.parse_args(argv)
    try:
        values = load_config(options.config)
    except (OSError, ValueError) as exc:
        parser.error(f'无法读取配置：{exc}')
    selected = (plain_menu if options.plain else menu)(values, options.config)
    if selected is not None:
        run_game(selected)


if __name__ == '__main__':
    main()
