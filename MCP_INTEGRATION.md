# Mini Code Assistant · MCP 接入开发文档

> 目标：让 `mini-code-assistant`（用于学习 Codex / Claude Code / OpenCode 内部原理的迷你编程助手）能够接入 **MCP（Model Context Protocol）**，从而复用整个 MCP 生态的工具 / 数据源。
>
> 范围：**开发文档**（设计 + 骨架），实现分阶段进行。聚焦 `stdio` 传输（本地、零网络配置），`Streamable HTTP / SSE` 列为后续方向。
>
> 参考协议版本：**2025-06-18**（稳定版）。文末注明 2026 草案变动。
>
> **状态（2026-08-04）**：v0.6.0 + v0.6.1 已实现 —— `mini_code_assistant/mcp.py`、`tools.py` / `cli.py` 接线、`tests/` 下的 mock Server 与测试均已落地并通过。

---

## 1. 为什么接 MCP

当前 `tools.py` 的 10 个工具是**写死在代码里**的（文件操作、搜索、Shell 等）。接 MCP 的价值：

- **不写代码就能扩展能力**：数据库查询、GitHub、浏览器、各种 SaaS，只要有 MCP Server，就能即插即用。
- **生态对齐**：Claude Code / Cursor / Codex 都走 MCP，理解它的客户端实现，正是学习"真实编程助手的工具总线"的最佳样本。
- **与沙箱互补**：MCP Server 也是第三方代码，应在上一节设计的沙箱里运行（见 `SANDBOX_DESIGN.md`）。

核心思路一句话：**把 MCP Server 暴露的工具，翻译成 OpenAI Tool Calling 的 function 定义，塞进现有的 `tools.definitions`；LLM 一旦调用，就路由回对应的 Server。**

---

## 2. MCP 协议精要

### 2.1 形态

- 所有交互是 **JSON-RPC 2.0** 的 request/response 或 notification。
- `stdio` 传输：客户端把 Server 作为子进程拉起，通过 **stdin/stdout 逐行（换行分隔）收发 JSON**；Server 的日志必须写 stderr，不能污染 stdout。
- 一次会话三阶段：**initialize（握手）→ operation（tools/list、tools/call…）→ shutdown（关 stdin，必要时 SIGTERM/KILL）**。

### 2.2 关键方法

| 方向 | 方法 | 作用 |
|------|------|------|
| C→S | `initialize` | 协商 `protocolVersion` / `capabilities` / `clientInfo` |
| C→S | `notifications/initialized` | 握手完成通知（无 id） |
| C→S | `tools/list` | 发现可用工具（支持 `cursor` 分页、`ttlMs`、`cacheScope`） |
| C→S | `tools/call` | 调用工具：`{name, arguments}` |
| C→S | `resources/list`、`resources/read` | 发现 / 读取只读数据（按 URI） |
| C→S | `prompts/list`、`prompts/get` | 发现 / 获取提示词模板 |
| S→C | `notifications/tools/list_changed` | 工具列表变化时主动通知 |

### 2.3 工具定义与结果

工具定义（`tools/list` 返回）：

```json
{
  "name": "get_weather",
  "description": "Get current weather for a location",
  "inputSchema": { "type": "object", "properties": { "location": {"type": "string"} }, "required": ["location"] },
  "annotations": { "readOnlyHint": true, "destructiveHint": false }
}
```

调用结果（`tools/call` 返回）：

```json
{
  "content": [ { "type": "text", "text": "Current weather in NY: 72°F" } ],
  "isError": false
}
```

> 注意：`annotations`（readOnlyHint / destructiveHint / idempotentHint / openWorldHint）**不可信**——恶意 Server 可谎称只读。宿主应用（我们）必须自行获取用户确认，不能依赖它。

### 2.4 与 OpenAI Tool Calling 的映射

MCP 的 `inputSchema` 就是 OpenAI function 的 `parameters`（都是 JSON Schema）：

```
OpenAI: { "type":"function", "function": { "name", "description", "parameters": <inputSchema> } }
MCP  : { "name", "description", "inputSchema" }
```

所以"翻译"几乎是字段改名，没有语义转换。

---

## 3. 集成架构

