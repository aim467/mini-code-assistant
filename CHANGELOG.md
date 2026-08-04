# Changelog

## v0.2.2 (2026-08-04) — 引入结构化日志

将散落在各处的诊断/状态 `print` 统一替换为标准 `logging` 模块（对应 ROADMAP B11）。

### 变更
- **新增 `setup_logging()`**（`cli.py`）：在 `main()` 启动时配置全局日志
  - 级别由环境变量 `MCA_LOG_LEVEL` 控制（默认 `INFO`，支持 `DEBUG`/`WARNING`/`ERROR`）
  - 输出到 **stderr**，避免污染 stdout 上的应用内容（流式回复、diff 等）
  - 结构化格式：`时间 [级别] 模块: 消息`
- **`tools.py` 诊断输出 → 日志**：`[WRITE]`/`[EDIT]`/`[MKDIR]`/`[MOVE]` 用 `logger.info`，
  `[DELETE]` 用 `logger.warning`，`run_command` 的 `[COMMAND]`/超时/工作目录改用 `logger.info`
- **`cli.py` 错误/警告 → 日志**：工作目录不存在、缺少 API_KEY、`LLM stream error`、
  `Max iterations reached`、`Task interrupted`、未捕获异常分别用 `logger.error`/`warning`/`exception`

### 说明（未替换的 print）
以下 `print`/`_console.print` **保留为应用界面输出**，不属于日志，不应走 logging：
- 启动横幅、帮助信息
- LLM 流式回复文本（核心产品输出）
- diff 渲染（`diff.py` 的 `show_diff`/`show_new_file`）
- 工具调用/结果展示面板、token 用量、REPL 提示符与 Bye/cleared 等用户反馈

---

## v0.2.1 (2026-08-04) — 代码卫生与加固

Phase 0 收尾：修复 4 个安全/稳定性问题，清理项目残留。

### 安全性
- **run_command 加固**：`shell=True` 改为 `shell=False` + `shlex` 参数解析，杜绝命令注入
  （`echo hi; rm -rf /` 现在只是 echo 的参数，不再被 shell 执行）。

### 稳定性 / 资源
- **search_files 流式读取**：逐行读取替代 `read_text()` 全量加载，并跳过 >50MB 文件，避免大文件 OOM。
- **read_file 分段读取**：截断阈值从 8000 提升到 32000 字符，新增 `offset` / `limit` 参数支持大文件分段读取。

### Bug 修复
- **_trim_if_needed 过度裁剪**：原实现依赖 SDK `usage` 缓存的 token 数做裁剪判断，导致删除消息后计数不下降、
  裁剪循环永不收敛（几乎删空对话）。改为使用本地 `count_tokens` 估算，确保裁剪收敛且 tool_call/tool 结果始终成对删除。

### 工程清理
- 删除临时调试脚本 `_probe_tools.py`、`_verify_p0.py`
- 恢复被测试代码污染的 `reasonix.toml` 为合法权限配置
- `.gitignore` 增加 `.env` 与 `*.pyc`（避免密钥泄露与编译缓存入库）
- 删除散落在包目录下的 `__init__.pyc`

---

## v0.2.0 (2026-07-29) — 功能增强
- 改用 OpenAI SDK（流式输出 SSE）
- 新增 Token 计数模块（tiktoken 精确 + 字符估算降级）
- 新增智能摘要替代粗暴裁剪（LLM 摘要 + 回退裁剪）
- Diff 支持 pygments 语法高亮（可选依赖）
- prompt_toolkit 增强 REPL（历史/补全/搜索，可选）
- temperature 可配置（CLI/环境变量，默认 0.3）

## v0.1.0 (2026-07-29) — 初始版本
- 基础 Agent Loop + 10 工具 + requests HTTP 通信
