# heart_flow 与 heartbeat_v2 对比与选型

## 一、职责对比

| 维度 | heart_flow（原项目） | heartbeat_v2 |
|------|----------------------|--------------|
| **驱动方式** | 消息驱动：来一条消息 → 触发该会话的心流 | 周期驱动：fast/normal/slow 定时 tick，消费 ingress 队列 |
| **粒度** | 按会话：每个 chat_id 一个 HeartFChatting / BrainChatting 实例 | 全局：一个系统，多会话状态一起参与规划 |
| **回复路径** | 会话主循环轮询**本会话**新消息 → 阈值/提及/频率 → 观察 → ActionPlanner → ActionManager → 发回复 | 规划器根据**全局** state 生成 intent（含 reply）→ executor 执行 → reply_generator_adapter 调原有生成链发回复 |
| **延迟** | 消息到后很快（仅受轮询间隔和阈值影响） | 至少等下一个 tick（normal 约 60s），再叠加规划+执行 |
| **主动行为** | 无：只对本会话消息做反应式回复 | 有：explore（如未读/目标驱动 search）、可分享到其他会话、与 active_goal/experience_store 集成 |
| **实现复杂度** | 相对简单，与现有 ActionPlanner、expression、memory、情绪、频率控制已打通 | 高：planner/queue/executor/router/skill/state_fabric/去重/超时等 |

## 二、当前入口事实

- 在 `src/chat/message_receive/bot.py` 的 `message_process` 中：
  - **已注释**：`heartflow_message_receiver.process_message(message)`（即原 heart_flow 的完整处理）
  - **当前生效**：`get_heartbeat_v2_system().ingest_message(message)`（消息只进 v2 的 ingress）
- 因此**目前所有「是否回复、何时回复」都由 heartbeat_v2 的 tick + 规划器决定**；v2 的 reply intent 再通过 `reply_generator_adapter` 调用原有回复生成与发送逻辑。

## 三、何时用哪个

- **更适合用 heart_flow（用回原来的）**  
  - 产品形态以「单会话、有人说话就尽快回复」为主。  
  - 不需要多会话统一调度、不需要「主动探索」（如定时/目标触发的 search 并分享）。  
  - 希望实现简单、故障面小（例如避免 v2 里 search_web 超时等运维问题）。

- **更适合保留 / 主用 heartbeat_v2**  
  - 需要多会话调度、主动探索、目标驱动搜索与分享、与 memory/dream/experience 的深度集成。  
  - 能接受「至少一个 tick 的延迟」和更高的实现与运维成本。

## 四、若选择「用回原来的」heart_flow

1. **恢复 heart_flow 消息处理**  
   在 `bot.py` 的 `message_process` 中：
   - 取消注释并调用：`await self.heartflow_message_receiver.process_message(message)`  
   - 根据是否还要保留 v2 的「仅主动探索」能力，二选一：  
     - **完全切回 heart_flow**：去掉或注释掉 `get_heartbeat_v2_system().ingest_message(message)`，并在 `main.py` 中不再 `await get_heartbeat_v2_system().start()`，这样回复完全由 heart_flow 驱动。  
     - **混合模式**：保留 v2 的 `start()` 和 slow 循环等，但**不**对普通消息调用 `ingest_message`（或只对特定类型消息注入），让 v2 只做「不依赖即时消息的主动探索」，回复仍由 heart_flow 负责。

2. **依赖关系**  
   - `frequency_control_manager`、`ActionPlanner`、`ChatHistorySummarizer` 等仍被 heart_flow 使用，无需删除。  
   - 若完全停用 v2，可保留 `heartbeat_v2` 代码库不动，仅通过入口切换；若长期不用，再考虑下架或归档。

## 五、若选择「保留 heartbeat_v2」为主路径

- 维持现状，继续做你已在做的优化：规划器提示词压缩、情绪/VAD、超时与可观测性、去重等。  
- 若仍希望保留 heart_flow 的「单会话即时回复」体验，可采用**混合**：  
  - 消息同时走 `heartflow_message_receiver.process_message(message)` 和 `ingest_message(message)`；  
  - 在 heart_flow 侧：照常跑 HeartFChatting/BrainChatting，负责即时回复；  
  - 在 v2 侧：通过 policy_gate 或规划器约束，对「已有 heart_flow 正在处理的会话」少生成或降优 reply intent，让 v2 侧重 explore、retrieve、多会话与目标驱动行为。  
  - 需要防止同一会话被两套同时回复，可通过简单策略（例如「该会话最近 N 秒内已有 heart_flow 回复则 v2 不再 reply」）做协调。

## 六、简短结论

- **heart_flow**：单会话、反应式、实现成熟，适合「只做即时回复、少主动行为」的场景。  
- **heartbeat_v2**：全局、周期、结构化意图与技能编排，适合「多会话 + 主动探索 + 目标驱动」；代价是延迟与复杂度。  
- **是否有必要保留 v2**：取决于你是否要「主动探索、多会话调度、目标驱动」等能力；若不要，用回 heart_flow 更简单稳定；若要，保留 v2 并可与 heart_flow 混合使用，由入口与策略决定谁主谁辅。
