# 示例：接收消息后的话题与 ChatHistory 查询

本文用**一条消息**从进入到被回复的完整路径，说明「话题」如何被记录、以及「ChatHistory」如何在回复时被查询。

---

## 场景假设

- 用户小明在群聊里发了一条消息：「上次我们说的那个百度网盘链接还能再发一下吗？」
- 麦麦决定回复这条消息（Planner 选了 reply 动作）。

---

## 一、消息接收与存储（与话题/History 的间接关系）

```
1. bot.message_process(message_data)                    # src/chat/message_receive/bot.py
   ↓
2. HeartFCMessageReceiver.process_message(message)       # src/chat/heart_flow/heartflow_message_processor.py
   ↓
3. MessageStorage.store_message(message, chat)          # 写入 Messages 表（原始消息）
   ↓
4. heartflow.get_or_create_heartflow_chat(chat.stream_id)
   → 若该 chat 首次出现，会创建 HeartFChatting(stream_id)，并 await chat.start()
   → chat.start() 里会：
      - 启动 _main_chat_loop()
      - await self.chat_history_summarizer.start()      # 启动概括器后台循环
```

**要点**：收到消息后，消息先落库（Messages），心流为该 chat 建好 HeartFChatting 并启动 **ChatHistorySummarizer** 的后台循环。此时**还没有**做话题识别或 ChatHistory 查询。

---

## 二、话题（Topic）是如何被「记录」的

话题**不是**在「收到一条消息」时立刻做的，而是由 **ChatHistorySummarizer** 的**定时循环**拉取一段时间内的新消息，满足条件后再做一次「话题检查」和打包。

### 2.1 定时循环拉取新消息

```text
HeartFChatting.start()
  → chat_history_summarizer.start()                     # src/memory_system/chat_history_summarizer.py
       → _periodic_check_loop():
            while self._running:
                await self.process()                    # 每次循环执行一次
                await asyncio.sleep(self.check_interval) # 默认 60 秒
```

### 2.2 单次 process()：拉新消息 → 更新批次 → 可能触发话题检查

```text
ChatHistorySummarizer.process(current_time)
  │
  ├─ new_messages = message_api.get_messages_by_time_in_chat(
  │      chat_id=self.chat_id,
  │      start_time=self.last_check_time,   # 上次检查时间
  │      end_time=current_time,
  │      ...
  │  )                                      # 从 Messages 表读取本 chat 在时间窗口内的消息
  │
  ├─ 若有新消息：
  │    若已有 current_batch → 把 new_messages 追加进 current_batch.messages
  │    否则 → 新建 MessageBatch(messages=new_messages, start_time=..., end_time=...)
  │    _persist_topic_cache()               # 话题缓存 + 批次时间范围写入 data/hippo_memorizer/{chat_id}.json
  │
  └─ await _check_and_run_topic_check(current_time)
```

也就是说，**小明的这条消息**会在**下一次** summarizer 的 `process()` 被调用时，通过 `get_messages_by_time_in_chat` 被拉进 `current_batch`（或新建 batch）。  
是否立刻做「话题检查」取决于下面 2.3 的触发条件。

### 2.3 话题检查的触发条件

```text
_check_and_run_topic_check(current_time)
  │
  ├─ 条件 1：current_batch 消息数 >= 80  → 触发
  ├─ 条件 2：距上次话题检查 > 8 小时 且 消息数 >= 20  → 触发
  │
  └─ 若触发 → await _run_topic_check_and_update_cache(messages)
```

- 若**未触发**：本轮只更新了 `current_batch` 并持久化，**不会**跑 LLM 做话题识别，也**不会**写 ChatHistory 表。
- 若**触发**：进入 `_run_topic_check_and_update_cache`：
  - 用 LLM 对当前 batch 做话题识别（历史话题列表 + 本批消息 → JSON `{topic, message_indices}`）；
  - 更新内存中的 **topic_cache**（topic → TopicCacheItem：messages、participants、no_update_checks）；
  - 对「满足打包条件」的话题（如 no_update_checks>=3 或 messages 条数>5）调用 `_finalize_and_store_topic` → **写入 ChatHistory 表**。

所以：**「接收信息」之后，话题的写入是异步、批量的**——先存消息，再由 summarizer 定时拉批、满足条件才做话题识别并可能写 ChatHistory。

---

## 三、ChatHistory 是如何被「查询」的（回复时）

当麦麦**决定要回复**小明这条消息时，会走 **reply** 动作 → 生成回复前会先做**记忆检索**，这里才会去**查 ChatHistory 表**。

### 3.1 从回复动作到记忆检索

```text
HeartFChatting._observe(...)
  → action_planner.plan(...) 得到 action_to_use_info（例如包含 reply）
  → _execute_action(reply_action, ...)
       → generator_api.generate_reply(chat_stream=..., reply_message=...)
```

在 **group_generator / private_generator** 里，生成回复 prompt 时会并行准备多块内容，其中一块是**记忆检索**：

```text
build_memory_retrieval_prompt(
    chat_talking_prompt_short,  # 近期对话文本
    sender,                     # 当前发送者，如 "小明"
    target,                     # 目标消息，如 "上次我们说的那个百度网盘链接..."
    self.chat_stream,
    ...
)
  → 在 memory_retrieval.py 中：build_memory_retrieval_prompt(...)
```

