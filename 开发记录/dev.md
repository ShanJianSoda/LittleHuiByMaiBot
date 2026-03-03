# 项目

项目开发的相关文档路径 `~/docs-src/`、  `~/开发记录/` 、`完整生命周期与模块示例.md`



设计原则：AI-bot 一致性。



## 整理

```
async def process_message(self, message: MessageRecv) -> None:
        """处理接收到的原始消息数据

        主要流程:
        1. 消息解析与初始化
        2. 消息缓冲处理
        3. 过滤检查
        4. 兴趣度计算
        5. 关系处理

        Args:
            message_data: 原始消息字符串
        """
        
src/chat/replyer/replyer_manager 负责在不同场景下选用 group/private generator
```



# TODO

## 通用

- [x] “对方正在输入” 消息支持以及识别

  日志

  ```
  # napcat
  02-27 08:17:38 [info] 小绘精灵 | [Notice] [输入状态] 1962560763 对方正在输入...
  02-27 08:17:43 [info] 小绘精灵 | [Notice] [输入状态] 1962560763 对方正在输入...
  02-27 08:17:48 [info] 小绘精灵 | [Notice] [输入状态] 1962560763
  02-27 08:17:50 [info] 小绘精灵 | [Notice] [输入状态] 1962560763
  
  
  # adapter
  2026-02-27 08:17:38 | WARNING  | src.recv_handler.notice_handler:handle_notice:108 - 不支持的notify类型: notify.input_status
  2026-02-27 08:17:38 | WARNING  | src.recv_handler.notice_handler:handle_notice:130 - notice处理失败或不支持
  ```

- [ ] 表情包管理&识别，能否识别表情包有名称的 （因为识别的结果不好） or 优化表情包识别提示词

- [ ] 02.04 已梳理：整个逻辑链路大致清楚 ”MaiBot 完整生命周期与 src 模块示例“

  在构建情绪系统的时候，情绪计算涉及到 用户关系 权重

  用户关系模块已存在，具体见**人物关系系统**

  02.05 已梳理：查看关系系统的生命周期，发现读和写是分开的，

  然后查看到**记忆模块**，有较完整的检索逻辑和强大的搜索，但是返回逻辑似乎不太好（ReAct Agent + Tool工具调用search_chat_history + get_chat_history_detail），并且和memory_point似乎没关系
  
- [ ] **心跳系统** 项目中是否有反向获取指定聊天对象的接口？ 是否需要写个表存储 群\个人、基本信息 与 chatid（？）的对应

- [ ] **心跳系统** 心跳间隔受**情绪系统**影响

