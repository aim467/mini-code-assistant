# Mini Code Assistant

一个迷你编程助手，用于学习 Codex / Claude Code / OpenCode 等工具的内部工作原理。

> **设计理念**：代码极简，聚焦核心原理。两个核心依赖（`openai` SDK + `rich`），可选依赖渐进增强。

---

## 架构

```
mini-code-assistant/
├── pyproject.toml              # 包配置 + console_scripts 入口
├── requirements.txt            # 依赖（openai + rich + 可选增强）
├── .env.example                # 配置模板
├── README.md
└── mini_code_assistant/        # Python 包
    ├── __init__.py             # 包标记 + __version__
    ├── __main__.py             # `python -m mini_code_assistant` 入口
    ├── cli.py                  # CLI 入口：argparse + REPL + Agent Loop
    ├── llm.py                  # LLM 客户端：OpenAI SDK, Chat Completions + Tool Calling + 流式输出
    ├── tools.py                # 工具系统：10 个工具（文件操作、搜索、Shell、glob）+ 安全机制
    ├── context.py              # 上下文管理：对话历史 + 智能摘要
    ├── token_counter.py        # Token 计数：tiktoken / 估算 + 上下文窗口映射
    └── diff.py                 # Diff 展示：difflib + ANSI 颜色 + pygments 语法高亮
```

### 核心概念

**1. Agent Loop（智能体循环）**

每个 AI 编程助手的核心——LLM 与工具之间的交互循环：

```
用户输入 → 发送给 LLM（流式）→ LLM 请求工具调用（如读取文件）
  → 执行工具 → 将结果反馈给 LLM → LLM 继续推理
  → 可能调用更多工具 → ... → LLM 给出最终响应
```

实现在 `cli.py` → `run_agent_loop()`。响应采用**流式输出**（SSE），你可以实时看到模型的生成过程，无需等待完整响应。

**2. Tool Calling（工具调用）**

LLM 无法直接操作文件系统。通过 OpenAI 的 Tool Calling 机制：
- 我们在 API 请求中声明 10 个可用工具（JSON Schema）
- LLM 决定调用某个工具，返回工具名 + 参数
- 我们执行工具，将结果作为 `role: "tool"` 消息发回
- LLM 根据结果继续推理

工具定义在 `tools.py` → `_build_definitions()`。

**3. Context management（上下文管理）**

LLM 是无状态的——每次 API 调用都是独立的。我们维护完整的对话历史，并在每次请求时发送。消息类型：
- `system`：系统提示词（定义助手行为）
- `user`：用户输入
- `assistant`：模型响应（可能包含 tool_calls）
- `tool`：工具执行结果

当对话接近模型上下文窗口的 ~80% 时，我们使用 LLM 对旧对话轮次进行**智能摘要**（或回退到裁剪），保留最近的对话轮次。Token 用量通过 SDK 的 `usage` 字段 / `tiktoken` / 字符估算来追踪，并通过 `/tokens` 实时显示。

实现在 `context.py` + `token_counter.py`。

**4. Safety（安全机制）**
- 路径穿越防护：所有文件操作限制在工作目录内
- 写入确认：任何文件修改前展示 diff
- 命令确认：`run_command` 执行前需用户确认
- 迭代上限（20 次）：防止无限工具调用循环

---

## 快速开始

### 方式 A：全局安装（推荐）

```bash
cd mini-code-assistant
pip install -e .          # 基础安装（openai + rich）
pip install -e ".[full]"  # 完整安装（增强 REPL、Token 计数、语法高亮）
```

这会在系统 PATH 中注册 `mca` 命令。安装后可在任意位置运行：

```bash
mca                          # 在当前目录运行
mca /path/to/project         # 在指定目录运行
mca --model deepseek-chat    # 覆盖模型
mca --api-key sk-xxx         # 覆盖 API Key
mca --temperature 0.0        # 覆盖采样温度
mca --version                # 显示版本
```

