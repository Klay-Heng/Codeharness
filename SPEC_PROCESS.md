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
| 9 | 护栏粒度？ | B. 三级分级 + 会话授权记忆 | HIGH(ASK_ALWAYS) / MEDIUM(ASK_ONCE) / LOW(ALLOW) |
| 10 | 反馈闭环深度？ | B. 失败分类 + 差异化策略 | 9 类 FailureCategory + StrategySelector + LoopController |
| 11 | 配置格式？ | C. TOML + 用户级全局配置 | `~/.coding-harness/config.toml` + `./harness.toml` |

### 1.2 让我修正原设想的关键追问

**第 9 问（护栏粒度）**让我从最简的"拦截/放行"二元模式转向三级风险 + 会话记忆机制。原来的设想是所有危险操作逐次审批，但智能体提出的 B 方案（中危首次审批后会话记住）更符合实际使用体验——既不会过度打扰用户，也不会放任危险操作。

**第 11 问（配置格式）**从单层项目配置扩展到用户级 + 项目级双层，项目覆盖用户默认值。这使得 `codeharness` 能在不同项目中自动适配不同规则。

### 1.3 我推翻/修正的 AI 建议

- **架构方案选择**：AI 提出了 3 种架构（事件驱动循环、管道/阶段链、插件架构），推荐方案 1（事件驱动循环）。我同意了。事后验证这个选择是正确的——每个模块独立可注入，完美契合 mock-LLM 单测要求。
- **AI 提议的 `Tool` 协议最初用 `async execute`**：实际实现时我发现 8 个工具全是同步操作（文件读写、subprocess.run），改回同步 `execute`，用 `asyncio.to_thread` 在需要时包装。改为同步后测试更简单、更可靠。
- **FeedbackEngine 的 timeout 默认值**：AI 初始实现用 `None`，我修正为 `60000ms`（60 秒默认），并接入 `FeedbackConfig.timeout_ms` 使整个反馈闭环可配置。

---

## 二、至少 3 轮关键迭代

### 迭代 1：设计章节逐节确认

```
AI: "设计 §1：系统总览与模块职责。9 个模块，每个有明确职责和对外接口。这个总览看起来合理吗？"
我: "合理"
AI: "设计 §2：Agent Loop 主循环。确定性状态机，5 种停机条件。这部分看着合理吗？"
我: "合理"
...（共 7 个设计章节逐一确认）
```

**决策**：逐节确认而非整份 SPEC 一次性评审。这让我有机会在每一节深入思考，避免了"全部写完后才发现理解偏差"的问题。反馈闭环那一节（§3）我花了最多时间确认。

### 迭代 2：未知 tool_calls 参数名导致文件无法写入

```
问题：输入"修改 hello.py"后，REPL 显示 Guard 拦截了 write_file（ask_once）。
审批通过后文件仍未被修改。Debug 发现 DeepSeek 使用的参数名是 file 而非 path。
AI 追查根因：_format_tools 发送空 properties {}，DeepSeek 只能猜参数名。
修复：给每个工具描述加上明确的参数文档（path/content/cwd 等），同时让 WriteFileTool
和 ReadFileTool 同时接受 path 和 file 两个参数名做兼容。
```

**决策**：在工具描述中加入参数文档是"提示词工程"方案，在 `execute()` 中同时接受两个参数名是"防御性代码"方案。两者都做了。

### 迭代 3：DeepSeek API tool_calls ID 不匹配导致 400 错误

```
问题：运行时报 BadRequestError: "Messages with role 'tool' must be a response to 
a preceding message with 'tool_calls'"。
根因：DeepSeek 返回的原始 tool_call.id 在 _decode_tool_calls 中被丢弃，
assistant 消息用 call_0/call_1 自编 ID，tool 结果用 action_id（新 UUID），
三个 ID 全部不匹配。
修复：_decode_tool_calls 保留原始 id → parser 传到 Action(action_id=...) →
loop 用 action.action_id 构建 assistant tool_calls。全程一致。
```

**决策**：这是 DeepSeek OpenAI 兼容 API 的硬性要求，必须精确匹配。修复涉及 4 个文件（deepseek.py、parser.py、loop.py、test_llm_deepseek.py），是所有迭代中改动范围最大的单次修复。