- [ ] 未来拓展 是否需要写一个统一的接口类(抽象类、工厂？），能对表进行基础的curd操作，让项目自主性最大？

- [ ] （部分）属性应该具有时间性，记忆更新也应该有时间性





## 增加情绪系统

参考index-TTS的情绪维度：**提供一个8浮点数列表来指定每种情绪的强度，顺序如下：[快乐，愤怒，悲伤，害怕，厌恶，忧郁，惊讶，平静]** 

 `[happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]`

**理解过程**

- ~~优化提示词（丰富上下文 添加记忆 更真实）~~
- 借鉴TTS的八维度向量，要求：**随机初始化（有偏好）、 连续性（间隔n秒更新，有占比：聊天内容，自身状态，外界（留拓展））、 有函数约束（防止跳跃）...**
- 迭代：影响情绪的上下文（当前，相关记忆（类似的事，相关的人），先前的情绪） 
- 初步过程： 信息 -> 情绪变化 -> 影响记忆检索 -> 影响回复



**原mood_manager步骤**

1. 初始化成功 `await mood_manager.start()`
2. 生命周期

```
# fork项目中的工作逻辑

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

3. 优化方向文档：

   相关文档路径 `src/mood/`

   `情绪模块开发文档V1.md`

   ...

   本仓库整理与结论（`开发记录/`）：
   - `情绪模块开发整理.md`：需求、生命周期、7 条规划与 VAD 数据说明
   - `情绪模块-开发文档合理性分析.md`：与 README_框架 / 实现对照及下一步建议

4. 优化方向规划
   1. 需要构建更好的情绪系统，以及可能需要的api（在plugin_system/apis）
      1. 完成 情绪系统V1版
      2. **new** VAD 词典，由llm根据历史话题和个性等权重增减VAD控制，能够在**~~概括器~~**和**做梦模块**一起更新（他太好用了你知道吗）
      3. 每个 message 的 VAD：可在接收时计算。**复杂度澄清**：单条消息为 O(消息词数)（分词 + 逐词查表）；O(20000) 指词典规模/加载成本。配合概率更新与时间间隔即可控频。
   2. 情绪记录需要有个单独的表，记录每一次更改，用处：可以画很酷的曲线（？），可以做实时情绪熔岩灯，可以用到chat_history系统获取当时话题的情绪。涉及更改：database模块，config模块
      1. 设计VAD model，以及常用方法（比如 加权），为DatabaseMessages（Model）、chat_history（列表） 等
   3. 需要对chat_history构建（新增）情绪值（VAD or），记录当前话题的情绪值，高情绪值高唤醒率（相对）。写在chat_history_summarizer概括器中
   4. 需要对记忆系统增加情绪模块的影响权重。写在memory_system记忆系统
   5. 情绪系统需要有回中性，更新受（Pre-VAD，关系模块，当前接收消息影响（需要缓冲和过滤，避免消息接收频繁导致的高频更新））权重影响
   6. 情绪迭代需要用户反馈（类似于表达方式学习反馈）
   7. VAD迭代防止过拟合（如何优化细化迭代模块） 



## 增强记忆系统

> [!TIP]
>
> 先了解原项目的memory_system，再了解fork的memory_graph，和OpenClaw的三层记忆



- [ ] add：将查询指定时间段消息、查询指定发言人消息、查询模糊消息内容放到工具类供AI调用
- [ ] add：增加主动思考+（联网查询+存储消息）（已有，但是只是存储到message里）



## 人物关系系统

`self.build_relation_info(chat_talking_prompt_half, sender)` 只在私聊中生效，还被注释掉了（？）

`store_person_memory_from_answer` 构建memory_point，未调用

topic，读的部分在memory模块



## 心跳模块

> [!TIP]
>
> 构建一个有趣的心跳模块

主动思考



## PLMM模块

> [!TIP]
>
> 该系统当做拓展知识，书籍来看待

- [ ] 增加原神、崩铁、绝区零、败犬、素晴，等相关知识

- [ ] 能够自主构建 - 涉及**心跳模块**的主动思考



## Filter

初版方案：bot输出后 增加一层单独的过滤器？这样可以不用重启bot（或者热部署）

联网搜索过滤



## 更高级、智能

> [!TIP]
>
> 但这并不是一个陪伴型AI的优先项





# 部署

```
cd MaiBot
# 启动一个screen
screen -S mmc2
# 运行mmc
uv run python3 bot.py

cd ../MaiBot-Napcat-Adapter
screen -S mmc-adapter
# 运行adapter
uv run python3 main.py

# 俩目录都有napcat，操作失误了
cd ../../../
screen -S napcat
# 第一次运行 curl -o napcat.sh https://raw.githubusercontent.com/NapNeko/napcat-linux-installer/refs/heads/main/install.sh && bash napcat.sh
# 运行napcat
sudo bash ./launcher.sh

# 退出
Ctrl + D
```



```
# 1. 
sudo nano /etc/systemd/system/bot.service

# 复制内容
[Unit]
Description=LittleHui Bot Service
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=root 
WorkingDirectory=/root/LittleHuiByMaiBot # 项目目录
ExecStart=/root/.local/bin/uv run python3 bot.py # 脚本
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target

# 2. 重载配置
sudo systemctl daemon-reload

# 3. 重启
sudo systemctl restart bot

systemctl status bot
```





## 远程ssh

```
ssh -L 18001:127.0.0.1:8001 root@112.124.67.104

# 下载日志
scp -r root@112.124.67.104:~/LittleHuiByMaiBot/logs/app_20260227_143538.log.jsonl C:\Users\276912\Downloads

scp -r root@112.124.67.104:~/napcat/logs/2026-02-27_08-05-50.690.log C:\Users\276912\Downloads


```



## 服务器同步

```
git stash push -m "tmmp"

git pull --no-rebase origin dev

git stash pop

git add . && git commit -m "update from cloud"

git push origin dev

```

```
# 取消上一次提交，退回到add
git reset --soft HEAD~1
```



### 后续扩展方向

1. **P1**: LLM 驱动的主动发言内容生成
2. **P1**: 工具调用集成（联网搜索、记忆检索）
3. **P2**: 更复杂的注意力得分算法（考虑关系亲密度、话题兴趣等）
4. **P2**: 思考模式的状态机优化
5. **P3**: WebUI 可视化展示（实时状态、聊天列表、注意力热力图）
6. **P3**: 学习用户的作息时间，调整心跳频率



### 二、心跳系统与“消息总结 / 做梦”等系统的关系

#### 2.1 潜在冲突点

- **资源层面**：
  - 心跳系统：会周期性触发 LLM（主动思考、主动发言）、数据库查询（扫描多 chat 状态）。
  - 做梦系统：会在后台大批量检索消息 / 记忆，生成“梦境”或长文本。
  - 消息总结：会定期拉取历史消息做摘要。
  - 如果三者各自起独立定时任务，可能出现：
    - 某些时间段集中消耗 LLM / DB 资源，带来延迟或限流。

- **行为层面**：
  - 做梦系统和心跳系统都可能在“无新消息时主动发言”：
    - 做梦：基于长记忆 / 夜间策略输出内容。
    - 心跳：基于冷场 / 输入状态 / 戳一戳等观察信号主动开口。
  - 若无协调，表现为：
    - 同一时间片内多次“不同风格”的主动输出，降低人格一致性。

#### 2.2 统一调度思路

为避免冲突，建议将所有“主动行为请求”集中到一个调度中心（可由心跳系统承担）：

- 各子系统（做梦、affinity 插件、总结器等）不直接“自己发消息”，而是：
  - 向调度器提交：`request_proactive_action(source, chat_id, intent_type, meta)`
    - `source`: `"heartbeat" | "dream" | "affinity" | "summary" | ...`
    - `intent_type`: `"small_talk" | "care_long_typing" | "share_dream" | ...`
    - `meta`: 附加信息（如最近行为、记忆检索结果摘要）。
- 调度器统一考虑：
  - 当前 chat 的冷却时间（`min_proactive_interval_seconds`）
  - 最近是否已经有主动发言
  - 各 `source` 的优先级 / 权重
  - 当前全局负载（LLM 调用频率、队列长度等）
- 决定：
  - 执行哪个 source 的请求
  - 何时执行（立即 / 延时）
  - 是否合并多个 intent（例如“冷场 + 做梦”组合成一条更自然的输出）。

这样，心跳系统扮演的是“主动行为总调度 + 全局节奏控制”的角色，而做梦 / 总结 / affinity 插件更多是“提供候选想法”的角色。

