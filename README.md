# CodeHarness

> **Agent = LLM + Harness**。LLM 负责决策，Harness 负责工程。

一个本地 CLI 编程智能体（Coding Agent）框架，以**反馈闭环（Feedback Loop）**为核心深度实现。CodeHarness 驱动 LLM 在你的本地文件系统和终端上执行编码任务——读写文件、运行命令、执行测试——并对每次操作进行护栏审查、失败分类和策略化自我修正。所有核心机制（工具分发、治理护栏、反馈分类、循环控制）均为**确定性代码实现**，可在 Mock LLM 下用单元测试完全验证，无需网络、无需真实 LLM。

---

## 项目概览

| 模块 | 职责 |
|------|------|
| **Agent Loop** | 主循环：组装上下文 → 调用 LLM → 解析动作 → 护栏审查 → 执行工具 → 收集反馈 → 决定停止/重试/升级 |
| **LLM Backend** | 双实现：`DeepSeekBackend`（OpenAI SDK 兼容）用于生产；`MockBackend`（脚本化响应）用于测试 |
| **8 个内置工具** | `read_file`、`write_file`、`search_code`、`glob_files`、`run_shell`、`run_tests`、`git_op`、`package_op` |
| **Guard Engine** | 每次工具调用前进行确定性审查：危险命令正则匹配、路径边界检查、风险等级映射 → `ALLOW` / `ASK_ONCE` / `ASK_ALWAYS` |
| **Feedback Engine** ★ | **主力维度**：`FailureClassifier`（9 类失败识别）→ `StrategySelector`（分类修正策略）→ `LoopController`（停止/重试/升级 + regression 检测） |
| **Memory Store** | 跨会话记忆：项目约定 + 历史决策，Markdown 格式持久化 |
| **Config Store** | 三层配置合并：内置默认值 < `~/.coding-harness/config.toml` < `./harness.toml` |
| **Credential Store** | API Key 安全存储：操作系统密钥链（keyring）+ 环境变量兜底 |
| **REPL** | 基于 `rich` 的交互终端，显示 Agent 思考过程、护栏审批提示、彩色编码反馈 |

---

## 系统架构

```
用户输入 (task)
  │
  ▼
┌─────────────────────────────────────────────────────────┐
│                   AgentLoop (loop.py)                    │
│                                                          │
│  START → 组装上下文 → LLM 调用 → 解析响应 → 护栏审查     │
│     → 执行工具 → 反馈分类 → 策略选择 → 停止/重试/升级    │
│                                                          │
│  依赖注入 (Protocol):                                    │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐ ┌─────────┐    │
│  │ LLM  │ │Tools │ │Guard │ │Feedback│ │ Memory  │    │
│  └──┬───┘ └──┬───┘ └──┬───┘ └───┬────┘ └────┬────┘    │
└─────┼────────┼────────┼────────┼────────────┼──────────┘
      │        │        │        │            │
  ┌───▼──┐ ┌──▼──┐ ┌───▼──┐ ┌───▼────┐ ┌─────▼──────┐
  │DeepSeek│ │8 个 │ │正则  │ │分类器  │ │约定 + 决策 │
  │Backend│ │工具 │ │匹配  │ │策略选择│ │(Markdown)  │
  │Mock   │ │    │ │HITL │ │循环控制│ │            │
  └───────┘ └─────┘ └──────┘ └────────┘ └────────────┘
```

---

## 安装

### 环境要求

