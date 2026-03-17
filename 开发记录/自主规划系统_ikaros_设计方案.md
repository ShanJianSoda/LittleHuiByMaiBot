# 自主规划系统（ikaros）设计方案

> 基于对 Open-LLM-VTuber、AIRI、MaiBot 及《新心跳系统开发文档》的阅读分析，面向「记忆模块、情绪模块、操作模块」可扩展的自主规划中枢设计。  
> 文档状态：v0.1 | 更新时间：2026-03

---

## 一、文档分析结论摘要

### 1.1 三项目定位与可复用点

| 来源 | 定位 | 对「AI 之心」的可复用思路 |
|------|------|---------------------------|
| **Open-LLM-VTuber** | UI 型：LLM + 语音 + Avatar | 单会话 ServiceContext、Agent 管道（记忆 + 工具 + 表情）、MCP 工具一等公民 |
| **AIRI** | 行为型 Agent：感知→认知→行动 | EventBus 事件流、RuleEngine→Reflex→Brain、REPL 式动作执行、ActionRegistry 与 TaskExecutor |
| **MaiBot** | 社交型 Agent：记忆+情绪+人格 | 两阶段 LLM（Planner 决策 / Replyer 生成）、MoodManager、心流路由、HeartFChatting 主循环 |
| **新心跳系统文档** | 全局规划中枢 | 意图→计划→执行回执、多频心跳、Intent Provider / Policy Gate / Action Adapter、no_op 合法化 |

### 1.2 核心结论

- **「ikaros」的职责**：不是「聊天触发器」，而是**持续认知与行动编排内核**——从全局状态做决策，在聊天、工具、记忆、情绪、外部世界操作之间做仲裁与调度。
- **记忆 / 情绪 / 操作** 应作为**可插拔的能力模块**接入这颗心，而不是心只服务聊天。
- 心跳系统已有的抽象（影响→意图→计划→执行回执、多频心跳、策略闸门）可直接作为「AI 之心」的骨架；记忆、情绪、操作则通过**统一接口**注入「影响」与「行动」。

---

## 二、自主规划系统（ikaros）总体架构

### 2.1 目标

构建一个**自主规划中枢**，能够：

1. **记忆模块**：长期/工作记忆的读写、检索、摘要、遗忘策略，并作为「影响」输入与「行动」类型参与决策。
2. **情绪模块**：当前情绪与历史情绪作为「影响」输入，并可能驱动表达、主动行为或 no_op（例如低落时少说话）。
3. **操作模块**：与外界交互（如 Minecraft、VRChat、MCP 工具、联网搜索），作为与「发消息」并列的一等行动类型。

### 2.2 在现有 MaiBot 中的位置

```
                     ┌─────────────────────────────────────────────────┐
                     │           自主规划系统（AI 之心）                 │
                     │  ┌─────────┐  ┌─────────┐  ┌─────────────────┐  │
                     │  │ 影响聚合 │→│ 意图仲裁 │→│ 计划排序+策略闸门 │  │
                     │  └────┬────┘  └────┬────┘  └────────┬──────────┘  │
                     │       │            │                │             │
                     │       ▼            ▼                ▼             │
                     │  Intent Queue   Plan Queue    Action Adapters     │
                     └───────┬────────────┬────────────────┬─────────────┘
                             │            │                │
         ┌───────────────────┼────────────┼────────────────┼───────────────────┐
         │                   │            │                │                   │
         ▼                   ▼            ▼                ▼                   ▼
   ┌──────────┐       ┌──────────┐  ┌──────────┐   ┌──────────┐        ┌──────────┐
   │ 记忆模块  │       │ 情绪模块  │  │ 聊天模块  │   │ 操作模块  │        │ 其他扩展  │
   │ 召回/写入 │       │ 状态/历史 │  │ AFC/KFC  │   │ MC/VR/MCP │        │ 任务/日历 │
   └──────────┘       └──────────┘  └──────────┘   └──────────┘        └──────────┘
```

- **心**：统一接收「影响」、产出「意图」、生成「计划」、通过「行动适配器」执行并回收「执行回执」。
- **记忆 / 情绪**：既向「影响聚合」提供输入，也可作为可调度的「行动」（例如：记忆整理、情绪更新策略）。
- **操作模块**：与聊天平级，由心仲裁「何时发消息、何时玩 MC、何时调用 MCP」。

---

## 三、核心抽象与数据流（对齐新心跳文档）

### 3.1 影响（Influence）

