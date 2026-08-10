# SPEC_PROCESS.md — CodeHarness 规约与计划协作过程

> 记录与 Superpowers 协作生成 SPEC 和 PLAN 的完整过程，包含 brainstorming 关键节点、冷启动验证结果及修订决策。

---

## 一、Brainstorming 关键节点

### 1.1 智能体追问的关键问题

Brainstorming 阶段，智能体共追问了 **11 个逐层递进的问题**，每问一次只问一个，逐步收敛设计空间：

| # | 问题 | 我的选择 | 对设计的影响 |
|---|---|---|---|
| 1 | Harness 的使用场景？ | A. 本地 CLI 工具（类似简化版 Claude Code） | 确定了 REPL 交互模式、PyPI 分发 |
| 2 | 选择哪个维度作为主力贡献？ | B. 反馈闭环 | 决定了 FeedbackEngine 是整个项目的工程深度所在 |
| 3 | LLM 供应商？ | DeepSeek（兼容 OpenAI SDK） | 技术栈锁定 `openai` SDK + DeepSeek API |
| 4 | 凭据存储方案？ | A. Windows Credential Manager | 确定 `keyring` 库为主要方案 |
| 5 | 分发形态？ | B. PyPI 包 | `pip install codeharness` 一键安装 |
| 6 | Agent 工具范围？ | C. 扩展工具集（含 Git、包管理） | 8 个工具的完整矩阵 |
| 7 | 交互模式？ | A. REPL 交互式 | 持续会话，`rich` 库渲染 |
| 8 | 记忆范围？ | B. 项目约定 + 历史决策 | Markdown 双层存储 |
| 9 | 护栏粒度？ | B. 三级分级 + 会话授权记忆 | HIGH/MEDIUM/LOW + session 记忆 |
| 10 | 反馈闭环深度？ | B. 失败分类 + 差异化策略 | 9 类 FailureCategory + StrategySelector + LoopController |
| 11 | 配置格式？ | C. TOML + 用户级全局配置 | 双层覆盖优先级 |

### 1.2 让我修正原设想的关键追问

**第 9 问（护栏粒度）** 让我从最简的"拦截/放行"二元模式转向三级风险 + 会话记忆机制。原来的设想是所有危险操作逐次审批，但智能体提出的方案（中危首次审批后会话记住）更符合实际使用体验——既不会过度打扰用户，也不会放任危险操作。

**第 11 问（配置格式）** 从单层项目配置扩展到用户级 + 项目级双层。这使得同一个 `codeharness` 安装能自动适配不同项目的规则。

### 1.3 我推翻或修正的 AI 建议

- **架构方案选择**：AI 提出了 3 种（事件驱动循环、管道/阶段链、插件架构），推荐方案 1。我同意了。事后验证是正确的——每个模块独立可注入，完美契合 mock-LLM 单测要求。
- **Tool 协议的 `async execute`**：AI 初始实现用了 async，但 8 个工具全是同步操作（文件读写、subprocess.run）。我改为同步 `execute`，测试更简单可靠。
- **三个逐层叠加的"推回"机制**（read_only / silent-content / done-but-reads-only）：AI 在调试"Agent 只读不写"问题时逐步叠加了这三个推回检查，但每加一个都破坏了正常的读-改-写流程，最终形成死循环（读 → 被推回 → 再读 → escalate → interrupted）。我在 `d8a2c9e` 一次性删除了 118 行推回代码，回到干净的 system prompt + 护栏审批 + 反馈闭环核心流程。**教训**：行为修正应该在 system prompt 层做，而不是在循环控制层叠加条件。

---

## 二、关键迭代

### 迭代 1：设计章节逐节确认

逐节确认（共 7 个设计章节）让我有机会在每一节深入思考，避免了"全部写完后才发现理解偏差"。反馈闭环那一节（§3）花了最多时间确认。

### 迭代 2：DeepSeek tool_calls 参数名不匹配

```
问题：输入"修改 hello.py"后，Guard 拦截了 write_file。审批后文件仍未修改。
Debug 发现 DeepSeek 返回的参数名是 file 而非工具期望的 path。
根因：_format_tools 发送空 properties {}，DeepSeek 只能猜参数名。
修复：给每个工具描述加入明确的参数文档，同时让工具同时接受 path 和 file。
```

### 迭代 3：DeepSeek API tool_calls ID 不匹配导致 400 错误

```
问题：BadRequestError: "Messages with role 'tool' must be a response to a
preceding message with 'tool_calls'"
根因：DeepSeek 原始 tool_call.id 在 _decode_tool_calls 中被丢弃，
assistant/tool 消息的 tool_call_id 三者不一致。
修复：_decode_tool_calls 保留原始 id → parser 传到 Action.action_id →
loop 用 action.action_id 构建 assistant tool_calls。全程一致，涉及 4 个文件。
```

---

## 三、Brainstorming 技能的反思

### 做得好的

1. **逐问收敛**：一次一个问题，从粗到细，没有跳跃。
2. **方案对比**：提出 2-3 个可行方案并推荐一个，比直接"告诉我怎么做"更有参与感。
3. **分节确认**：每章不超过 300 字，审阅负担小。

### 不够好的

1. **SPEC 详略不均**：核心数据模型写得很细（12 个 dataclass 字段完整），但跨模块接口（LLMResponse.tool_calls 元素结构）只有文字描述没有类型定义。冷启动验证中恰恰是这类遗漏最让陌生 agent 困惑。
2. **SPEC 自审太表面**：只检查了 TBD/占位符和结构一致性，没发现接口类型定义缺失。
3. **没有在 SPEC 阶段显式声明平台假设**：SPEC 写"跨平台"，但没有明确 Windows 下 `make` 不可用时的等价命令。

