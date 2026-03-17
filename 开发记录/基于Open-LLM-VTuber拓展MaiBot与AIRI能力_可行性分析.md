# 基于 Open-LLM-VTuber 拓展 MaiBot / AIRI 能力 —— 可行性分析

> 基于 Open-LLM-VTuber 源码与架构文档的分析结论：在该项目上拓展「更优对话/记忆模块」与「Agent 游玩 Minecraft」是否可行、如何做。  
> 文档状态：v0.1 | 更新时间：2026-03

---

## 一、Open-LLM-VTuber 框架与模块概览（源码结论）

### 1.1 技术栈与入口

- **语言**：Python  
- **入口**：`run_server.py` → 读 `conf.yaml`、校验配置 → `WebSocketServer` → `default_context_cache.load_from_config(config)` → Uvicorn。  
- **客户端通道**：WebSocket `/client-ws`，由 `WebSocketHandler` 按消息 `type` 分发到对应 `_handle_*`。

### 1.2 核心模块（与拓展相关的部分）

| 模块 | 路径/文件 | 职责（源码级） |
|------|------------|----------------|
| **服务上下文** | `service_context.py` | 单会话容器：持有 `asr_engine`、`tts_engine`、`agent_engine`、`live2d_model`、MCP 相关（tool_manager、tool_executor、mcp_prompt）、`history_uid`；`init_agent()` 内调 `AgentFactory.create_agent(...)` 创建并注入。 |
| **Agent 接口** | `agent/agents/agent_interface.py` | 抽象：`chat(input_data) -> AsyncIterator[BaseOutput]`、`handle_interrupt(heard_response)`、`set_memory_from_history(conf_uid, history_uid)`。 |
| **BasicMemoryAgent** | `agent/agents/basic_memory_agent.py` | 实现：`_memory` 列表 + `chat_history_manager.get_history` 持久化；`_to_messages()` 用 `_memory.copy()` + 本轮 user content（文本/图）拼 messages；支持 MCP 工具循环（Claude/OpenAI）；输出经 sentence_divider → actions_extractor → display_processor → tts_filter。 |
| **Agent 工厂** | `agent/agent_factory.py` | `create_agent(conversation_agent_choice, ...)` 根据配置返回 `BasicMemoryAgent` / `mem0_agent` / `letta_agent` / `hume_ai_agent` 等；BasicMemoryAgent 依赖 StatelessLLMFactory 创建的 LLM、live2d_model、tool_manager、tool_executor、mcp_prompt_string。 |
| **对话编排** | `conversations/single_conversation.py` | `process_single_conversation(context, websocket_send, client_uid, user_input, images, metadata)`：process_user_input → create_batch_input → store_message（可选）→ **context.agent_engine.chat(batch_input)** → process_agent_output（TTS/展示）→ store_message(ai)。 |
| **对话历史** | `chat_history_manager.py` | 按 `conf_uid` / `history_uid` 存 JSON 文件；`store_message`、`get_history`、`get_history_list`、`create_new_history`、`delete_history`；仅「按会话的历史列表」，无长期记忆检索、无情绪。 |
| **输入/输出类型** | `agent/input_types.py`, `output_types.py` | `BatchInput(texts, images, metadata)`；`SentenceOutput`(display_text, tts_text, actions)、`AudioOutput`。 |

### 1.3 数据流（单轮对话）

```
用户输入(文本/语音/图) 
  → process_user_input(ASR 等) 
  → create_batch_input(texts, images, metadata) 
  → store_message(human) [若未 skip_history]
  → context.agent_engine.chat(batch_input)
       → BasicMemoryAgent._to_messages(batch_input)  # _memory + 本轮 user
       → LLM 流式（含可选 tool 循环）
       → sentence_divider → actions_extractor → display_processor → tts_filter
       → yield SentenceOutput / AudioOutput / tool_call_status
  → process_agent_output → TTS 队列 → websocket_send
  → store_message(ai)
```

结论：Open-LLM-VTuber 的「对话」是 **单次 Agent.chat 调用**，记忆只有 **会话内 _memory + 按 history_uid 的 JSON 历史**，没有 MaiBot 式的 Planner/Replyer 两阶段、没有情绪、没有长期记忆检索。

### 1.4 聊天部分是否为异步流式（与 MaiBot 结合的关键）

**结论：是，Open-LLM-VTuber 的聊天是端到端异步流式的。**