**定义**：进入决策系统的所有输入的结构化表示。

| 来源 | 内容示例 | 提供方 |
|------|----------|--------|
| 聊天 | 新消息、at、回复概率增强、冷场时长 | 现有 HeartFCMessageReceiver / ChatManager |
| **记忆** | 此刻召回的片段、待写入的重要事实、到期提醒 | 记忆模块（见下节） |
| **情绪** | 当前情绪、强度、前 n 次情绪轨迹 | 情绪模块（MoodManager 扩展） |
| 外部事件 | 游戏内事件、VRChat 事件、MCP 回调 | 操作模块 / 适配器 |
| 系统 | 当前时间、预算、心跳周期、上次执行回执 | 心跳运行时 |

建议结构（示例）：

```python
@dataclass
class Influence:
    timestamp: datetime
    source: str  # "chat" | "memory" | "emotion" | "operation" | "system"
    payload: dict  # 源相关数据
    priority_hint: Optional[float] = None
    ttl_seconds: Optional[float] = None
```

### 3.2 意图（Intent）与计划（Plan）

- **Intent**：由「影响」经规则或轻量模型生成，表示「系统为何要考虑做某事」。
- **Plan**：由 normal/slow 心跳中的决策模型生成，包含 `action_type`（如 `chat_reply`、`mcp_call`、`memory_update`、`operation_minecraft`、`no_op`）及参数。
- **ExecutionReceipt**：执行后的结果摘要，可写回记忆、更新情绪、或触发后续意图。

与《新心跳系统开发文档》中的 Intent Provider、Plan Ranking、Policy Gate 完全一致；此处仅明确：**记忆、情绪、操作** 都会通过 Influence 参与意图形成，并以 Action Adapter 形式参与执行。

### 3.3 多频心跳中的分工

| 层级 | 周期 | 记忆 | 情绪 | 操作 |
|------|------|------|------|------|
| **fast** | 秒级 | 仅读取缓存/预取 | 读取当前状态 | 仅紧急反馈（如游戏死亡） |
| **normal** | 10~60s | 检索注入影响、可选写入 | 状态注入影响、更新由消息驱动 | 常规操作调度（聊天/MC/MCP） |
| **slow** | 小时级 | 整理、摘要、遗忘策略 | 慢速衰减/恢复、策略参数 | 长时间任务、资源回收 |

---

## 四、记忆模块（未来优秀形态）

### 4.1 目标

- **工作记忆**：当前会话/当前焦点下的短上下文，与现有「聊天历史 + 最近消息缓存」对齐并可扩展。
- **长期记忆**：可检索、可更新、可摘要、可遗忘，支持「回忆」与「记忆写入」作为显式行动。

### 4.2 与「ikaros」的接口

- **作为影响输入**（每次 normal 心跳或触发决策前）：
  - `memory.get_influence_for_decision(stream_id, time_range, max_items)`  
    返回：近期召回的记忆片段、到期提醒、待确认写入，封装为 `Influence(source="memory", payload=...)`。
- **作为行动**：
  - `action_type: "memory_update"`：写入/更新/删除指定记忆。
  - `action_type: "memory_recall"`：主动检索并可能触发后续 `chat_reply` 或 `summarize`。

### 4.3 实现顺序建议

1. **接口层**：在心跳/规划侧定义 `MemoryInfluenceProvider` 与 `MemoryActionAdapter`，先与现有记忆实现（若有）做简单桥接。
2. **分层**：工作记忆（现有 ChatStream + DB 历史）保持不变；长期记忆明确「检索 API + 写入 API + 摘要/过期策略」。
3. **与 Replyer 的关系**：保持现有「think_level≥1 时检索记忆注入 Replyer Prompt」；同时让规划器能看到「记忆模块提供的影响」（例如：有一条「用户偏好 X」被召回），从而在 Planner 阶段就能考虑「要不要在回复里用上」。

---

## 五、情绪模块（未来优秀形态）

### 5.1 目标

- 情绪作为**状态**（当前情绪 + 强度 + 可选历史轨迹）参与决策，而不只是回复时的 Prompt 装饰。
- 情绪可驱动：回复风格、是否主动说话、是否 no_op、表达习惯（与现有 expression 结合）。

### 5.2 与「ikaros」的接口

- **作为影响输入**：
  - `emotion.get_influence_for_decision(stream_id)`  
    返回：当前情绪、强度、前 n 次变化，封装为 `Influence(source="emotion", payload=...)`。
