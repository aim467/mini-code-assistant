"""
__main__.py - 模块执行入口

允许通过 `python -m mini_code_assistant` 运行，效果与 `mca` 命令相同。
这在未全局安装时很有用，例如：
    python -m mini_code_assistant /path/to/project
"""

from .cli import main

if __name__ == "__main__":
    main()