---

## 三、AI 建议的采纳、推翻与修正

| 建议 | 来源 | 处理 | 原因 |
|---|---|---|---|
| 架构用事件驱动循环 | 方案对比 | ✅ 采纳 | 模块独立可注入，mock 测试最直接 |
| 护栏三级风险 | 第 9 问 | ✅ 采纳 | 比二元拦截更实用 |
| 反馈闭环 9 类 FailureCategory | SPEC 设计 | ✅ 采纳 | 覆盖所有 coding 场景 |
| TOML 双层配置 | 第 11 问 | ✅ 采纳 | 用户级默认 + 项目级覆盖 |
| read_only 推回机制 | 集成调试 | ❌ 推翻 | 过于激进，LLM 需要先读取再写入，第一轮读取就推回破坏了正常流程 |
| silent-content 强制回答 | 集成调试 | ❌ 推翻 | DeepSeek 用完工具后 content 为空是正常行为，强制推回导致无限循环 |
| done-but-reads-only 检测 | 集成调试 | ❌ 推翻 | 和 read_only 推回类似，破坏读-改-写自然流程 |
| `async execute` 在 Tool 协议 | 初始实现 | ❌ 修正 | 8 个工具全是同步操作，改为同步更简单可靠 |
| `timeout_ms=None` 默认值 | Task 11 实现 | ❌ 修正 | 应为 `60000`（60s），并接入 `FeedbackConfig` |

### 推翻 read_only/silent/done-but-reads-only 推回的详细原因

这三个推回机制是我在调试"Agent 只读不写"问题时逐步叠加的。每次加一个，都不够好：

1. **read_only 推回**：假设任务一定是修改型，但用户问"这个项目用什么测试框架"也会触发（因为有文件读取）。修复：加了 `is_modify_task` 关键词检测来判断是否是修改任务。
2. **silent-content 推回**：假设 LLM 用完工具后必须有文字回复，但 DeepSeek 用 tool_calls 时 `content` 字段本身就是空的。修复：加了 `not actions and last_results` 条件限制触发范围。
3. **done-but-reads-only 推回**：假设 `finish_reason == "stop"` 且只有读取就是假完成，但 `finish_reason` 的判断不够可靠。

最终这些推回层层叠加形成了无法逃脱的循环，agent 从原本能正常修改代码变成了"读 → 被推回 → 再读 → escalate → interrupted"的死循环。**最后 `d8a2c9e` 提交一次性删除了 118 行推回代码**，回到干净的 system prompt + 护栏审批 + 反馈闭环核心流程。

**教训**：当 agent 行为不符合预期时，应该先检查 API 调用是否正确（tool_calls 格式、参数名匹配），而非在 loop 层叠加行为修正。循环控制层的改动副作用极大。

---

## 四、Brainstorming 技能的反思

### 做得好的地方

1. **逐问收敛**：一次一个问题的节奏非常适合澄清模糊需求。11 个问题从"做什么场景"到"配置什么格式"，从粗到细，没有跳跃。
2. **方案对比**：提出 2-3 个可行方案并推荐一个，比直接"告诉我怎么做"更有参与感。架构三选一的过程让我真正理解了事件驱动循环的设计意图。
3. **分节确认**：设计分 7 个章节逐一确认，每章不超过 300 字。我可以在每节深入思考，而不是被一份 30 页的 SPEC 砸晕。
4. **SPEC 自审**：写完后自动扫描 TBD/TODO、内部一致性、歧义，确保了我审阅的是完整产物。

### 让我不满的地方