| 层级 | 行为（源码依据） |
|------|------------------|
| **LLM 层** | `_llm.chat_completion(messages, system, tools=...)` 返回 **async generator**，`async for event in stream` 逐 token/事件产出（如 `openai_compatible_llm`、`claude_llm`、`llama_cpp_llm` 等均为 `stream=True`）。 |
| **Agent 层** | BasicMemoryAgent 的 `chat()` 经装饰器链：**token 流** → `sentence_divider`（按句切分）→ `actions_extractor`（表情）→ `display_processor` → `tts_filter` → 逐句 **yield SentenceOutput**（及可选 `dict` 的 tool_call_status）。 |
| **编排层** | `single_conversation` 中 `agent_output_stream = context.agent_engine.chat(batch_input)`，然后 **`async for output_item in agent_output_stream`**，对每个 `SentenceOutput` 调用 `process_agent_output`（TTS、websocket_send）。 |

因此：**下游只依赖「Agent 产出的是 AsyncIterator[BaseOutput]」**，且每个 item 为 `SentenceOutput` / `AudioOutput` / `dict(tool_call_status)`；**只要新 Agent 按同一契约 yield，无需重写 single_conversation、process_agent_output、tts_manager 等流程**。

---

## 二、拓展目标与可行性总览

| 拓展目标 | 来源 | 可行性 | 推荐方式 |
|----------|------|--------|----------|
| 更优对话模块（两阶段规划+回复、情绪、表达） | MaiBot | **可行** | 新 Agent 实现 / 或 Prompt+记忆注入（见下） |
| 更优记忆模块（长期记忆、检索、摘要） | MaiBot | **可行** | 新 Agent 或扩展现有 Agent 的 _to_messages / 独立记忆服务 |
| Agent 游玩 Minecraft | AIRI | **可行但跨栈** | 以「MCP 或 HTTP 桥接」接入 AIRI 服务，或 Python 侧仿造 REPL 执行（见下） |

---

## 三、拓展一：更优对话模块（MaiBot 优势）

### 3.1 MaiBot 相对 Open-LLM-VTuber 的差异

- **两阶段 LLM**：Planner 决策「是否回复、回复谁、理由」→ Replyer 生成「具体文案」；Open-LLM-VTuber 只有一次 chat。
- **情绪**：MoodManager 维护 per-chat 情绪，参与 Planner/Replyer 的 Prompt。
- **表达/人格**：表达习惯、人际关系等注入 Replyer Prompt。

### 3.2 可行方案（按侵入性从低到高）

**方案 A：在现有 BasicMemoryAgent 上做「Prompt + 外部状态」增强（最小侵入）**

- 不新增 Agent 类型。在 `ServiceContext.init_agent` 或 Agent 构造时，从**外部服务/模块**拉取：
  - 当前情绪摘要（若 MaiBot 或独立情绪服务提供 HTTP/内网 API）；
  - 长期记忆检索结果（若 MaiBot 或独立记忆服务提供 API）。
- 在 `construct_system_prompt()` 或 BasicMemoryAgent 的 `_system` 中拼接上述内容；在 `_to_messages()` 里把「检索到的记忆片段」作为 system 或单独一条 user 消息注入。
- **前提**：MaiBot 或单独服务暴露「按 session/user 的情绪、记忆检索」接口；Open-LLM-VTuber 用 HTTP 或本地 Python 调这些接口。
- **优点**：不改 Agent 接口、不改对话编排，只扩展现有 prompt 与 _to_messages。  
- **缺点**：没有两阶段决策（不选「是否回复」），只是「每次 chat 都带更多上下文」。

**方案 B：新增一种 Agent 实现（如 `MaiBotStyleAgent`），实现 `AgentInterface`**

- 在 `agent/agents/` 下新增 `maibot_style_agent.py`（或类似命名），实现 `chat()`、`handle_interrupt()`、`set_memory_from_history()`。
- `chat()` 内部：
  - 先调「规划器」（可本地调 MaiBot 的 Planner 逻辑或复刻一版）：输入当前上下文 + 情绪，得到「是否回复、目标、理由」；
  - 若决定回复，再调「回复器」（或 MaiBot Replyer 复刻）：输入上下文 + 情绪 + 记忆检索 + 规划结果，得到回复文本；
  - 将回复文本转成 Open-LLM-VTuber 需要的 `SentenceOutput` 流（可整段或按句 yield），走现有 `process_agent_output`（TTS、展示）。
- 情绪、记忆：通过 MaiBot 的 MoodManager / 记忆检索模块的 **Python 调用**（若同机部署）或 **HTTP/ gRPC**（若 MaiBot 以服务形式提供）。
- 在 `AgentFactory.create_agent` 中增加 `conversation_agent_choice == "maibot_style_agent"` 分支，传入 system_prompt、live2d_model、tts 相关、以及「MaiBot 后端地址或 Python 模块引用」。
- **优点**：完整保留两阶段与情绪/记忆能力，与现有 BasicMemoryAgent 并存，可配置切换。  
- **缺点**：需在 Open-LLM-VTuber 侧维护一套与 MaiBot 的对接（配置、错误处理、超时）。

