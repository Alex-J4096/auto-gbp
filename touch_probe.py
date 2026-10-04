"""Compatibility entry point for the standalone adb_gbp_touch package."""
import subprocess
import time  # Compatibility with existing mocked-clock tests.
from adb_gbp_touch.probe import Adb, Touch, VERSION, connection, demo, main, touch_packet

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n测试中止。')
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f'触摸测试失败: {exc}')
