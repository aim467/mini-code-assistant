# Mini Code Assistant · 沙箱机制设计文档

> 设计目标：为 `mini-code-assistant`（一个用于学习 Codex / Claude Code / OpenCode 内部原理的迷你编程助手）设计一套**可叠加、可单独开关**的沙箱机制，把"生成的代码直接跑在宿主上"升级为"经过策略 + 隔离原语再执行"。
>
> 适用版本路线：v0.3.0（本地沙箱）→ v0.4.0（资源限制 + diff 报告）→ v0.5.0（容器隔离）。

---

## 1. 现状与动机

当前 `tools.py` 中的 `_tool_run_command` 已经是"加固版"执行器：

```python
result = subprocess.run(
    args,                  # shlex 解析，shell=False → 防命令注入
    shell=False,
    capture_output=True,
    text=True,
    timeout=timeout,       # 超时防卡死
    cwd=str(self.working_dir),
)
```

它解决了**注入**和**防呆**（人工确认），但**没有真正的隔离**：

- 直接在真实工作目录里执行，能读写宿主机的全部文件；
- 默认拥有宿主机的完整网络访问；
- 没有 CPU / 内存 / 进程数上限，一条 `while True` 或内存炸弹就能拖垮机器；
- 执行后无法直观看到"这次命令到底改了哪些文件"。

这就是沙箱要补的核心缺口：**让 LLM 生成的、不可完全信任的代码，在一个受限环境里跑，炸了也不影响宿主。**

---

## 2. 设计原则

1. **分层、可独立开关** —— 每一层隔离都能单独启用/关闭，项目可从简到繁逐步加。
2. **策略声明式** —— "这次执行允许什么"用 `SandboxPolicy` 参数化，LLM 可不传（走默认）。
3. **后端可插拔** —— 同一套接口下有本地 / 容器 / 微虚机多种实现，按可信需求切换。
4. **默认最小权限** —— 默认**禁网 + 源目录只读**，只在沙箱副本里写。
5. **零依赖起步** —— 第一版后端不引入任何第三方依赖，任何机器都能跑。

---

## 3. 分层架构

```svg
<svg viewBox="0 0 680 440" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <title>Sandbox layered architecture</title>
  <desc>A five-layer sandbox: integration, policy, backend, isolation primitives, host OS.</desc>
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <text x="340" y="22" text-anchor="middle" font-size="14" font-weight="500" fill="#26215C">沙箱分层架构</text>

  <rect x="48" y="44" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="54" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="73" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">接入层</text>
  <text x="174" y="66" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">Agent Loop → run_command</text>
  <text x="174" y="84" dominant-baseline="central" font-size="12" fill="#185FA5">Sandbox.run(cmd, policy)</text>

  <line x1="340" y1="102" x2="340" y2="116" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>

  <rect x="48" y="116" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="126" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="145" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">策略层</text>
  <text x="174" y="138" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">SandboxPolicy（声明式）</text>
  <text x="174" y="156" dominant-baseline="central" font-size="12" fill="#185FA5">timeout · memory_mb · cpu_sec · network · read_only</text>

  <line x1="340" y1="174" x2="340" y2="188" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>

  <rect x="48" y="188" width="584" height="58" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="198" width="92" height="38" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="217" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">后端层</text>
  <text x="174" y="210" dominant-baseline="central" font-size="14" font-weight="500" fill="#042C53">SandboxBackend（可插拔）</text>
  <text x="174" y="228" dominant-baseline="central" font-size="12" fill="#185FA5">LocalSubprocess · Docker · WSL2 · Firecracker</text>

  <line x1="340" y1="246" x2="340" y2="260" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>

  <rect x="48" y="260" width="584" height="84" rx="12" fill="#E6F1FB" stroke="#185FA5" stroke-width="0.5"/>
  <rect x="60" y="272" width="92" height="60" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="106" y="302" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">隔离原语</text>
  <rect x="176" y="272" width="104" height="60" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="228" y="302" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">文件系统</text>
  <rect x="292" y="272" width="104" height="60" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="344" y="302" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">资源限制</text>
  <rect x="408" y="272" width="104" height="60" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="460" y="302" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">网络隔离</text>
  <rect x="524" y="272" width="104" height="60" rx="8" fill="#B5D4F4" stroke="#185FA5" stroke-width="0.5"/>
  <text x="576" y="302" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#0C447C">系统调用</text>

  <line x1="340" y1="344" x2="340" y2="358" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#arrow)"/>

  <rect x="48" y="358" width="584" height="58" rx="12" fill="#F1EFE8" stroke="#5F5E5A" stroke-width="0.5"/>
  <rect x="60" y="368" width="92" height="38" rx="8" fill="#D3D1C7" stroke="#5F5E5A" stroke-width="0.5"/>
  <text x="106" y="387" text-anchor="middle" dominant-baseline="central" font-size="13" font-weight="500" fill="#444441">宿主 OS</text>
  <text x="174" y="380" dominant-baseline="central" font-size="14" font-weight="500" fill="#2C2C2A">Host OS</text>
  <text x="174" y="398" dominant-baseline="central" font-size="12" fill="#5F5E5A">Windows / Linux · namespaces · cgroups · seccomp</text>
</svg>
```