### 3.2 记忆检索内部：问题 → ReAct → 工具查 ChatHistory

```text
build_memory_retrieval_prompt(...)
  │
  ├─ 得到 single_question，例如由 LLM 生成或 Planner 提供：
  │     "小明之前提到的百度网盘链接是什么时候说的"
  │
  ├─ result = await _process_single_question(
  │       question=single_question,
  │       chat_id=chat_stream.stream_id,   # 当前聊天流 ID
  │       context=message,
  │       ...
  │   )
  │
  └─ _process_single_question 内部会调用：
        _react_agent_solve_question(question, chat_id, ...)
```

在 **ReAct 循环**里，LLM 可能输出要调用的工具名和参数，例如：

- `search_chat_history(keyword="百度网盘", participant="小明")`
- 或 `get_chat_history_detail(memory_ids="123")`

框架会执行这些工具，并**自动注入 chat_id**（因为工具函数签名里有 `chat_id`）：

```text
# memory_retrieval.py 中执行工具时（约 626–632 行）
sig = inspect.signature(tool.execute_func)
tool_params = tool_args.copy()
if "chat_id" in sig.parameters:
    tool_params["chat_id"] = chat_id   # 当前回复所在聊天流
observation = await tool_instance.execute(**tool_params)
```

### 3.3 工具内部：真正查 ChatHistory 表

```text
# 工具 1：按关键词/参与人查「记忆列表」
search_chat_history(chat_id, keyword="百度网盘", participant="小明")
  → src/memory_system/retrieval_tools/query_chat_history.py
  → 根据配置决定是「仅当前 chat」还是「全局」：
       query = ChatHistory.select().where(ChatHistory.chat_id == chat_id)  # 本地
       # 或全局时不用 chat_id 限制
  → 再按 keyword、participant、start_time、end_time 过滤
  → records = list(query.order_by(ChatHistory.start_time.desc()).limit(50))
  → 返回给 LLM 的是「记忆 ID、主题 theme、关键词」等摘要，供 LLM 决定是否再查详情

# 工具 2：按记忆 ID 查「单条详情」
get_chat_history_detail(chat_id, memory_ids="123")
  → ChatHistory.select().where((ChatHistory.chat_id == chat_id) & (ChatHistory.id.in_([123])))
  → 返回该条的 theme、summary、keywords、key_point、participants 等
```

查到的内容会作为「观察」拼回 ReAct 的对话，LLM 可能继续调工具或调用 `found_answer` 给出最终答案。**最终**，记忆检索模块会把「问题 + 答案」格式化成一段文本，作为 **memory_retrieval** 块拼进回复用的 prompt。  
因此，**ChatHistory 的「读」发生在「准备回复」这一侧，而不是「接收消息」那一侧**。

---

## 四、时间线小结（针对「小明的这一条消息」）

| 时刻 | 发生了什么 |
|------|-------------|
| T0 | 小明发「上次我们说的那个百度网盘链接还能再发一下吗？」→ 消息进 Messages，心流/概括器已启动。 |
| T0+几秒 | 主循环发现需要回复 → Planner 选 reply → `generate_reply` → **记忆检索**：生成问题 → ReAct 调用 **search_chat_history / get_chat_history_detail** → **查询 ChatHistory 表** → 结果拼进 prompt → 生成回复并发出。 |
| T0+约 60s 起 | Summarizer 的 `_periodic_check_loop` 再次执行 `process()`，把这条消息纳入 **current_batch**（若在时间窗口内）；若本批满足「话题检查」条件，会做话题识别并可能把某话题**写入** ChatHistory 表。 |

总结：

- **History 查询**：在**回复前**，由记忆检索通过 **search_chat_history / get_chat_history_detail** 查 **ChatHistory 表**，和「当前收到的这条消息」在同一条回复链路里发生。
- **话题记录**：在**后台定时**，由 Summarizer 的 **process()** 拉取新消息、更新 topic_cache，满足条件时再写 **ChatHistory 表**；所以同一条消息对「话题」的贡献会稍晚一些、以批次形式体现。

---

## 五、代码定位速查

| 目的 | 位置 |
|------|------|
| 消息接收后存库、启动心流与概括器 | `heartflow_message_processor.process_message` → `store_message`；`HeartFChatting.start()` → `chat_history_summarizer.start()` |
| 话题：定时拉新消息、更新批次、写缓存 | `ChatHistorySummarizer._periodic_check_loop` → `process()` → `_check_and_run_topic_check` → `_run_topic_check_and_update_cache`；持久化 `_persist_topic_cache` |
| 话题：打包写入 ChatHistory 表 | `_finalize_and_store_topic` → `_store_to_database` |
| 回复时做记忆检索、查 ChatHistory | `build_memory_retrieval_prompt`（memory_retrieval.py）→ `_process_single_question` → `_react_agent_solve_question` → 工具执行时注入 `chat_id` → `query_chat_history.search_chat_history` / `get_chat_history_detail` → `ChatHistory.select(...)` |

按上述调用链在仓库中搜索对应函数名即可对照实现。
