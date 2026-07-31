"""
context.py - 上下文管理

核心原理：
  LLM 是无状态的——每次调用都是独立的，模型不记得之前的对话。
  所以我们需要自己维护"对话历史"，每次请求都把完整历史发给模型。

  对话历史中的消息类型：
  - system:    系统提示词，定义助手的行为准则（只发一次，放在最前面）
  - user:      用户输入
  - assistant: 模型回复（可能包含 tool_calls）
  - tool:      工具执行结果（回传给模型）

  ## 智能摘要（替代粗暴裁剪）

  当对话 token 数接近模型上下文窗口的 80% 时，自动触发摘要：
  1. 用 LLM 对旧对话轮次生成结构化摘要
  2. 摘要替换原始消息，保留 system prompt + 摘要 + 最近几轮对话
  3. 这样既控制了 token 用量，又保留了关键上下文（文件路径、决策、发现等）

  相比直接删除旧消息，摘要能保留更多信息，让 LLM 在长对话中不"失忆"。
"""

from pathlib import Path

from .token_counter import (
    count_tokens,
    get_context_limit,
    get_summarize_threshold,
    SUMMARIZE_THRESHOLD,
    KEEP_RECENT_TURNS,
)


class Context:
    """管理对话历史、系统提示词和智能摘要。"""

    def __init__(self, working_dir: str, llm=None, model: str = "gpt-4o"):
        """
        参数:
            working_dir: 工作目录
            llm:         LLMClient 实例（用于智能摘要，可选）
            model:       模型名称（用于查询上下文窗口大小和 token 计数）
        """
        self.working_dir = Path(working_dir).resolve()
        self.llm = llm
        self.model = model
        self.context_limit = get_context_limit(model)
        self.summarize_threshold = get_summarize_threshold(model)
        self.messages: list[dict] = []
        self._summary: str | None = None  # 当前摘要内容
        self._init_system_prompt()

    def _init_system_prompt(self):
        """
        构建系统提示词。

        系统提示词是编程助手的核心——它定义了：
        1. 助手的身份和目标
        2. 可用工具及使用规范
        3. 工作流程和行为准则
        """
        prompt = f"""You are a coding assistant that helps users read, understand, and modify code.

## Working directory
{self.working_dir}

## Available tools
You have the following tools:
- list_files: list files and subdirectories in a directory
- read_file: read the full content of a file
- write_file: write to a file (create new or overwrite existing)
- edit_file: edit a file (replace old_text with new_text)
- search_files: search for text across files in a directory
- run_command: execute a shell command (run tests, builds, linters, etc.)
- glob: find files matching a glob pattern (e.g. '**/*.py')
- create_directory: create a directory and any missing parent directories
- delete_file: delete a file (with confirmation)
- move_file: move or rename a file (with confirmation)

## When to use tools
- Use tools ONLY when the user's request requires reading, searching, or modifying code
- For greetings, general questions, explanations, or casual conversation, respond directly WITHOUT calling any tools
- Do NOT proactively explore the codebase unless the user asks you to do something with the code

## Workflow (when code tasks are requested)
1. Understand the user's request
2. Use list_files and glob to explore the codebase structure
3. Use read_file to understand specific files
4. Use search_files to find relevant code patterns
5. Use write_file or edit_file to make changes
6. Use run_command to run tests or verify changes
7. Briefly explain what you did

## Important rules
- Always read a file with read_file before modifying it
- edit_file's old_text must match the file content exactly (including indentation)
- edit_file's old_text should be specific enough to match only one location
- Use write_file only for creating new files or complete rewrites
- Never guess file content - always read first
- Reply in the same language as the user's input
"""
        self.messages.append({"role": "system", "content": prompt})

    def add_user_message(self, content: str):
        """添加用户消息。"""
        self.messages.append({"role": "user", "content": content})
        self._manage_context()

    def add_assistant_message(self, content: str, tool_calls: list | None = None):
        """
        添加助手消息。

        注意：如果模型返回了 tool_calls，必须把完整的 tool_calls 信息
        存入历史，否则 API 会报错（tool 结果消息找不到对应的调用）。
        """
        msg = {"role": "assistant", "content": content if content else ""}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)
        self._manage_context()

    def add_tool_result(self, tool_call_id: str, result: str):
        """
        添加工具执行结果。

        tool_call_id 必须和 assistant 消息中的 tool_calls[].id 对应，
        这样模型才知道这个结果是哪个工具调用产生的。
        """
        self.messages.append(
            {
                "role": "tool",
                "content": result,
                "tool_call_id": tool_call_id,
            }
        )
        self._manage_context()

    def clear(self):
        """清空对话历史（保留 system prompt）。"""
        self.messages = [self.messages[0]]
        self._summary = None

    # ── Token 计数 ─────────────────────────────────────────────

    def token_count(self) -> int:
        """估算当前对话历史的 token 数。"""
        return count_tokens(self.messages, self.model)

    def token_info(self) -> str:
        """返回 token 用量信息字符串。"""
        count = self.token_count()
        pct = (count / self.context_limit * 100) if self.context_limit > 0 else 0
        return f"{count:,} / {self.context_limit:,} tokens ({pct:.0f}%)"

    # ── 智能上下文管理 ─────────────────────────────────────────

    def _manage_context(self):
        """
        检查 token 用量，超阈值时触发智能摘要。

        优先使用 LLM 摘要（如果 llm 可用），否则回退到裁剪。
        """
        token_count = self.token_count()

        if token_count <= self.summarize_threshold:
            return  # 未超阈值，无需处理

        if self.llm:
            self._summarize_if_needed()
        else:
            self._trim_if_needed()

    def _summarize_if_needed(self):
        """
        智能摘要：用 LLM 压缩旧对话，保留最近几轮。

        流程：
        1. 找到所有 user 消息（轮次边界）
        2. 确定分割点：保留最后 KEEP_RECENT_TURNS 轮
        3. 对分割点之前的消息调用 LLM 生成摘要
        4. 用摘要消息替换旧消息

        消息结构变化：
        Before: [system, user1, assistant1, tool1, ..., userN, assistantN, ...]
        After:  [system, {summary_msg}, userN-3, assistantN-3, ..., userN, ...]
        """
        # 找到所有 user 消息的索引（跳过 system prompt）
        user_indices = [
            i for i, m in enumerate(self.messages)
            if m["role"] == "user"
        ]

        # 至少需要 KEEP_RECENT_TURNS + 1 轮才有摘要的意义
        if len(user_indices) <= KEEP_RECENT_TURNS:
            return

        # 分割点：保留最后 KEEP_RECENT_TURNS 轮
        # user_indices[-KEEP_RECENT_TURNS] 是要保留的第一轮的起始索引
        split_idx = user_indices[-KEEP_RECENT_TURNS]

        # 需要摘要的消息：从 system prompt 之后到 split_idx 之前
        # 如果已有旧摘要，包含旧摘要一起重新摘要
        messages_to_summarize = self.messages[1:split_idx]

        if not messages_to_summarize:
            return

        # 调用 LLM 生成摘要
        summary = self.llm.summarize(messages_to_summarize)

        # 构建摘要消息
        summary_content = f"[Conversation Summary]\n{summary}"
        summary_msg = {"role": "system", "content": summary_content}

        # 替换：system + summary + 保留的最近轮次
        self.messages = [self.messages[0], summary_msg] + self.messages[split_idx:]
        self._summary = summary

    # ── 回退：粗裁剪（无 LLM 时使用）────────────────────────────

    def _trim_if_needed(self):
        """
        无 LLM 时的回退策略：按轮次删除最早的消息。

        删除策略：按"轮次"删除，一轮从 user 消息开始到下一个 user 消息之前。
        这样保证删除后剩余消息仍构成有效的 API 对话序列：
          system → user → assistant [→ tool → assistant] → user → ...
        """
        while (
            len(self.messages) > 2
            and self.token_count() > self.summarize_threshold
        ):
            user_indices = [
                i for i, m in enumerate(self.messages) if m["role"] == "user"
            ]

            if len(user_indices) < 2:
                break

            end_of_first_turn = user_indices[1]
            del self.messages[1:end_of_first_turn]
