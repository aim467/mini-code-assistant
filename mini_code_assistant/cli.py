#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py - Mini Code Assistant 命令行入口

核心原理：
  这是整个编程助手的入口，实现了 **REPL**（Read-Eval-Print Loop）交互循环
  和 **Agent Loop**（智能体循环）。

  REPL 循环（外层）：
    用户输入 → 处理 → 等待下一次输入

  Agent Loop（内层）：
    用户消息 → 发给 LLM → LLM 请求工具调用 → 执行工具 → 结果喂回 LLM
    → LLM 继续推理 → ... → LLM 给出最终回复

  这两个嵌套循环就是 Claude Code、Codex 等工具的基本架构。

使用方式:
    # 全局安装后（推荐）
    mca                          # 在当前目录运行
    mca /path/to/project         # 在指定目录运行
    mca --model deepseek-chat    # 覆盖模型
    mca --api-key sk-xxx         # 覆盖 API 密钥

    # 未安装，直接运行模块
    python -m mini_code_assistant [目录] [选项]

配置优先级（从高到低）:
    1. 命令行参数 (--model, --api-key, --base-url)
    2. Shell 环境变量 (export API_KEY=xxx)
    3. 本地 ./.env 文件（项目级配置）
    4. 全局 ~/.mca/.env 文件（用户级配置）
    5. 内置默认值
"""

import os
import sys
import json
import argparse
from pathlib import Path

from .llm import LLMClient
from .tools import ToolSystem
from .context import Context
from .token_counter import format_token_count, HAS_TIKTOKEN
from . import __version__

# ── Rich 可选依赖 ────────────────────────────────────────────
# 如果安装了 rich，用 Markdown 渲染 + 面板展示；否则降级为纯文本
try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel

    _console = Console()
    HAS_RICH = True
except ImportError:
    _console = None
    HAS_RICH = False

# ── prompt_toolkit 可选依赖 ──────────────────────────────────
# 如果安装了 prompt_toolkit，用增强 REPL（命令历史、自动补全、多行编辑）；
# 否则降级为 input() 原始 REPL
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False


# ── 多路径 .env 加载器 ───────────────────────────────────────────
# 不依赖 python-dotenv，手动解析 .env 文件
# 支持两级配置：全局 ~/.mca/.env + 本地 ./.env（本地覆盖全局）
def load_env():
    """
    加载配置文件，优先级（从高到低）：
      1. Shell 环境变量（已存在的不会被覆盖）
      2. 本地 ./.env（项目级配置，覆盖全局）
      3. 全局 ~/.mca/.env（用户级配置）

    实现方式：先加载全局配置到字典，再用本地配置覆盖，
    最后用 setdefault 写入环境变量（不覆盖已有的 shell 变量）。
    """
    config = {}

    # 1. 先加载全局配置（~/.mca/.env）
    global_path = Path.home() / ".mca" / ".env"
    _parse_env_file(global_path, config)

    # 2. 再加载本地配置（./.env），覆盖全局
    local_path = Path.cwd() / ".env"
    _parse_env_file(local_path, config)

    # 3. 应用到环境变量（不覆盖已有的 shell 变量）
    for key, value in config.items():
        os.environ.setdefault(key, value)


def _parse_env_file(path: Path, config: dict):
    """解析 .env 文件，将键值对写入 config 字典。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip('"').strip("'")


# ── 终端颜色 ───────────────────────────────────────────────────
class C:
    """ANSI 颜色码简写。"""
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