1. **SPEC 过于详尽导致假安全感**：冷启动验证暴露了大量"写下来了但不够精确"的缺陷（见 §五）。一份 2000 字的 SPEC 很容易让人觉得"已经说清楚了"，但陌生 agent 在每一处留白都会受阻。
2. **SPEC 自审不够严格**：自审只检查了占位符和结构一致性，但没能发现 §§3.2 与 §6.1 之间的类型定义缺失（LLMResponse.tool_calls 元素结构）。这类"类型级遗漏"需要更形式化的检查。
3. **Plan 的 Task 粒度描述不均衡**：Task 1-10 每个都有详细的测试用例和接口定义，但 Task 12+（Agent Loop、REPL）的描述偏向设计意图而非具体接口，导致 subagent 在这些 task 上需要更多自行判断。
4. **没有在 SPEC 阶段就明确目标平台的假设**：SPEC §7.2 写"跨平台"，但没有显式声明"make 在 Windows 上不可用时的等价命令"。这是事后才发现的。

---

## 五、冷启动自我验证（§4.5）

### 5.1 验证设置

- **第二 agent**：CodeBuddy（与主开发 agent Claude Code 类型不同）
- **提供的文档**：仅 `SPEC.md` + `PLAN.md`，无任何会话历史或口头补充
- **任务**：从 PLAN 选 Task 1（脚手架）+ Task 2（数据模型）自主推进
- **约束**：明确要求"遇到不确定之处即暂停询问，而非凭猜测继续"

### 5.2 CodeBuddy 暴露的 SPEC 缺陷

CodeBuddy 在实现 Task 1+2 过程中，共遇到 **9 个需要自行判断的点**，其中 6 个被确认为 SPEC/PLAN 缺陷：

| # | 问题类别 | 严重程度 | CodeBuddy 的处理 |
|---|---|---|---|
| 1 | `FeedbackCategory` vs `FailureCategory` 命名冲突 | 中 | 创建类型别名 `FeedbackCategory = FailureCategory` |
| 2 | 项目根目录结构 SPEC 与 PLAN 矛盾 | **高** | 暂停并询问我确认 |
| 3 | `TurnContext` 字段定义缺失 | 中 | 自行推断为累积容器 |
| 4 | `Config` 子结构粒度模糊 | 低 | 按 PLAN 示例补全嵌套 dataclass |
| 5 | Windows 无 `make` 命令 | 中 | 保留 Makefile，用 `python -m pytest` 替代 |
| 6 | 护栏"默认拒绝未知操作"口径模糊 | 低 | 记录为预判问题 |
| 7 | `RiskLevel` 是类属性还是实例属性 | 低 | 实现为类属性 |
| 8 | `LLMResponse.tool_calls` 元素结构未定义 | **高** | 按 PLAN MockBackend 格式实现 |
| 9 | `Action.params` 与工具参数契约 | 低 | 保留 `dict[str, Any]` |

### 5.3 最关键的发现

**发现 1（#2）：根目录矛盾**

CodeBuddy 在这一点上**暂停并向我提问**，因为 SPEC §6.2 的文件布局和 PLAN 的文件结构图对根目录的定义不一致。这是"未经明文写下"的最典型上下文——我和主 agent 在 brainstorming 中默认了 `AI4SE/` 就是项目根目录，但从未在 SPEC 中显式声明。任何一个陌生 agent 都会在这里受阻。

**发现 2（#8）：LLMResponse.tool_calls 形状未定义**

SPEC §3.2 定义了 `LLMBackend.chat()` 的接口，但 `LLMResponse.tool_calls` 的每个元素结构（`{name, params}` 还是 OpenAI 风格的 `{function: {name, arguments}}`）没有定义。CodeBuddy 按 PLAN 中 MockBackend 和 Demo 代码的写法推断为 `{name, params}` 格式，并正确判断 DeepSeek backend 层应负责 OpenAI→内部格式的转换。这在后期集成中恰恰是一个实际出现的 bug（tool_calls ID 不匹配）。

**发现 3（#1）：命名冲突**

PLAN 的 Demo 3 中出现了 `FeedbackCategory`，但 SPEC 只定义了 `FailureCategory`。CodeBuddy 的判断——创建别名——是正确的处理。这暴露了 PLAN 中的 demo 代码未经 SPEC 对照审查。

### 5.4 我据此对 SPEC/PLAN 做的修订