### 逐层说明

1. **接入层（最薄）** —— 把 `_tool_run_command` 改成只负责"把命令交给沙箱"，自己不再直接 `subprocess.run`。所有执行逻辑收敛到唯一入口 `Sandbox.run(cmd, policy)`，将来换隔离强度只改这一处。
2. **策略层（声明式）** —— `SandboxPolicy` 把"这次执行允许什么"参数化：超时、内存上限、CPU 时间、是否联网、是否只读、允许访问的目录。LLM 调用时可以不传（走默认），也可以细化。
3. **后端层（可插拔）** —— `SandboxBackend` 抽象基类挂不同实现：
   - `LocalBackend`：仍是 subprocess，但加上资源限制 + 临时目录隔离（本机零依赖可跑）；
   - `DockerBackend`：丢进容器（只读 rootfs、禁网、drop capabilities、seccomp）；
   - `WSL2Backend`：Windows 上跑 Linux 工具的折中；
   - `FirecrackerBackend`：生产级 microVM（Codex 这类工具用的）。
4. **隔离原语（真正干活的四件事）** —— 文件系统隔离 / 资源限制 / 网络隔离 / 系统调用过滤。
5. **宿主层** —— Windows / Linux 内核提供的 namespaces、cgroups、seccomp，上面所有层最终都落在这里。

---

## 4. 执行生命周期

```svg
<svg viewBox="0 0 680 492" width="100%" xmlns="http://www.w3.org/2000/svg" role="img">
  <title>Sandbox execution lifecycle</title>
  <desc>Command enters, policy check, clone to temp sandbox, run with limits, reap and cleanup.</desc>
  <defs>
    <marker id="ar2" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </marker>
  </defs>

  <text x="340" y="22" text-anchor="middle" font-size="14" font-weight="500" fill="#04342C">一次执行的完整生命周期</text>

  <rect x="130" y="40" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="64" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">1 · 命令进入</text>
  <text x="340" y="84" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">run_command → Sandbox.run(cmd, policy)</text>

  <line x1="340" y1="104" x2="340" y2="132" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="132" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="156" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">2 · 策略校验</text>
  <text x="340" y="176" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">用户确认 + Policy（超时 / 内存 / 网络）</text>

  <line x1="340" y1="196" x2="340" y2="224" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="224" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="248" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">3 · 准备隔离环境</text>
  <text x="340" y="268" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">克隆工作目录到临时沙箱目录</text>

  <line x1="340" y1="288" x2="340" y2="316" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="316" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="340" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">4 · 执行 + 资源限制</text>
  <text x="340" y="360" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">subprocess + setrlimit / Job Object</text>

  <line x1="340" y1="380" x2="340" y2="408" stroke="#5F5E5A" stroke-width="1.5" marker-end="url(#ar2)"/>

  <rect x="130" y="408" width="420" height="64" rx="12" fill="#E1F5EE" stroke="#0F6E56" stroke-width="0.5"/>
  <text x="340" y="432" text-anchor="middle" dominant-baseline="central" font-size="14" font-weight="500" fill="#04342C">5 · 回收结果 + 清理</text>
  <text x="340" y="452" text-anchor="middle" dominant-baseline="central" font-size="12" fill="#0F6E56">捕获输出、对比差异、删除临时目录</text>
</svg>
```

---

## 5. 核心抽象

```python
# mini_code_assistant/sandbox.py
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import subprocess, tempfile, shutil, time

class NetworkMode(Enum):
    ALLOW = "allow"
    DENY = "deny"

@dataclass
class SandboxPolicy:
    timeout: int = 30
    memory_mb: int = 256
    cpu_sec: int = 10
    network: NetworkMode = NetworkMode.DENY   # 默认禁网
    read_only: bool = False                   # 默认对源目录只读
    allowed_paths: list = field(default_factory=list)

@dataclass
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool = False
    oom_killed: bool = False

class SandboxBackend:
    def run(self, command, cwd: Path, policy: SandboxPolicy) -> SandboxResult:
        raise NotImplementedError
```

---

## 6. 模块骨架：`LocalBackend`

零依赖后端：临时目录隔离 + 超时 + （Linux）资源限制。

