# CodeHarness — SPEC 设计文档

> 一个本地 CLI Coding Agent Harness，聚焦反馈闭环的工程实现。
> Agent = LLM + Harness。LLM 做决策，Harness 做工程。

---

## 1. 问题陈述

### 1.1 要解决什么问题

现有的 Coding Agent 工具（Claude Code、Codex CLI 等）将主循环、工具分发、治理、反馈等机制封闭在框架内部，用户难以理解 agent 如何工作，也难以验证单个机制的可靠性。本项目构建一个**透明、可测试、聚焦反馈闭环**的 Coding Agent Harness，将每个机制以独立代码模块暴露出来。

### 1.2 目标用户

- 希望在本地终端中与 AI 协作编码的 Python 开发者。
- 希望理解 agent harness 内部机制的 AI4SE 学习者。
- 需要通过 mock LLM 验证 agent 行为确定性的工程师。

### 1.3 为什么值得做

当 LLM 能完成大部分编码工作时，工程师的价值落在 harness 层。本项目证明：核心机制（反馈、治理、工具分发）用代码实现而非提示词，可以独立于 LLM 用单元测试验证——这是可靠系统的基础。

---

## 2. 用户故事

### US-1: 交互式编码任务

> 作为 Python 开发者，我启动 `codeharness` 进入 REPL，输入"帮我给 `src/utils.py` 写单元测试"，agent 自动读取文件、编写测试、运行测试、根据失败修正，最终交付通过全部测试的代码。

**验收**：从任务输入到全部测试通过，agent 在一个 REPL 会话内闭环完成，无需人工中途介入（除非触发护栏）。

### US-2: 护栏阻止危险操作

> 作为用户，当 agent 尝试执行 `rm -rf /` 或写入项目目录外的文件时，系统拦截该操作并请求我确认。我可以拒绝，agent 跳过该操作继续执行。

**验收**：危险命令被确定性拦截（非依赖 LLM 自觉），用户可选择同意/拒绝/会话内记住。

### US-3: 首次配置凭据

> 作为新用户，首次运行 `codeharness setup` 时，系统引导我通过隐藏输入录入 DeepSeek API key，并安全存储到 Windows Credential Manager。之后所有 LLM 调用自动使用存储的 key，不会回显明文。

**验收**：`codeharness setup` → 隐藏输入 → 存储成功 → `codeharness status` 显示 "API key: configured"（不回显 key 内容）。

### US-4: 跨会话记忆

> 作为用户，我在一次会话中决定"使用 pytest 而非 unittest"，并将其记录为项目决策。下次启动新会话时，agent 会自动加载这个决策，不会再次提议使用 unittest。

**验收**：决策保存到 `.harness/memory/decisions/` → 新会话启动 → system prompt 中包含该决策。

### US-5: 项目配置定制

> 作为项目维护者，我在项目根目录放置 `harness.toml`，指定测试框架为 pytest、lint 工具为 ruff、危险命令额外规则。团队成员 clone 项目后，agent 自动按项目配置运行。

**验收**：agent 启动时加载项目级配置，行为与配置一致（如使用指定的 test command）。

### US-6: 修正失败时请求帮助

> 作为用户，当 agent 对同一个错误连续修正 3 次仍未通过，系统暂停并通知我："无法自动修复，请人工介入"，而不是无限循环。

**验收**：注入一个 agent 无法修复的失败 → 3 轮同类型失败后 → agent 停机请求人工介入。

### US-7: 查看和清除凭据

> 作为用户，我可以随时查看 API key 的配置状态（已配置/未配置），以及在不再使用时安全清除存储的 key。

**验收**：`codeharness status` 显示状态 → `codeharness setup --clear` 清除 key → `codeharness status` 显示未配置。

---

## 3. 功能规约

### 3.1 Agent Loop（主循环）

| 项 | 描述 |
|---|---|
| **输入** | 用户任务字符串 + 配置 + 记忆上下文 |
| **行为** | 组装上下文 → 调用 LLM → 解析响应为 Action 列表 → 每个 Action 过护栏 → 执行 → 收集反馈 → 停机判断（成功/失败/继续修正） |
| **输出** | `RunResult { status, rounds, duration_ms, final_context }` |
| **边界条件** | 空任务拒绝；单轮无 Action 视为 DONE；最大轮数 50（防死循环） |
| **错误处理** | LLM 调用失败重试 2 次（指数退避）；解析失败记录为无效轮次计入上限；工具执行异常捕获并回灌为反馈 |

