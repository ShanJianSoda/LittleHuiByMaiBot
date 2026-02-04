```
1. 初始化阶段
   └─ MoodManager.start() 启动情绪回归后台任务
   └─ ChatMood 懒加载初始化（第一次使用时）

2. 情绪更新阶段（写入操作）
   └─ message_handler._preprocess_message()
      └─ chat_mood.update_mood_by_message()
         └─ 根据 interest_rate 和时间概率决定是否更新
         └─ 调用 LLM 生成新的情绪状态
         └─ 更新 self.mood_state 和 self.last_change_time

3. 情绪使用阶段（读取操作）
   ├─ plan_filter._build_prompt()
   │  └─ 读取 mood_state，影响动作决策
   │
   ├─ default_generator.build_prompt_reply_context()
   │  └─ 读取 mood_state，影响回复生成
   │
   ├─ default_generator.build_prompt_rewrite_context()
   │  └─ 读取 mood_state，影响回复重写
   │
   ├─ proactive_thinking_executor.gather_context()
   │  └─ 读取 mood_state，影响主动思考决策
   │
   └─ planner.get_mood_stats()
      └─ 读取完整情绪统计信息（用于查询）

4. 后台维护阶段
   └─ MoodRegressionTask.run()
      └─ 每30秒执行一次
      └─ 检查超过3分钟未变化的聊天流
      └─ 调用 chat_mood.regress_mood() 情绪回归
      └─ 调用 LLM 让情绪回归平静
```

### 关键设计特点：

1. 单一写入点： message_handler._preprocess_message 是唯一更新情绪状态的地方

1. 多处读取点： 多个模块读取情绪状态作为决策上下文

1. 概率性更新： 并非每条消息都会更新情绪，取决于兴趣度和时间间隔

1. 懒加载机制： ChatMood 在首次使用时才初始化

1. 后台回归： 情绪会随着时间自动回归平静

1. 失眠机制： 某些聊天流可以被锁定，禁止情绪更新（mood_manager.insomnia_chats）

这个设计确保了情绪系统的一致性和可控性，同时支持多个模块的情绪感知需求。