- **作为被更新对象**：
  - 延续现有逻辑：消息入库后异步更新情绪（MoodManager）；心跳不直接「写情绪」，而是通过「执行了某行动」间接影响（例如回复被点赞→后续情绪更新时考虑）。
- **可选行动**：
  - `action_type: "emotion_expression"`：仅表达情绪（表情/动作），不发言，由操作模块或 Avatar 执行。

### 5.3 实现顺序建议

1. **标准化输出**：MoodManager 提供 `get_influence_for_decision()`，返回与 `Influence` 兼容的结构，供规划器与 Replyer 共用。
2. **策略化**：在 Policy Gate 或 Plan Ranking 中引入「情绪—行为」规则（例如：极低情绪时提高 no_op 得分、或限制主动发言频率）。
3. **与操作模块联动**：若接入 VRChat/虚拟形象，情绪可映射为「表情/动作」通过操作模块发送。

---

## 六、操作模块（与外界交互：Minecraft、VRChat 等）

### 6.1 目标

- 将「玩游戏、进 VRChat、调用 MCP、联网搜索」视为与「发一条群消息」同等的**行动类型**，由心统一仲裁。
- 事件可自外部回写为「影响」，形成闭环（例如：MC 里被攻击 → 产生意图 → 计划包含「逃跑或反击」→ 执行 → 执行回执再写回记忆/情绪）。

### 6.2 与「ikaros」的接口

- **作为影响输入**：
  - 操作适配器将外部事件（游戏事件、VRChat 事件、MCP 回调）转为 `Influence(source="operation", payload=...)`，推入意图队列或直接参与当轮决策。
- **作为行动**：
  - `action_type: "operation_minecraft"` / `"operation_vrchat"` / `"mcp_call"` / `"web_search"` 等，参数由 Plan 携带；执行由各 Action Adapter 调用具体 SDK/API。
  - 执行结果统一为 `ExecutionReceipt`，可写回记忆、触发后续意图（如 `chat_reply` 总结战况）。

### 6.3 实现顺序建议

1. **抽象层**：定义 `OperationActionAdapter` 基类，注册 `action_type → 执行函数`；先实现一个占位适配器（例如 echo 或日志），保证心跳与调度链路跑通。
2. **Minecraft**：参考 AIRI 的 CognitiveEngine：EventBus 将游戏事件转为 `Influence` 或等价事件，由「ikaros」的 normal 心跳消费；Brain 的「REPL + ActionRegistry」可视为 MaiBot 侧某个 `OperationMinecraftAdapter` 的执行后端（即：心产出 `operation_minecraft` 计划，适配器调用 MC 的 ActionRegistry 执行）。
3. **VRChat**：同理，VRChat 事件 → Influence；计划中的 `operation_vrchat`（移动、表情、发言）→ VRChat SDK/API。
4. **MCP/搜索**：已在新心跳文档中列为一等公民，沿用「计划 → 执行 → ExecutionReceipt → 可选后续 chat/summarize」即可。

---

## 七、实施路径建议（如何做）

### 阶段一：心核与现有聊天打通（不破坏现有行为）

1. **实现最小「ikaros」**  
   - 仅保留一层 normal 心跳（或复用现有 HeartFChatting 的 _loopbody 周期）。  
   - 引入「影响」聚合：当前仅聚合「聊天相关影响」（新消息、at、冷场等），仍用现有 ActionPlanner 决策。  
   - 输出仍为现有 `List[ActionPlannerInfo]`，通过现有 reply/no_reply/插件动作执行。  
   - 目标：心作为「唯一入口」存在，但行为与现在一致。

2. **定义稳定数据结构**  
   - `Influence`、`Intent`、`Plan`、`ExecutionReceipt` 的 dataclass 或 Pydantic 模型。  
   - 在配置/模板中预留「心跳周期、队列长度、策略开关」等，便于后续调参。

### 阶段二：记忆与情绪作为影响与行动接入

3. **记忆模块**  
   - 实现 `MemoryInfluenceProvider`：在每次决策前调用现有记忆检索（或占位），产出 `Influence(source="memory", ...)`。  
   - 在 Planner Prompt 中增加「当前召回记忆」片段（若已有记忆检索，可复用结果并格式化）。  
   - 可选：增加 `memory_update` / `memory_recall` 为规划器可选动作，由 Action Adapter 执行。