```python
class LocalBackend(SandboxBackend):
    """零依赖后端：临时目录隔离 + 超时 + （Linux）资源限制。"""
    def run(self, command, cwd, policy):
        sandbox = Path(tempfile.mkdtemp(prefix="mca-sbx-"))
        shutil.copytree(cwd, sandbox / "work")      # 3. 克隆到沙箱
        work = sandbox / "work"
        start = time.time()
        try:
            proc = subprocess.run(
                command, shell=False, capture_output=True, text=True,
                cwd=work, timeout=policy.timeout,
                # Linux: preexec_fn=lambda: setrlimit(RLIMIT_AS, ...)
            )
            return SandboxResult(proc.stdout, proc.stderr, proc.returncode, time.time() - start)
        except subprocess.TimeoutExpired:
            return SandboxResult("", "timeout", -1, policy.timeout, timed_out=True)
        finally:
            # 5. 对比差异（read_only 时不应有改动）→ 清理临时目录
            shutil.rmtree(sandbox, ignore_errors=True)
```

### 接入点改造：`_tool_run_command`

把"只委托、不自己跑"的逻辑替换进 `tools.py`：

```python
def _tool_run_command(self, command: str, timeout: int = 30) -> str:
    from .sandbox import LocalBackend, SandboxPolicy
    policy = SandboxPolicy(timeout=max(5, min(timeout, 120)))
    if not self._confirm(f"Run in sandbox? {command}"):
        return "Command cancelled"
    r = LocalBackend().run(command, self.working_dir, policy)
    parts = []
    if r.stdout: parts.append(f"--- stdout ---\n{r.stdout.rstrip()}")
    if r.stderr: parts.append(f"--- stderr ---\n{r.stderr.rstrip()}")
    if r.timed_out: parts.append("⚠ timed out")
    if r.oom_killed: parts.append("⚠ killed (OOM)")
    if r.exit_code != 0: parts.append(f"Exit code: {r.exit_code}")
    out = "\n".join(parts) or "Command completed (no output)"
    return out[:8000] + ("\n...(truncated)" if len(out) > 8000 else "")
```

---

## 7. 各隔离层实现要点

| 隔离层 | Linux 实现 | Windows 实现 | 备注 |
|--------|-----------|--------------|------|
| 文件系统 | 临时副本 / `mount --bind` 只读 | `tempfile.mkdtemp` + `shutil.copytree` | 源目录默认只读，改动只落在副本 |
| 资源限制 | `setrlimit`(RLIMIT_AS/CPU) 或 cgroups | Job Object（`pywin32`） | Windows 无 `setrlimit` |
| 网络隔离 | `unshare -n` 无路由 / 容器 `--network=none` | 容器禁网最干净；宿主机无 `unshare` | 真·断网在 Windows 上靠 Docker/WSL2 |
| 系统调用 | seccomp-bpf 过滤危险 syscall | 容器 seccomp profile | 本地后端暂不强求 |

**关键设计取舍（真实工具也这么做）：**

- **默认禁网 + 默认只读**：只在沙箱副本里写，宿主源目录保平安。
- **执行后做 diff 报告**：让用户清楚"这次命令到底改了哪些文件"，比单纯确认更稳。
- **脚本 vs 非脚本**：生成命令常需要管道 / 重定向，本地后端为防注入默认 `shell=False + shlex`（与现有代码一致，会牺牲管道）；需要完整 shell 时建议放到 `DockerBackend` 里用受控 shell。

---

## 8. 落地路线图

| 阶段 | 做啥 | 收益 | 依赖 |
|------|------|------|------|
| **v0.3.0** | `LocalBackend` + 临时目录克隆 + 超时 + 输出截断 | 零依赖、任何机器能跑；源目录不再被直接改 | 无 |
| **v0.4.0** | 资源限制：Linux `setrlimit` / Windows Job Object；执行后 diff 报告 | 防死循环、内存爆炸、fork bomb | `pywin32`（Win） |
| **v0.5.0** | 可选 `DockerBackend`（只读 rootfs、禁网、`--network=none`、seccomp） | 真正的网络 / 系统调用隔离 | Docker |

> Windows 环境特别提示：内存 / CPU 限制需 Job Object；真·网络隔离在 Windows 上最干净的做法是 Docker / WSL2（没有 `unshare`）。因此 v0.5.0 的容器后端在 Windows 上性价比最高。

---

## 9. 下一步建议

1. 先实现 **v0.3.0** 的 `sandbox.py` + 改造后的 `tools.py`，跑通"克隆 → 执行 → 回收"闭环。
2. 补一份 `SandboxPolicy` 单元测试，验证每一层隔离是否真的生效（例如：断言沙箱内写文件不影响宿主源目录）。
3. 视学习进度推进 v0.4.0（资源限制）/ v0.5.0（容器）。