**方案 C：Open-LLM-VTuber 仅作「前端」，对话完全由 MaiBot 服务负责**

- 用户语音/文本在 Open-LLM-VTuber 内转成文本后，通过 HTTP/WebSocket 发给 MaiBot；MaiBot 返回「最终回复文本」（或带分段信息）。
- Open-LLM-VTuber 侧只做：ASR、TTS、Live2D、发送/接收；不再调用本地 Agent，或仅用占位 Agent 把「从 MaiBot 拿到的文本」转成 SentenceOutput 流。
- **优点**：对话与记忆/情绪完全沿用 MaiBot，零改动 MaiBot 核心。  
- **缺点**：强依赖 MaiBot 服务可用性与延迟；两套系统部署与运维。

**小结**：若希望「在 Open-LLM-VTuber 项目内」直接享受 MaiBot 的对话与记忆优势，**方案 B（新 Agent 实现 + MaiBot 作为后端或库）** 最平衡；若只想要「更好的上下文」而接受单阶段 chat，**方案 A** 即可。

### 3.3 与 MaiBot 结合时：流式契约与是否重写代码

- **MaiBot 当前行为**：Replyer 的 `llm_generate_content(prompt)` 是 **await 一次拿整段回复**（非流式），返回 `(content, reasoning_content, model_name, tool_calls)`。  
- **Open-LLM-VTuber 的契约**：`Agent.chat(batch_input)` 必须返回 **AsyncIterator**，逐条 yield `SentenceOutput`（或 AudioOutput / tool_call_status），下游 `async for output_item in agent_output_stream` 按条消费并送 TTS/前端。

**结论：与 MaiBot 结合时，无需重写 Open-LLM-VTuber 的对话流程（single_conversation、process_agent_output、tts_manager），只需在新 Agent 内「适配输出形态」即可。**

两种适配方式（二选一即可）：

| 方式 | 做法 | 是否重写 |
|------|------|----------|
| **不改 MaiBot** | 新 Agent（如 MaiBotStyleAgent）内：调用 MaiBot 的 Replyer 或 HTTP 拿到 **整段回复文本** → 用按句切分（如 pysbd 或 Open-LLM-VTuber 已有的 SentenceDivider）→ **按句 yield SentenceOutput**，每句一个。下游仍是 `async for` 消费，TTS 仍可「第一句出来就开读」，体感上为「句级流式」。 | **仅在新 Agent 内写适配**；Open-LLM-VTuber 其余代码不动；MaiBot 不动。 |
| **改 MaiBot 支持流式** | MaiBot 的 Replyer / `express_model.generate_response_async` 改为支持 **流式**（async iterator 或 callback 吐 token/片段）；新 Agent 消费该流，按句缓冲后 yield SentenceOutput。 | **需改 MaiBot**：Replyer 或 LLM 封装暴露流式接口；Open-LLM-VTuber 仍只在新 Agent 内对接，不重写 single_conversation 等。 |

推荐优先采用「不改 MaiBot、整段拿回再按句 yield」：实现简单、兼容现有 MaiBot，且句级流式对 TTS 与前端展示已足够（首句即可开播）。若后续需要「首 token 更快」再考虑在 MaiBot 侧加流式 API。

### 3.4 考虑响应速度：是否增加流式 API，以及还需流式化的地方

**结论：若优先考虑响应速度（首句/首 token 尽早到达 Open-LLM-VTuber），建议在 MaiBot 侧增加流式回复 API；Open-LLM-VTuber 只消费该流，无需改对话编排。其他需要流式的地方仅限 MaiBot 的「回复生成」一条链路。**

#### 3.4.1 推荐整体流程（增加流式 API 时）

```
用户输入进入 Open-LLM-VTuber
  → 转成文本后请求 MaiBot「流式回复 API」（如 POST /api/v1/chat/stream）
  → MaiBot 内部：Planner（一次性）→ 构建 Replyer Prompt → 调用 LLM 流式生成
  → MaiBot 将 LLM 流（token 或按句）写入该 API 的响应体（SSE 或 chunked NDJSON）
  → Open-LLM-VTuber 的 MaiBotStyleAgent 消费该响应流，按句 yield SentenceOutput
  → 下游 TTS/前端 按句消费，首句到达即可开播
```

这样「内容进入 MaiBot → MaiBot 处理 → 请求流式 API → 流式 API 返回给 Open-LLM-VTuber」的链路成立，且由 MaiBot 主动推流，Open-LLM-VTuber 只拉流并转成 SentenceOutput，无需在 VTuber 侧再增加其它流式接口。