- **Python** ≥ 3.12
- **操作系统**：Windows / macOS / Linux
- **DeepSeek API Key**：[DeepSeek 开放平台](https://platform.deepseek.com/) 注册获取

### 本地开发安装

```bash
# 克隆仓库
git clone https://github.com/Klay-Heng/Codeharness.git
cd Codeharness

# 可编辑安装（含开发依赖）
pip install -e ".[dev]"
```

> **Windows 用户注意事项**：
>
> 1. **Python 权限问题**：如果 Python 装在系统目录（`C:\Python314\`），普通用户对 `Scripts` 子目录没有写入权限，pip 无法生成 `codeharness.exe` 入口点。解决方法：
>    - **方案 A（推荐）**：以管理员身份打开 PowerShell，重新运行 `pip install -e ".[dev]"`
>    - **方案 B**：使用用户级安装 `pip install -e ".[dev]" --user`，入口点安装到 `%APPDATA%\Python\Python314\Scripts\`
>    - **方案 C（根本解决）**：重装 Python 时选择 "Install for current user only"，避免所有系统目录权限问题
>
> 2. **`codeharness` 命令找不到**：`pip install` 将 `codeharness.exe` 生成在 Python 的 `Scripts` 目录下，该目录必须在系统 PATH 中。如果安装后仍无法识别：
>    ```powershell
>    # 检查 Scripts 是否在 PATH 中
>    python -c "import sys, os; s = os.path.join(sys.prefix, 'Scripts'); print(s in os.environ['PATH'])"
>    # 检查入口点文件是否存在
>    python -c "import sys, os; s = os.path.join(sys.prefix, 'Scripts'); [print(f) for f in os.listdir(s) if 'codeharness' in f.lower()]"
>    ```
>    - 如果 Scripts 不在 PATH：重启终端，或手动将 `C:\Python314\Scripts` 添加到系统 PATH
>    - 如果文件不存在：以管理员身份重装或使用 `--user` 安装
>    - 临时兜底：创建 `C:\Python314\Scripts\codeharness.bat`，内容为 `@echo off` 换行 `python -m codeharness.main %*`
>
> 3. **`make` 命令不可用**：Windows 不自带 `make`。直接使用等价命令：
>    ```powershell
>    pytest tests/ -v          # 替代 make test
>    ruff check src/ tests/    # 替代 make lint
>    pip install -e ".[dev]"   # 替代 make install
>    ```

---

## 快速开始

### 1. 配置 API Key

```bash
codeharness setup                    # 配置 API Key
python -m codeharness.main setup     # 备选方案
```

按提示粘贴你的 DeepSeek API Key（输入不可见，不会记录到任何日志或历史中）。

```bash
codeharness status                   # 查看配置状态（不显示明文 Key）
python -m codeharness.main status    # 备选方案
```

### 2. 启动 REPL

```bash
codeharness                  # 入口点命令（需 pip install 成功生成）
python -m codeharness.main   # 备选方案，不依赖 PATH，效果完全相同
```

### 3. 给 Agent 一个任务

```
> 你好，请用中文告诉我这个项目的结构和测试框架
```

Agent 会自动读取项目文件、执行搜索，然后用中文回答你的问题。你也可以给它编码任务：

```
> 请创建一个 hello.py 文件，包含一个 hello_world() 函数，返回 "Hello, World!"，
  然后写一个 pytest 测试文件 test_hello.py 并运行测试验证
```

Agent 会执行 **读取文件 → 编写代码 → 运行测试 → 根据失败修正** 的完整闭环。危险操作会触发护栏拦截，需要你确认后才能执行。

---

## API Key 安全

CodeHarness 遵循"凭据绝不落地"原则：

| 存储方式 | 说明 |
|----------|------|
| **OS 密钥链**（主方案） | Windows Credential Manager / macOS Keychain / Linux Secret Service，通过 `keyring` 库读写 |
| **环境变量**（兜底） | `DEEPSEEK_API_KEY` 环境变量，仅用于无法使用密钥链的场景（如 CI 容器），文档标注明文风险 |
| **绝不** | 明文出现在源码、git、日志、终端历史、配置文件中 |

- `codeharness status` 仅显示 `set` / `not_set`，**绝不回显明文 Key**
- `codeharness setup --reset` 替换已有 Key
- `codeharness setup --clear` 清除已存储的 Key

### 在目标机器上配置 Key

```bash
# 首次配置（推荐，Key 存入操作系统密钥链）
codeharness setup

# CI / 无密钥链环境
export DEEPSEEK_API_KEY="sk-your-key-here"
codeharness status    # 输出: API key: set
```

---

## 命令参考

| 命令 | 说明 |
|------|------|
| `codeharness setup` | 首次配置 API Key（隐藏输入） |
| `codeharness setup --reset` | 替换已有 API Key |
| `codeharness setup --clear` | 清除 API Key |
| `codeharness status` | 查看 Key 状态和当前模型/供应商 |
| `codeharness` | 启动交互式 REPL |
| `make install` | 可编辑安装 + 开发依赖 |
| `make test` | 运行全部单元测试 (`pytest tests/ -v`) |
| `pytest tests/ -v` | 同上（Windows 无 make 时直接用） |
| `make lint` | 运行 ruff 代码检查 (`ruff check src/ tests/`) |
| `make clean` | 清理构建产物 |
| `pytest tests/demo_mechanisms.py -v` | 运行机制演示（Mock LLM，无网络） |
| `python tests/demo_mechanisms.py` | 同上，以普通脚本运行 |

> **Windows 用户**：PowerShell 中没有 `make` 命令。可以直接用 `pytest tests/ -v`、`ruff check src/ tests/` 等等价命令。安装 Git Bash 后也可使用 `make`。

---

## 配置

配置从三个来源合并，优先级从低到高：

1. **内置默认值** — `src/codeharness/models.py` 中各 Config dataclass 的默认值
2. **用户级** — `~/.coding-harness/config.toml`
3. **项目级** — `./harness.toml`（最终覆盖）

参阅 [`harness.toml`](harness.toml) 了解完整注释示例。每个顶级节（`llm`、`feedback`、`guard`、`memory`、`tools`、`project`）映射到一个配置 dataclass。TOML 键名支持 kebab-case 和 snake_case。

**特殊规则**：`guard.extra_dangerous_patterns` 跨层级**追加**（项目级的额外规则不会覆盖用户级或内置规则）；其余列表字段为**替换**写入。

---

## 项目目录结构

```
Codeharness/
├── .github/workflows/ci.yml          # GitHub Actions CI 配置
├── .gitignore
├── AGENT_LOG.md                       # 实现决策日志
├── Makefile                           # make test / lint / clean
├── README.md                          # 本文件
├── REFLECTION.md                      # 反思报告
├── SPEC_PROCESS.md                    # 规约过程文档
├── harness.toml                       # 项目级配置示例
├── pyproject.toml                     # 包元数据与依赖
├── docs/superpowers/                  # SPEC 与 PLAN 文档
│   ├── specs/2026-07-08-codeharness-design.md
│   └── plans/2026-07-08-codeharness-plan.md
├── src/codeharness/                   # 源代码（harness 内核）
│   ├── __init__.py
│   ├── main.py                        # CLI 入口（typer）
│   ├── loop.py                        # Agent 主循环
│   ├── parser.py                      # LLM 响应解析
│   ├── guard.py                       # 治理护栏引擎
│   ├── feedback.py                    # 反馈引擎 ★（主力维度）
│   ├── memory.py                      # 跨会话记忆
│   ├── config.py                      # TOML 配置加载
│   ├── credentials.py                 # 凭据安全存储
│   ├── repl.py                        # 交互终端（rich）
│   ├── models.py                      # 数据模型、枚举、Protocol
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── deepseek.py                # DeepSeek 后端
│   │   └── mock.py                    # Mock 后端（测试用）
│   └── tools/
│       ├── __init__.py
│       ├── registry.py                # 工具注册与分发
│       ├── file_ops.py                # read_file / write_file
│       ├── search.py                  # search_code / glob_files
│       ├── shell.py                   # run_shell
│       ├── testing_tool.py            # run_tests (pytest)
│       ├── git_ops.py                 # git_op
│       └── package_ops.py             # package_op (pip)
└── tests/                             # 单元测试（259 项）
    ├── conftest.py
    ├── demo_mechanisms.py             # A.6 机制演示脚本
    ├── test_models.py
    ├── test_config.py
    ├── test_credentials.py
    ├── test_parser.py
    ├── test_memory.py
    ├── test_llm_mock.py
    ├── test_llm_deepseek.py
    ├── test_tools.py
    ├── test_guard.py
    ├── test_feedback.py
    ├── test_loop.py
    ├── test_repl.py
    └── test_main.py
```

---

## 分发

### PyPI 包（推荐）

```bash
# 本地可编辑安装
pip install -e ".[dev]"

# 构建分发包
python -m build

# 发布到 PyPI（需有 PyPI 账号）
twine upload dist/*
```

### 入口命令

安装后 `codeharness` 命令全局可用，由 `pyproject.toml` 中的 `[project.scripts]` 定义：

```toml
[project.scripts]
codeharness = "codeharness.main:app"
```

### CI/CD

GitHub Actions 配置位于 `.github/workflows/ci.yml`。每次 push 到 `main` 分支或发起 PR 时自动运行：

- `pytest tests/ -v` — 全部单元测试（含 Mock LLM 确定性测试）
- `python tests/demo_mechanisms.py` — 机制演示
- `ruff check src/ tests/` — 代码规范检查

CI 注入占位环境变量 `DEEPSEEK_API_KEY: sk-mock-for-ci`，绝不接触真实凭据。

---

## Mock LLM 确定性测试

本项目的核心设计原则：**每个机制移除 LLM 后仍能用单元测试验证**。

`MockBackend` 按顺序回放脚本化的 `LLMResponse` 列表，使 Agent Loop、Guard Engine、Feedback Engine 的行为完全确定性、可重复。全部 259 项测试不依赖网络或真实 LLM，可在离线环境运行。

```bash
make test           # 259 项测试，约 35 秒
pytest tests/ -v    # Windows 无 make 时直接用此命令
```

### 机制演示（SPEC A.6）

```bash
python tests/demo_mechanisms.py
```

三个确定性演示：
1. **护栏拦截** — `rm -rf /` 被判定为 `ASK_ALWAYS`，`read_file` 被放行为 `ALLOW`
2. **反馈驱动修正** — 注入一次失败 → Agent 收到反馈 → 改变行为修正代码
3. **分类器精度** — 精确解析 SyntaxError 的文件/行号，所有 9 类失败均有策略，连续 3 次同类错误触发升级

---

## 安全边界

- **护栏判定是代码实现的**，不依赖 LLM 遵从提示词。危险命令通过正则匹配确定性地拦截
- **项目目录外文件操作被路径边界检查拦截**：`write_file` 和 `run_shell` 的目标路径必须在项目根目录内
- **危险命令模式覆盖**：`rm -rf`（含所有变体）`sudo`、`chmod 777`、`> /dev/`、`git push --force`、`git reset --hard`、`mkfs`、fork bomb 等
- **`run_shell` 使用 `shell=True`**：护栏会审查命令，但不会沙箱隔离执行环境。请在信任的项目上运行本工具
- **首次运行不会自动联网**：未配置 API Key 时 REPL 拒绝启动

---

## 已知限制

| 限制 | 说明 |
|------|------|
| **分类器基于正则** | 失败分类针对 pytest/ruff/mypy 风格输出；其他测试框架或非常规 traceback 可能归类为 `UNKNOWN` |
| **仅支持 DeepSeek** | LLM 后端通过 OpenAI SDK 对接 DeepSeek API；其他供应商需新增 backend |
| **`run_shell` 使用 `shell=True`** | 护栏会审查命令但不会沙箱隔离，请在信任的项目上运行 |
| **记忆为纯 Markdown** | 无去重或语义索引，仅按文件名排序 |
| **依赖系统密钥链** | 无密钥链后端时，Key 回退到 `DEEPSEEK_API_KEY` 环境变量（明文，见安全一节） |
| **仅支持 Python 3.12+** | 使用了 `PEP 695` 等新版语法特性 |

---

## 技术选型

| 选型 | 选择 | 理由 |
|------|------|------|
| 语言 | Python 3.12+ | 开发效率高、生态成熟；`openai` SDK 一等支持 |
| LLM | DeepSeek (`deepseek-chat`) | 兼容 OpenAI SDK，接入成本低，编码能力强 |
| 凭据存储 | `keyring` | 跨平台密钥链抽象（Windows/macOS/Linux） |
| 终端 UI | `rich` | Python 最成熟的终端渲染库 |
| 配置格式 | TOML | Python 官方配置格式，可读性优于 JSON/YAML |
| 测试框架 | `pytest` + `pytest-asyncio` | 标准测试框架，JUnit XML 便于 CI 集成 |
| 架构模式 | Protocol + 依赖注入 | `typing.Protocol` 无需 ABC 继承，Mock 替换零成本 |
| 分发 | PyPI (`pyproject.toml`) | `pip install` 对 Python 开发者分发成本最低 |

---

## 许可证

MIT — 详见分发中包含的 LICENSE 文件。