```svg
<svg viewBox="0 0 680 440" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <title>MCP integration architecture</title>
  <desc>LLM -> ToolSystem (merged defs) -> router -> MCPManager -> MCP servers over stdio.</desc>
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <text x="340" y="22" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">MCP 接入架构</text>

  <rect x="48" y="44" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="54" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="73" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">接入层</text>
  <text x="174" y="66" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">LLM · Chat Completions + Tool Calling</text>
  <text x="174" y="84" dominant-baseline="central" font-size="12" fill="#185FA5">tool_calls 驱动 Agent Loop</text>

  <line x1="340" y1="102" x2="340" y2="116" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar)"/>

  <rect x="48" y="116" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="126" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="145" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">工具层</text>
  <text x="174" y="138" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">ToolSystem.definitions</text>
  <text x="174" y="156" dominant-baseline="central" font-size="12" fill="#185FA5">builtin ∪ mcp__server__tool（命名空间合并）</text>

  <line x1="340" y1="174" x2="340" y2="188" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar)"/>

  <rect x="48" y="188" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="198" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="217" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">路由层</text>
  <text x="174" y="210" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">execute() 分发</text>
  <text x="174" y="228" dominant-baseline="central" font-size="12" fill="#185FA5">前缀 mcp__ → MCPManager；否则内置 handler</text>

  <line x1="340" y1="246" x2="340" y2="260" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar)"/>

  <rect x="48" y="260" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="270" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="289" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">管理层</text>
  <text x="174" y="282" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">MCPManager</text>
  <text x="174" y="300" dominant-baseline="central" font-size="12" fill="#185FA5">多 Server 连接注册表 + tools/call 路由</text>

  <line x1="340" y1="318" x2="340" y2="332" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar)"/>

  <rect x="48" y="332" width="584" height="58" rx="12" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <rect x="60" y="342" width="92" height="38" rx="8" fill="#D3D1C7" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="106" y="361" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#444441">传输层</text>
  <text x="174" y="354" dominant-baseline="central" font-size="14" font-weight="500" fill="#2C2C2A">MCP Servers（subprocess）</text>
  <text x="174" y="372" dominant-baseline="central" font-size="12" fill="#5F5E5A">stdio · 换行分隔 JSON-RPC 2.0</text>
</svg>
```

---

## 4. 一次 MCP 工具调用的数据流

```svg
<svg viewBox="0 0 680 492" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <title>MCP tool call dataflow</title>
  <desc>LLM tool_call -> ToolSystem routes by prefix -> MCPManager -> JSON-RPC tools/call -> server -> text back.</desc>
  <defs>
    <marker id="ar2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <text x="340" y="22" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">一次 MCP 工具调用的数据流</text>

  <rect x="130" y="40" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="64" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">1 · LLM 返回 tool_call</text>
  <text x="340" y="84" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">mcp__fs__read_file</text>

  <line x1="340" y1="104" x2="340" y2="132" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="132" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="156" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">2 · 识别命名空间</text>
  <text x="340" y="176" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">ToolSystem.execute 检测到 mcp__ 前缀</text>

  <line x1="340" y1="196" x2="340" y2="224" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="224" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="248" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">3 · 转发 Manager</text>
  <text x="340" y="268" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">MCPManager 解析 server=fs，找到连接</text>

  <line x1="340" y1="288" x2="340" y2="316" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="316" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="340" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">4 · JSON-RPC 调用</text>
  <text x="340" y="360" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">tools/call（name, arguments）→ Server 子进程</text>

  <line x1="340" y1="380" x2="340" y2="408" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="408" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="432" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">5 · 回收并喂回</text>
  <text x="340" y="452" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">content[] 转文本 → 加入历史 → 喂回 LLM</text>
</svg>
```

---

## 5. 设计要点

1. **工具命名空间**：每个 MCP 工具在喂给 LLM 时改名 `mcp__<server>__<tool>`，避免与内置工具或其它 Server 重名；`tools/call` 时再拆回 `server` + `tool`。
2. **定义合并**：`ToolSystem.definitions = 内置定义 + MCPManager.tool_definitions()`。LLM 完全无感这是 MCP 还是内置。
3. **路由分发**：`execute(name, args)` 里，名字以 `mcp__` 开头 → 转发 `MCPManager.call()`；否则走原 handler。
4. **连接管理**：`MCPManager` 持有多个 `MCPConnection`（每个 Server 一个子进程），负责握手、`tools/list` 聚合、`tools/call` 路由、关闭清理。
5. **同步最小实现**：现有项目是纯同步的。官方 `mcp` SDK 是异步（anyio），为契合"代码极简、理解原理"，**先用同步自研客户端**走通协议；后续可选换成官方 SDK。

