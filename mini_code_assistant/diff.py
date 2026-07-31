"""
diff.py - 代码差异展示工具

核心原理：
  编程助手在修改文件后，需要让用户清楚地看到"改了什么"。
  这里用 Python 标准库 difflib 生成 unified diff（统一差异格式），
  再用 ANSI 转义码着色，在终端中直观展示。

  ## 语法高亮（pygments 可选）

  如果安装了 pygments，diff 中的代码行会根据文件类型做语法高亮，
  让代码结构更清晰。缺失时降级为纯 ANSI 颜色。

  这正是 Claude Code / Codex 等工具在终端中显示红绿差异的底层做法。
"""

import difflib

try:
    from pygments import highlight
    from pygments.lexers import get_lexer_for_filename, TextLexer, guess_lexer_for_filename
    from pygments.formatters import TerminalFormatter
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False


# ── ANSI 颜色码 ──────────────────────────────────────────────
class Color:
    RED = "\033[91m"      # 删除的行
    GREEN = "\033[92m"    # 新增的行
    CYAN = "\033[96m"     # 差异区块标题（@@ ... @@）
    YELLOW = "\033[93m"   # 文件头（--- / +++）
    BOLD = "\033[1m"      # 加粗
    DIM = "\033[2m"       # 暗淡（上下文行）
    RESET = "\033[0m"     # 重置所有样式


# ── 语法高亮 ──────────────────────────────────────────────────

# 文件扩展名 → pygments lexer 名称的映射（常见语言）
_LEXER_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".dockerfile": "docker",
    "Dockerfile": "docker",
}

_formatter = TerminalFormatter() if HAS_PYGMENTS else None


def _get_lexer(filename: str):
    """根据文件名获取 pygments lexer，找不到则返回 None。"""
    if not HAS_PYGMENTS:
        return None

    from pathlib import Path
    ext = Path(filename).suffix.lower()
    name = Path(filename).name

    # 先查映射表
    lexer_name = _LEXER_MAP.get(ext) or _LEXER_MAP.get(name)
    if lexer_name:
        try:
            from pygments.lexers import get_lexer_by_name
            return get_lexer_by_name(lexer_name)
        except Exception:
            pass

    # 回退到 pygments 猜测
    try:
        return get_lexer_for_filename(filename)
    except Exception:
        return None


def _highlight_line(code: str, lexer) -> str:
    """
    对单行代码做语法高亮。

    pygments 设计上是对整段代码高亮，逐行高亮会丢失多行结构（如多行字符串）。
    但对 diff 展示来说精度够用，且视觉提升明显。
    """
    if not lexer or not HAS_PYGMENTS:
        return code

    try:
        highlighted = highlight(code, lexer, _formatter)
        # pygments 输出末尾带换行符，去掉
        return highlighted.rstrip("\n")
    except Exception:
        return code


def _highlight_codeblock(code: str, filename: str) -> str:
    """
    对整段代码做语法高亮，返回带 ANSI 颜色的字符串。

    用于 show_new_file 等需要高亮完整代码的场景。
    """
    if not HAS_PYGMENTS:
        return code

    lexer = _get_lexer(filename)
    if not lexer:
        return code

    try:
        return highlight(code, lexer, _formatter).rstrip("\n")
    except Exception:
        return code


# ── Diff 展示 ────────────────────────────────────────────────

def show_diff(old_content: str, new_content: str, filename: str) -> None:
    """
    生成并打印文件的 unified diff（带语法高亮）。

    参数:
        old_content: 修改前的文件内容
        new_content: 修改后的文件内容
        filename:    文件名（用于 diff 头部显示和语法高亮 lexer 选择）
    """
    if old_content == new_content:
        print(f"  {Color.DIM}(no changes){Color.RESET}")
        return

    # 获取 lexer（用于语法高亮）
    lexer = _get_lexer(filename) if HAS_PYGMENTS else None

    # 将文本拆成行列表，difflib 按行比较
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    # 生成 unified diff
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{filename} (before)",
        tofile=f"{filename} (after)",
        lineterm="",
    )

    # 逐行着色输出
    for line in diff:
        # 去掉行尾换行符（后面手动控制）
        line = line.rstrip("\n\r")

        if line.startswith("+++") or line.startswith("---"):
            print(f"  {Color.YELLOW}{Color.BOLD}{line}{Color.RESET}")
        elif line.startswith("@@"):
            print(f"  {Color.CYAN}{line}{Color.RESET}")
        elif line.startswith("+"):
            # 新增行：绿色前缀 + 语法高亮内容
            code = line[1:]
            if lexer:
                code = _highlight_line(code, lexer)
            print(f"  {Color.GREEN}+{code}{Color.RESET}")
        elif line.startswith("-"):
            # 删除行：红色前缀（不做语法高亮，因为旧代码可能不完整）
            print(f"  {Color.RED}{line}{Color.RESET}")
        else:
            # 上下文行（差异前后的参考行）
            code = line[1:] if line.startswith(" ") else line
            if lexer and line.startswith(" "):
                code = _highlight_line(code, lexer)
                print(f"  {Color.DIM} {code}{Color.RESET}")
            else:
                print(f"  {Color.DIM}{line}{Color.RESET}")


def show_new_file(content: str, filename: str) -> None:
    """
    展示新创建文件的内容（全部为新增行，带语法高亮）。

    如果 pygments 可用，对整个文件做语法高亮后再逐行展示。
    """
    # 先对整段代码做语法高亮
    highlighted = _highlight_codeblock(content, filename)

    print(f"  {Color.YELLOW}{Color.BOLD}+++ {filename} (new file){Color.RESET}")

    if highlighted != content:
        # pygments 可用，高亮后的内容已有 ANSI 颜色
        for line in highlighted.splitlines():
            print(f"  {Color.GREEN}+{line}{Color.RESET}")
    else:
        # 无 pygments，纯绿色输出
        for line in content.splitlines():
            print(f"  {Color.GREEN}+{line}{Color.RESET}")