**原理**：`pyproject.toml` 声明了 `[project.scripts]` 入口点 `mca = "mini_code_assistant.cli:main"`。`pip install` 时，pip 会生成包装可执行文件：
- **Windows**：`mca.exe` 在 `Python\Scripts\` 目录下
- **Linux/macOS**：`mca` 脚本在 `~/.local/bin/` 或 `/usr/local/bin/` 目录下

两者在安装 Python 后通常已在系统 PATH 中。

### 方式 B：以模块运行（无需安装）

```bash
pip install -r requirements.txt
python -m mini_code_assistant [目录] [选项]
```

---

## 配置

配置优先级（从高到低）：

| 优先级 | 来源 | 作用域 |
|--------|------|--------|
| 1 | CLI 参数（`--model`、`--api-key`、`--base-url`、`--temperature`） | 单次调用 |
| 2 | Shell 环境变量（`export API_KEY=xxx`） | 当前会话 |
| 3 | 本地 `./.env` 文件 | 当前项目 |
| 4 | 全局 `~/.mca/.env` 文件 | 当前用户 |
| 5 | 内置默认值 | 兜底 |

### 设置

**全局配置**（一次性，全局生效）：

```bash
mkdir -p ~/.mca
cat > ~/.mca/.env << 'EOF'
API_KEY=your-api-key-here
BASE_URL=https://api.openai.com/v1
MODEL=gpt-4o
TEMPERATURE=0.3
EOF
```

**项目配置**（覆盖全局）：

```bash
cp .env.example .env
# 编辑 .env 填入项目特定设置
```

**支持的提供商**（任何兼容 OpenAI 的 API）：

| 提供商 | BASE_URL | MODEL 示例 |
|--------|----------|------------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 本地 Ollama | `http://localhost:11434/v1` | `llama3` |

---

## 使用

```
>>> read main.py and find issues
>>> add a logging function to utils.py
>>> search for all uses of requests
```

### REPL 内命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/exit` | 退出 |
| `/clear` | 清除对话历史 |
| `/files` | 列出工作目录文件 |
| `/tokens` | 显示 Token 用量 |

### REPL 快捷键

安装 `prompt_toolkit`（`pip install -e ".[full]"`）后，REPL 支持以下增强功能：

| 快捷键 | 说明 |
|--------|------|
| `↑` / `↓` | 浏览命令历史 |
| `Tab` | 自动补全 `/` 命令 |
| `Ctrl+R` | 搜索命令历史 |

未安装时自动降级为基础 `input()` REPL，所有功能正常使用。

### CLI 选项

```
mca [目录] [选项]

位置参数：
  目录                   工作目录（默认：当前目录）

选项：
  -m, --model MODEL      覆盖模型名称
  -k, --api-key KEY      覆盖 API Key
  -u, --base-url URL     覆盖 API 基础 URL
  -t, --temperature T    覆盖采样温度（默认：0.3）
  -V, --version          显示版本并退出
```

---

## 可用工具

| 工具 | 说明 |
|------|------|
| `list_files` | 列出目录内容 |
| `read_file` | 读取文件内容 |
| `write_file` | 写入文件（创建或覆盖） |
| `edit_file` | 编辑文件（文本替换） |
| `search_files` | 跨文件搜索文本 |
| `run_command` | 执行 Shell 命令（需确认） |
| `glob` | 按 glob 模式查找文件 |
| `create_directory` | 创建目录（含父目录） |
| `delete_file` | 删除文件（需确认） |
| `move_file` | 移动或重命名文件（需确认） |

---

## 与真实工具的对比

| 特性 | 本项目 | Claude Code / Codex |
|------|--------|---------------------|
| Agent Loop | 基础 | 高级（并行、子任务） |
| Tool Calling | 10 个工具 | 更多工具（终端、搜索等） |
| 上下文管理 | Token 追踪 + 智能摘要 | 更智能（Token 管理、摘要） |
| Diff 展示 | 统一 diff + pygments 语法高亮 | 更丰富（语法高亮） |
| REPL | 增强（历史、补全、搜索） | 完整终端集成 |
| 流式输出 | 支持（SSE） | 支持 |
| 多文件编辑 | 不支持 | 支持 |
| Git 集成 | 不支持 | 支持 |
| 代码执行 | 支持（需确认） | 支持 |

---

## 可选依赖

本项目采用渐进增强设计——核心功能依赖 `openai` + `rich`；可选依赖解锁额外功能：

| 依赖 | 功能 | 安装方式 |
|------|------|----------|
| `prompt_toolkit` | 增强交互式 REPL（命令历史、Tab 补全、Ctrl+R 搜索） | `pip install prompt_toolkit` |
| `tiktoken` | 精确 Token 计数（OpenAI 模型） | `pip install tiktoken` |
| `pygments` | Diff 语法高亮 | `pip install pygments` |

一键安装全部可选依赖：

```bash
pip install -e ".[full]"
# 或
pip install -r requirements.txt
```

---

## 扩展思路

1. **多文件编辑**：支持一次调用中编辑多个文件
2. **Git 集成**：支持查看 diff、提交等操作
3. **并行工具调用**：支持 LLM 一次请求多个工具调用并行执行
4. **迭代预算**：基于剩余 Token 更智能地分配工具循环预算
5. **测试验证**：编辑后自动运行测试 / Linter（`run_command` 已部分覆盖）