#### 3.4.2 MaiBot 侧需要增加/改造的点

| 位置 | 是否需要流式 | 说明 |
|------|--------------|------|
| **流式 API 端点** | **需要新增** | 例如 `POST /api/v1/chat/stream`（或 `/reply/stream`）：入参同「生成回复」（chat_id、目标消息、上下文等），响应为 **Server-Sent Events (SSE)** 或 **Transfer-Encoding: chunked + NDJSON**，每行一个 JSON：`{"type":"delta","text":"..."}` 或 `{"type":"sentence","text":"..."}`。 |
| **Replyer 生成逻辑** | **需要支持流式** | 在「仅回复」路径下，不再只调用 `llm_generate_content` 等整段返回，而是调用 **LLM 的流式接口**（若底层 LLM 支持），将 token/片段写入上述 API 的 response stream；若底层暂不支持流式，可退化为「整段生成完后按句切分再逐句推送」，仍能实现句级流式。 |
| **LLM 封装（如 utils_model）** | **可选但建议** | 增加 `generate_response_stream_async(prompt) -> AsyncIterator[str]`（或按句 yield），供 Replyer 和流式 API 共用；若当前仅 HTTP 调用第三方，需确认该第三方是否支持 stream（如 OpenAI/OpenAI 兼容的 `stream=True`），再在封装层暴露流式迭代器。 |
| **Planner** | **不需要流式** | 规划结果体量小，一次性返回即可；流式 API 内部先 await 规划完成，再对「回复内容」做流式输出。 |

#### 3.4.3 Open-LLM-VTuber 侧

| 位置 | 是否需要流式 | 说明 |
|------|--------------|------|
| **MaiBotStyleAgent** | **仅消费流** | 请求 MaiBot 的流式 API（如 aiohttp/httpx 的 stream 模式或 SSE 客户端），按收到的 delta/sentence 缓冲到句边界后 **yield SentenceOutput**；下游已有 `async for output_item in agent_output_stream`，无需改动。 |
| **single_conversation / process_agent_output / tts_manager** | **不需要改** | 已按流式契约消费 Agent 的 yield，无需增加其它流式接口。 |

#### 3.4.4 其它是否要增加流式的地方

- **MaiBot 内部**：与「回复给 Open-LLM-VTuber」相关的，只有 **回复生成 → 流式 API 响应** 这一段需要流式；Planner、记忆检索、情绪、表达习惯等均为输入侧，不要求流式输出。若未来有「在 MaiBot 自己的 QQ/Web 前端里也想要打字机效果」，可复用同一套 Replyer 流式接口或同一 LLM 流式封装。
- **Open-LLM-VTuber 内部**：对话编排、TTS、WebSocket 推送已是按句/按事件流式处理的，**不需要再增加额外的流式 API**；只需保证 MaiBot 侧流式 API 存在，且 MaiBotStyleAgent 正确消费并转成 SentenceOutput 流即可。

**小结**：为提升响应速度，建议在 MaiBot 增加 **流式回复 API**，MaiBot 处理后把回复内容通过该 API 流式返回给 Open-LLM-VTuber；需要增加流式的地方仅限 MaiBot 的「回复生成 + 该 API 的响应体」，以及（可选）底层 LLM 封装的流式接口；Open-LLM-VTuber 仅在新 Agent 内消费该流，无需重写或新增其它流式流程。

---

## 四、拓展二：更优记忆模块

### 4.1 Open-LLM-VTuber 当前记忆

- `BasicMemoryAgent._memory`：当前会话的 message 列表（user/assistant）。
- `chat_history_manager`：按 `conf_uid`/`history_uid` 的 JSON 文件，仅做持久化与 `get_history` 加载，无检索、无摘要。

### 4.2 可行方案

- **在 Agent 内增强 _to_messages**  
  - 在每次 `_to_messages()` 调用前，从「长期记忆」服务或 MaiBot 记忆模块做一次 **检索**（按当前 query 或最近几句），得到若干片段；  
  - 将片段拼成 system 或一条 user 消息（如「相关记忆：…」）插入 `messages`。  
  - 这样无需改 `AgentInterface`，只需 BasicMemoryAgent（或新子类）多依赖一个「记忆检索接口」。

- **写入长期记忆**  
  - 在 `process_single_conversation` 中，在 `store_message(ai)` 之后，异步调用 MaiBot 或独立记忆服务的「写入/更新」接口（重要事实、用户偏好等）。  
  - 或在新 Agent 的 `chat()` 流结束后，在内部调用写入逻辑。

