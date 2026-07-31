"""
tools.py - 工具系统

核心原理：
  这是编程助手与文件系统交互的桥梁。LLM 本身不能直接操作文件，
  但它可以通过"工具调用"来请求执行操作。

  工作流程：
  1. LLM 决定需要读取文件 → 返回 tool_call: read_file(path="main.py")
  2. 我们执行 _tool_read_file("main.py") → 返回文件内容
  3. 文件内容被喂回 LLM → LLM 基于内容继续推理

  每个工具需要两部分：
  - 定义（schema）：JSON Schema 格式，告诉 LLM 工具名、描述、参数
  - 执行函数：实际执行操作的 Python 方法

  安全设计：
  - 所有文件操作都被限制在工作目录内（防止路径穿越攻击）
  - 写操作前展示 diff，让用户确认
"""

import os
import json
import subprocess
from pathlib import Path

from .diff import show_diff, show_new_file, Color


class ToolSystem:
    """工具定义与执行系统。"""

    def __init__(self, working_dir: str):
        self.working_dir = Path(working_dir).resolve()
        # 工具定义列表，会发给 LLM
        self.definitions = self._build_definitions()

    # ── 工具定义（发给 LLM 的 schema）──────────────────────────
    def _build_definitions(self) -> list:
        """
        构建工具定义列表。

        每个定义是一个 JSON Schema，告诉 LLM：
        - 工具叫什么（name）
        - 做什么用（description）
        - 需要什么参数（parameters）

        LLM 会根据这些定义决定何时调用哪个工具、传什么参数。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files and subdirectories in a directory. Use to understand project structure.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Directory path relative to working directory. Defaults to '.'",
                            }
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the full content of a file. Always read before modifying.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to working directory",
                            }
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write to a file. Overwrites if exists, creates if not. Use for new files or complete rewrites.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to working directory",
                            },
                            "content": {
                                "type": "string",
                                "description": "Full file content to write",
                            },
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Edit a file by replacing old_text with new_text. old_text must match exactly (including indentation).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to working directory",
                            },
                            "old_text": {
                                "type": "string",
                                "description": "The exact text to replace (must match file content exactly)",
                            },
                            "new_text": {
                                "type": "string",
                                "description": "The new text to insert",
                            },
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_files",
                    "description": "Search for text across files in a directory. Returns matching filenames and line content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Text to search for",
                            },
                            "path": {
                                "type": "string",
                                "description": "Directory to search in, defaults to '.'",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Execute a shell command and return its output. Use for running tests, builds, linters, or any command-line tool. Requires user confirmation.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {
                                "type": "string",
                                "description": "The shell command to execute",
                            },
                            "timeout": {
                                "type": "integer",
                                "description": "Timeout in seconds (default 30, max 120)",
                            },
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "glob",
                    "description": "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts'). Returns matching file paths.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Glob pattern to match files (e.g. '**/*.py', '*.md', 'src/**/*.ts')",
                            },
                            "path": {
                                "type": "string",
                                "description": "Directory to search in, defaults to '.'",
                            },
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_directory",
                    "description": "Create a directory and any missing parent directories.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Directory path to create, relative to working directory",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_file",
                    "description": "Delete a file. Requires user confirmation. Use with caution.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path to delete, relative to working directory",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_file",
                    "description": "Move or rename a file. Requires user confirmation.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "Source file path, relative to working directory",
                            },
                            "destination": {
                                "type": "string",
                                "description": "Destination file path, relative to working directory",
                            },
                        },
                        "required": ["source", "destination"],
                    },
                },
            },
        ]

    # ── 路径安全检查 ────────────────────────────────────────────
    def _safe_path(self, path: str) -> Path:
        """
        将相对路径转为绝对路径，并确保不超出工作目录。

        这是为了防止路径穿越攻击（如 path="../../../etc/passwd"）。
        所有真实编程助手都有类似的安全检查。
        """
        full = (self.working_dir / path).resolve()
        if not str(full).startswith(str(self.working_dir)):
            raise ValueError(f"Path escapes working directory: {path}")
        return full

    # ── 用户确认 ────────────────────────────────────────────────
    def _confirm(self, message: str) -> bool:
        """询问用户是否确认操作。"""
        answer = input(f"  {Color.YELLOW}{message} [y/N]: {Color.RESET}").strip().lower()
        return answer in ("y", "yes")

    # ── 工具执行入口 ────────────────────────────────────────────
    def execute(self, tool_name: str, arguments: dict) -> str:
        """
        执行工具调用。

        根据 tool_name 找到对应的处理方法并执行。
        所有工具都返回字符串（LLM 只能处理文本）。
        """
        # 工具名 → 处理方法的映射
        handlers = {
            "list_files": self._tool_list_files,
            "read_file": self._tool_read_file,
            "write_file": self._tool_write_file,
            "edit_file": self._tool_edit_file,
            "search_files": self._tool_search_files,
            "run_command": self._tool_run_command,
            "glob": self._tool_glob,
            "create_directory": self._tool_create_directory,
            "delete_file": self._tool_delete_file,
            "move_file": self._tool_move_file,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return f"Error: unknown tool '{tool_name}'"

        try:
            return handler(**arguments)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    # ── 具体工具实现 ────────────────────────────────────────────

    def _tool_list_files(self, path: str = ".") -> str:
        """列出目录内容，忽略常见无关目录（如 .git, node_modules）。"""
        target = self._safe_path(path)
        if not target.exists():
            return f"Error: directory does not exist: {path}"
        if not target.is_dir():
            return f"Error: not a directory: {path}"

        ignore = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".mca"}
        entries = []
        for item in sorted(target.iterdir()):
            if item.name in ignore:
                continue
            if item.is_dir():
                entries.append(f"  [DIR]  {item.name}/")
            else:
                size = item.stat().st_size
                entries.append(f"  [FILE] {item.name}  ({size} bytes)")

        return f"Directory {path}:\n" + "\n".join(entries) if entries else f"Directory {path} is empty"

    def _tool_read_file(self, path: str) -> str:
        """读取文件内容。"""
        target = self._safe_path(path)
        if not target.exists():
            return f"Error: file does not exist: {path}"
        if not target.is_file():
            return f"Error: not a file: {path}"

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Error: cannot read file (possibly binary): {path}"

        # 限制返回长度，避免占用过多 token
        max_chars = 8000
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n\n...(file truncated, total {len(content)} chars)"
        return content

    def _tool_write_file(self, path: str, content: str) -> str:
        """写入文件（创建或覆盖），会先展示内容再请求确认。"""
        target = self._safe_path(path)

        if target.exists():
            # 文件已存在，展示 diff
            old_content = target.read_text(encoding="utf-8")
            print(f"\n  [WRITE] {path} will be overwritten:")
            show_diff(old_content, content, path)
        else:
            # 新文件，展示内容
            print(f"\n  [WRITE] creating new file {path}:")
            show_new_file(content, path)

        if not self._confirm("Confirm write?"):
            return "Operation cancelled"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"File written: {path} ({len(content)} chars)"

    def _tool_edit_file(self, path: str, old_text: str, new_text: str) -> str:
        """编辑文件：将 old_text 替换为 new_text，会先展示 diff 再请求确认。"""
        target = self._safe_path(path)
        if not target.exists():
            return f"Error: file does not exist: {path}"

        content = target.read_text(encoding="utf-8")

        # 检查 old_text 是否存在
        count = content.count(old_text)
        if count == 0:
            return f"Error: old_text not found in file. Please read the file first to verify content."
        if count > 1:
            return f"Error: old_text appears {count} times in file. Please provide more specific text to match uniquely."

        # 生成新内容
        new_content = content.replace(old_text, new_text)

        # 展示 diff
        print(f"\n  [EDIT] {path} will be modified:")
        show_diff(content, new_content, path)

        if not self._confirm("Confirm edit?"):
            return "Operation cancelled"

        target.write_text(new_content, encoding="utf-8")
        return f"File edited: {path}"

    def _tool_search_files(self, pattern: str, path: str = ".") -> str:
        """在目录下搜索文本，返回匹配的文件和行。"""
        target = self._safe_path(path)
        if not target.exists():
            return f"Error: directory does not exist: {path}"

        ignore = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mca"}
        results = []
        max_results = 50  # 限制结果数量

        for root, dirs, files in os.walk(target):
            # 过滤无关目录
            dirs[:] = [d for d in dirs if d not in ignore]

            for fname in files:
                fpath = Path(root) / fname
                try:
                    text = fpath.read_text(encoding="utf-8")
                except (UnicodeDecodeError, PermissionError):
                    continue

                for i, line in enumerate(text.splitlines(), 1):
                    if pattern.lower() in line.lower():
                        rel = fpath.relative_to(self.working_dir)
                        # 截取匹配行的上下文
                        display = line.strip()[:120]
                        results.append(f"  {rel}:{i}: {display}")
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        if not results:
            return f"No matches found for '{pattern}'"

        header = f"Search results for '{pattern}' ({len(results)} matches):\n"
        return header + "\n".join(results)

    # ── 新增工具实现 ────────────────────────────────────────────

    def _tool_run_command(self, command: str, timeout: int = 30) -> str:
        """
        执行 shell 命令并返回输出。

        安全设计：
        - 需要用户确认后才执行
        - 设置超时防止命令卡死
        - 限制输出长度避免占用过多 token
        - 在工作目录下执行
        """
        # 限制超时范围
        timeout = max(5, min(timeout, 120))

        print(f"\n  {Color.YELLOW}[COMMAND]{Color.RESET} {command}")
        print(f"  {Color.YELLOW}Timeout:{Color.RESET} {timeout}s  {Color.YELLOW}Working dir:{Color.RESET} {self.working_dir}")

        if not self._confirm("Run this command?"):
            return "Command cancelled"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.working_dir),
            )

            output_parts = []

            # 标准输出
            if result.stdout:
                output_parts.append(f"--- stdout ---\n{result.stdout.rstrip()}")

            # 标准错误
            if result.stderr:
                output_parts.append(f"--- stderr ---\n{result.stderr.rstrip()}")

            # 退出码
            if result.returncode != 0:
                output_parts.append(f"Exit code: {result.returncode}")

            output = "\n".join(output_parts) if output_parts else "Command completed (no output)"

            # 限制输出长度
            max_chars = 8000
            if len(output) > max_chars:
                output = output[:max_chars] + f"\n\n...(output truncated, total {len(output)} chars)"

            return output

        except subprocess.TimeoutExpired:
            return f"Command timed out after {timeout}s: {command}"
        except Exception as e:
            return f"Error running command: {type(e).__name__}: {e}"

    def _tool_glob(self, pattern: str, path: str = ".") -> str:
        """
        按 glob 模式查找文件。

        支持 ** 递归匹配，例如：
        - '**/*.py'   匹配所有 Python 文件
        - 'src/**/*.ts'  匹配 src 下所有 TypeScript 文件
        - '*.md'      匹配根目录下的 Markdown 文件
        """
        target = self._safe_path(path)
        if not target.exists():
            return f"Error: directory does not exist: {path}"
        if not target.is_dir():
            return f"Error: not a directory: {path}"

        ignore = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode", ".mca"}

        try:
            matches = list(target.glob(pattern))
        except Exception as e:
            return f"Error: invalid glob pattern '{pattern}': {e}"

        # 过滤无关目录
        results = []
        for match in matches:
            # 检查路径中是否包含忽略目录
            parts = match.relative_to(target).parts
            if any(part in ignore for part in parts):
                continue

            rel = match.relative_to(self.working_dir)
            if match.is_dir():
                results.append(f"  [DIR]  {rel}/")
            else:
                size = match.stat().st_size
                results.append(f"  [FILE] {rel}  ({size} bytes)")

        if not results:
            return f"No files matching '{pattern}' in {path}"

        # 按路径排序
        results.sort()
        header = f"Glob '{pattern}' in {path} ({len(results)} matches):\n"
        return header + "\n".join(results)

    def _tool_create_directory(self, path: str) -> str:
        """创建目录（含父目录），类似 mkdir -p。"""
        target = self._safe_path(path)

        if target.exists():
            if target.is_dir():
                return f"Directory already exists: {path}"
            return f"Error: a file already exists at: {path}"

        print(f"\n  {Color.YELLOW}[MKDIR]{Color.RESET} {path}")

        if not self._confirm("Create directory?"):
            return "Operation cancelled"

        target.mkdir(parents=True, exist_ok=True)
        return f"Directory created: {path}"

    def _tool_delete_file(self, path: str) -> str:
        """删除文件，需要用户确认。"""
        target = self._safe_path(path)

        if not target.exists():
            return f"Error: file does not exist: {path}"
        if target.is_dir():
            return f"Error: path is a directory, not a file: {path}. Use remove_directory if needed."

        size = target.stat().st_size
        print(f"\n  {Color.RED}[DELETE]{Color.RESET} {path}  ({size} bytes)")

        if not self._confirm("Delete this file?"):
            return "Operation cancelled"

        target.unlink()
        return f"File deleted: {path}"

    def _tool_move_file(self, source: str, destination: str) -> str:
        """移动或重命名文件，需要用户确认。"""
        src = self._safe_path(source)
        dst = self._safe_path(destination)

        if not src.exists():
            return f"Error: source does not exist: {source}"
        if dst.exists():
            return f"Error: destination already exists: {destination}"

        print(f"\n  {Color.YELLOW}[MOVE]{Color.RESET} {source} → {destination}")

        if not self._confirm("Move file?"):
            return "Operation cancelled"

        # 确保目标目录存在
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            src.rename(dst)
        except OSError as e:
            # 跨文件系统移动时 rename 可能失败，回退到 shutil
            import shutil
            shutil.move(str(src), str(dst))

        return f"File moved: {source} → {destination}"