### 3.2 LLM Backend

| 项 | 描述 |
|---|---|
| **接口** | `chat(messages, tools) -> LLMResponse` |
| **DeepSeekBackend** | 使用 `openai` SDK，base_url 指向 `https://api.deepseek.com`，model = `deepseek-chat`；支持 tool calling |
| **MockBackend** | 构造函数接收 `script: list[LLMResponse]`，按调用顺序逐次返回确定性响应；用于所有单元测试和机制演示 |
| **边界条件** | API key 未配置时在启动阶段报错（非运行时）；token 超限时截断历史消息保留 system + 最近 5 轮 |
| **错误处理** | 网络错误指数退避重试；429 速率限制等待 Retry-After；401 认证错误立即报错提示检查 key |

### 3.3 Tool Registry（工具系统）

注册 8 个工具，每个实现 `Tool` 协议（`name`, `description`, `risk_level`, `execute()`, `dry_run()`）：

| 工具 | 风险 | 功能 | 参数 |
|---|---|---|---|
| `read_file` | LOW | 读取文件内容 | `path`, `start_line?`, `end_line?` |
| `write_file` | MEDIUM | 创建或覆盖文件 | `path`, `content` |
| `search_code` | LOW | grep 搜索 | `pattern`, `path?`, `glob?` |
| `glob_files` | LOW | 文件模式匹配 | `pattern` |
| `run_shell` | MEDIUM | 执行 shell 命令 | `command`, `cwd?`, `timeout?` |
| `run_tests` | LOW | 运行 pytest | `path?`, `flags?` |
| `git_op` | VARIES | Git 操作 | `operation`, `args?` |
| `package_op` | HIGH | pip 操作 | `operation`, `package?` |

**工具分发**：`dispatch(action) -> ToolResult`，执行前统一过护栏 `guard.check(action)`。

**边界条件**：`write_file` 仅限项目根目录内；`run_shell` 默认超时 60s；`run_tests` 默认生成 JUnit XML 报告。

**错误处理**：工具不存在 → 返回错误 ToolResult；执行异常 → 捕获并返回 ToolResult(success=False)。

### 3.4 Guard Engine（治理护栏）

**三级风险 + 三种裁决**：

| 风险等级 | 裁决 | 行为 |
|---|---|---|
| LOW | ALLOW | 自动放行 |
| MEDIUM | ASK_ONCE | 首次询问用户，批准后会话内记住 |
| HIGH | ASK_ALWAYS | 每次询问用户 |

**护栏规则**（代码判定，非提示词）：

1. 危险命令模式匹配（正则）：`rm -rf`, `sudo`, `chmod 777`, `> /dev/`, `git push --force`, `git reset --hard`
2. 路径边界检查：`write_file` 和 `run_shell` 的目标路径必须在项目根目录内
3. 工具基础风险等级：见上表
4. 用户可通过 `harness.toml` 追加 `extra_dangerous_patterns`

**HITL 审批流程**：
```
Guard → ASK_ONCE/ASK_ALWAYS → REPL 显示 [y/n/session]
  y → 放行执行
  n → 跳过该 action, 记录到会话日志
  session → 仅 ASK_ONCE 有效, 后续同类自动放行
```

**边界条件**：非交互模式（将来）下 ASK_ALWAYS 默认拒绝；用户 30s 无响应 → 默认拒绝。

### 3.5 Feedback Engine ★（主力维度）

#### 信号收集

| 信号源 | 采集方式 | 确定性 |
|---|---|---|
| pytest | 解析 stdout + `--junitxml=` XML | ✅ 是 |
| ruff | 解析 stdout | ✅ 是 |
| mypy | 解析 stdout | ✅ 是 |
| 命令退出码 | `ToolResult.exit_code` | ✅ 是 |

#### 失败分类器（纯代码函数）

`classify(result: ExecutionResult) -> list[ClassifiedFailure]`