---

## 四、冷启动自我验证（§4.5）

### 4.1 验证设置

- **第二 agent**：CodeBuddy（类型不同于主开发 agent Claude Code）
- **提供的文档**：仅 `SPEC.md` + `PLAN.md`，无会话历史或口头补充
- **任务**：从 PLAN 选 Task 1（脚手架）+ Task 2（数据模型）自主推进
- **约束**：要求"遇到不确定之处即暂停询问，而非凭猜测继续"

### 4.2 实验直接暴露的 SPEC 缺陷（局限于 Task 1+2 涉及的文件）

CodeBuddy 在**实际动手写代码**的过程中，遇到了以下 SPEC/PLAN 模糊点。这些都是 Task 1（pyproject.toml、Makefile、conftest.py）和 Task 2（models.py）直接涉及的。

**缺陷 1：TurnContext 字段定义缺失**

- 涉及文件：`src/codeharness/models.py`（Task 2）
- 现象：SPEC §6.1 把 `TurnContext` 列为"核心实体"，§3.1 的 `RunResult.final_context` 类型也是它。但 SPEC 没有给出 `TurnContext` 的任何一个字段。
- CodeBuddy 的推断：自行实现为累积容器（`messages` + `results` + `add_message`/`add_result`），因为要作为 `RunResult.final_context` 承载整段对话历史。
- 与主 agent 对比：主 agent 的实现（`messages`, `round_count`, `last_results`, `correction_history`）和推断方向一致，但多了 `round_count` 和 `correction_history` 字段。
- 结论：SPEC 对一个跨模块使用的核心实体只给了名字没给字段。"写下来了但没写完整"。

**缺陷 2：LLMResponse.tool_calls 元素结构缺失**

- 涉及文件：`src/codeharness/models.py`（Task 2），影响 Task 6（parser）和 Task 15（backend）
- 现象：SPEC §3.2 定义了 `LLMBackend.chat()` 接口，但 `LLMResponse.tool_calls` 中每个元素的内部结构没有定义。是 `{name, params}` 还是 OpenAI 风格的 `{function: {name, arguments}}`？
- CodeBuddy 的推断：按 PLAN MockBackend 代码推断为 `{name, params}` 格式，并判断 backend 层负责 OpenAI → 内部格式转换。
- 与主 agent 对比：推断完全正确。但后期集成中，tool_calls 元素的 `id` 字段缺失导致了一个实际 bug——assistant 消息和 tool 结果用了不同的 ID，API 报 400 错误。
- 结论：这是跨模块接口契约的缺失。parser/backend/loop 三方都依赖这个结构，SPEC 应当显式定义。

**缺陷 3：Config 嵌套结构 SPEC 未定义**

- 涉及文件：`src/codeharness/models.py`（Task 2）
- 现象：SPEC §3.7 描述了配置项的分类（LLM 参数、反馈参数等），但 §6.1 没有定义 Config 是扁平 dict 还是嵌套 dataclass。
- CodeBuddy 的推断：按 PLAN 中 `config.llm.model` 的写法推断为 `LLMConfig`/`GuardConfig` 等嵌套 dataclass。
- 与主 agent 对比：结构完全一致。
- 结论：SPEC 写意图，PLAN 给线索，实现者自己补结构。增加了推理负担但没有导致错误。

### 4.3 CodeBuddy 阅读完整 SPEC/PLAN 后的理解对比

以下发现不是 Task 1+2 实验直接暴露的（CodeBuddy 只实现了这两个 task），而是它**完整阅读 SPEC 和 PLAN 后**基于理解提出的与主 agent 实现的对比点。

**对比1：PLAN Demo 代码中的命名笔误**

- CodeBuddy 注意到：PLAN Task 16 的 Demo 3 代码使用了 `FeedbackCategory`，但 SPEC 只定义了 `FailureCategory`。
- 与主 agent 对比：主 agent 全程使用 `FailureCategory`，Demo 3 已统一。这是 PLAN 的 demo 草稿未经 SPEC 交叉审查的笔误。
- 可改进点：PLAN 中嵌入的示例代码应与 SPEC 做一次类型名一致性检查。

**对比2：护栏"默认拒绝未知操作"的精确范围**

- CodeBuddy 注意到：SPEC §3.4 和 §11 都写"默认拒绝未知操作类型"。它在未实现 guard 的情况下预判：若"未知操作类型"包括未命中任何规则的已知工具，`read_file` 这类 LOW 工具会被误拦。
- 与主 agent 对比：主 agent 的实现逻辑是：未知工具名 → ASK_ALWAYS，已知工具按 risk_level 映射（LOW→ALLOW），再加危险正则和路径边界覆盖。CodeBuddy 预判的"过度拦截"没有出现，但精确表述可以避免这种误读。
- 可改进点：SPEC 的"默认拒绝未知操作类型"应精确表述为"对未注册的工具名拒绝执行"。

### 4.4 实验结论

CodeBuddy 在 Task 1+2 上的代码产出与主 agent 高度一致——数据结构、配置拆分、Tool Protocol 设计都匹配。这说明 SPEC 在**数据模型层**的质量是合格的。

实验暴露的 3 个缺陷全部集中在同一个模式：**SPEC 给了名字和用途，但没给完整的字段/类型定义**（TurnContext 无字段、LLMResponse.tool_calls 无元素结构、Config 无嵌套拆分）。这是 brainstorming → SPEC 流程中最容易出现的遗漏——因为主 agent 和我在反复讨论中已经对"这些结构长什么样"达成了隐性共识，SPEC 只录入了名称和意图，漏掉了结构细节。