- **与 MaiBot 记忆对接形态**  
  - 若 MaiBot 与 Open-LLM-VTuber 同机：可直接 import MaiBot 的记忆检索/写入模块（需解耦为可被调用的 API，避免依赖整个 Bot 启动）。  
  - 若跨进程：MaiBot 暴露 HTTP/gRPC，Open-LLM-VTuber 用 `conf_uid`/`client_uid` 等做 session 映射后调用。

结论：**可行**。记忆的「检索 + 注入 prompt」与「写入」都可以在现有单轮 chat 流程内完成，不破坏现有架构。

---

## 五、拓展三：Agent 游玩 Minecraft（AIRI 能力）

### 5.1 AIRI Minecraft 的形态（源码结论）

- **语言/运行时**：TypeScript/Node；独立进程。
- **入口**：`services/minecraft/src/main.ts` → mineflayer 连接 MC 服务器 → `bot.loadPlugin(CognitiveEngine({ airiClient }))`。
- **认知链**：EventBus(raw:* → signal:* → conscious:signal:*) → Brain 消费 conscious 信号 → 构建上下文 → **LLMAgent（xsai）** 生成回复 → 回复文本当作 **JavaScript REPL** 在沙箱执行 → 脚本内调用 `chat()`、`goToPlayer()`、`collectBlocks()` 等 → **TaskExecutor** + **ActionRegistry** 执行 → mineflayer API。
- **与 Stage 的关系**：当前 `airiClient` 仅被传入并保留引用，**未参与认知流程**；LLM 在本进程内通过 xsai 调用。

因此：Minecraft 的「玩」是 **独立 Node 服务**，与 Open-LLM-VTuber（Python）**不同进程、不同语言**。

### 5.2 可行方案（按耦合从松到紧）

**方案 1：双进程并列，通过「桥」连接（推荐）**

- Open-LLM-VTuber 与 AIRI Minecraft 各自运行。
- 用户对 VTuber 说「去挖点木头」→ Open-LLM-VTuber 的 Agent 通过 **MCP 工具** 或 **HTTP 调用** 将「意图」发给 Minecraft 服务（例如 `minecraft_execute` 或 `minecraft_chat`）。
- Minecraft 侧暴露 **小型 HTTP/WebSocket 接口**（或 MCP server）：接收「自然语言指令」或「结构化动作序列」，在 Brain 中入队或直接由 TaskExecutor 执行（需在 AIRI 侧加一层「外部指令 → 内部动作」的转换）。
- 执行结果（成功/失败/日志）通过同一通道回传 Open-LLM-VTuber，再通过 TTS/展示反馈给用户（如「已经挖了 16 个橡木」）。
- **实现要点**：  
  - Open-LLM-VTuber：新增 MCP 工具或调用现有 MCP 的「自定义 server」，工具名如 `minecraft_command`，参数为文本或 JSON。  
  - AIRI Minecraft：在 `main.ts` 或 CognitiveEngine 中增加一个 HTTP/WS 端点，接收指令后调用 Brain 的「注入一条 conscious 事件」或直接调用 TaskExecutor 的封装（若 AIRI 已有类似接口可复用）。

**方案 2：Open-LLM-VTuber 内置「Minecraft 客户端」桥**

- 在 Open-LLM-VTuber 的 Python 侧实现一个 **轻量 Minecraft 桥接服务**：通过 HTTP/WebSocket 与 AIRI Minecraft 进程通信。
- Agent 的 MCP 工具描述中声明「可向 Minecraft 发送指令、查询状态」；工具实现内部请求该桥，桥再请求 AIRI 服务。
- 这样用户与 VTuber 的对话中，LLM 可以决定何时调用「挖矿」「移动」等，由 Minecraft 真正执行；VTuber 负责「说」和「听」，Minecraft 负责「做」。

**方案 3：在 Python 侧复刻「REPL + 动作表」逻辑（不依赖 AIRI 进程）**

- 在 Open-LLM-VTuber 内用 Python 的 mineflayer 等价物（如 `mineflayer-python` 或通过子进程调 Node 脚本）实现一套简化版：  
  - 维护一份「动作注册表」（类似 AIRI 的 llm-actions），LLM 输出为「脚本」或「结构化动作」；  
  - Python 解析并执行这些动作，控制 MC 角色。  
- 优点：单进程、无跨语言；缺点：需在 Python 侧重写/移植 AIRI 的 Brain、规则、技能等，工作量大，且与 AIRI 官方更新脱节。

**小结**：**方案 1（双进程 + 桥接）** 最现实：保留 AIRI Minecraft 完整能力，Open-LLM-VTuber 只负责「理解用户话 + 发指令 + 播报结果」，Minecraft 负责执行；通过 MCP 或 HTTP 定义清晰协议即可。

---

## 六、综合结论与实施顺序建议

### 6.1 是否可行