---

## 6. 代码骨架

### 6.1 新模块 `mini_code_assistant/mcp.py`

```python
import json, os, subprocess, threading, queue, time, logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)
PROTOCOL_VERSION = "2025-06-18"

@dataclass
class MCPServerConfig:
    name: str
    command: str
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)

def _mcp_tool_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"

def _parse_mcp_tool_name(full: str):
    if not full.startswith("mcp__"):
        return None
    _, server, tool = full.split("__", 2)
    return server, tool


class MCPConnection:
    """单个 MCP Server 的 stdio 连接（同步、最小实现）。"""

    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self.proc = subprocess.Popen(
            [cfg.command, *cfg.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            env={**os.environ, **cfg.env},
            text=True, bufsize=1,
        )
        self._send_q: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 1
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._initialize()
        self.tools = self._list_tools()

    # ── 底层收发 ──────────────────────────────
    def _send(self, method, params=None, notification=False):
        with self._lock:
            msg_id = None if notification else self._next_id
            if msg_id is not None:
                self._next_id += 1
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            if msg_id is not None:
                msg["id"] = msg_id
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return msg_id

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and "method" not in msg:   # response
                self._send_q.put(msg)
            else:                                     # notification
                logger.info(f"[mcp:{self.cfg.name}] notification: {msg.get('method')}")

    def _request(self, method, params=None, timeout=30):
        msg_id = self._send(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self._send_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")
        raise TimeoutError(f"MCP request timeout: {method}")

    # ── 生命周期 ──────────────────────────────
    def _initialize(self):
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "mini-code-assistant", "version": "0.2.0"},
        })
        self._send("notifications/initialized", notification=True)

    def _list_tools(self):
        return self._request("tools/list").get("tools", [])

    def call_tool(self, name, arguments):
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


class MCPManager:
    """管理多个 MCP Server 连接，并对外提供统一接口。"""

    def __init__(self, configs):
        self.conns = {}
        for cfg in configs:
            try:
                self.conns[cfg.name] = MCPConnection(cfg)
                logger.info(f"[mcp] connected: {cfg.name}")
            except Exception as e:
                logger.error(f"[mcp] failed to start {cfg.name}: {e}")

    def tool_definitions(self):
        defs = []
        for server, conn in self.conns.items():
            for tool in conn.tools:
                defs.append({
                    "type": "function",
                    "function": {
                        "name": _mcp_tool_name(server, tool["name"]),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                })
        return defs

    def call(self, full_name, arguments):
        parsed = _parse_mcp_tool_name(full_name)
        if not parsed:
            return f"Error: not an MCP tool: {full_name}"
        server, tool = parsed
        conn = self.conns.get(server)
        if not conn:
            return f"Error: MCP server not connected: {server}"
        try:
            result = conn.call_tool(tool, arguments)
        except Exception as e:
            return f"Error calling MCP tool: {e}"
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            else:
                parts.append(f"[{item.get('type')} content omitted]")
        text = "\n".join(parts)
        if result.get("isError"):
            text = "Tool error: " + text
        return text

    def list_servers(self):
        return {name: [t["name"] for t in c.tools] for name, c in self.conns.items()}

    def close_all(self):
        for conn in self.conns.values():
            conn.close()
```

### 6.2 改造 `tools.py`

```python
class ToolSystem:
    def __init__(self, working_dir: str, mcp_manager=None):
        self.working_dir = Path(working_dir).resolve()
        self.mcp_manager = mcp_manager
        self.definitions = self._build_definitions()
        # 合并 MCP 工具定义
        if self.mcp_manager:
            self.definitions += self.mcp_manager.tool_definitions()

    def execute(self, tool_name: str, arguments: dict) -> str:
        # MCP 工具优先路由
        if self.mcp_manager and tool_name.startswith("mcp__"):
            return self.mcp_manager.call(tool_name, arguments)
        handlers = { ... }   # 原内置 handler 不变
        ...
```

