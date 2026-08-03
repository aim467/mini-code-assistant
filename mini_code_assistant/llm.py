#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
llm.py - LLM API 客户端（基于 OpenAI Python SDK）

核心原理：
  编程助手的大脑是一个 LLM（大语言模型）。我们通过 OpenAI 兼容的
  Chat Completions API，发送对话历史和工具定义，模型返回文本回复或
  工具调用请求。

  关键概念是 **Tool Calling（工具调用）**：
  - 我们在请求中声明一组工具（函数）及其参数 schema
  - 模型可以决定调用某个工具，返回工具名和参数
  - 我们执行工具，把结果喂回模型
  - 模型基于结果继续推理，直到给出最终回复

  这个"请求 → 工具调用 → 执行 → 喂回 → 继续"的循环，
  就是所有 AI 编程助手的核心工作模式（Agent Loop）。

  本模块使用 OpenAI Python SDK（openai 库）替代原始 HTTP 请求：
  - SDK 内置 SSE 流式解析，无需手写
  - SDK 内置错误重试、超时处理
  - SDK 返回结构化对象，工具调用解析更可靠
  - SDK 的 usage 字段提供精确的 token 计数

  支持两种调用方式：
  - chat(): 非流式调用，等待完整响应后返回
  - chat_stream(): 流式调用，逐块返回响应增量