- **在 Open-LLM-VTuber 上拓展 MaiBot 的对话与记忆**：**可行**。  
  - 通过新 Agent 实现（或扩展现有 Agent）接入 MaiBot 的 Planner/Replyer、情绪、记忆检索与写入，接口清晰（AgentInterface + BatchInput/BaseOutput）。  
- **在 Open-LLM-VTuber 上拓展「Agent 游玩 Minecraft」**：**可行**。  
  - 推荐以 **桥接方式** 连接现有 AIRI Minecraft 服务（MCP 或 HTTP/WebSocket），由 VTuber 负责语音/形象与「发指令 + 播报」，Minecraft 负责游戏内执行。

### 6.2 实施顺序建议

1. **记忆与对话增强（方案 A 或 B）**  
   - 先实现「记忆检索 + 注入 _to_messages」和可选「情绪注入 system」；若需两阶段再上 MaiBotStyleAgent。  
   - 配置上增加「记忆/情绪服务地址」或「使用 MaiBot 模块」的开关。

2. **Minecraft 桥（方案 1）**  
   - 在 AIRI Minecraft 侧加一层「外部指令」HTTP/WS 或 MCP server；  
   - 在 Open-LLM-VTuber 的 MCP 或工具中增加 `minecraft_*` 工具，调用该桥；  
   - 测试「用户说 → VTuber 理解 → 发指令 → MC 执行 → 结果回传 → TTS 播报」闭环。

3. **可选**  
   - 若希望 VTuber 与 Stage 同屏（形象 + 游戏画面），再考虑与 AIRI 前端的集成方式（同源或跨域、事件同步等），与上述后端拓展相对独立。

---

## 七、与《OpenLLM_and_AIRI_and_MaiBot》文档的对应

| 该文档中的项目 | 本分析中的角色 |
|----------------|----------------|
| Open-LLM-VTuber | **基座**：提供语音、Live2D、单轮 chat、MCP；扩展点为 Agent 实现与 MCP/工具。 |
| MaiBot | **能力来源**：对话（两阶段/情绪）、记忆（长期、检索）；以新 Agent 或 Prompt 注入方式接入 VTuber。 |
| AIRI（Minecraft） | **能力来源**：游戏内行为；以独立进程 + 桥接方式接入 VTuber，VTuber 不替代 Brain，只发指令与播报。 |

整体上，**以 Open-LLM-VTuber 为基座拓展另外两项目的优势是可行的**；优先做「记忆 + 对话增强」和「Minecraft 桥」即可在不大改现有架构的前提下，同时获得更好的对话/记忆与「能玩 MC」的体验。

---

## 附录 A：Open-LLM-VTuber 的存储与接入 MaiBot 数据

### A.1 Open-LLM-VTuber 是否有存储

**有，但仅限「对话历史」且为文件存储，无数据库。**

| 项目 | 说明 |
|------|------|
| **存储位置** | `chat_history_manager.py`，路径：`chat_history/{conf_uid}/{history_uid}.json` |
| **存储内容** | 单文件 = 一条「历史」：JSON 数组，每项为 `{ "role": "human"\|"ai", "timestamp": str, "content": str, "name"?, "avatar"? }`；首项可为 `{ "role": "metadata", "timestamp": ... }` |
| **键** | `conf_uid`（角色/配置标识）、`history_uid`（某次会话/历史标识，如 `2025-03-17_12-00-00_{uuid}`） |
| **接口** | `store_message(conf_uid, history_uid, role, content, name?, avatar?)`、`get_history(conf_uid, history_uid)`、`get_history_list(conf_uid)`、`create_new_history(conf_uid)`、`delete_history`、`modify_latest_message`、`rename_history_file` 等 |
| **使用处** | `single_conversation` / `group_conversation` 存/取消息；`BasicMemoryAgent.set_memory_from_history(conf_uid, history_uid)` 用 `get_history` 加载到 `_memory`；WebSocket handler 处理 fetch-and-set-history、create-new-history、delete-history |

没有：用户表、群组表、情绪、长期记忆、检索库等；只有「按 conf + history 的对话列表」。

### A.2 MaiBot 的存储形态（对比）

| 项目 | 说明 |
|------|------|
| **存储** | 数据库（Peewee，SQLite/PostgreSQL/MySQL）：`ChatStreams`、`Messages`、`ActionRecords`、`LLMUsage`、情绪/人物/记忆等表 |
| **消息** | `Messages` 表：`message_id`、`time`、`chat_id`（= stream_id）、`processed_plain_text`、`display_message`、`user_id`、`user_nickname`、`chat_info_stream_id`、`chat_info_group_id`、`reply_to`、`is_mentioned` 等，结构比 VTuber 丰富 |
| **会话** | 按 `stream_id`（= 一个群或一个私聊）维度的消息流，无「history_uid」概念；查历史用 `get_raw_msg_before_timestamp_with_chat(chat_id, timestamp, limit)` 等 |