def print_banner(workdir: Path, model: str, context_limit: int):
    """打印启动横幅。"""
    token_mode = "tiktoken" if HAS_TIKTOKEN else "estimated"
    repl_mode = "prompt_toolkit" if HAS_PROMPT_TOOLKIT else "basic"
    if HAS_RICH:
        _console.print(Panel(
            f"[bold cyan]Mini Code Assistant[/]  v{__version__}\n"
            f"[dim]workdir:[/] {workdir}\n"
            f"[dim]model:  [/] {model}\n"
            f"[dim]context:[/] {context_limit:,} tokens  [dim]token count: {token_mode}[/]\n"
            f"[dim]repl:   [/] {repl_mode}",
            border_style="cyan",
            padding=(1, 2),
        ))
        if HAS_PROMPT_TOOLKIT:
            _console.print("  type [yellow]/help[/] for help, [yellow]/exit[/] to quit  [dim](↑↓ history, Tab complete, Ctrl+R search)[/]\n")
        else:
            _console.print("  type [yellow]/help[/] for help, [yellow]/exit[/] to quit\n")
    else:
        print(f"{C.CYAN}{C.BOLD}")
        print("  +======================================+")
        print("  |       Mini Code Assistant            |")
        print("  |       v" + __version__ + " - " + " " * (22 - len(__version__)) + "       |")
        print("  +======================================+")
        print(f"{C.RESET}")
        print(f"  {C.DIM}workdir:{C.RESET} {workdir}")
        print(f"  {C.DIM}model:  {C.RESET} {model}")
        print(f"  {C.DIM}context:{C.RESET} {context_limit:,} tokens  {C.DIM}(token count: {token_mode}){C.RESET}")
        print(f"  {C.DIM}repl:   {C.RESET} {repl_mode}")
        print(f"  {C.DIM}--------------------------------------{C.RESET}")
        if HAS_PROMPT_TOOLKIT:
            print(f"  type {C.YELLOW}/help{C.RESET} for help, {C.YELLOW}/exit{C.RESET} to quit  {C.DIM}(↑↓ history, Tab complete, Ctrl+R search){C.RESET}\n")
        else:
            print(f"  type {C.YELLOW}/help{C.RESET} for help, {C.YELLOW}/exit{C.RESET} to quit\n")


def print_help():
    """打印帮助信息。"""
    if HAS_RICH:
        repl_hint = ""
        if HAS_PROMPT_TOOLKIT:
            repl_hint = (
                "\n[bold]REPL Shortcuts:[/]\n"
                "  [yellow]↑/↓[/]       navigate command history\n"
                "  [yellow]Tab[/]       auto-complete /commands\n"
                "  [yellow]Ctrl+R[/]    search command history\n"
            )
        _console.print(Panel(
            "[bold]Commands:[/]\n"
            "  [yellow]/help[/]     show this help\n"
            "  [yellow]/exit[/]     quit\n"
            "  [yellow]/clear[/]    clear conversation history\n"
            "  [yellow]/files[/]    list files in workdir\n"
            "  [yellow]/tokens[/]   show token usage\n"
            f"{repl_hint}\n"
            "[bold]Usage:[/]\n"
            "  Just type your question or task, e.g.:\n"
            '  [dim]"read main.py and find issues"[/]\n'
            '  [dim]"add a logging function to utils.py"[/]\n'
            '  [dim]"search for all uses of requests"[/]',
            title="Help",
            border_style="yellow",
        ))
    else:
        print(f"\n  {C.BOLD}Commands:{C.RESET}")
        print(f"  {C.YELLOW}/help{C.RESET}     show this help")
        print(f"  {C.YELLOW}/exit{C.RESET}     quit")
        print(f"  {C.YELLOW}/clear{C.RESET}    clear conversation history")
        print(f"  {C.YELLOW}/files{C.RESET}    list files in workdir")
        print(f"  {C.YELLOW}/tokens{C.RESET}   show token usage")
        if HAS_PROMPT_TOOLKIT:
            print(f"  {C.DIM}--------------------------------------{C.RESET}")
            print(f"  {C.BOLD}REPL Shortcuts:{C.RESET}")
            print(f"  {C.YELLOW}↑/↓{C.RESET}       navigate command history")
            print(f"  {C.YELLOW}Tab{C.RESET}       auto-complete /commands")
            print(f"  {C.YELLOW}Ctrl+R{C.RESET}    search command history")
        print(f"  {C.DIM}--------------------------------------{C.RESET}")
        print(f"  {C.BOLD}Usage:{C.RESET}")
        print(f"  Just type your question or task, e.g.:")
        print(f'  {C.DIM}"read main.py and find issues"{C.RESET}')
        print(f'  {C.DIM}"add a logging function to utils.py"{C.RESET}')
        print(f'  {C.DIM}"search for all uses of requests"{C.RESET}\n')