"""

from openai import OpenAI, APITimeoutError, APIConnectionError, APIStatusError


# 摘要任务的 system 提示词：负责角色与全局约束（防幻觉、保留原文）。
# 任务本身（结构、语言、数据）放在 user 消息里，避免角色重复声明。
SUMMARY_SYSTEM = (
    "You are a meticulous conversation summarizer for a coding assistant. "
    "Summarize only what actually appears in the transcript; never invent "
    "files, function names, or decisions. Preserve file paths, function names, "
    "and error messages verbatim."
)


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
        # 使用 OpenAI SDK 客户端，通过 base_url 兼容 DeepSeek / Moonshot 等
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout=120.0,  # LLM 响应可能较慢，给足时间
            max_retries=2,  # 自动重试 2 次
        )
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
                "finish_reason": str,    # 结束原因
                "usage": dict | None,    # token 用量（prompt_tokens, completion_tokens, total_tokens）
            }
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        # 如果提供了工具定义，加入请求
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"  # 让模型自己决定是否调用工具

        # ── 发送请求 ────────────────────────────────────────
        # SDK 自动处理超时、重试和 HTTP 错误
        try:
            response = self.client.chat.completions.create(**kwargs)
        except APIConnectionError:
            return {
                "content": "[错误] 无法连接到 API 服务器，请检查 base_url 和网络。",
                "tool_calls": [],
                "finish_reason": "error",
                "usage": None,
            }
        except APITimeoutError:
            return {
                "content": "[错误] API 请求超时。",
                "tool_calls": [],
                "finish_reason": "error",
                "usage": None,
            }
        except APIStatusError as e:
            return {
                "content": f"[错误] API 返回 {e.status_code}: {e.message[:200]}",
                "tool_calls": [],
                "finish_reason": "error",
                "usage": None,
            }

        # ── 解析响应 ────────────────────────────────────────
        # SDK 返回结构化对象，无需手动解析 JSON
        choice = response.choices[0]
        message = choice.message

        # 将 SDK 的 tool_calls 对象转为 dict 列表，保持与原有代码兼容
        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "finish_reason": choice.finish_reason,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            } if response.usage else None,
        }

    def chat_stream(self, messages: list, tools: list = None):
        """
        流式调用 LLM，逐块返回响应（SSE - Server-Sent Events）。

        与 chat() 不同，此方法通过 HTTP 流式传输实时返回 LLM 的输出，
        用户可以立即看到模型的生成过程，而不必等待完整响应。

        SDK 自动处理 SSE 解析，我们只需遍历 stream 对象即可。

        参数:
            messages: 对话历史
            tools:    可用工具定义列表

        Yields 事件字典:
            {"type": "text_delta", "content": "..."}              - 文本增量
            {"type": "tool_call_delta", "index": N,              - 工具调用增量
             "id": "...", "name": "...", "arguments_delta": "..."}
            {"type": "done", "finish_reason": "..."}             - 流结束
            {"type": "error", "content": "..."}                  - 错误
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": True,  # 关键：启用流式传输
            "stream_options": {"include_usage": True},  # 请求流式响应包含 token 用量
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # ── 发送流式请求 ────────────────────────────────────
        # SDK 内置 SSE 解析，无需手写逐行解析逻辑
        try:
            stream = self.client.chat.completions.create(**kwargs)
        except APIConnectionError:
            yield {"type": "error", "content": "[错误] 无法连接到 API 服务器，请检查 base_url 和网络。"}
            return
        except APITimeoutError:
            yield {"type": "error", "content": "[错误] API 请求超时。"}
            return
        except APIStatusError as e:
            yield {"type": "error", "content": f"[错误] API 返回 {e.status_code}: {e.message[:200]}"}
            return

        # ── 遍历流式响应 ────────────────────────────────────
        # SDK 已将 SSE 事件解析为结构化对象，直接访问字段即可
        for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta
            finish_reason = choice.finish_reason

            # ── 文本增量 ──────────────────────────────────
            if delta.content:
                yield {"type": "text_delta", "content": delta.content}

            # ── 工具调用增量 ──────────────────────────────
            # SDK 将流式工具调用解析为结构化对象
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    yield {
                        "type": "tool_call_delta",
                        "index": tc.index,
                        "id": tc.id,
                        "name": tc.function.name if tc.function else None,
                        "arguments_delta": tc.function.arguments if tc.function else "",
                    }

            # ── 流结束 ────────────────────────────────────
            if finish_reason:
                # 尝试获取 usage（需要 stream_options 包含 include_usage）
                usage_info = None
                if chunk.usage:
                    usage_info = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }
                done_event = {"type": "done", "finish_reason": finish_reason}
                if usage_info:
                    done_event["usage"] = usage_info
                yield done_event
                return

        # 如果流意外结束（没有收到 finish_reason），发送 done 事件
        yield {"type": "done", "finish_reason": "stop"}

    def summarize(self, messages: list, language: str = "中文") -> str:
        """
        让 LLM 对一段对话历史生成摘要。

        用于上下文管理：当对话 token 数接近模型上限时，把旧的对话轮次
        压缩成一段摘要，腾出空间给新对话。

        参数:
            messages: 需要摘要的对话消息列表（不含 system prompt）
            language: 摘要正文使用的语言（默认中文）；文件路径、函数名、
                      代码与报错信息始终保留原文

        返回:
            摘要文本
        """
        # ── 序列化对话历史 ─────────────────────────────────
        # 注意：assistant 的 tool_calls 消息 content 通常为空，
        # 工具名和参数都在 tool_calls 字段里，必须一并序列化，
        # 否则模型无从得知"用了什么工具"。
        transcript_lines = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content") or ""

            # assistant 请求调用工具的消息：content 常为空，工具信息在 tool_calls 里
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = (fn.get("arguments") or "")[:200]
                    transcript_lines.append(
                        f"[assistant tool_call]: {fn.get('name')}({args})"
                    )

            if content:
                if role == "tool":
                    # 工具结果可能很长，截取前 200 字符
                    content = content[:200] + "..." if len(content) > 200 else content
                    role = "tool_result"
                transcript_lines.append(f"[{role}]: {content}")

        # 没有可总结的内容时直接返回，避免浪费一次 API 调用
        if not transcript_lines:
            return ""
        transcript = "\n".join(transcript_lines)

        # 任务提示词：角色与全局约束在 SUMMARY_SYSTEM（system 消息）里，
        # 这里只描述任务本身、输出结构与语言
        summary_prompt = (
            "Summarize the following conversation between a user and a coding assistant. "
            "This summary will be used as the assistant's memory for future turns, so it "
            "must be self-contained: a reader who has not seen the original conversation "
            "must be able to continue the work from it.\n\n"
            "Use this Markdown structure and keep the whole summary under ~150 words:\n"
            "## Goal\n"
            "What the user asked for.\n"
            "## Changes\n"
            "Files created or modified (full paths) and functions added/changed.\n"
            "## Tool usage\n"
            "Which tools were called and their key results.\n"
            "## Decisions & Findings\n"
            "Decisions made, important findings, exact error messages.\n"
            "## Next steps\n"
            "Unresolved issues and pending actions.\n\n"
            f"Write the prose in {language}. File paths, function names, code, "
            "and error messages stay verbatim.\n\n"
            f"Conversation to summarize:\n{transcript}"
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM},
                    {"role": "user", "content": summary_prompt},
                ],
                temperature=0.0,  # 摘要需要确定性
            )
            return response.choices[0].message.content or ""
        except Exception:
            # 摘要失败时回退：返回一个简单的截断提示
            return (
                "[摘要生成失败，以下是早期对话的截断记录]\n"
                + transcript[:500]
            )