### A.3 能否「轻松」接入 MaiBot 的数据

**不能即插即用，但通过一层适配可以接入，工作量可控。**

差异与对应关系：

| 维度 | Open-LLM-VTuber | MaiBot | 接入思路 |
|------|------------------|--------|----------|
| **存储** | 本地 JSON 文件 | 数据库 | 在 VTuber 侧做「历史读写」适配器，内部调 MaiBot DB 或 MaiBot 提供的 HTTP API |
| **会话键** | `conf_uid` + `history_uid`（多历史/会话） | `stream_id`（单聊/群一个流） | 映射：`conf_uid` → MaiBot 的 `stream_id`（或固定一个）；`history_uid` 可忽略（只读「当前流」最近 N 条）或映射为时间范围/标签（若 MaiBot 后续支持） |
| **消息结构** | role + content + timestamp + name? + avatar? | 多字段（processed_plain_text、display_message、user_id 等） | 适配器读 MaiBot 的 Messages 时：human = 非 bot 发送，ai = bot 发送；content 取 `processed_plain_text` 或 `display_message`；timestamp 转成 VTuber 需要的格式 |

两种接入方式（二选一或组合）：

**方式 1：在 Open-LLM-VTuber 内做「可插拔历史提供者」**

- 将当前对 `chat_history_manager` 的调用（`get_history`、`store_message`、`create_new_history`、`get_history_list` 等）抽象为接口（如 `ChatHistoryProvider`）。
- 默认实现：沿用现有文件存储。
- MaiBot 适配实现：内部通过 **MaiBot 的 Python 包**（同进程）查 DB：根据 `conf_uid` 解析出 MaiBot 的 `stream_id`，用 `get_raw_msg_before_timestamp_with_chat` 或等价查询得到消息列表，转成 `List[HistoryMessage]`；写入时调用 MaiBot 的 `MessageStorage.store_message` 或直接写 `Messages` 表（需构造 MaiBot 所需字段）。
- 配置项：如 `history_provider: "local" | "maibot"`，若为 `maibot` 则再配 MaiBot DB 连接或 MaiBot 安装路径以便 import。

**方式 2：MaiBot 暴露「历史」HTTP API，VTuber 调 API**

- MaiBot 提供例如：`GET /api/v1/history?stream_id=xxx&limit=100`、`POST /api/v1/history` 存一条消息；返回格式与 VTuber 的 `HistoryMessage` 对齐（role、content、timestamp 等）。
- Open-LLM-VTuber 侧：实现一个 `ChatHistoryProvider`，内部用 aiohttp/httpx 请求上述 API；`conf_uid` 在配置里映射为 MaiBot 的 `stream_id`（或由 MaiBot API 接受 conf_uid 并内部映射）。
- 这样 VTuber 不直接连 MaiBot 的 DB，适合跨进程/跨机部署。

**关于「轻松」**

- **不改 VTuber 业务逻辑**：只需把「直接调 chat_history_manager」改为「调抽象接口」，默认实现仍用现有文件；**工作量小**。
- **适配器实现**：需要做键映射（conf_uid/history_uid ↔ stream_id）、消息格式转换、以及（若同机）MaiBot 依赖与 DB 连接；或（若 HTTP）在 MaiBot 侧加少量 API。**不算「零成本」，但在一个模块内可完成**。
- **MaiBot 多历史**：MaiBot 当前没有「同一 stream_id 下多条独立历史」的概念，若 VTuber 的「多 history_uid」要完全对应到 MaiBot，需在 MaiBot 侧扩展（例如按时间窗口或标签区分），或 VTuber 侧只使用「一个 stream_id 对应一条逻辑历史」（最近 N 条消息）。

**小结**：Open-LLM-VTuber 有存储，但仅限基于文件的对话历史；要接入 MaiBot 的数据，需要一层「历史提供者」适配（键与消息格式映射），不能直接共用同一套存储，但可以较容易地接入。

---

## 附录 B：仅传最新输入 + Observation 与多模态流式

### B.1 VTuber 侧历史的两个用途，与「只传最新输入」的简化

Open-LLM-VTuber 里历史只做两件事：

1. **展示**：前端「对话列表」从 `get_history` / `get_history_list` 取，用于界面展示。
2. **上下文**：`set_memory_from_history` → `get_history` 加载到 Agent 的 `_memory`，在 `_to_messages()` 里和本轮 user 一起送给 LLM。

若**回复完全由 MaiBot 负责**，可以不做「把历史放进上下文」这一步：