### 6.3 改造 `cli.py`

```python
# main() 中加载 MCP 配置并注入
def load_mcp_config() -> list:
    path = Path.home() / ".mca" / "mcp.json"
    if not path.exists():
        return []
    import json
    return [MCPServerConfig(**c) for c in json.loads(path.read_text(encoding="utf-8"))]

# 启动
mcp_manager = MCPManager(load_mcp_config())
tools = ToolSystem(working_dir, mcp_manager=mcp_manager)
...
# REPL 退出 / 程序结束时
mcp_manager.close_all()
```

新增 REPL 命令 `/mcp`（列出已连接的 Server 与其工具）：

```python
elif cmd == "/mcp":
    for server, tools_list in tools.mcp_manager.list_servers().items():
        print(f"  [MCP] {server}: {', '.join(tools_list)}")
    continue
```

---

## 7. 配置格式 `~/.mca/mcp.json`

```json
[
  {
    "name": "filesystem",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/allowed/dir"],
    "env": {}
  },
  {
    "name": "github",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-github"],
    "env": { "GITHUB_TOKEN": "ghp_xxx" }
  }
]
```

---

## 8. 风险与坑

| 风险 | 应对 |
|------|------|
| Server 启动失败 / 崩溃 | `MCPManager.__init__` 逐个 try/except，单个失败不影响其余工具 |
| 调用卡死 | 每个 `_request` 设超时（默认 30s），叠加现有 `MAX_ITERATIONS=20` |
| 协议版本不兼容 | 发送 `2025-06-18`，接受 Server 回相同或更低；不兼容则断开该 Server |
| 命名冲突 | `mcp__server__tool` 前缀天然去重 |
| **信任问题** | MCP Server 是第三方代码，应在沙箱里运行（见 `SANDBOX_DESIGN.md`）；`annotations` 不可信，危险操作仍需确认 |
| stdio 协议细节 | 换行分隔 JSON；Server 日志必须走 stderr，否则会污染 stdout 被当消息解析 |
| 输出交错 | 每个 Server 独立子进程，互不干扰 |

---

## 9. 分阶段路线图

| 阶段 | 内容 | 产出 |
|------|------|------|
| **v0.6.0** | `mcp.py`：`MCPConnection`（stdio + JSON-RPC + 握手）+ `MCPManager`（聚合路由） | 同步最小客户端走通协议 |
| **v0.6.1** | `tools.py` 合并定义 + `execute()` 路由；`cli.py` 接线 + `mcp.json` + `/mcp` | 端到端可用 |
| **v0.7.0** | 支持 `resources`（只读数据）与 `prompts`（提示词模板） | 能力补全 |
| 可选 | 用官方 `mcp` SDK（异步）替换自研同步客户端 | 生产化 |

> 与沙箱的关系：MCP Server 本质是"被拉起的第三方子进程"，正是 `SANDBOX_DESIGN.md` 里后端层要隔离的对象。建议 v0.6.x 跑通后，把 MCP Server 的启动塞进沙箱后端（如 Docker / Job Object 限制资源），让"工具总线"和"隔离"形成闭环。

---

## 10. 测试建议

1. **冒烟测试**：用官方 `mcp` SDK 写一个 `echo` Server，验证 `initialize → tools/list → tools/call` 全链路。
2. **单测**：mock 一个按固定 JSON 回应的 Server 脚本，断言命名空间转换、`isError` 回显、Server 崩溃时优雅降级。
3. **集成**：接一个真实 Server（如 filesystem），让 LLM 实际发起一次 MCP 工具调用，确认结果能正确喂回对话。

---

## 11. 版本与兼容性备注

- 本文基于稳定版 **2025-06-18**。
- 新版（如 `2025-11-25`、以及 2026 草案 `SEP-2575`）拟**移除 `initialize` 握手、改为无状态、每请求携带版本**（在 `_meta` 里）。若后续升级协议，本设计的 `MCPConnection._initialize()` 需相应调整；其余翻译/路由逻辑不受影响。
- HTTP 传输（`Streamable HTTP` / `SSE`）需在每个请求带 `MCP-Protocol-Version` 头，列为远程 Server 方向的后续工作。
