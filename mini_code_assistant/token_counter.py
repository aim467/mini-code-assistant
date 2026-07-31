"""
token_counter.py - Token 计数

核心原理：
  LLM 有上下文窗口限制（如 gpt-4o 是 128k tokens）。我们需要估算
  当前对话消耗了多少 token，以便：
  1. 在接近上限时触发智能摘要（而非粗暴裁剪）
  2. 向用户实时展示 token 用量

  两种计数模式：
  - 精确模式：使用 tiktoken（OpenAI 的 tokenizer），精确计算 token 数
  - 估算模式：1 token ≈ 3 字符（中英混合粗略值），无需额外依赖

  tiktoken 只对 OpenAI 模型精确，对 DeepSeek / Moonshot 等有偏差，
  但作为触发摘要的阈值判断已经够用。
"""

try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    HAS_TIKTOKEN = False


# ── 常见模型的上下文窗口大小 ──────────────────────────────────
MODEL_CONTEXT_SIZES = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-4-32k": 32_768,
    "gpt-3.5-turbo": 16_385,
    "gpt-3.5-turbo-16k": 16_385,
    # DeepSeek
    "deepseek-chat": 64_000,
    "deepseek-reasoner": 64_000,
    "deepseek-coder": 16_000,
    # Moonshot
    "moonshot-v1-8k": 8_192,
    "moonshot-v1-32k": 32_768,
    "moonshot-v1-128k": 131_072,
    # Claude (兼容 API 场景)
    "claude-3-5-sonnet": 200_000,
    "claude-3-5-haiku": 200_000,
    "claude-3-opus": 200_000,
    # 本地模型
    "llama3": 8_192,
    "llama3.1": 131_072,
    "qwen2": 32_768,
}

# 摘要触发的阈值比例（占上下文窗口的百分比）
SUMMARIZE_THRESHOLD = 0.80

# 摘要后保留的最近轮次数（不参与摘要的最近对话）
KEEP_RECENT_TURNS = 4


def count_tokens(messages: list, model: str = "gpt-4o") -> int:
    """
    估算对话历史的 token 数。

    参数:
        messages: 对话历史列表
        model:    模型名称（用于选择 tokenizer）

    返回:
        估算的 token 数
    """
    if HAS_TIKTOKEN:
        return _count_with_tiktoken(messages, model)
    return _count_with_estimate(messages)


def _count_with_tiktoken(messages: list, model: str) -> int:
    """使用 tiktoken 精确计算 token 数。"""
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        # 未知模型，回退到通用编码
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            return _count_with_estimate(messages)

    total = 0
    for msg in messages:
        # 每条消息有固定开销（role 标记等）
        total += 4
        content = msg.get("content") or ""
        total += len(enc.encode(content))
        # 工具调用的参数也计入
        for tc in msg.get("tool_calls", []):
            args = tc.get("function", {}).get("arguments", "")
            total += len(enc.encode(args))
    total += 2  # 对话结束标记
    return total


def _count_with_estimate(messages: list) -> int:
    """
    无 tiktoken 时的粗略估算。

    经验值：英文约 4 字符/token，中文约 1.5 字符/token，
    混合代码取 3 字符/token 作为折中。
    """
    total = 0
    for msg in messages:
        total += 4
        content = msg.get("content") or ""
        total += len(content) // 3
        for tc in msg.get("tool_calls", []):
            args = tc.get("function", {}).get("arguments", "")
            total += len(args) // 3
    total += 2
    return total


def get_context_limit(model: str) -> int:
    """
    获取模型的上下文窗口大小（token 数）。

    先精确匹配，再前缀模糊匹配，最后回退到 32k 默认值。
    """
    if model in MODEL_CONTEXT_SIZES:
        return MODEL_CONTEXT_SIZES[model]

    # 前缀模糊匹配（如 "gpt-4o-2024-08-06" 匹配 "gpt-4o"）
    for key, size in MODEL_CONTEXT_SIZES.items():
        if model.startswith(key):
            return size

    return 32_768  # 安全默认值


def get_summarize_threshold(model: str) -> int:
    """获取触发摘要的 token 阈值。"""
    return int(get_context_limit(model) * SUMMARIZE_THRESHOLD)


def format_token_count(count: int, limit: int) -> str:
    """
    格式化 token 用量显示。

    示例: "12.3k / 128k (10%)"
    """
    pct = (count / limit * 100) if limit > 0 else 0
    return f"{_format_k(count)} / {_format_k(limit)} ({pct:.0f}%)"


def _format_k(n: int) -> str:
    """将数字格式化为 k 单位。"""
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