- **上下文**：只在 MaiBot 侧维护。VTuber 不再给 Agent 灌历史，只把**本轮的「用户最新输入」**（文本 + 可选图片）传给 MaiBot；MaiBot 用自己的 DB（按 stream_id 的 Messages）拼上下文并生成回复。
- **展示**：若前端仍需看历史，可 (1) 仍用 VTuber 本地存一份（只写不读给模型），或 (2) 由 MaiBot 提供「按 session/stream_id 的历史列表」接口，前端或 VTuber 调该接口做展示。

因此：**可以不用在 VTuber 侧做「把历史放到上下文」**，只把**当前轮的用户输入**传到 MaiBot 即可；会话标识用 VTuber 的 client_uid/session 映射到 MaiBot 的 stream_id，保证 MaiBot 侧上下文是同一会话。

### B.2 用 Observation 做统一入口

把「用户最新输入」抽象成**单条观测（Observation）**，便于统一处理多模态和后续扩展：

- **Observation 建议字段**（示例）：  
  `timestamp`、`session_id`（或 stream_id）、`text?`、`images?`（base64 或 URL 列表）、`audio?`（base64 或 URL）、`video_frames?`（或 video_url）  
  实际可先实现 `text` + `images`，再按需加 `audio` / `video_frames`。

- **VTuber 侧**：在进 Agent 或进 MaiBot 前，把「当前轮」的 ASR 结果、可选图片（以及若支持的音频/视频）打成一条 Observation，再发给 MaiBot 或统一多模态接口。

- **MaiBot 侧**：若当前只支持「文本（+ 图片）」回复，则只消费 Observation 的 `text` 与 `images`；音频/视频要么在 VTuber 先转成文本（或关键帧），要么由下游多模态接口处理（见下）。

### B.3 语音、视频帧怎么处理；是否需要多模态流式接口

**语音**

- **做法一（当前常见）**：在 VTuber 内用 ASR 把语音转成文本，Observation 里只带 `text`（和可选 `images`），发给 MaiBot。MaiBot 不需要多模态，沿用现有「文本入、文本出」即可。
- **做法二**：Observation 带 `audio`，不先在 VTuber 做 ASR，而是把整条 Observation 交给**多模态服务**：由该服务内部做 ASR（或端到端多模态模型），再生成回复并流式返回。此时需要「接受 Observation、流式返回回复」的接口（见下）。

**视频 / 截帧**

- **做法一**：在 VTuber 内对视频做截帧（按间隔或关键帧），得到若干张图，Observation 里带 `images`（或 `video_frames`），不发原始视频。下游若 MaiBot 的模型支持「文本 + 多图」，就调用现有或扩展后的 Replyer（多图入）；若不支持，可先走一个 VLM 把画面描述成文本，再只把文本给 MaiBot。
- **做法二**：Observation 带 `video_frames` 或 `video_url`，由**多模态模型**直接看画面（或抽帧后的图像序列），再生成回复。同样需要多模态流式接口。

**是否需要「多模态的流式接口」**

- **若采用「先在 VTuber 做模态规约」**（语音→ASR→文本，视频→关键帧→图像或 VLM→文本）：  
  下游只需**文本（+ 可选图片）**的流式回复 API（如现有 MaiBot 流式回复），**不必**再设计一套「多模态流式」接口；多模态只发生在 VTuber 或单独预处理服务里。

- **若希望模型端「直接听/看」**（原始或轻度处理的音频、视频帧作为输入）：  
  就需要一个**多模态流式接口**，例如：  
  - 入参：一条 **Observation**（text?、images?、audio?、video_frames?），以及 session_id/stream_id。  
  - 后端：多模态模型（或 ASR + VLM + LLM 管道）消费 Observation，流式生成回复。  
  - 出参：与现有「流式回复」一致（SSE 或 chunked NDJSON，按句/按 token 推送），便于 VTuber 或 MaiBotStyleAgent 消费。

**小结**

- 历史在 VTuber 侧可以**不参与上下文**，只把**用户最新输入**打成 Observation 传到 MaiBot（或统一多模态服务）；展示可仍用 VTuber 本地或 MaiBot 的历史 API。
- 用 **Observation** 做统一入口，便于后续加音频、视频。
- 语音：可在 VTuber 用 ASR 转文本再传，也可把 audio 放进 Observation 交给多模态接口处理。
- 视频：可在 VTuber 截帧成 images 再传，或把 video_frames 放进 Observation 交给多模态接口。
- **是否需要多模态流式接口**：若坚持「模型端直接看/听」则**需要**设计一条「Observation 入、流式回复出」的多模态流式 API；若接受「VTuber 侧先转成文本/图」则用现有「文本（+ 图）流式回复」即可。