| 修订 | 文件 | 修订前 | 修订后 |
|---|---|---|---|
| 明确 LLMResponse.tool_calls 元素结构 | SPEC §3.2 | 未定义 | 补充 `{"id": str, "name": str, "params": dict}` 格式说明 |
| 补充 TurnContext 字段定义 | SPEC §6.1 | 仅有名称 | 补充 `messages`, `last_results`, `correction_history`, `round_count` 字段 |
| 澄清护栏默认拒绝范围 | SPEC §3.4 | "默认拒绝未知操作类型" | 明确仅针对未注册 tool 名 + 危险正则 + 越界路径 |
| Windows 环境说明 | SPEC §7.2 | "跨平台" | 补充 Windows 用 `python -m pytest` 替代 `make test` |
| `risk_level` 为工具类属性 | SPEC §3.3 | 未说明 | 补充"risk_level 为工具固有类属性，不可被单次调用改变" |
| Demo 代码中统一 FailureCategory | PLAN Task 16 | `FeedbackCategory`（不存在） | 已在实际代码中统一为 `FailureCategory` |

### 5.5 产出与预期差距

CodeBuddy 的产出质量**高于预期**。它在 Task 1+2 上的实现与主 agent 的最终实现高度一致（数据模型结构、Config 拆分粒度、Tool Protocol 设计），仅有 `FeedbackCategory` 别名这一个额外产物。这说明 SPEC+PLAN 的清晰度在核心数据模型层面是足够的，但在跨模块集成细节（tool_calls 格式、护栏范围）上有明显的"隐性上下文"依赖。

最关键的差距不在于代码质量，而在于：CodeBuddy 在 2 个 task 中就遇到了 9 个需要自行判断的点。如果把剩余的 16 个 task 也交给它，那些判断会累积成显著的实现偏离——这恰恰证明了冷启动验证的必要性。

---

## 六、反思：Superpowers 方法论的批判

### 它假设了什么

1. **假设 SPEC 可以被写得足够清晰**：brainstorming → writing-plans 流程的核心理念是"规约驱动"。但我的项目经历证明——即使 2000 字的 SPEC 经过了逐节确认和自审，仍然在交给陌生 agent 时暴露了 6 个缺陷。规约永远有隐性假设。
2. **假设 TDD 在 AI 协作下是放大器**：实际上 TDD 确实起到了作用——Task 11 的 29 个测试在多次重构中保持了行为正确性。但在集成调试阶段（DeepSeek API 格式问题），TDD 无法覆盖"LLM 返回了错误的参数名"这类真实 API 行为差异。
3. **假设 subagent 会用相同方式理解同一份文档**：CodeBuddy 在 TurnContext 上的推断（累积容器）与主 agent 的实现一致，但在护栏"默认拒绝"的口径上有不同解读。两个 agent 阅读同一段 SPEC 可能得出不同结论。

### 这些假设在我的项目里成立吗

- **SPEC 足够清晰**：部分成立。核心数据模型部分成立——CodeBuddy 无需额外上下文就实现了正确的 models.py。但跨模块接口部分不成立——tool_calls 格式、护栏范围等必须在集成中补全。
- **TDD 是放大器**：几乎完全成立。258 个测试在每次修改后提供了即时回归保护，尤其是 FeedbackEngine 的 29 个测试在分类器重构期间防止了多次退化。
- **subagent 一致性**：基本成立。CodeBuddy 的实现与主 agent 在数据结构层面高度一致，证明 SPEC 在"数据层"是完备的。差异集中在"行为层"（护栏范围、推回策略），这些恰恰是 SPEC 中最模糊的部分。

### 如果重做会改变什么

1. **SPEC 增加"类型契约"章节**：显式定义每个跨模块接口的数据形状（LLMResponse.tool_calls、Action.params 等），作为 parser/backend 的共同契约。
2. **PLAN 中为每个 Task 补充"冷启动陷阱"标注**：标注"此 task 可能因以下假设而偏离——"，让 subagent 在已知风险点上更谨慎。
3. **冷启动验证不只做前 2 个 task**：选择最复杂的 task（Agent Loop）来验证，因为集成点的 SPEC 缺陷在核心数据模型层暴露不了。

---

*文档版本: v1.0 | 日期: 2026-07-08 | 作者: 学生本人撰写，CodeBuddy 冷启动验证报告由 AI 辅助记录*