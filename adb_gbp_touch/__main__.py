"""Run the independent touch probe."""
import subprocess
from .probe import main

try:
    main()
except KeyboardInterrupt:
    print('\n测试中止。')
except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
    raise SystemExit(f'触摸测试失败: {exc}')