| 类别 | 识别规则 |
|---|---|
| `SYNTAX_ERROR` | 匹配 `SyntaxError:` 或 `IndentationError:` |
| `IMPORT_ERROR` | 匹配 `ModuleNotFoundError:` 或 `ImportError:` |
| `ASSERTION_FAILURE` | 匹配 `AssertionError:` 或 pytest assert 输出 |
| `RUNTIME_ERROR` | 匹配 `TypeError:`, `ValueError:`, `AttributeError:` 等 |
| `TIMEOUT` | `ToolResult.duration_ms >= timeout` |
| `LINT_WARNING` | ruff 输出行匹配 `:[0-9]+:[0-9]+: [A-Z]` |
| `TYPE_ERROR` | mypy 输出行匹配 `error:` |
| `COMMAND_FAILED` | `exit_code != 0` 且不匹配以上任何类型 |
| `UNKNOWN` | 兜底 |

#### 策略选择器（纯代码函数）

`select_strategy(failure: ClassifiedFailure) -> FeedbackStrategy`

每种失败类别对应一个策略，策略定义回灌给 LLM 的上下文模板：

| 类别 | 回灌内容 | 修正指引 |
|---|---|---|
| SYNTAX_ERROR | 文件+行号+错误信息+代码片段 | "仅修复语法，不改逻辑" |
| IMPORT_ERROR | 缺失模块名+当前依赖 | "检查 import 路径或添加依赖" |
| ASSERTION_FAILURE | 预期 vs 实际 diff+测试名 | "分析 diff 修改逻辑" |
| RUNTIME_ERROR | 完整 traceback | "定位根因，加防御检查" |
| TIMEOUT | 超时阈值+已用时间 | "检查死循环或添加缓存" |
| LINT_WARNING | 文件+行号+规则名 | "仅修复 lint 问题" |
| TYPE_ERROR | 类型不匹配详情 | "修正类型注解或使用" |

#### 循环控制器

`LoopController.decide(failures, history) -> LoopDecision`

**决策逻辑**（代码判定）：
1. 无失败 → `stop_success`
2. `round >= max_correction_rounds` → `stop_failure`
3. 连续同类错误 `>= max_same_error` → `escalate`（求助人工）
4. 检测到 regression（新文件或新类别失败）→ `escalate`
5. 其他 → `retry`（继续修正）

**Regression 检测**：对比本轮和上轮失败，出现新文件失败或新类别失败即判定为 regression。

#### 回灌格式

`FeedbackContext` dataclass 序列化为结构化文本注入 LLM dialogue：

```
[FEEDBACK] Round 2/5 | 1 failure(s)
  [ASSERTION_FAILURE] tests/test_utils.py:15 test_parse_empty
    expected: None
    actual: ValueError("empty input")
    Strategy: Analyze the diff between expected and actual values.
    Fix the logic so the function returns None for empty input.
```

### 3.6 Memory Store（记忆）

**两层模型**：

| 层 | 存储位置 | 生命周期 | 内容 |
|---|---|---|---|
| Session Context | 内存 | 单次会话 | 消息历史、action 记录、护栏审批状态、工具执行结果 |
| Persistent Store | `.harness/memory/` | 跨会话 | 项目约定(conventions/) + 历史决策(decisions/) |

**持久化格式**：Markdown 文件，人类和 LLM 都可读。

**注入策略**：会话启动时加载全部 conventions + 最近 10 条 decisions → 组装为 system prompt 的 "项目上下文" 段，不超过 2k tokens。

**接口**：`load_conventions()`, `load_recent_decisions(limit)`, `save_decision()`, `save_convention()`, `build_system_context()`

### 3.7 Config Store（配置）

**双层叠加**：内置默认值 < `~/.coding-harness/config.toml` < `./harness.toml`（项目级覆盖用户级）

**用户级配置项**：LLM 参数（provider/model/api_base/max_tokens/temperature）、反馈参数（max_correction_rounds/max_same_error/signal_sources）、护栏参数（extra_dangerous_patterns/session_approval）、记忆参数（max_decisions_loaded）、工具参数（disabled/shell_timeout）

**项目级配置项**：project（name/language/test_framework/lint_tool/type_checker）、guard（project_root/allowed_dirs）、feedback（test_command/lint_command）

**接口**：`ConfigLoader.load(project_dir?) -> Config`

### 3.8 Credential Store（凭据）

**方案**：Windows Credential Manager 优先（`keyring` 库），`.env` 仅作为 CI 测试兜底。

