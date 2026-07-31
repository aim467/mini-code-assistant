"""
llm.py - LLM API 客户端

核心原理：
  编程助手的大脑是一个 LLM（大语言模型）。我们通过 HTTP 调用
  OpenAI 兼容的 Chat Completions API，发送对话历史和工具定义，
  模型返回文本回复或工具调用请求。

  这里的关键概念是 **Tool Calling（工具调用）**：
  - 我们在请求中声明一组工具（函数）及其参数 schema
  - 模型可以决定调用某个工具，返回工具名和参数
  - 我们执行工具，把结果喂回模型
  - 模型基于结果继续推理，直到给出最终回复

  这个"请求 → 工具调用 → 执行 → 喂回 → 继续"的循环，
  就是所有 AI 编程助手的核心工作模式（Agent Loop）。
"""

import json
import requests


class LLMClient:
    """封装 OpenAI 兼容的 Chat Completions API。"""

    def __init__(self, api_key: str, base_url: str, model: str, temperature: float = 0.3):
        """
        参数:
            api_key:      API 密钥
            base_url:     API 基础地址（如 https://api.openai.com/v1）
            model:        模型名称（如 gpt-4o, deepseek-chat 等）
            temperature:  采样温度，编程任务推荐低值（默认 0.3）
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature

    def chat(self, messages: list, tools: list = None) -> dict:
        """
        调用 LLM，返回响应。

        参数:
            messages: 对话历史（包含 system / user / assistant / tool 消息）
            tools:    可用工具的定义列表（JSON Schema 格式）

        返回:
            {
                "content": str,          # 模型的文本回复
                "tool_calls": list,      # 模型请求的工具调用列表
                "finish_reason": str     # 结束原因
            }
        """
        # ── 构建请求体 ────────────────────────────────────────
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        # 如果提供了工具定义，加入请求
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"  # 让模型自己决定是否调用工具

        # ── 发送 HTTP 请求 ───────────────────────────────────
        # 这里直接用 requests，不依赖 openai SDK，便于理解底层交互
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,  # LLM 响可能较慢，给足时间
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            return {
                "content": "[错误] 无法连接到 API 服务器，请检查 base_url 和网络。",
                "tool_calls": [],
                "finish_reason": "error",
            }
        except requests.exceptions.Timeout:
            return {
                "content": "[错误] API 请求超时。",
                "tool_calls": [],
                "finish_reason": "error",
            }
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code
            body = e.response.text[:200]
            return {
                "content": f"[错误] API 返回 {status}: {body}",
                "tool_calls": [],
                "finish_reason": "error",
            }

        # ── 解析响应 ──────────────────────────────────────────
        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]

        return {
            "content": message.get("content") or "",
            "tool_calls": message.get("tool_calls") or [],
            "finish_reason": choice["finish_reason"],
        }

    def summarize(self, messages: list) -> str:
        """
        让 LLM 对一段对话历史生成摘要。

        用于上下文管理：当对话 token 数接近模型上限时，把旧的对话轮次
        压缩成一段摘要，腾出空间给新对话。

        参数:
            messages: 需要摘要的对话消息列表（不含 system prompt）

        返回:
            摘要文本
        """
        # 把对话历史序列化为可读文本
        transcript_lines = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content") or ""
            if role == "tool":
                # 工具结果可能很长，截取前 200 字符
                content = content[:200] + "..." if len(content) > 200 else content
                role = "tool_result"
            transcript_lines.append(f"[{role}]: {content}")
        transcript = "\n".join(transcript_lines)

        summary_prompt = (
            "You are a conversation summarizer. Summarize the following conversation "
            "between a user and a coding assistant. Focus on:\n"
            "1. What the user asked for\n"
            "2. What files were read, created, or modified (include file paths)\n"
            "3. What tools were used and key results\n"
            "4. Any decisions made or important findings\n"
            "5. Any unresolved issues or next steps\n\n"
            "Keep the summary concise but preserve all important technical details "
            "(file paths, function names, error messages). Write in the same language "
            "as the conversation.\n\n"
            f"Conversation to summarize:\n{transcript}"
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful conversation summarizer."},
                {"role": "user", "content": summary_prompt},
            ],
            "temperature": 0.0,  # 摘要需要确定性
        }

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception:
            # 摘要失败时回退：返回一个简单的截断提示
            return (
                "[摘要生成失败，以下是早期对话的截断记录]\n"
                + transcript[:500]
            )