4. **情绪模块**  
   - 实现 `EmotionInfluenceProvider`：封装 `get_mood_for_prompt` 或等价接口，产出 `Influence(source="emotion", ...)`。  
   - 在 Policy Gate 或 Plan Ranking 中增加简单规则（如情绪极低时倾向 no_op）。  
   - 保持现有 MoodManager 的更新路径不变。

### 阶段三：操作模块与多频心跳

5. **操作模块**  
   - 实现 `OperationActionAdapter` 注册表与基类。  
   - 先接 1 个具体后端：MCP 或「模拟 MC」的占位，使「ikaros」能生成 `mcp_call` / `operation_*` 计划并执行、写回 ExecutionReceipt。  
   - 再按需接入真实 Minecraft（参考 AIRI EventBus + Brain）、VRChat、联网搜索。

6. **多频心跳**  
   - 实现 fast / normal / slow 三层；fast 仅做健康与紧急意图，slow 做记忆整理与策略校正。  
   - 将「记忆整理」「情绪衰减」等放入 slow 的 Action Adapter 或独立任务，由心统一调度。

### 阶段四：治理与可观测性

7. **策略与成本**  
   - 实现 Policy Gate：预算、冷却、单目标限频、no_op 显式化。  
   - 可观测性：行为质量、节律分布、资源效率、安全拦截率（对齐新心跳文档第 12 节）。

---

## 八、与《新心跳系统开发文档》的对应关系

| 新心跳文档章节 | 本方案对应 |
|----------------|------------|
| §4 核心抽象（影响/意图/计划/执行回执） | §3 影响/意图/计划/执行回执；§4–§6 记忆/情绪/操作作为影响与行动 |
| §5 Intent Provider / Policy Gate / Action Adapter | 记忆/情绪/操作均通过 Provider 提供影响、通过 Adapter 执行行动 |
| §6 决策模型（Intent→候选计划→排序+闸门） | 心核统一实现；记忆与情绪参与「影响→意图」与排序权重 |
| §7 多频心跳 | §3.3 与 §7 阶段三 |
| §8 双队列与调度 | 心核实现 intent_queue / plan_queue；记忆与操作的回执可反哺意图 |
| §9 与现有聊天模块关系 | 聊天作为 Chat Action Adapter，心为仲裁者 |
| §10 MCP/工具一等公民 | 操作模块中的 MCP/搜索，与 MC/VRChat 并列 |

---

## 九、是否有必要构建：成本与速度视角

### 9.1 现实情况

- 符合人类直觉的「心」（意图→计划→多源仲裁）在**速度和成本上通常不是最优解**：
  - 多轮决策 = 多次 LLM 调用（Planner + Replyer 已两次）、队列与闸门 = 额外延迟与实现复杂度。
  - 大部分开源对话/Agent 采用更简单管道：**单次 LLM 调用 + 流式输出 + 可选工具调用**，即可满足多数场景。
- 因此：**不是「必须」建完整心核，而是按需求做取舍。**

### 9.2 何时值得建「心」

| 场景 | 建议 |
|------|------|
| 仅群聊/私聊回复 + 偶尔工具 | **不建**。保持现有 Planner + Replyer，或进一步合并为单模型 + 流式，成本和延迟更优。 |
| 需要「聊天 vs 玩游戏 vs 搜网」等多行动仲裁 | **值得**。心作为统一入口，避免各模块抢时机、刷屏或冲突。 |
| 强治理（预算、冷却、no_op、安全） | **值得**。心上的 Policy Gate 是自然落点。 |
| 长期记忆/情绪参与「是否回复、何时回复」 | **可简化**：仅把记忆/情绪当 Prompt 输入，不必上完整意图队列与多频心跳。 |

### 9.3 折中方案（要一点「心」但控制成本）

- **轻量心**：只做「单层 normal 心跳 + 影响聚合」，决策仍是一次 Planner 调用；不引入 intent_queue/plan_queue，不做 fast/slow 分层。
- **合并调用**：探索 Planner 与 Replyer 合并为单次 LLM（结构化输出中同时含「是否回复 + 理由 + 回复内容」），减少一次模型调用与往返延迟。
- **先做伪实时体验**：把资源优先放在「流式输出 + 即时反馈」上，再考虑是否上完整心核。

---

## 十、伪实时即时输入输出：怎么做

「伪实时」= 用户**尽快看到反馈**、**尽快看到回复开始出现**，且可**打断**，而不是等整段生成完再一次性看到。

### 10.1 当前 MaiBot 行为（简要）