**流程**：
- **录入**：`codeharness setup` → `getpass.getpass()` 隐藏输入 → `keyring.set_password()`
- **读取**：`CredentialStore.get_key()` → 仅用于初始化 LLM client，不缓存
- **清除**：`codeharness setup --clear` → `keyring.delete_password()`
- **状态**：`codeharness status` → 显示 "configured" / "not configured"，**绝不复显明文**

**威胁模型**：
- 攻击者获得机器本地访问权 → 可读取 Credential Manager（需用户登录会话），无法通过本项目代码获取
- 攻击者获得源码/仓库访问 → 无法获取 key（不在代码/git/config/log 中）
- 进程内存 dump → key 仅存在于 LLM client 初始化瞬间，不常驻
- `.env` 兜底 → 明文存储，CI 容器内使用，文档标注风险

### 3.9 REPL（交互界面）

**启动**：`codeharness` 在终端启动 REPL。

**输入**：单行或多行（`\` 续行或 `"""` 包裹）任务描述。

**输出**：
- 使用 `rich` 库渲染面板
- `thought` 内容默认折叠
- 护栏询问 `[y/n/session]` 阻塞等待用户输入
- 反馈结果颜色编码：🟢通过 / 🔴失败 / 🟡警告 / 🔵信息
- 每轮显示轮数和耗时

**退出**：`exit` / `quit` / Ctrl+C / Ctrl+D。退出时提示是否保存关键决策。

---

## 4. 非功能性需求

### 4.1 性能

- REPL 启动时间（含配置加载+凭据读取）：< 1s
- LLM 非流式：无额外要求（DeepSeek 响应时间即总延迟）
- 工具执行：`read_file` < 50ms（1MB 文件）；`search_code` < 2s（1000 文件项目）
- 记忆加载：< 200ms（100 条决策 + 20 条约定）

### 4.2 安全

- API key 绝不在源码/git/log/终端/shell history/配置文件中以明文出现
- 护栏判定为代码实现，不依赖 LLM 遵从
- 项目目录外的文件操作被路径边界检查拦截
- 危险命令模式匹配覆盖至少 6 种常见危险模式

### 4.3 可用性

- `codeharness setup` 一条命令完成首次配置
- `codeharness status` 显示全部状态信息
- 护栏询问文字清晰，说明操作、风险等级、选项
- 错误信息包含上下文（什么操作、为什么失败、建议动作）

### 4.4 可观测性

- 每轮循环记录 `round_id` + 时间戳 + action 列表 + 结果
- 工具执行记录 `tool_name` + 参数摘要 + 耗时 + 结果摘要
- 反馈记录 `round_id` + 失败列表 + 分类 + 策略 + 决策
- 日志级别：INFO（默认），DEBUG（`--verbose`）

---

## 5. 系统架构

### 5.1 组件图

```
┌────────────────────────────────────────────────────────┐
│                     REPL (repl.py)                      │
│              rich 渲染 | 输入处理 | 状态显示              │
└──────────────────────────┬─────────────────────────────┘
                           │ task: str
┌──────────────────────────▼─────────────────────────────┐
│                  Agent Loop (loop.py)                    │
│                                                          │
│  START → BuildContext → LLMCall → Parse → GuardCheck    │
│     → Execute → FeedbackCheck → Retry/Stop/Escalate     │
│                                                          │
│  依赖注入 (Protocol):                                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐ ┌─────────┐    │
│  │ LLM  │ │Tools │ │Guard │ │Feedback│ │ Memory  │    │
│  └──┬───┘ └──┬───┘ └──┬───┘ └───┬────┘ └────┬────┘    │
└─────┼────────┼────────┼────────┼────────────┼──────────┘
      │        │        │        │            │
  ┌───▼──┐ ┌──▼──┐ ┌───▼──┐ ┌───▼────┐ ┌─────▼──────┐
  │DeepSeek│ │8个  │ │Rule  │ │Classifier│ │Conventions │
  │Backend│ │Tools│ │Match │ │Strategy  │ │+ Decisions │
  │Mock   │ │     │ │HITL  │ │Controller│ │(Markdown)  │
  └───────┘ └─────┘ └──────┘ └─────────┘ └────────────┘

  ┌──────────┐  ┌────────────┐
  │  Config   │  │ Credential  │
  │  Loader   │  │  Store      │
  │  (TOML)   │  │  (keyring)  │
  └──────────┘  └────────────┘
```

### 5.2 数据流

```
用户输入 (task)
  │
  ▼
AgentLoop.run()
  │
  ├─(1)─► ConfigLoader.load() ─► Config
  ├─(2)─► MemoryStore.build_system_context() ─► conventions + decisions
  ├─(3)─► 组装 messages = [system_prompt, context, user_task]
  │
  ▼
  while not done:
  │
  ├─(4)─► LLMBackend.chat(messages, tools) ─► LLMResponse
  ├─(5)─► Parser.parse(response) ─► list[Action]
  │
  ├─(6)─► for each action:
  │         GuardEngine.check(action) ─► ALLOW / ASK_ONCE / ASK_ALWAYS
  │         if ASK_* → REPL.request_approval() → y/n/session
  │         if allowed → ToolRegistry.dispatch(action) ─► ToolResult
  │
  ├─(7)─► FeedbackEngine.evaluate(results) ─► FeedbackContext
  │         ├─ ResultCollector.collect() ─► 汇总所有信号
  │         ├─ Classifier.classify() ─► list[ClassifiedFailure]
  │         ├─ StrategySelector.select() ─► list[FeedbackStrategy]
  │         └─ LoopController.decide() ─► LoopDecision
  │
  ├─(8)─► if stop_success → return RunResult
  │       if stop_failure → return RunResult
  │       if escalate → REPL.request_help() → 等待用户
  │       if retry → 回灌 FeedbackContext → goto (4)
  │
  ▼
RunResult
```

### 5.3 外部依赖

| 依赖 | 用途 | 版本 |
|---|---|---|
| `openai` | DeepSeek API 调用（兼容 OpenAI SDK） | >=1.0 |
| `keyring` | 跨平台凭据安全存储 | >=24.0 |
| `rich` | 终端 UI 渲染 | >=13.0 |
| `tomli` / `tomli-w` | TOML 配置解析与写入 | >=2.0 |
| `pytest` | 测试框架 | >=8.0 |
| `pytest-asyncio` | 异步测试支持 | >=0.24 |

---

## 6. 数据模型

### 6.1 核心实体

```
Message
  - role: "system" | "user" | "assistant" | "tool"
  - content: str
  - tool_call_id: str | None
  - timestamp: datetime

Action
  - action_id: str (UUID)
  - tool: str
  - params: dict[str, Any]
  - timestamp: datetime

ToolResult
  - action_id: str
  - success: bool
  - output: str
  - error: str | None
  - exit_code: int | None
  - duration_ms: int

ClassifiedFailure
  - category: FailureCategory (enum)
  - file: str | None
  - line: int | None
  - message: str
  - raw_output: str

FeedbackContext
  - round_id: int
  - failures: list[ClassifiedFailure]
  - strategies: list[FeedbackStrategy]
  - serialized: str  # 给 LLM 的文本

CorrectionRecord
  - round_id: int
  - action_ids: list[str]
  - failures_before: list[ClassifiedFailure]
  - failures_after: list[ClassifiedFailure]
  - decision: LoopDecision

Decision (记忆)
  - title: str
  - date: date
  - context: str
  - decision: str
  - reasons: list[str]
  - alternatives: list[str]

Convention (记忆)
  - name: str  # e.g. "coding-style", "naming"
  - content: str  # Markdown
  - updated_at: datetime

GuardVerdict (enum)
  - ALLOW
  - ASK_ONCE
  - ASK_ALWAYS

LoopDecision (enum)
  - action: "retry" | "stop_success" | "stop_failure" | "escalate"
  - reason: str
  - round_number: int

RunResult
  - status: "success" | "failure" | "max_rounds" | "interrupted"
  - rounds: int
  - duration_ms: int
  - final_context: TurnContext
```

### 6.2 文件系统布局

```
项目根目录/
├── harness.toml                 # 项目级配置（可选）
├── .harness/
│   ├── memory/
│   │   ├── conventions/
│   │   │   ├── coding-style.md
│   │   │   └── naming.md
│   │   └── decisions/
│   │       └── 2026-07-08-use-pytest.md
│   └── session_state.json      # 会话审批记忆
├── src/codeharness/            # 源代码
│   ├── __init__.py
│   ├── main.py                 # 入口 + CLI 命令
│   ├── loop.py                 # Agent 主循环
│   ├── parser.py               # LLM 响应解析
│   ├── guard.py                # 治理护栏
│   ├── feedback.py             # 反馈引擎 ★
│   ├── memory.py               # 记忆存储
│   ├── config.py               # 配置加载
│   ├── credentials.py          # 凭据管理
│   ├── repl.py                 # REPL 界面
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── protocol.py         # LLMBackend Protocol
│   │   ├── deepseek.py         # DeepSeek 实现
│   │   └── mock.py             # Mock 实现
│   └── tools/
│       ├── __init__.py
│       ├── registry.py         # 工具注册与分发
│       ├── file_ops.py         # read_file, write_file
│       ├── search.py           # search_code, glob_files
│       ├── shell.py            # run_shell
│       ├── testing.py          # run_tests
│       ├── git_ops.py          # git_op
│       └── package_ops.py      # package_op
└── tests/
    ├── test_loop.py
    ├── test_guard.py
    ├── test_feedback.py
    ├── test_memory.py
    ├── test_config.py
    ├── test_credentials.py
    ├── test_parser.py
    ├── test_tools.py
    └── demo_mechanisms.py      # A.6 机制演示脚本
```

---

## 7. 凭据与分发设计

### 7.1 凭据方案

- **主方案**：`keyring` → Windows Credential Manager（开发）/ macOS Keychain / Linux Secret Service
- **兜底**：`DEEPSEEK_API_KEY` 环境变量（仅 CI 测试），文档标注明文风险
- **录入**：`codeharness setup` → `getpass.getpass()` 隐藏输入
- **更新**：`codeharness setup --reset` → 覆盖旧 key
- **清除**：`codeharness setup --clear` → 删除
- **状态**：`codeharness status` → "API key: configured"（不回显明文）

### 7.2 分发方案

- **形态**：PyPI 包，`pip install codeharness`
- **入口**：`codeharness` CLI 命令
- **平台**：Python 3.12+，跨平台（Windows/macOS/Linux）
- **依赖**：`openai`, `keyring`, `rich`, `tomli`
- **已知限制**：Windows Credential Manager 需要用户登录会话；Linux Secret Service 需要 D-Bus

### 7.3 README 须包含

- `pip install codeharness` 安装命令
- `codeharness setup` 首次配置
- `codeharness` 启动 REPL
- API key 安全配置说明
- 已知限制

---

## 8. 技术选型与理由

| 选型 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.12+ | 开发效率高、生态成熟、AI4SE 课程通用语言；`openai` SDK 一等支持 |
| LLM 供应商 | DeepSeek | 用户提供 API key；兼容 OpenAI SDK 格式，接入成本低；coding 能力强 |
| LLM SDK | `openai` | DeepSeek 兼容 OpenAI API 格式，无需额外适配层 |
| 凭据存储 | `keyring` | 跨平台抽象（Windows/macOS/Linux），一行 API 读写凭据 |
| 终端 UI | `rich` | Python 最成熟的终端渲染库，面板/颜色/布局开箱即用 |
| 配置格式 | TOML | Python 官方配置格式（`pyproject.toml`），可读性好，`tomli` 轻量 |
| 测试 | `pytest` + `pytest-asyncio` | Python 标准测试框架，JUnit XML 报告方便反馈引擎解析 |
| 分发 | PyPI | `pip install` 一条命令安装，对 Python 开发者分发成本最低 |
| 架构 | Protocol + DI | `typing.Protocol` 无需 ABC 继承，mock 替换零成本，符合作业可测性要求 |

---

## 9. 验收标准

| # | 标准 | 验证方式 |
|---|---|---|
| 1 | `pip install codeharness` 成功，`codeharness` 命令可用 | 全新 venv 中执行 |
| 2 | `codeharness setup` 引导录入 key，`codeharness status` 显示状态无明文 | 手动 |
| 3 | 输入编码任务 → agent 执行多轮 → 测试通过或停机 | 集成测试 |
| 4 | 危险命令被护栏拦截，用户可 y/n/session 决定 | 演示脚本 |
| 5 | mock LLM 下护栏拦截可单测验证 | `test_guard.py` |
| 6 | mock LLM 下失败注入 → 反馈分类正确 → agent 收到反馈后改变行为 | `test_feedback.py` + `demo_mechanisms.py` |
| 7 | 同类型失败 3 轮不改 → escalate | `test_feedback.py` |
| 8 | 决策保存后新会话加载正确 | `test_memory.py` |
| 9 | 项目级配置覆盖用户级配置 | `test_config.py` |
| 10 | `make test` 一键运行全部测试并通过 | CI (GitHub Actions) |
| 11 | 全部核心机制 mock LLM 单测（不依赖网络） | 所有 `test_*.py` |

---

## 10. 领域与机制设计（A.5 额外章节）

### 10.1 领域的反馈信号

Coding 领域的客观反馈信号天然适合确定性判定：

- **测试框架输出**（pytest）：每条失败精确定位文件 + 行号 + 预期值 vs 实际值，是最高质量的反馈信号
- **Lint 工具输出**（ruff）：规则名 + 文件 + 行号，格式规范，解析简单
- **类型检查输出**（mypy）：类型不匹配精确到表达式，辅助 LLM 理解类型约束
- **命令退出码**：最粗糙但最确定的信号——成功或失败

### 10.2 领域的危险动作

- 破坏性文件操作（`rm -rf`、越界写入）
- 危险 Git 操作（`push --force`、`reset --hard`）
- 系统级包管理（`pip install` 可能引入恶意包）
- 任意 shell 命令的副作用（fork bomb、资源耗尽）

### 10.3 领域的工具需求

读写文件 + shell 执行 + git + 包管理 + 搜索，覆盖 coding agent 的核心动作空间。

### 10.4 领域的记忆需求

项目约定（编码风格、命名规范）确保行为一致；历史决策（技术选型、架构决策）避免 agent 反复提出已否决的方案。

### 10.5 主力维度：反馈闭环

选择反馈闭环作为主力维度，理由：

1. Coding 领域的反馈信号天然是确定性的——测试结果可精确解析、分类、回灌
2. 反馈闭环的每个子组件（分类器、策略选择器、循环控制器）都是纯代码函数，完美契合 §A.4-C 的 mock 单测判据
3. 失败分类 + 差异化策略 + regression 检测形成了一个有工程深度的子系统
4. 反馈闭环是 harness "闭环"二字的字面含义，是 agent 可靠性的直接保障

**编码实现要点**（呼应 §A.4）：
- `FailureClassifier.classify()` —— 纯函数，输入字符串输出分类，无需 LLM
- `StrategySelector.select()` —— 查找表 + 模板，确定性映射
- `LoopController.decide()` —— 状态机，输入历史输出决策
- 以上三者全部可用 mock 数据单测，不依赖网络或真实 LLM

---

## 11. 风险与未决问题

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| DeepSeek API 的 tool calling 格式与 OpenAI 有细微差异 | LLM 响应解析失败 | 抽象层隔离差异，提前用 MockBackend 验证解析器 |
| `keyring` 在部分 Linux 桌面环境不可用 | 凭据存储失败 | 降级到加密文件方案（session keyring），文档说明 |
| LLM 响应不稳定（同样输入不同输出） | 测试不可重复 | MockBackend 用于所有单元测试，真实 LLM 仅用于集成/验收 |
| 护栏规则不完善（漏拦截的危险命令） | 安全风险 | 正则白名单+黑名单组合，默认拒绝未知操作类型 |
| 反馈分类器对非标准输出格式误判 | 错误分类导致错误的修正策略 | 严格正则匹配，匹配不到归入 UNKNOWN，不做猜测 |
| deepseek-chat 上下文窗口有限（64K） | 长会话导致上下文截断 | 记忆系统分担上下文，超出时截断历史保留 system + 最近对话 |

---

## 附录 A：SPEC 自审清单

- [x] 无 "TBD"、"TODO" 占位符
- [x] 所有模块接口明确（输入/输出/边界条件/错误处理）
- [x] 架构图与数据流自洽
- [x] 6 个维度（决策/工具/记忆/治理/反馈/配置）均有覆盖
- [x] 主力维度（反馈闭环）有足够的深度细节
- [x] 凭据威胁模型和分析完整
- [x] 验收标准可客观判定
- [x] 风险列表覆盖主要不确定项

---

*文档版本: v1.0 | 日期: 2026-07-08 | 状态: 待用户审阅*