# ── prompt_toolkit 增强交互 ──────────────────────────────────
# 命令补全器：为 /help, /exit 等斜杠命令提供 Tab 补全
class SlashCommandCompleter(Completer):
    """斜杠命令补全器，支持 Tab 自动补全。"""

    COMMANDS = {
        "/help": "show this help",
        "/exit": "quit",
        "/clear": "clear conversation history",
        "/files": "list files in workdir",
        "/tokens": "show token usage",
    }

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        # 只在输入以 / 开头时触发补全
        if text.startswith("/"):
            for cmd, desc in self.COMMANDS.items():
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=f"{cmd}  ({desc})",
                    )


def create_prompt_session() -> "PromptSession | None":
    """
    创建 prompt_toolkit 的 PromptSession。

    功能：
      - 命令历史：持久化到 ~/.mca/history，跨会话保留
      - Tab 补全：输入 / 时自动补全斜杠命令
      - 多行编辑：Shift+Enter 换行，Enter 提交
      - Emacs/Vi 模式：自动检测用户偏好

    Returns:
      PromptSession 实例，或 None（如果 prompt_toolkit 未安装）
    """
    if not HAS_PROMPT_TOOLKIT:
        return None

    # 历史文件路径：~/.mca/history
    history_dir = Path.home() / ".mca"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "history"

    return PromptSession(
        history=FileHistory(str(history_file)),
        completer=SlashCommandCompleter(),
        multiline=False,  # 单行模式，与原始 REPL 行为一致
        enable_history_search=True,  # Ctrl+R 搜索历史
    )


def _prompt_style():
    """返回 prompt_toolkit 的样式配置。"""
    if not HAS_PROMPT_TOOLKIT:
        return None
    from prompt_toolkit.styles import Style

    return Style.from_dict({
        "prompt": "bold ansigreen",  # >>> 提示符：绿色加粗，与原始 REPL 一致
    })


# ── 核心：Agent Loop ───────────────────────────────────────────
def run_agent_loop(llm: LLMClient, tools: ToolSystem, context: Context):
    """
    Agent 循环——编程助手的核心引擎。

    流程：
    1. 把对话历史（含用户最新消息）发给 LLM
    2. LLM 返回回复：
       a. 如果包含 tool_calls → 执行工具，把结果加入历史，回到步骤 1
       b. 如果只有文本 → 这是最终回复，显示给用户，结束循环

    这个循环会重复执行，直到：
    - LLM 不再请求工具调用（给出最终回复）
    - 达到最大迭代次数（防止无限循环）
    - 发生错误
    """
    MAX_ITERATIONS = 20  # 安全限制：防止 LLM 陷入无限工具调用循环

    # 记录 Agent Loop 开始前的消息数，用于检测是否发生了摘要
    msg_count_before = len(context.messages)

    for iteration in range(MAX_ITERATIONS):
        # ── 调用 LLM ──────────────────────────────────────
        response = llm.chat(context.messages, tools.definitions)

        content = response["content"]
        tool_calls = response["tool_calls"]

        # 如果有错误
        if response["finish_reason"] == "error":
            if HAS_RICH:
                _console.print(f"\n  [yellow]{content}[/]")
            else:
                print(f"\n  {C.YELLOW}{content}{C.RESET}")
            return

        # ── 显示文本回复 ──────────────────────────────────
        if content:
            if HAS_RICH:
                _console.print(Markdown(content))
            else:
                print(f"\n  {C.CYAN}{C.BOLD}Assistant:{C.RESET} {content}")

        # ── 没有工具调用 → LLM 给出了最终回复，结束循环 ──
        if not tool_calls:
            context.add_assistant_message(content)
            _print_token_usage(context, msg_count_before)
            return

        # ── 有工具调用 → 记录助手消息（含 tool_calls）─────
        context.add_assistant_message(content, tool_calls)

        # ── 执行每个工具调用 ──────────────────────────────
        for tc in tool_calls:
            func = tc["function"]
            name = func["name"]

            # 解析参数（LLM 返回的是 JSON 字符串）
            try:
                args = json.loads(func["arguments"])
            except json.JSONDecodeError:
                args = {}

            # 展示工具调用信息
            args_str = ", ".join(f"{k}={repr(v)[:60]}" for k, v in args.items())
            if HAS_RICH:
                _console.print(f"  [dim]tool:[/] [bold]{name}[/]({args_str})")
            else:
                print(f"\n  {C.DIM}tool: {C.RESET}{name}({args_str})")

            # 执行工具
            result = tools.execute(name, args)

            # 展示结果（截断过长的输出）
            preview = result
            if len(preview) > 600:
                preview = preview[:600] + f"\n  ...(total {len(result)} chars)"
            if HAS_RICH:
                _console.print(Panel(
                    preview,
                    title=f"[dim]result:[/] {name}",
                    border_style="dim",
                    padding=(0, 1),
                ))
            else:
                print(f"  {C.DIM}result:{C.RESET}\n{preview}")

            # 把工具结果加入对话历史（LLM 下一轮会看到）
            context.add_tool_result(tc["id"], result)

    # 达到最大迭代次数
    if HAS_RICH:
        _console.print(f"\n  [yellow]Max iterations ({MAX_ITERATIONS}) reached.[/]")
    else:
        print(f"\n  {C.YELLOW}Max iterations ({MAX_ITERATIONS}) reached.{C.RESET}")
    _print_token_usage(context, msg_count_before)


