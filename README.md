# Mini Code Assistant

A mini coding assistant for learning how tools like Codex / Claude Code / OpenCode work internally.

> **Design philosophy**: minimal code, focus on core principles. Under 500 lines, single core dependency (`requests`), optional dependencies for progressive enhancement.

---

## Architecture

```
mini-code-assistant/
├── pyproject.toml              # Package config + console_scripts entry point
├── requirements.txt            # Dependencies (requests + optional enhancements)
├── .env.example                # Config template
├── main.py                     # Backward-compat wrapper (optional)
├── README.md
└── mini_code_assistant/        # Python package
    ├── __init__.py             # Package marker + __version__
    ├── __main__.py             # `python -m mini_code_assistant` entry
    ├── cli.py                  # CLI entry: argparse + REPL + Agent Loop
    ├── llm.py                  # LLM client: Chat Completions API + Tool Calling
    ├── tools.py                # Tool system: file read/write/edit/search + safety
    ├── context.py              # Context manager: conversation history + system prompt
    └── diff.py                 # Diff display: difflib + ANSI colors
```

### Core concepts

**1. Agent Loop**

The heart of every AI coding assistant — the interaction cycle between LLM and tools:

```
user input → send to LLM → LLM requests tool call (e.g. read file)
  → execute tool → feed result back to LLM → LLM continues reasoning
  → may call more tools → ... → LLM gives final response
```

Implemented in `cli.py` → `run_agent_loop()`.

**2. Tool Calling**

The LLM can't touch the filesystem directly. Via OpenAI's Tool Calling mechanism:
- We declare available tools (JSON Schema) in the API request
- The LLM decides to call a tool, returns tool name + arguments
- We execute the tool, send the result back as a `role: "tool"` message
- The LLM continues reasoning based on the result

Tool definitions are in `tools.py` → `_build_definitions()`.

**3. Context management**

The LLM is stateless — each API call is independent. We maintain the full conversation history and send it with every request. Message types:
- `system`: system prompt (defines assistant behavior)
- `user`: user input
- `assistant`: model response (may contain tool_calls)
- `tool`: tool execution result

Implemented in `context.py`.

**4. Safety**
- Path traversal protection: all file ops restricted to working directory
- Write confirmation: diff shown before any file modification
- Iteration cap: prevents infinite tool-calling loops

---

## Quick start

### Option A: Global install (recommended)

```bash
cd mini-code-assistant
pip install -e .          # basic install
pip install -e ".[full]"  # full install (enhanced REPL, token counting, syntax highlighting)
```

This registers the `mca` command on your system PATH. After install, run from anywhere:

```bash
mca                          # run in current directory
mca /path/to/project         # run in a specific directory
mca --model deepseek-chat    # override model
mca --api-key sk-xxx         # override API key
mca --version                # show version
```

**How it works**: `pyproject.toml` declares a `[project.scripts]` entry point `mca = "mini_code_assistant.cli:main"`. When you `pip install`, pip generates a wrapper executable:
- **Windows**: `mca.exe` in `Python\Scripts\`
- **Linux/macOS**: `mca` script in `~/.local/bin/` or `/usr/local/bin/`

Both are already on your system PATH if Python is installed.

### Option B: Run as module (no install)

```bash
pip install -r requirements.txt
python -m mini_code_assistant [directory] [options]
```

### Option C: Direct run (backward compat)

```bash
python main.py [directory]
```

---

## Configuration

Config priority (highest to lowest):

| Priority | Source | Scope |
|----------|--------|-------|
| 1 | CLI flags (`--model`, `--api-key`, `--base-url`) | per invocation |
| 2 | Shell environment variables (`export API_KEY=xxx`) | per session |
| 3 | Local `./.env` file | per project |
| 4 | Global `~/.mca/.env` file | per user |
| 5 | Built-in defaults | fallback |

### Setup

**Global config** (one-time, works everywhere):

```bash
mkdir -p ~/.mca
cat > ~/.mca/.env << 'EOF'
API_KEY=your-api-key-here
BASE_URL=https://api.openai.com/v1
MODEL=gpt-4o
EOF
```

**Per-project config** (overrides global):

```bash
cp .env.example .env
# edit .env with project-specific settings
```

**Supported providers** (any OpenAI-compatible API):

| Provider | BASE_URL | MODEL example |
|----------|----------|---------------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| Local Ollama | `http://localhost:11434/v1` | `llama3` |

---

## Usage

```
>>> read main.py and find issues
>>> add a logging function to utils.py
>>> search for all uses of requests
```

### In-REPL commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/exit` | Quit |
| `/clear` | Clear conversation history |
| `/files` | List files in working directory |
| `/tokens` | Show token usage |

### REPL shortcuts

With `prompt_toolkit` installed (`pip install -e ".[full]"`), the REPL supports:

| Shortcut | Description |
|----------|-------------|
| `↑` / `↓` | Navigate command history |
| `Tab` | Auto-complete `/` commands |
| `Ctrl+R` | Search command history |

Without `prompt_toolkit`, the REPL gracefully falls back to basic `input()` — all features work normally.

### CLI options

```
mca [directory] [options]

positional:
  directory              working directory (default: current dir)

options:
  -m, --model MODEL      override model name
  -k, --api-key KEY      override API key
  -u, --base-url URL     override API base URL
  -V, --version          show version and exit
```

---

## Available tools

| Tool | Description |
|------|-------------|
| `list_files` | List directory contents |
| `read_file` | Read file content |
| `write_file` | Write file (create or overwrite) |
| `edit_file` | Edit file (text replacement) |
| `search_files` | Search text across files |

---

## Comparison with real tools

| Feature | This project | Claude Code / Codex |
|---------|-------------|---------------------|
| Agent Loop | basic | advanced (parallel, subtasks) |
| Tool Calling | 5 tools | more tools (terminal, search, etc.) |
| Context management | full history | smarter (token management, summaries) |
| Diff display | basic | richer (syntax highlighting) |
| REPL | enhanced (history, completion, search) | full terminal integration |
| Streaming | no | yes |
| Multi-file edit | no | yes |
| Git integration | no | yes |
| Code execution | no | yes |

---

## Optional dependencies

This project uses progressive enhancement — core functionality depends only on `requests`, optional dependencies unlock extra features:

| Dependency | Feature | Install |
|------------|---------|---------|
| `rich` | Markdown rendering + styled output | `pip install rich` |
| `prompt_toolkit` | Enhanced interactive REPL (command history, Tab completion, Ctrl+R search) | `pip install prompt_toolkit` |
| `tiktoken` | Accurate token counting (OpenAI models) | `pip install tiktoken` |
| `pygments` | Diff syntax highlighting | `pip install pygments` |

Install all optional dependencies at once:

```bash
pip install -e ".[full]"
# or
pip install -r requirements.txt
```

---

## Extension ideas

1. **Streaming**: use SSE for real-time LLM response display
2. **Code execution tool**: add a `run_command` tool for shell commands
3. **Multi-file editing**: support editing multiple files in one call
4. **Git integration**: support viewing diffs, commits, etc.
5. **Parallel tool calls**: support LLM requesting multiple tool calls in parallel
