#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp.py - MCP（Model Context Protocol）客户端（同步、最小实现）

核心原理：
  MCP 让编程助手复用整个生态的工具 / 数据源。本模块实现 MCP 客户端的
  **stdio 传输**部分：

  - 把每个 MCP Server 作为子进程拉起（stdin/stdout 走换行分隔的 JSON-RPC 2.0）
  - 握手：initialize → notifications/initialized
  - 发现工具：tools/list（支持 cursor 分页）
  - 调用工具：tools/call
  - 关闭：关 stdin → SIGTERM/KILL

  设计取舍（见 MCP_INTEGRATION.md）：
  - 现有项目是纯同步的，官方 `mcp` SDK 是异步（anyio）。为契合“代码极简、
    理解原理”，这里用**同步自研客户端**走通协议；后续可选换成官方 SDK。
  - 把 MCP 工具名改成 `mcp__<server>__<tool>`，避免与内置工具或其它 Server
    重名；tools/call 时再拆回。

  关键不变量（stdio 协议细节）：
  - 客户端通过 stdin 写 JSON，每行一个请求 / 通知
  - Server 的日志必须走 **stderr**，绝不能写 stdout（否则会被当成 JSON-RPC 消息）
"""

import json
import os
import subprocess
import threading
import queue
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    from . import __version__ as MCA_VERSION
except Exception:  # pragma: no cover - 保证独立可导入
    MCA_VERSION = "0.6.1"

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"


# ── 配置 ───────────────────────────────────────────────────────
@dataclass
class MCPServerConfig:
    """单个 MCP Server 的启动配置。"""

    name: str
    command: str
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    disabled: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "MCPServerConfig":
        """从 mcp.json 的单条记录构造，忽略未知字段，保证向前兼容。"""
        return cls(
            name=str(d.get("name", "")),
            command=str(d.get("command", "")),
            args=list(d.get("args", [])),
            env=dict(d.get("env", {})),
            disabled=bool(d.get("disabled", False)),
        )


# ── 命名空间转换 ──────────────────────────────────────────────
def mcp_tool_name(server: str, tool: str) -> str:
    """把 (server, tool) 转成喂给 LLM 的工具名 `mcp__<server>__<tool>`。"""
    return f"mcp__{server}__{tool}"


def parse_mcp_tool_name(full: str):
    """
    把 `mcp__<server>__<tool>` 拆回 (server, tool)。

    返回 (server, tool) 或 None（不是 MCP 工具）。
    注意：只切两刀，工具名里若含 "__" 也保持完整。
    """
    if not full.startswith("mcp__"):
        return None
    parts = full.split("__", 2)
    if len(parts) != 3:
        return None
    return parts[1], parts[2]


# ── 单个 Server 连接 ──────────────────────────────────────────
class MCPConnection:
    """单个 MCP Server 的 stdio 连接（同步、最小实现）。"""

    def __init__(
        self,
        cfg: MCPServerConfig,
        init_timeout: float = 30.0,
        request_timeout: float = 30.0,
    ):
        self.cfg = cfg
        self.init_timeout = init_timeout
        self.request_timeout = request_timeout
        self.server_info: dict = {}
        self.protocol_version: str = PROTOCOL_VERSION
        self.tools: list = []

        # 拉起子进程（Server 的 stderr 默认继承我们的 stderr，不污染 stdout）
        try:
            self.proc = subprocess.Popen(
                [cfg.command, *cfg.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,  # 继承宿主 stderr，避免污染 JSON-RPC 的 stdout
                env={**os.environ, **cfg.env},
                text=True,
                bufsize=1,
            )
        except (FileNotFoundError, OSError) as e:
            raise RuntimeError(
                f"cannot start MCP server '{cfg.name}': command '{cfg.command}' "
                f"not found or failed to launch: {e}"
            )

        # 收发基础设施
        self._send_q: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 1
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # 握手 + 列举工具（任一卡死都会超时抛错，由 MCPManager 捕获降级）
        try:
            self._initialize()
            self.tools = self._list_tools()
        except Exception:
            self.close()
            raise

    # ── 底层收发 ──────────────────────────────────────────────
    def _send(self, method: str, params=None, notification: bool = False) -> Optional[int]:
        """
        发送一条 JSON-RPC 消息。

        返回该消息的 id（notification 返回 None）。锁覆盖整段，避免写与 id
        自增被并发打断。
        """
        with self._lock:
            msg_id = None if notification else self._next_id
            if msg_id is not None:
                self._next_id += 1
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            if msg_id is not None:
                msg["id"] = msg_id
            payload = json.dumps(msg, ensure_ascii=False)
        self._check_alive()
        self.proc.stdin.write(payload + "\n")
        self.proc.stdin.flush()
        return msg_id

    def _read_loop(self):
        """后台线程：逐行读取 stdout，区分响应与通知。"""
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # 非 JSON（理论上不该出现，可能 Server 把日志写到了 stdout）
                    logger.warning(
                        f"[mcp:{self.cfg.name}] non-JSON stdout: {line[:200]}"
                    )
                    continue
                if "id" in msg and "method" not in msg:
                    # 响应（带 id 且无 method）
                    self._send_q.put(msg)
                else:
                    # 通知（如 notifications/tools/list_changed）或无 id 消息
                    method = msg.get("method")
                    if method:
                        logger.info(
                            f"[mcp:{self.cfg.name}] notification: {method}"
                        )
        except (ValueError, OSError):
            # 进程关闭，stdout 读到 EOF，循环自然结束
            pass

    def _request(self, method: str, params=None, timeout: Optional[float] = None) -> dict:
        """
        发送一个请求并等待响应（带超时）。

        返回 result（dict）。出错抛 RuntimeError（含协议 error 信息）。
        """
        if timeout is None:
            timeout = self.request_timeout
        msg_id = self._send(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._reader.is_alive() and self.proc.poll() is not None:
                raise RuntimeError(
                    f"MCP server '{self.cfg.name}' process exited "
                    f"before responding to {method}"
                )
            try:
                msg = self._send_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    err = msg["error"]
                    raise RuntimeError(
                        f"MCP error from '{self.cfg.name}' on {method}: "
                        f"{err.get('message', err)}"
                    )
                return msg.get("result", {})
        raise TimeoutError(
            f"MCP request timeout ({timeout}s): {method} on server '{self.cfg.name}'"
        )

    def _check_alive(self):
        if self.proc.poll() is not None:
            raise RuntimeError(
                f"MCP server '{self.cfg.name}' process already exited "
                f"(code {self.proc.poll()})"
            )

    # ── 生命周期 ──────────────────────────────────────────────
    def _initialize(self):
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "mini-code-assistant", "version": MCA_VERSION},
            },
            timeout=self.init_timeout,
        )
        # 记录 Server 声明的版本（仅记录，不强校验；不兼容由后续调用暴露）
        self.protocol_version = result.get("protocolVersion", PROTOCOL_VERSION)
        self.server_info = result.get("serverInfo", {})
        if self.protocol_version != PROTOCOL_VERSION:
            logger.info(
                f"[mcp:{self.cfg.name}] protocol version mismatch: "
                f"client={PROTOCOL_VERSION}, server={self.protocol_version}"
            )
        # 握手完成通知（无 id，不期待响应）
        self._send("notifications/initialized", notification=True)

    def _list_tools(self) -> list:
        tools = []
        cursor = None
        # 支持 cursor 分页：只要 Server 返回 nextCursor 就继续拉
        while True:
            params = {}
            if cursor:
                params["cursor"] = cursor
            result = self._request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict, timeout: Optional[float] = None) -> dict:
        """
        调用工具，返回 tools/call 的原始 result dict（含 content / isError）。

        出错抛 RuntimeError；调用方（MCPManager）负责转成文本。
        """
        return self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=timeout,
        )

    def close(self):
        """优雅关闭：关 stdin → 等 5s → 否则 KILL。"""
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


# ── 多 Server 管理 ────────────────────────────────────────────
class MCPManager:
    """管理多个 MCP Server 连接，对外提供统一接口。"""

    def __init__(self, configs: list):
        """
        逐个启动 Server。单个失败只记录日志，不影响其余（优雅降级）。
        """
        self.conns: dict = {}
        self.failed: dict = {}
        for cfg in configs:
            if not isinstance(cfg, MCPServerConfig):
                cfg = MCPServerConfig.from_dict(cfg) if isinstance(cfg, dict) else cfg
            if getattr(cfg, "disabled", False):
                logger.info(f"[mcp] skipped (disabled): {cfg.name}")
                continue
            try:
                conn = MCPConnection(cfg)
                self.conns[cfg.name] = conn
                tool_count = len(conn.tools)
                logger.info(
                    f"[mcp] connected: {cfg.name} ({tool_count} tools)"
                )
            except Exception as e:
                self.failed[cfg.name] = str(e)
                logger.error(f"[mcp] failed to start {cfg.name}: {e}")

    def tool_definitions(self) -> list:
        """
        把所有已连接 Server 的工具，翻译成 OpenAI function 定义。

        LLM 完全无感这是 MCP 还是内置（inputSchema 直接当 parameters）。
        """
        defs = []
        for server, conn in self.conns.items():
            for tool in conn.tools:
                schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
                defs.append(
                    {
                        "type": "function",
                        "function": {
                            "name": mcp_tool_name(server, tool.get("name", "")),
                            "description": tool.get("description", ""),
                            "parameters": schema,
                        },
                    }
                )
        return defs

    def call(self, full_name: str, arguments: dict) -> str:
        """
        按 `mcp__<server>__<tool>` 路由调用，返回文本结果。

        任意异常都被吞掉并返回错误文本，避免一次工具失败炸掉整个 Agent Loop。
        """
        parsed = parse_mcp_tool_name(full_name)
        if not parsed:
            return f"Error: not an MCP tool: {full_name}"
        server, tool = parsed
        conn = self.conns.get(server)
        if not conn:
            return f"Error: MCP server not connected: {server}"
        try:
            result = conn.call_tool(tool, arguments)
        except Exception as e:
            return f"Error calling MCP tool '{full_name}': {e}"

        # 把 content[] 拼成文本（非 text 类型降级提示）
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(f"[{item.get('type')} content omitted]")
        text = "\n".join(parts)
        if result.get("isError"):
            text = "Tool error: " + text
        return text or "(empty result)"

    def list_servers(self) -> dict:
        """返回 {server: [tool_names]} 供 /mcp 命令展示。"""
        return {name: [t.get("name", "?") for t in c.tools] for name, c in self.conns.items()}

    def is_empty(self) -> bool:
        return not self.conns

    def close_all(self):
        for name, conn in self.conns.items():
            try:
                conn.close()
                logger.info(f"[mcp] closed: {name}")
            except Exception as e:
                logger.warning(f"[mcp] error closing {name}: {e}")
        self.conns.clear()