def _print_token_usage(context: Context, msg_count_before: int):
    """打印 token 用量和摘要通知。"""
    token_count = context.token_count()
    token_str = format_token_count(token_count, context.context_limit)

    # 检查是否发生了摘要（消息数减少了）
    summarized = len(context.messages) < msg_count_before

    if HAS_RICH:
        parts = [f"[dim]tokens:[/] {token_str}"]
        if summarized:
            parts.append("[yellow](auto-summarized)[/]")
        if context._summary:
            parts.append(f"[dim]({len(context.messages)} msgs)[/]")
        _console.print("  " + "  ".join(parts))
    else:
        parts = [f"{C.DIM}tokens:{C.RESET} {token_str}"]
        if summarized:
            parts.append(f"{C.YELLOW}(auto-summarized){C.RESET}")
        if context._summary:
            parts.append(f"{C.DIM}({len(context.messages)} msgs){C.RESET}")
        print("  " + "  ".join(parts))


# ── 主函数 ─────────────────────────────────────────────────────
def main():
    # ── 解析命令行参数 ──────────────────────────────────────
    parser = argparse.ArgumentParser(
        prog="mca",
        description="Mini Code Assistant - a mini AI coding assistant for learning",
        epilog=(
            "Config via env vars or .env file: API_KEY, BASE_URL, MODEL\n"
            "  Local config:  ./.env\n"
            "  Global config: ~/.mca/.env"
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="working directory (default: current directory)",
    )
    parser.add_argument(
        "-m", "--model",
        help="override model name (e.g. gpt-4o, deepseek-chat)",
    )
    parser.add_argument(
        "-k", "--api-key",
        help="override API key",
    )
    parser.add_argument(
        "-u", "--base-url",
        help="override API base URL",
    )
    parser.add_argument(
        "-t", "--temperature",
        type=float,
        help="override sampling temperature (default: 0.3)",
    )
    parser.add_argument(
        "-V", "--version",
        action="version",
        version=f"Mini Code Assistant {__version__}",
    )
    args = parser.parse_args()

    # ── 加载配置 ────────────────────────────────────────────
    load_env()

    # 配置优先级：命令行参数 > 环境变量 > .env 文件 > 默认值
    api_key = args.api_key or os.getenv("API_KEY", "")
    base_url = args.base_url or os.getenv("BASE_URL", "https://api.openai.com/v1")
    model = args.model or os.getenv("MODEL", "gpt-4o")
    temperature = args.temperature if args.temperature is not None else float(os.getenv("TEMPERATURE", "0.3"))

    # 工作目录
    working_dir = Path(args.directory).resolve()
    if not working_dir.exists():
        print(f"Error: working directory does not exist: {working_dir}")
        sys.exit(1)

    if not api_key:
        print("Error: API_KEY is required.")
        print("\nYou can configure it via:")
        print("  1. CLI flag:   mca --api-key sk-xxx")
        print("  2. Local env:  create .env in current directory")
        print("  3. Global env: create ~/.mca/.env")
        print("  4. Shell env:  export API_KEY=sk-xxx")
        sys.exit(1)

    # 初始化核心组件
    llm = LLMClient(api_key, base_url, model, temperature=temperature)
    tools = ToolSystem(working_dir)
    context = Context(working_dir, llm=llm, model=model)

    print_banner(working_dir, model, context.context_limit)

    # ── 创建增强 REPL 会话（如果 prompt_toolkit 可用）────
    session = create_prompt_session()

    # ── REPL 循环 ──────────────────────────────────────
    while True:
        try:
            if session is not None:
                # prompt_toolkit 增强 REPL：支持历史、补全、搜索
                user_input = session.prompt(
                    [("class:prompt", ">>> ")],
                    style=_prompt_style() if HAS_PROMPT_TOOLKIT else None,
                ).strip()
            else:
                # 降级为原始 input()
                user_input = input(f"{C.GREEN}{C.BOLD}>>> {C.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            if HAS_RICH:
                _console.print("[dim]Bye![/]\n")
            else:
                print(f"\n{C.DIM}Bye!{C.RESET}\n")
            break

        if not user_input:
            continue

        # ── 处理特殊命令 ─────────────────────────────────
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd == "/exit":
                if HAS_RICH:
                    _console.print("[dim]Bye![/]\n")
                else:
                    print(f"{C.DIM}Bye!{C.RESET}\n")
                break
            elif cmd == "/help":
                print_help()
                continue
            elif cmd == "/clear":
                context.clear()
                if HAS_RICH:
                    _console.print("[dim]Conversation history cleared.[/]\n")
                else:
                    print(f"{C.DIM}Conversation history cleared.{C.RESET}\n")
                continue
            elif cmd == "/files":
                result = tools.execute("list_files", {})
                print(result)
                continue
            elif cmd == "/tokens":
                token_str = format_token_count(context.token_count(), context.context_limit)
                msg_count = len(context.messages)
                if HAS_RICH:
                    _console.print(f"[dim]messages:[/] {msg_count}  [dim]tokens:[/] {token_str}", style="white")
                    if context._summary:
                        _console.print("[dim]context has been auto-summarized[/]")
                else:
                    print(f"\n  {C.DIM}messages:{C.RESET} {msg_count}  {C.DIM}tokens:{C.RESET} {token_str}")
                    if context._summary:
                        print(f"  {C.DIM}(context has been auto-summarized){C.RESET}")
                print()
                continue
            else:
                if HAS_RICH:
                    _console.print(f"[yellow]Unknown command: {user_input}[/]")
                else:
                    print(f"{C.YELLOW}Unknown command: {user_input}{C.RESET}")
                continue

        # ── 正常对话：启动 Agent Loop ────────────────────
        context.add_user_message(user_input)
        try:
            run_agent_loop(llm, tools, context)
        except KeyboardInterrupt:
            if HAS_RICH:
                _console.print("[yellow]Task interrupted.[/]")
            else:
                print(f"\n{C.YELLOW}Task interrupted.{C.RESET}")
        except Exception as e:
            if HAS_RICH:
                _console.print(f"[yellow]Error: {e}[/]")
            else:
                print(f"\n{C.YELLOW}Error: {e}{C.RESET}")


if __name__ == "__main__":
    main()