- 回复路径：`generate_reply` → `llm_generate_content(prompt)` **等整段生成完毕** → `text_to_stream()` 发整条消息。
- 仅有 **typing 模拟**（按长度 `calculate_typing_time` 延迟）营造「在打字」的感觉，但**首字延迟 = Planner + 完整 Replyer 生成 + typing_time**，并非真正即时。

### 10.2 伪实时的关键手段

| 手段 | 说明 | 实现难度 |
|------|------|----------|
| **流式输出（Streaming）** | LLM 边生成边推给前端/平台，首 token 到首字时间大幅缩短 | 中：需 Replyer 与发送层支持流式 API 与分段发送 |
| **尽早「正在输入」** | 一旦决定 reply，立即发「正在输入」或占位，再流式追内容 | 低：在调用 LLM 前发 typing，与现有 WebUI typing 类似 |
| **可打断（Interrupt）** | 用户新消息到达时取消当前生成、可保留「已说一半」状态 | 中：需任务取消 + 可选写入记忆/对话状态 |
| **更小/更快模型** | 简单轮次用小模型或缓存，降低首 token 延迟 | 配置与路由 |

### 10.3 推荐实现顺序（在不建完整心核的前提下）

1. **流式 Replyer**  
   - 使用当前 LLM 的 **stream** 接口（若支持），在 `group_generator` / `private_generator` 的 `llm_generate_content` 中改为异步迭代 token/片段。  
   - 每累积一小段（如一句或 N 个字符）就调用一次发送（或通过 WebSocket 推给 WebUI），平台侧若支持「分段收」则同样分段发往 QQ 等。  
   - 效果：用户先看到前半句，再看到后半句，**体感延迟 ≈ 首句生成时间**，而不是整段。

2. **先发 typing，再流式内容**  
   - 在 `generate_reply` 里，在调用 `llm_generate_content` **之前**就对目标 stream 发「正在输入」（与现有 `typing_time` 逻辑错开：typing 表示「在思考/在生成」，流式表示「在出字」）。  
   - 平台/WebUI 已有 typing 的可复用；没有的可用「占位消息 + 后续更新」模拟。

3. **打断**  
   - 在 HeartFChatting/BrainChatting 的循环中：若在等待 `generate_reply` 或流式生成过程中收到**同一 chat 的新消息**，则取消当前生成任务（`asyncio.Task.cancel`），并将「已生成但未发送」的片段按需写入记忆或丢弃。  
   - 可选：像 Open-LLM-VTuber 的 interrupt 一样，在对话历史里插入「[用户打断了]」并截断上一条 assistant 内容。

4. **成本与模型选择**  
   - 流式不增加 token 成本，只改变输出消费方式。  
   - 若希望进一步降低首字延迟，可对「简单回复」走小模型或缓存（与是否建心无关）。

### 10.4 与「心」的关系

- **伪实时不依赖心**：流式、typing、打断都可以在现有「消息 → Planner → Replyer → 发送」链路上做，无需意图队列或多频心跳。
- 若未来上心核：心只决定「何时触发 reply」；**一旦触发，reply 的生成与发送仍建议走流式 + 可打断**，这样既保留全局仲裁，又保证单次回复的即时感。

---

## 十一、总结

- **AI 之心** = 以《新心跳系统开发文档》为骨架的**自主规划中枢**，负责从全局「影响」生成「意图」与「计划」，经策略闸门后由各 Action Adapter 执行并回收执行回执。
- **记忆模块**：通过 `MemoryInfluenceProvider` 与 `MemoryActionAdapter` 接入，提供「召回/到期/待写」影响，并支持记忆更新与主动回忆为显式行动。
- **情绪模块**：通过 `EmotionInfluenceProvider` 提供当前状态与历史轨迹，参与排序与策略（如 no_op 倾向）；可选地通过 `emotion_expression` 与操作模块驱动虚拟形象。
- **操作模块**：外部事件 → Influence；计划中的 `operation_*` / `mcp_call` / `web_search` 由 OperationActionAdapter 执行，回执可写回记忆或触发后续聊天/总结。

按「阶段一 → 二 → 三 → 四」推进，可在不破坏现有 MaiBot 聊天体验的前提下，逐步长成具备优秀记忆、情绪与操作能力的自主规划系统。

**建议优先顺序**：若资源有限，先做 **§10 伪实时（流式 + 尽早 typing + 可打断）**，再根据是否需要多行动仲裁与强治理，决定是否上完整心核或仅做轻量心（§9.3）。
