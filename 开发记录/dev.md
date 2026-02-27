# 项目

项目开发的相关文档路径 `/docs-src`

`完整生命周期与模块示例.md`



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

- [ ] “对方正在输入” 消息支持以及识别

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

​	放在规划器中还是

​	

​	

- [ ] 表情包管理&识别，能否识别表情包有名称的 （因为识别的结果不好） or 优化表情包识别提示词

- [ ] 02.04 已梳理：整个逻辑链路大致清楚 ”MaiBot 完整生命周期与 src 模块示例“

  在构建情绪系统的时候，情绪计算涉及到 用户关系 权重

  用户关系模块已存在，具体见**人物关系系统**

  02.05 已梳理：查看关系系统的生命周期，发现读和写是分开的，

  然后查看到**记忆模块**，有较完整的检索逻辑和强大的搜索，但是返回逻辑似乎不太好（ReAct Agent + Tool工具调用search_chat_history + get_chat_history_detail），并且和memory_point似乎没关系







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
scp -r root@112.124.67.104:~/LittleHuiByMaiBot/logs/日志文件 C:\Users\276912\Downloads

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

---

# 通知消息处理 - 开发文档

**创建时间**: 2026-02-05  
**状态**: 设计阶段  
**优先级**: P1 (重要)

## 一、需求分析

### 1.1 背景

当前系统已接收到 NapCat 发送的 `input_status` 通知消息，但仅做了日志记录，未进行业务处理。为了实现更真实的交互体验，需要将"用户正在输入"作为一种观察信号，供心跳系统和情绪系统使用。

### 1.2 关键约束

1. **群聊限制**: 群聊中没有"用户正在输入"的 notice，仅私聊场景有效
2. **频率控制**: 需要在可配置时间窗口内（默认 30 秒）去重，避免频繁触发
3. **被动记录**: 不主动响应该通知，仅作为历史记录供心跳系统读取
4. **情绪系统**: 如果将其作为消息存储，会自动触发情绪更新（需评估是否合理）

### 1.3 核心目标

- ✅ 记录"用户正在输入"事件到数据库
- ✅ 为心跳系统提供历史查询能力
- ✅ 支持基于输入行为的主动发言决策
- ⚠️ 避免对情绪系统造成不必要的干扰

## 二、架构设计

### 2.1 数据流向

```
NapCat (input_status)
    ↓
Adapter (转发)
    ↓
main.py (register_custom_message_handler)
    ↓
bot.py::input_status_process (当前: 仅日志)
    ↓ [新增]
MessageStorage (存储为特殊消息类型)
    ↓
Messages 表 (新增字段标识)
    ↓
心跳系统 (定期查询历史)
    ↓
主动发言决策
```

### 2.2 存储方案对比

#### 方案 A: 作为特殊消息存储到 Messages 表 (推荐)

**优点**:
- 复用现有消息存储逻辑
- 自动获得时间戳、chat_id 等索引
- 可以与普通消息统一查询和分析
- 自动进入情绪系统（可通过配置控制）

**缺点**:
- 会触发情绪更新（需要在情绪模块中特殊处理）
- 可能污染消息历史（需要过滤标记）

**实现要点**:
```python
# 在 Messages 表中新增字段
is_input_status_notice = BooleanField(default=False)  # 标识为输入状态通知

# 在 storage.py 中特殊处理
if message.is_input_status_notice:
    # 不计算 VAD
    # 不触发关键词提取
    # 标记为系统消息
```

#### 方案 B: 创建独立的 InputStatusEvents 表

**优点**:
- 数据结构清晰，不污染消息表
- 可以设计专门的查询接口
- 不会触发情绪系统

**缺点**:
- 需要额外的表结构和查询逻辑
- 与消息历史割裂，查询时需要 JOIN
- 增加维护成本

**表结构**:
```python
class InputStatusEvents(BaseModel):
    event_id = TextField(primary_key=True)
    chat_id = TextField(index=True)
    user_id = TextField(index=True)
    timestamp = DoubleField(index=True)
    is_typing = BooleanField()  # True=开始输入, False=停止输入
    duration = FloatField(null=True)  # 输入持续时间（秒）
```

### 2.3 推荐方案: **方案 A (特殊消息存储)**

理由:
1. 实现成本低，复用现有基础设施
2. 心跳系统可以统一查询消息历史
3. 情绪系统的干扰可以通过配置和过滤解决
4. 未来可以轻松扩展其他类型的通知消息

## 三、实现方案

### 3.1 配置项新增

**文件**: `src/config/official_configs.py`

```python
@dataclass
class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""
    
    ban_words: set[str] = field(default_factory=lambda: set())
    ban_msgs_regex: list[str] = field(default_factory=lambda: [])
    
    # 新增: 输入状态通知配置
    enable_input_status_tracking: bool = True
    """是否启用输入状态跟踪"""
    
    input_status_dedup_window: int = 30
    """输入状态去重时间窗口（秒），同一用户在此时间内的重复通知将被忽略"""
```

**配置文件**: `config/bot_config.toml`

```toml
[message_receive]
# ... 现有配置 ...

# 输入状态通知跟踪
enable_input_status_tracking = true  # 是否启用输入状态跟踪
input_status_dedup_window = 30       # 去重时间窗口（秒）
```

### 3.2 数据库模型扩展

**文件**: `src/common/database/database_model.py`

```python
class Messages(BaseModel):
    # ... 现有字段 ...
    
    # 新增: 标识特殊消息类型
    is_input_status_notice = BooleanField(default=False)
    """标识该记录为"用户正在输入"通知，而非真实消息"""
    
    input_status_typing = BooleanField(null=True)
    """输入状态: True=正在输入, False=停止输入, None=非输入状态通知"""
```

**数据库迁移**:
```python
# 在 src/common/database/database.py 或迁移脚本中
def migrate_add_input_status_fields():
    """添加输入状态相关字段"""
    from peewee import BooleanField
    from src.common.database.database_model import Messages
    
    migrator = SqliteMigrator(db)
    
    migrate(
        migrator.add_column('messages', 'is_input_status_notice', BooleanField(default=False)),
        migrator.add_column('messages', 'input_status_typing', BooleanField(null=True)),
    )
```

### 3.3 消息处理逻辑

**文件**: `src/chat/message_receive/bot.py`

```python
class ChatBot:
    def __init__(self):
        self.bot = None
        self._started = False
        self.heartflow_message_receiver = HeartFCMessageReceiver()
        
        # 新增: 输入状态去重缓存 {chat_id: last_timestamp}
        self._input_status_cache: Dict[str, float] = {}
    
    async def input_status_process(self, raw_data: Dict[str, Any]) -> None:
        """
        处理输入状态通知
        
        策略:
        1. 仅记录"正在输入"事件（停止输入不记录）
        2. 在配置的时间窗口内去重
        3. 作为特殊消息存储到 Messages 表
        4. 不触发情绪更新和关键词提取
        """
        from src.config.config import global_config
        
        # 检查是否启用
        if not global_config.message_receive.enable_input_status_tracking:
            return
        
        message_data: Dict[str, Any] = raw_data.get("content", {})
        if not message_data or message_data.get("type") != "input_status":
            return
        
        user_id = message_data.get("user_id")
        group_id = message_data.get("group_id")
        is_typing = message_data.get("is_typing", False)
        platform = message_data.get("platform", "qq")
        
        # 群聊中没有输入状态通知，理论上不会走到这里
        if group_id:
            logger.debug(f"[输入状态] 群聊中收到输入状态通知（异常），忽略: 群 {group_id}")
            return
        
        # 仅记录"正在输入"事件
        if not is_typing:
            logger.debug(f"[输入状态] 用户 {user_id} 停止输入，不记录")
            return
        
        # 构造 chat_id
        chat_id = f"{platform}_private_{user_id}"
        
        # 去重检查
        current_time = time.time()
        dedup_window = global_config.message_receive.input_status_dedup_window
        
        if chat_id in self._input_status_cache:
            last_time = self._input_status_cache[chat_id]
            if current_time - last_time < dedup_window:
                logger.debug(
                    f"[输入状态] 用户 {user_id} 在 {dedup_window}s 内重复输入，去重"
                )
                return
        
        # 更新缓存
        self._input_status_cache[chat_id] = current_time
        
        # 清理过期缓存（避免内存泄漏）
        expired_keys = [
            k for k, v in self._input_status_cache.items()
            if current_time - v > dedup_window * 2
        ]
        for k in expired_keys:
            del self._input_status_cache[k]
        
        logger.info(f"[输入状态] 用户 {user_id} 正在输入，记录到数据库")
        
        # 构造特殊消息对象
        try:
            # 获取或创建聊天流
            from src.chat.message_receive.chat_stream import get_chat_manager
            from maim_message import UserInfo
            
            user_info = UserInfo(
                user_id=user_id,
                user_nickname=message_data.get("user_nickname", f"用户{user_id}"),
                platform=platform,
            )
            
            chat = await get_chat_manager().get_or_create_stream(
                platform=platform,
                user_info=user_info,
                group_info=None,
            )
            
            # 构造伪消息数据
            fake_message_data = {
                "message_info": {
                    "message_id": f"input_status_{int(current_time * 1000)}",
                    "time": current_time,
                    "platform": platform,
                    "user_info": {
                        "user_id": user_id,
                        "user_nickname": user_info.user_nickname,
                        "platform": platform,
                    },
                    "group_info": None,
                },
                "message_segment": {
                    "type": "text",
                    "data": "[用户正在输入...]",
                },
            }
            
            # 创建 MessageRecv 对象
            from src.chat.message_receive.message import MessageRecv
            input_status_message = MessageRecv(fake_message_data)
            input_status_message.chat_stream = chat
            input_status_message.processed_plain_text = "[用户正在输入...]"
            
            # 标记为输入状态通知
            input_status_message.is_input_status_notice = True
            input_status_message.input_status_typing = True
            input_status_message.is_notify = True  # 标记为通知消息，避免触发正常流程
            
            # 存储到数据库
            from src.chat.message_receive.storage import MessageStorage
            await MessageStorage.store_message(input_status_message, chat)
            
            logger.debug(f"[输入状态] 成功记录用户 {user_id} 的输入状态")
            
        except Exception as e:
            logger.error(f"[输入状态] 记录输入状态时出错: {e}")
            logger.error(traceback.format_exc())
```

### 3.4 存储逻辑调整

**文件**: `src/chat/message_receive/storage.py`

```python
class MessageStorage:
    @staticmethod
    async def store_message(message: Union[MessageSending, MessageRecv], chat_stream: ChatStream) -> None:
        """存储消息到数据库"""
        try:
            # 通知消息不存储（但输入状态通知例外）
            if isinstance(message, MessageRecv) and message.is_notify:
                # 输入状态通知需要存储
                if not getattr(message, 'is_input_status_notice', False):
                    logger.debug("通知消息，跳过存储")
                    return
            
            # ... 现有代码 ...
            
            if isinstance(message, MessageRecv):
                # ... 现有字段赋值 ...
                
                # 新增: 输入状态通知字段
                is_input_status_notice = getattr(message, 'is_input_status_notice', False)
                input_status_typing = getattr(message, 'input_status_typing', None)
                
                # 输入状态通知不计算 VAD
                if is_input_status_notice:
                    emotion_v, emotion_a, emotion_d = None, None, None
                    key_words = ""
                    key_words_lite = ""
                    interest_value = 0  # 不参与兴趣度计算
                else:
                    # 正常消息的 VAD 计算
                    emotion_v, emotion_a, emotion_d = _compute_message_vad(filtered_processed_plain_text)
                    # ... 现有关键词提取逻辑 ...
            
            # 创建数据库记录
            Messages.create(
                # ... 现有字段 ...
                is_input_status_notice=is_input_status_notice if isinstance(message, MessageRecv) else False,
                input_status_typing=input_status_typing if isinstance(message, MessageRecv) else None,
            )
            
        except Exception as e:
            logger.error(f"存储消息失败: {e}")
            traceback.print_exc()
```

### 3.5 情绪系统适配

**文件**: `src/chat/heart_flow/heartflow_message_processor.py`

```python
async def process_message(self, message: MessageRecv) -> None:
    """处理接收到的原始消息数据"""
    try:
        # 通知消息不处理
        if message.is_notify:
            logger.debug("通知消息，跳过处理")
            return
        
        # ... 现有代码 ...
        
        # P1 接入：消息入库后触发情绪更新
        if getattr(global_config.mood, "enable_mood", True):
            try:
                db_messages = find_messages(
                    message_filter={
                        "chat_id": chat.stream_id,
                        "message_id": message.message_info.message_id,
                    },
                    limit=1,
                    limit_mode="latest",
                    filter_bot=False,
                    filter_command=False,
                )
                if db_messages:
                    db_message = db_messages[-1]
                    
                    # 新增: 输入状态通知不触发情绪更新
                    if getattr(db_message, 'is_input_status_notice', False):
                        logger.debug("输入状态通知，跳过情绪更新")
                        return
                    
                    # ... 现有情绪更新逻辑 ...
```

**文件**: `src/common/message_repository.py`

```python
def find_messages(
    message_filter: Optional[Dict[str, Any]] = None,
    limit: int = 20,
    limit_mode: str = "latest",
    filter_bot: bool = True,
    filter_command: bool = True,
    filter_input_status: bool = True,  # 新增: 默认过滤输入状态通知
    # ... 其他参数 ...
) -> List[DatabaseMessages]:
    """查询消息"""
    
    query = Messages.select()
    
    # ... 现有过滤逻辑 ...
    
    # 新增: 过滤输入状态通知
    if filter_input_status:
        query = query.where(Messages.is_input_status_notice == False)
    
    # ... 其余查询逻辑 ...
```

### 3.6 心跳系统查询接口

**文件**: `src/chat/heartbeat/heartbeat_system.py` (新建)

```python
"""
心跳系统 - 定时 + 随机触发的主动思考机制

负责:
1. 定期检查聊天状态（冷场、输入行为等）
2. 基于观察信号决策是否主动发言
3. 触发主动思考流程
"""

import time
from typing import List, Dict, Any, Optional
from src.common.logger import get_logger
from src.common.database.database_model import Messages
from src.common.message_repository import find_messages

logger = get_logger("heartbeat")


class HeartbeatSystem:
    """心跳系统"""
    
    @staticmethod
    def get_recent_input_status_events(
        chat_id: str,
        time_window: int = 120,  # 默认查询最近 120 秒
    ) -> List[Dict[str, Any]]:
        """
        查询指定聊天流最近的输入状态事件
        
        Args:
            chat_id: 聊天流 ID
            time_window: 时间窗口（秒）
        
        Returns:
            输入状态事件列表，按时间倒序
        """
        current_time = time.time()
        cutoff_time = current_time - time_window
        
        try:
            # 查询输入状态通知
            query = (
                Messages
                .select()
                .where(
                    (Messages.chat_id == chat_id) &
                    (Messages.is_input_status_notice == True) &
                    (Messages.time >= cutoff_time)
                )
                .order_by(Messages.time.desc())
            )
            
            events = []
            for msg in query:
                events.append({
                    "message_id": msg.message_id,
                    "time": msg.time,
                    "user_id": msg.chat_info_user_id,
                    "is_typing": msg.input_status_typing,
                    "age": current_time - msg.time,  # 距今多少秒
                })
            
            return events
            
        except Exception as e:
            logger.error(f"查询输入状态事件失败: {e}")
            return []
    
    @staticmethod
    def analyze_input_behavior(chat_id: str) -> Dict[str, Any]:
        """
        分析用户输入行为模式
        
        返回:
        {
            "has_recent_input": bool,  # 最近是否有输入
            "input_count_60s": int,    # 60秒内输入次数
            "input_count_120s": int,   # 120秒内输入次数
            "last_input_time": float,  # 最后一次输入时间
            "suggest_proactive": bool, # 是否建议主动发言
            "reason": str,             # 建议原因
        }
        """
        events_60s = HeartbeatSystem.get_recent_input_status_events(chat_id, 60)
        events_120s = HeartbeatSystem.get_recent_input_status_events(chat_id, 120)
        
        analysis = {
            "has_recent_input": len(events_60s) > 0,
            "input_count_60s": len(events_60s),
            "input_count_120s": len(events_120s),
            "last_input_time": events_120s[0]["time"] if events_120s else None,
            "suggest_proactive": False,
            "reason": "",
        }
        
        # 决策逻辑
        if len(events_60s) >= 2:
            # 60秒内连续2次以上输入，但没有发送消息
            last_real_message = find_messages(
                message_filter={"chat_id": chat_id},
                limit=1,
                limit_mode="latest",
                filter_bot=True,
                filter_command=True,
                filter_input_status=True,  # 排除输入状态通知
            )
            
            if last_real_message:
                time_since_last_message = time.time() - last_real_message[0].time
                
                if time_since_last_message > 60:
                    analysis["suggest_proactive"] = True
                    analysis["reason"] = (
                        f"用户在60秒内{len(events_60s)}次开始输入但未发送消息，"
                        f"距上次消息已{time_since_last_message:.0f}秒，可能需要关心"
                    )
        
        elif len(events_120s) >= 3:
            # 120秒内3次以上输入，可能在犹豫
            analysis["suggest_proactive"] = True
            analysis["reason"] = (
                f"用户在120秒内{len(events_120s)}次开始输入，"
                f"可能在犹豫或需要帮助"
            )
        
        return analysis


# 全局实例
heartbeat_system = HeartbeatSystem()
```

### 3.7 MessageRecv 模型扩展

**文件**: `src/chat/message_receive/message.py`

```python
class MessageRecv:
    def __init__(self, message_data: Dict[str, Any]):
        # ... 现有初始化 ...
        
        # 新增: 输入状态通知标识
        self.is_input_status_notice: bool = False
        self.input_status_typing: Optional[bool] = None
```

## 四、使用示例

### 4.1 心跳系统中的使用

```python
# 在心跳系统的定时任务中
async def heartbeat_check_task():
    """心跳检查任务（每30秒执行一次）"""
    
    from src.chat.heartbeat.heartbeat_system import heartbeat_system
    from src.chat.message_receive.chat_stream import get_chat_manager
    
    # 遍历所有活跃的私聊
    for chat in get_chat_manager().get_all_private_chats():
        chat_id = chat.stream_id
        
        # 分析输入行为
        analysis = heartbeat_system.analyze_input_behavior(chat_id)
        
        if analysis["suggest_proactive"]:
            logger.info(f"[心跳] {chat_id} 建议主动发言: {analysis['reason']}")
            
            # 触发主动发言流程
            await trigger_proactive_speak(
                chat_id=chat_id,
                reason=analysis["reason"],
                context={
                    "input_count_60s": analysis["input_count_60s"],
                    "input_count_120s": analysis["input_count_120s"],
                }
            )
```

### 4.2 查询输入状态历史

```python
# 在规划器或决策模块中
from src.chat.heartbeat.heartbeat_system import heartbeat_system

# 获取最近60秒的输入状态事件
events = heartbeat_system.get_recent_input_status_events(chat_id, time_window=60)

for event in events:
    print(f"用户 {event['user_id']} 在 {event['age']:.0f} 秒前开始输入")
```

## 五、测试计划

### 5.1 单元测试

```python
# tests/test_input_status.py

import pytest
import time
from src.chat.message_receive.bot import ChatBot

@pytest.mark.asyncio
async def test_input_status_dedup():
    """测试输入状态去重"""
    bot = ChatBot()
    
    # 模拟第一次输入
    await bot.input_status_process({
        "content": {
            "type": "input_status",
            "user_id": "123456",
            "is_typing": True,
            "platform": "qq",
        }
    })
    
    # 立即再次输入（应该被去重）
    await bot.input_status_process({
        "content": {
            "type": "input_status",
            "user_id": "123456",
            "is_typing": True,
            "platform": "qq",
        }
    })
    
    # 查询数据库，应该只有一条记录
    from src.common.database.database_model import Messages
    count = Messages.select().where(
        Messages.is_input_status_notice == True
    ).count()
    
    assert count == 1, "去重失败，应该只有一条记录"

@pytest.mark.asyncio
async def test_heartbeat_analysis():
    """测试心跳系统分析"""
    from src.chat.heartbeat.heartbeat_system import heartbeat_system
    
    chat_id = "qq_private_123456"
    
    # 模拟多次输入
    # ... 插入测试数据 ...
    
    # 分析输入行为
    analysis = heartbeat_system.analyze_input_behavior(chat_id)
    
    assert analysis["input_count_60s"] >= 2
    assert analysis["suggest_proactive"] == True
```

### 5.2 集成测试

1. **场景1: 用户频繁输入但不发送**
   - 模拟用户在60秒内3次开始输入
   - 验证心跳系统建议主动发言
   - 验证主动发言内容合理

2. **场景2: 去重机制**
   - 模拟用户在30秒内多次输入
   - 验证只记录一次

3. **场景3: 情绪系统隔离**
   - 记录输入状态通知
   - 验证情绪系统未被触发

## 六、配置示例

```toml
# config/bot_config.toml

[message_receive]
ban_words = []
ban_msgs_regex = []

# 输入状态通知跟踪
enable_input_status_tracking = true  # 是否启用
input_status_dedup_window = 30       # 去重窗口（秒）

[mood]
enable_mood = true
mood_update_threshold = 1.0
enable_emotion_history = false
use_vad_path = false
```

## 七、风险评估与缓解

### 7.1 风险点

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 频繁触发导致数据库压力 | 中 | 低 | 去重机制 + 可配置窗口 |
| 污染消息历史 | 低 | 中 | 标记字段 + 查询时过滤 |
| 误触发情绪更新 | 中 | 低 | 特殊处理 + 配置开关 |
| 内存泄漏（去重缓存） | 低 | 低 | 定期清理过期缓存 |

### 7.2 回滚方案

如果出现问题，可以通过以下方式快速回滚:

```toml
# 关闭输入状态跟踪
[message_receive]
enable_input_status_tracking = false
```

或者在数据库中清理:

```sql
-- 删除所有输入状态通知记录
DELETE FROM messages WHERE is_input_status_notice = 1;
```

## 八、后续优化方向

1. **P2**: 支持群聊场景（如果未来 NapCat 支持）
2. **P2**: 记录输入持续时间（开始输入 → 停止输入）
3. **P3**: 输入行为模式分析（快速连续输入 vs 缓慢输入）
4. **P3**: 与情绪系统深度集成（输入犹豫 → 焦虑情绪）
5. **P3**: 可视化展示（WebUI 中显示用户输入状态）

## 九、开发检查清单

- [ ] 配置项新增 (`official_configs.py`)
- [ ] 数据库模型扩展 (`database_model.py`)
- [ ] 数据库迁移脚本
- [ ] 消息处理逻辑 (`bot.py::input_status_process`)
- [ ] 存储逻辑调整 (`storage.py`)
- [ ] 情绪系统适配 (`heartflow_message_processor.py`)
- [ ] 消息查询过滤 (`message_repository.py`)
- [ ] 心跳系统查询接口 (`heartbeat_system.py`)
- [ ] MessageRecv 模型扩展 (`message.py`)
- [ ] 单元测试编写
- [ ] 集成测试验证
- [ ] 文档更新
- [ ] 配置文件示例更新

## 十、参考资料

- NapCat input_status 通知格式文档
- 情绪系统开发文档 (`src/mood/情绪模块开发文档V1.md`)
- 消息存储架构 (`src/chat/message_receive/storage.py`)
- 心跳系统设计（待补充）

---

**最后更新**: 2026-02-05  
**负责人**: [待分配]  
**审核状态**: 待审核

---

# V2 版本优化方向

## 优化 1: 统一通知消息记录机制

### 问题分析

当前 `handle_notice_message()` 对不同类型的通知消息处理不一致:
- ✅ **撤回消息**: 有日志记录，但不入库
- ✅ **戳一戳**: 有日志记录，但不入库
- ✅ **正在输入**: 通过 `input_status_process` 单独处理

**改进方案**: 统一所有通知消息的记录机制

### 设计方案

#### 1. 扩展数据库模型

```python
# src/common/database/database_model.py

class Messages(BaseModel):
    # ... 现有字段 ...
    
    # 通知消息类型标识（替代单一的 is_input_status_notice）
    is_notice = BooleanField(default=False)
    """标识该记录为通知消息（撤回、戳一戳、输入状态等）"""
    
    notice_type = TextField(null=True)
    """通知类型: recall(撤回), poke(戳一戳), input_status(输入状态), 等"""
    
    notice_data = TextField(null=True)
    """通知消息的附加数据（JSON格式）"""
    
    # 保留兼容字段（可选）
    is_input_status_notice = BooleanField(default=False)
    input_status_typing = BooleanField(null=True)
```

#### 2. 通知类型枚举

```python
# src/common/data_models/message_data_model.py

class NoticeType(Enum):
    """通知消息类型"""
    RECALL = "recall"              # 撤回消息
    POKE = "poke"                  # 戳一戳
    INPUT_STATUS = "input_status"  # 输入状态
    USER_JOIN = "user_join"        # 用户加入群聊
    USER_LEAVE = "user_leave"      # 用户离开群聊
    ADMIN_CHANGE = "admin_change"  # 管理员变更
    GROUP_NAME_CHANGE = "group_name_change"  # 群名修改
    # 可扩展其他类型...
```

#### 3. 统一处理逻辑

```python
# src/chat/message_receive/bot.py

class ChatBot:
    def __init__(self):
        self.bot = None
        self._started = False
        self.heartflow_message_receiver = HeartFCMessageReceiver()
        
        # 通知消息去重缓存 {(chat_id, notice_type): last_timestamp}
        self._notice_dedup_cache: Dict[Tuple[str, str], float] = {}
    
    async def handle_notice_message(self, message: MessageRecv):
        """
        统一处理所有通知消息
        
        V2 改进:
        1. 所有通知消息都记录到数据库
        2. 统一的去重机制
        3. 可配置的通知类型过滤
        """
        if message.message_info.message_id == "notice":
            message.is_notify = True
            logger.debug("notice消息")
            
            try:
                seg = message.message_segment
                mi = message.message_info
                
                if getattr(seg, "type", None) != "notify":
                    return True
                
                notice_data = getattr(seg, "data", {})
                if not isinstance(notice_data, dict):
                    return True
                
                sub_type = notice_data.get("sub_type")
                scene = notice_data.get("scene")
                
                # 解析通知类型
                notice_type = self._parse_notice_type(sub_type, scene)
                if not notice_type:
                    logger.debug(f"[notice] 未识别的通知类型: sub_type={sub_type}, scene={scene}")
                    return True
                
                # 构造 chat_id
                user_id = mi.user_info.user_id if mi.user_info else None
                group_id = mi.group_info.group_id if mi.group_info else None
                platform = mi.platform
                
                if group_id:
                    chat_id = f"{platform}_group_{group_id}"
                elif user_id:
                    chat_id = f"{platform}_private_{user_id}"
                else:
                    logger.warning("[notice] 无法确定 chat_id")
                    return True
                
                # 去重检查
                if not self._should_record_notice(chat_id, notice_type):
                    logger.debug(f"[notice] {notice_type} 去重，跳过记录")
                    return True
                
                # 记录到数据库
                await self._store_notice_message(
                    message=message,
                    chat_id=chat_id,
                    notice_type=notice_type,
                    notice_data=notice_data,
                )
                
                # 打印日志
                self._log_notice_message(notice_type, notice_data, mi)
                
            except Exception as e:
                logger.error(f"[notice] 处理通知消息失败: {e}")
                logger.error(traceback.format_exc())
            
            return True
        
        return False
    
    def _parse_notice_type(self, sub_type: Optional[str], scene: Optional[str]) -> Optional[str]:
        """解析通知类型"""
        if sub_type == "recall":
            return "recall"
        elif scene == "poke" or sub_type == "poke":
            return "poke"
        # 可扩展其他类型
        return None
    
    def _should_record_notice(self, chat_id: str, notice_type: str) -> bool:
        """判断是否应该记录该通知（去重）"""
        from src.config.config import global_config
        
        # 检查配置是否启用
        if not getattr(global_config.message_receive, 'enable_notice_tracking', True):
            return False
        
        # 获取去重窗口（不同类型可以有不同窗口）
        dedup_windows = {
            "recall": 0,  # 撤回消息不去重，每次都记录
            "poke": 10,   # 戳一戳 10秒去重
            "input_status": 30,  # 输入状态 30秒去重
        }
        
        dedup_window = dedup_windows.get(notice_type, 30)
        if dedup_window == 0:
            return True
        
        # 检查缓存
        cache_key = (chat_id, notice_type)
        current_time = time.time()
        
        if cache_key in self._notice_dedup_cache:
            last_time = self._notice_dedup_cache[cache_key]
            if current_time - last_time < dedup_window:
                return False
        
        # 更新缓存
        self._notice_dedup_cache[cache_key] = current_time
        
        # 清理过期缓存
        expired_keys = [
            k for k, v in self._notice_dedup_cache.items()
            if current_time - v > max(dedup_windows.values()) * 2
        ]
        for k in expired_keys:
            del self._notice_dedup_cache[k]
        
        return True
    
    async def _store_notice_message(
        self,
        message: MessageRecv,
        chat_id: str,
        notice_type: str,
        notice_data: Dict[str, Any],
    ):
        """存储通知消息到数据库"""
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager
            from src.chat.message_receive.storage import MessageStorage
            import json
            
            # 获取聊天流
            chat = get_chat_manager().get_stream(chat_id)
            if not chat:
                logger.warning(f"[notice] 未找到聊天流: {chat_id}")
                return
            
            # 构造可读的消息文本
            readable_text = self._format_notice_text(notice_type, notice_data, message.message_info)
            
            # 标记消息属性
            message.is_notice = True
            message.notice_type = notice_type
            message.notice_data = json.dumps(notice_data, ensure_ascii=False)
            message.processed_plain_text = readable_text
            message.chat_stream = chat
            
            # 存储
            await MessageStorage.store_message(message, chat)
            
            logger.info(f"[notice] 记录 {notice_type} 通知到数据库: {chat_id}")
            
        except Exception as e:
            logger.error(f"[notice] 存储通知消息失败: {e}")
            logger.error(traceback.format_exc())
    
    def _format_notice_text(
        self,
        notice_type: str,
        notice_data: Dict[str, Any],
        message_info: Any,
    ) -> str:
        """格式化通知消息为可读文本"""
        user_info = message_info.user_info
        user_name = getattr(user_info, 'user_nickname', '未知用户')
        
        if notice_type == "recall":
            recalled = notice_data.get("recalled_user_info", {})
            if isinstance(recalled, dict):
                recalled_name = recalled.get("user_nickname", "某人")
                if recalled.get("user_id") != getattr(user_info, "user_id", None):
                    return f"[通知] {user_name} 撤回了 {recalled_name} 的消息"
            return f"[通知] {user_name} 撤回了消息"
        
        elif notice_type == "poke":
            return f"[通知] {user_name} 戳了戳你"
        
        elif notice_type == "input_status":
            return f"[通知] {user_name} 正在输入..."
        
        return f"[通知] {notice_type}"
    
    def _log_notice_message(
        self,
        notice_type: str,
        notice_data: Dict[str, Any],
        message_info: Any,
    ):
        """打印通知消息日志"""
        user_info = message_info.user_info
        user_name = getattr(user_info, 'user_nickname', '未知')
        
        if notice_type == "recall":
            recalled = notice_data.get("recalled_user_info", {})
            if isinstance(recalled, dict):
                recalled_name = recalled.get("user_nickname", "某人")
                logger.info(f"{user_name} 撤回了 {recalled_name} 的消息")
            else:
                logger.info(f"{user_name} 撤回了消息")
        
        elif notice_type == "poke":
            logger.info(f"{user_name} 戳了戳你")
        
        else:
            logger.debug(f"[notice] {notice_type}: {notice_data}")
```

#### 4. 存储逻辑适配

```python
# src/chat/message_receive/storage.py

@staticmethod
async def store_message(message: Union[MessageSending, MessageRecv], chat_stream: ChatStream) -> None:
    """存储消息到数据库"""
    try:
        # 通知消息特殊处理
        if isinstance(message, MessageRecv) and message.is_notify:
            # 检查是否是需要记录的通知类型
            if not getattr(message, 'is_notice', False):
                logger.debug("通知消息（非记录类型），跳过存储")
                return
        
        # ... 现有代码 ...
        
        if isinstance(message, MessageRecv):
            # ... 现有字段赋值 ...
            
            # 通知消息字段
            is_notice = getattr(message, 'is_notice', False)
            notice_type = getattr(message, 'notice_type', None)
            notice_data = getattr(message, 'notice_data', None)
            
            # 通知消息不计算 VAD、关键词、兴趣度
            if is_notice:
                emotion_v, emotion_a, emotion_d = None, None, None
                key_words = ""
                key_words_lite = ""
                interest_value = 0
            else:
                # 正常消息处理
                emotion_v, emotion_a, emotion_d = _compute_message_vad(filtered_processed_plain_text)
                # ... 关键词提取 ...
        
        # 创建数据库记录
        Messages.create(
            # ... 现有字段 ...
            is_notice=is_notice if isinstance(message, MessageRecv) else False,
            notice_type=notice_type if isinstance(message, MessageRecv) else None,
            notice_data=notice_data if isinstance(message, MessageRecv) else None,
        )
```

#### 5. 配置项

```python
# src/config/official_configs.py

@dataclass
class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""
    
    ban_words: set[str] = field(default_factory=lambda: set())
    ban_msgs_regex: list[str] = field(default_factory=lambda: [])
    
    # 通知消息跟踪
    enable_notice_tracking: bool = True
    """是否启用通知消息跟踪（撤回、戳一戳、输入状态等）"""
    
    notice_dedup_windows: Dict[str, int] = field(default_factory=lambda: {
        "recall": 0,       # 撤回消息不去重
        "poke": 10,        # 戳一戳 10秒去重
        "input_status": 30,  # 输入状态 30秒去重
    })
    """各类通知消息的去重时间窗口（秒），0 表示不去重"""
```

### 优势分析

1. **统一性**: 所有通知消息使用相同的处理流程
2. **可扩展性**: 新增通知类型只需添加枚举和格式化逻辑
3. **可查询性**: 心跳系统可以统一查询各类通知历史
4. **可配置性**: 每种通知类型的去重窗口可独立配置
5. **数据完整性**: 保留完整的通知历史，支持行为分析

---

## 优化 2: 全局心跳系统 (Heartbeat System)

### 设计目标

构建一个 **全局状态管理 + 主动思考决策** 的心跳系统，让 Bot 具有:
1. 🧠 **全局视野**: 能够感知所有聊天流的状态
2. 🎯 **主动切换**: 可以在不同聊天流之间切换注意力
3. 💭 **自主思考**: 定时 + 随机触发的主动思考
4. 🔧 **工具调用**: 主动联网搜索、记忆检索等
5. 🤖 **真实体验**: 模拟真人的多任务处理和主动关心

### 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                  Global Heartbeat System                 │
│  ┌───────────────────────────────────────────────────┐  │
│  │          Heartbeat State Manager                  │  │
│  │  - current_chat_id: Optional[str]                 │  │
│  │  - active_chats: List[ChatInfo]                   │  │
│  │  - attention_queue: PriorityQueue                 │  │
│  │  - thinking_mode: ThinkingMode                    │  │
│  └───────────────────────────────────────────────────┘  │
│                          ↓                               │
│  ┌───────────────────────────────────────────────────┐  │
│  │         Heartbeat Scheduler (定时器)              │  │
│  │  - 快速心跳: 每 5s  (检查紧急事件)               │  │
│  │  - 常规心跳: 每 30s (主动思考决策)               │  │
│  │  - 慢速心跳: 每 5m  (全局状态整理)               │  │
│  └───────────────────────────────────────────────────┘  │
│                          ↓                               │
│  ┌───────────────────────────────────────────────────┐  │
│  │       Decision Engine (决策引擎)                  │  │
│  │  - 分析各聊天流的观察信号                         │  │
│  │  - 计算注意力优先级                               │  │
│  │  - 决策是否切换聊天流                             │  │
│  │  - 决策是否主动发言                               │  │
│  └───────────────────────────────────────────────────┘  │
│                          ↓                               │
│  ┌───────────────────────────────────────────────────┐  │
│  │       Action Executor (行动执行器)                │  │
│  │  - 切换聊天流 (enter_chat / exit_chat)            │  │
│  │  - 主动思考 (think)                               │  │
│  │  - 发送消息 (send_message)                        │  │
│  │  - 调用工具 (search_web / retrieve_memory)       │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 核心组件设计

#### 1. 全局状态管理器

```python
# src/chat/heartbeat/heartbeat_state.py

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import time

class ThinkingMode(Enum):
    """思考模式"""
    IDLE = "idle"              # 空闲，无特定关注对象
    FOCUSED = "focused"        # 专注于某个聊天流
    OBSERVING = "observing"    # 观察多个聊天流
    SEARCHING = "searching"    # 正在搜索/检索信息
    REFLECTING = "reflecting"  # 反思/整理记忆

@dataclass
class ChatInfo:
    """聊天流信息快照"""
    chat_id: str
    chat_name: str
    last_message_time: float
    last_message_content: str
    unread_count: int
    is_cold: bool
    cold_duration: float
    has_input_status: bool  # 最近是否有"正在输入"
    has_poke: bool          # 最近是否被戳
    has_recall: bool        # 最近是否有撤回
    attention_score: float  # 注意力得分（越高越需要关注）
    
    def age(self) -> float:
        """距离最后一条消息的时间（秒）"""
        return time.time() - self.last_message_time

@dataclass
class HeartbeatState:
    """心跳系统全局状态"""
    
    # 当前状态
    current_chat_id: Optional[str] = None
    """当前正在关注的聊天流 ID"""
    
    thinking_mode: ThinkingMode = ThinkingMode.IDLE
    """当前思考模式"""
    
    last_think_time: float = field(default_factory=time.time)
    """上次主动思考的时间"""
    
    last_action_time: float = field(default_factory=time.time)
    """上次执行行动的时间"""
    
    # 聊天流管理
    active_chats: Dict[str, ChatInfo] = field(default_factory=dict)
    """所有活跃的聊天流信息"""
    
    attention_queue: List[str] = field(default_factory=list)
    """注意力队列（按优先级排序的 chat_id）"""
    
    # 统计信息
    total_thinks: int = 0
    """总思考次数"""
    
    total_proactive_messages: int = 0
    """总主动发言次数"""
    
    total_tool_calls: int = 0
    """总工具调用次数"""
    
    def is_idle(self) -> bool:
        """是否处于空闲状态"""
        return self.thinking_mode == ThinkingMode.IDLE and self.current_chat_id is None
    
    def is_focused(self) -> bool:
        """是否正在专注某个聊天"""
        return self.thinking_mode == ThinkingMode.FOCUSED and self.current_chat_id is not None
    
    def get_current_chat(self) -> Optional[ChatInfo]:
        """获取当前关注的聊天流信息"""
        if not self.current_chat_id:
            return None
        return self.active_chats.get(self.current_chat_id)


# 全局状态实例
_global_heartbeat_state = HeartbeatState()

def get_heartbeat_state() -> HeartbeatState:
    """获取全局心跳状态"""
    return _global_heartbeat_state
```

#### 2. 心跳系统核心

```python
# src/chat/heartbeat/heartbeat_system.py

import asyncio
import random
from typing import List, Optional, Dict, Any
from src.common.logger import get_logger
from src.chat.message_receive.chat_stream import get_chat_manager
from .heartbeat_state import (
    HeartbeatState,
    ChatInfo,
    ThinkingMode,
    get_heartbeat_state,
)

logger = get_logger("heartbeat")


class HeartbeatSystem:
    """
    全局心跳系统
    
    负责:
    1. 定时检查所有聊天流状态
    2. 计算注意力优先级
    3. 决策是否主动思考/发言
    4. 管理聊天流切换
    """
    
    def __init__(self):
        self.state = get_heartbeat_state()
        self._running = False
        self._tasks: List[asyncio.Task] = []
    
    async def start(self):
        """启动心跳系统"""
        if self._running:
            logger.warning("心跳系统已在运行")
            return
        
        self._running = True
        logger.info("心跳系统启动")
        
        # 启动多个心跳任务
        self._tasks = [
            asyncio.create_task(self._fast_heartbeat()),    # 5秒快速心跳
            asyncio.create_task(self._normal_heartbeat()),  # 30秒常规心跳
            asyncio.create_task(self._slow_heartbeat()),    # 5分钟慢速心跳
        ]
    
    async def stop(self):
        """停止心跳系统"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("心跳系统停止")
    
    # ========== 心跳任务 ==========
    
    async def _fast_heartbeat(self):
        """快速心跳：每5秒检查紧急事件"""
        while self._running:
            try:
                await self._check_urgent_events()
            except Exception as e:
                logger.error(f"快速心跳出错: {e}")
            await asyncio.sleep(5)
    
    async def _normal_heartbeat(self):
        """常规心跳：每30秒主动思考决策"""
        while self._running:
            try:
                await self._update_chat_states()
                await self._decide_and_act()
            except Exception as e:
                logger.error(f"常规心跳出错: {e}")
            await asyncio.sleep(30)
    
    async def _slow_heartbeat(self):
        """慢速心跳：每5分钟全局整理"""
        while self._running:
            try:
                await self._global_cleanup()
            except Exception as e:
                logger.error(f"慢速心跳出错: {e}")
            await asyncio.sleep(300)
    
    # ========== 状态更新 ==========
    
    async def _update_chat_states(self):
        """更新所有聊天流的状态"""
        from src.common.database.database_model import Messages
        import time
        
        chat_manager = get_chat_manager()
        all_streams = chat_manager.streams
        
        current_time = time.time()
        
        for chat_id, chat_stream in all_streams.items():
            try:
                # 获取最后一条消息
                last_msg = (
                    Messages
                    .select()
                    .where(Messages.chat_id == chat_id)
                    .order_by(Messages.time.desc())
                    .limit(1)
                    .first()
                )
                
                if not last_msg:
                    continue
                
                # 检查最近的通知消息
                recent_notices = (
                    Messages
                    .select()
                    .where(
                        (Messages.chat_id == chat_id) &
                        (Messages.is_notice == True) &
                        (Messages.time >= current_time - 120)  # 最近2分钟
                    )
                )
                
                has_input_status = any(n.notice_type == "input_status" for n in recent_notices)
                has_poke = any(n.notice_type == "poke" for n in recent_notices)
                has_recall = any(n.notice_type == "recall" for n in recent_notices)
                
                # 计算冷场时长
                time_since_last = current_time - last_msg.time
                is_cold = time_since_last > 300  # 5分钟无消息算冷场
                cold_duration = time_since_last if is_cold else 0
                
                # 计算注意力得分
                attention_score = self._calculate_attention_score(
                    time_since_last=time_since_last,
                    has_input_status=has_input_status,
                    has_poke=has_poke,
                    has_recall=has_recall,
                    is_cold=is_cold,
                )
                
                # 更新状态
                chat_info = ChatInfo(
                    chat_id=chat_id,
                    chat_name=chat_manager.get_stream_name(chat_id) or chat_id,
                    last_message_time=last_msg.time,
                    last_message_content=last_msg.processed_plain_text or "",
                    unread_count=0,  # TODO: 实现未读计数
                    is_cold=is_cold,
                    cold_duration=cold_duration,
                    has_input_status=has_input_status,
                    has_poke=has_poke,
                    has_recall=has_recall,
                    attention_score=attention_score,
                )
                
                self.state.active_chats[chat_id] = chat_info
                
            except Exception as e:
                logger.error(f"更新聊天流 {chat_id} 状态失败: {e}")
        
        # 更新注意力队列（按得分排序）
        self.state.attention_queue = sorted(
            self.state.active_chats.keys(),
            key=lambda cid: self.state.active_chats[cid].attention_score,
            reverse=True,
        )
        
        logger.debug(f"更新了 {len(self.state.active_chats)} 个聊天流状态")
    
    def _calculate_attention_score(
        self,
        time_since_last: float,
        has_input_status: bool,
        has_poke: bool,
        has_recall: bool,
        is_cold: bool,
    ) -> float:
        """
        计算注意力得分（0-100）
        
        得分越高，越需要关注
        """
        score = 0.0
        
        # 基础得分：根据时间衰减
        if time_since_last < 60:
            score += 50  # 1分钟内，高关注
        elif time_since_last < 300:
            score += 30  # 5分钟内，中等关注
        elif time_since_last < 1800:
            score += 10  # 30分钟内，低关注
        else:
            score += 5   # 超过30分钟，极低关注
        
        # 通知消息加分
        if has_input_status:
            score += 40  # 正在输入，高优先级
        if has_poke:
            score += 35  # 被戳，高优先级
        if has_recall:
            score += 15  # 有撤回，中等优先级
        
        # 冷场惩罚
        if is_cold:
            score *= 0.5
        
        return min(100.0, score)
    
    # ========== 决策引擎 ==========
    
    async def _check_urgent_events(self):
        """检查紧急事件（被戳、频繁输入等）"""
        for chat_id, chat_info in self.state.active_chats.items():
            # 紧急事件1: 被戳
            if chat_info.has_poke and chat_info.age() < 10:
                logger.info(f"[心跳] 检测到紧急事件: {chat_info.chat_name} 戳了你")
                await self._handle_urgent_event(chat_id, "poke")
            
            # 紧急事件2: 频繁输入但不发送
            if chat_info.has_input_status and chat_info.age() > 60:
                logger.info(f"[心跳] 检测到紧急事件: {chat_info.chat_name} 长时间输入未发送")
                await self._handle_urgent_event(chat_id, "long_typing")
    
    async def _decide_and_act(self):
        """主动思考并决策行动"""
        # 更新思考计数
        self.state.total_thinks += 1
        self.state.last_think_time = time.time()
        
        logger.info(f"[心跳] 第 {self.state.total_thinks} 次主动思考")
        
        # 决策1: 是否需要切换聊天流
        if self.state.is_idle() or random.random() < 0.3:
            await self._consider_switch_chat()
        
        # 决策2: 是否需要主动发言
        if self.state.current_chat_id:
            await self._consider_proactive_speak()
        
        # 决策3: 是否需要调用工具（随机触发）
        if random.random() < 0.1:  # 10% 概率
            await self._consider_tool_call()
    
    async def _consider_switch_chat(self):
        """考虑是否切换聊天流"""
        if not self.state.attention_queue:
            logger.debug("[心跳] 没有活跃聊天流")
            return
        
        # 获取注意力最高的聊天
        top_chat_id = self.state.attention_queue[0]
        top_chat = self.state.active_chats[top_chat_id]
        
        # 如果当前没有关注对象，或者新对象得分显著更高
        should_switch = False
        
        if not self.state.current_chat_id:
            should_switch = top_chat.attention_score > 50
        elif top_chat_id != self.state.current_chat_id:
            current_chat = self.state.get_current_chat()
            if current_chat and top_chat.attention_score > current_chat.attention_score * 1.5:
                should_switch = True
        
        if should_switch:
            logger.info(
                f"[心跳] 切换注意力: {top_chat.chat_name} "
                f"(得分: {top_chat.attention_score:.1f})"
            )
            await self.enter_chat(top_chat_id)
    
    async def _consider_proactive_speak(self):
        """考虑是否主动发言"""
        if not self.state.current_chat_id:
            return
        
        current_chat = self.state.get_current_chat()
        if not current_chat:
            return
        
        # 主动发言条件
        should_speak = False
        reason = ""
        
        # 条件1: 被戳
        if current_chat.has_poke and current_chat.age() < 30:
            should_speak = True
            reason = "回应戳一戳"
        
        # 条件2: 长时间输入未发送
        elif current_chat.has_input_status and current_chat.age() > 60:
            should_speak = True
            reason = "关心长时间输入"
        
        # 条件3: 冷场过久（随机触发）
        elif current_chat.is_cold and current_chat.cold_duration > 600 and random.random() < 0.2:
            should_speak = True
            reason = "打破冷场"
        
        if should_speak:
            logger.info(f"[心跳] 决定主动发言: {reason}")
            await self._proactive_speak(current_chat.chat_id, reason)
    
    async def _consider_tool_call(self):
        """考虑是否调用工具"""
        # TODO: 实现工具调用决策
        # 例如: 随机检索记忆、联网搜索感兴趣的话题等
        logger.debug("[心跳] 考虑工具调用（暂未实现）")
    
    # ========== 行动执行 ==========
    
    async def enter_chat(self, chat_id: str):
        """进入指定聊天流"""
        if self.state.current_chat_id == chat_id:
            logger.debug(f"[心跳] 已在聊天流 {chat_id} 中")
            return
        
        # 退出当前聊天
        if self.state.current_chat_id:
            await self.exit_chat()
        
        # 进入新聊天
        self.state.current_chat_id = chat_id
        self.state.thinking_mode = ThinkingMode.FOCUSED
        
        chat_info = self.state.active_chats.get(chat_id)
        chat_name = chat_info.chat_name if chat_info else chat_id
        
        logger.info(f"[心跳] 进入聊天流: {chat_name}")
    
    async def exit_chat(self):
        """退出当前聊天流"""
        if not self.state.current_chat_id:
            return
        
        chat_id = self.state.current_chat_id
        chat_info = self.state.active_chats.get(chat_id)
        chat_name = chat_info.chat_name if chat_info else chat_id
        
        logger.info(f"[心跳] 退出聊天流: {chat_name}")
        
        self.state.current_chat_id = None
        self.state.thinking_mode = ThinkingMode.IDLE
    
    async def _proactive_speak(self, chat_id: str, reason: str):
        """主动发言"""
        try:
            from src.plugin_system.apis import send_api
            
            # TODO: 调用 LLM 生成主动发言内容
            # 这里先用简单的模板
            templates = {
                "回应戳一戳": ["怎么啦？", "在呢~", "嗯？有什么事吗？"],
                "关心长时间输入": ["有什么想说的吗？", "需要帮忙吗？", "在想什么呢？"],
                "打破冷场": ["最近怎么样？", "有什么新鲜事吗？", "好久没聊天了~"],
            }
            
            message = random.choice(templates.get(reason, ["在吗？"]))
            
            success = await send_api.text_to_stream(
                text=message,
                stream_id=chat_id,
                typing=True,
            )
            
            if success:
                self.state.total_proactive_messages += 1
                logger.info(f"[心跳] 主动发言成功: {message}")
            else:
                logger.warning(f"[心跳] 主动发言失败")
        
        except Exception as e:
            logger.error(f"[心跳] 主动发言出错: {e}")
    
    async def _handle_urgent_event(self, chat_id: str, event_type: str):
        """处理紧急事件"""
        logger.info(f"[心跳] 处理紧急事件: {event_type} @ {chat_id}")
        
        # 立即切换到该聊天
        await self.enter_chat(chat_id)
        
        # 根据事件类型决定行动
        if event_type == "poke":
            await self._proactive_speak(chat_id, "回应戳一戳")
        elif event_type == "long_typing":
            await self._proactive_speak(chat_id, "关心长时间输入")
    
    # ========== 全局维护 ==========
    
    async def _global_cleanup(self):
        """全局清理和整理"""
        logger.info("[心跳] 执行全局清理")
        
        # 清理过期的聊天流信息
        current_time = time.time()
        expired_chats = [
            cid for cid, info in self.state.active_chats.items()
            if current_time - info.last_message_time > 86400  # 24小时无消息
        ]
        
        for cid in expired_chats:
            del self.state.active_chats[cid]
        
        if expired_chats:
            logger.info(f"[心跳] 清理了 {len(expired_chats)} 个过期聊天流")
        
        # 打印统计信息
        logger.info(
            f"[心跳] 统计: 思考{self.state.total_thinks}次, "
            f"主动发言{self.state.total_proactive_messages}次, "
            f"工具调用{self.state.total_tool_calls}次"
        )
    
    # ========== 公共接口 ==========
    
    def get_chat_list(self) -> List[ChatInfo]:
        """获取所有聊天流列表（按注意力得分排序）"""
        return [
            self.state.active_chats[cid]
            for cid in self.state.attention_queue
            if cid in self.state.active_chats
        ]
    
    def get_current_chat_id(self) -> Optional[str]:
        """获取当前关注的聊天流 ID"""
        return self.state.current_chat_id
    
    def get_state(self) -> HeartbeatState:
        """获取全局状态"""
        return self.state


# 全局实例
_global_heartbeat_system: Optional[HeartbeatSystem] = None

def get_heartbeat_system() -> HeartbeatSystem:
    """获取全局心跳系统实例"""
    global _global_heartbeat_system
    if _global_heartbeat_system is None:
        _global_heartbeat_system = HeartbeatSystem()
    return _global_heartbeat_system
```

#### 3. 启动集成

```python
# src/main.py

async def main():
    # ... 现有初始化代码 ...
    
    # 启动心跳系统
    from src.chat.heartbeat.heartbeat_system import get_heartbeat_system
    heartbeat = get_heartbeat_system()
    await heartbeat.start()
    logger.info("心跳系统已启动")
    
    # ... 其余代码 ...
```

### 使用示例

#### 查询聊天列表

```python
from src.chat.heartbeat.heartbeat_system import get_heartbeat_system

heartbeat = get_heartbeat_system()

# 获取所有聊天流（按注意力得分排序）
chat_list = heartbeat.get_chat_list()

for chat in chat_list[:5]:  # 显示前5个
    print(f"{chat.chat_name}: 得分 {chat.attention_score:.1f}")
```

#### 手动切换聊天流

```python
# 进入指定聊天
await heartbeat.enter_chat("qq_private_123456")

# 获取当前聊天
current_chat_id = heartbeat.get_current_chat_id()

# 退出当前聊天
await heartbeat.exit_chat()
```

#### 查看全局状态

```python
state = heartbeat.get_state()

print(f"当前模式: {state.thinking_mode}")
print(f"当前聊天: {state.current_chat_id}")
print(f"活跃聊天数: {len(state.active_chats)}")
print(f"总思考次数: {state.total_thinks}")
```

### 后续扩展方向

1. **P1**: LLM 驱动的主动发言内容生成
2. **P1**: 工具调用集成（联网搜索、记忆检索）
3. **P2**: 更复杂的注意力得分算法（考虑关系亲密度、话题兴趣等）
4. **P2**: 思考模式的状态机优化
5. **P3**: WebUI 可视化展示（实时状态、聊天列表、注意力热力图）
6. **P3**: 学习用户的作息时间，调整心跳频率

---

## 总结

V2 版本的两个核心优化:

1. **统一通知消息记录**: 让所有通知类型（撤回、戳一戳、输入状态）都进入数据库，提供完整的行为历史
2. **全局心跳系统**: 构建真正的"多任务处理"能力，让 Bot 能够主动关注、切换、思考和发言

这两个优化相辅相成:
- 通知消息记录提供了**观察信号**
- 心跳系统利用这些信号做**决策和行动**

最终目标: **更加真实、主动、智能的交互体验**



## 配置模板同步与心跳系统集成设计（补充）

### 一、配置类与模板同步约定

- **原则**: 任何新增的配置字段，必须同时出现在三处：
  - 代码侧：`src/config/official_configs.py`、`src/config/config.py`
  - 运行侧：`config/bot_config.toml`
  - 模板侧：`template/bot_config_template.toml`
- **原因**：
  - 避免：实际配置文件缺字段导致解析失败 / 使用默认值不可见
  - 方便：新部署或迁移时，`template` 能完整暴露可调参数

当前与通知 / 心跳相关的新增配置（设计）包括：

- `MessageReceiveConfig`:
  - `enable_notice_tracking: bool` —— 是否启用通知跟踪（撤回、戳一戳、输入状态）
  - `notice_dedup_windows: Dict[str, int]` —— 各类通知的去重时间窗口
- `HeartbeatConfig`（建议新增独立配置块，如 `[heartbeat]`）:
  - `enable: bool` —— 是否启用心跳系统
  - `fast_interval: int` —— 快速心跳周期（秒），建议默认 5
  - `normal_interval: int` —— 常规心跳周期（秒），建议默认 30
  - `slow_interval: int` —— 慢速心跳周期（秒），建议默认 300
  - `active_time_ranges: list[str]` —— 心跳启用的时间段，如 `["08:00-23:30"]`
  - `min_proactive_interval_seconds: int` —— 同一 chat 最小主动发言间隔（秒）

> 后续实际实现时，需要在 `official_configs.py` 中定义对应 `HeartbeatConfig`，并在 `Config` 中挂载；再同步到 `bot_config.toml` 与 `bot_config_template.toml`。

---

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

---

### 三、心跳系统的时间段与频率配置

结合上面的调度设计，心跳系统本身需要可配置的“节奏参数”：

- **多层频率**（建议）：
  - `fast_interval`:
    - 用于处理“紧急类信号”：戳一戳 / 长时间输入未发出等。
    - 周期较短（如 5 秒），逻辑要非常轻量，尽量避免 LLM 调用。
  - `normal_interval`:
    - 用于常规的主动思考 / 主动发言决策。
    - 周期中等（如 30 秒），可以适量调用 LLM（视实现策略而定）。
  - `slow_interval`:
    - 用于全局整理 / 统计（清理过期 chat 状态、写入统计日志等）。
    - 周期较长（如 300 秒），主要是 DB 读写。

- **时间段控制**：
  - `active_time_ranges` 允许配置心跳系统在哪些时间段“更活跃”：
    - 示例：`["08:00-23:30"]` 白天正常工作，夜间可降低频率或关闭主动输出。
  - 实现上可以：
    - 在每次心跳 tick 前检查当前时间是否落在 active 区间；
    - 若不在，则：
      - 要么跳过主动发言逻辑，仅做轻量状态维护；
      - 要么完全 skip 当前 tick。

- **冷却控制**：
  - `min_proactive_interval_seconds` 控制“同一 chat 的主动输出冷却时间”：
    - 例如：600 秒 → 同一私聊至少 10 分钟才允许一次“纯心跳驱动的主动发言”。
    - 这样即使调度层决定“此处可以说话”，也会先检查是否在冷却期内，避免太黏人。

> 实现细节应与 `HeartbeatState` 结合：记录每个 chat 的上次主动发言时间，在决策前统一检查。

---

### 四、关于 fork 版本 `affinity_flow_chatter` 插件的参考价值

fork 项目路径：`D:\AI\little-hui-v2-by-MoFox\src\plugins\built_in\affinity_flow_chatter`  
推测特性（基于命名和项目上下文）：

- 可能已经实现了：
  - 基于“好感度 / 亲密度”的 chat 排序或打分；
  - 定期扫描“亲密 chat”，主动发起闲聊；
  - 结合关系系统 / 记忆系统，生成更贴合特定用户的内容。

对当前心跳系统设计的启发：

- **作为一个主动行为 source**：
  - 可以将 `affinity_flow_chatter` 抽象为：
    - `source = "affinity"`
    - 提供接口如 `plan_affinity_chat(chat_id) -> intent / prompt`。
  - 由心跳 / 调度中心在合适的节奏和窗口中，决定是否接受该建议并实际发送。

- **与关系系统 / 情绪系统结合**：
  - 心跳系统在计算 `attention_score` 时，可以预留 hooks：
    - 例如将“亲密度 score / 关系标签 / 当前情绪”纳入权重计算。
  - `affinity_flow_chatter` 负责根据这些权重给出“更柔和、更贴脸”的表达方式。

后续如果需要对 `affinity_flow_chatter` 做深入对比，可以：

- 把其核心调度 / 决策代码抽取出来，放到 `开发记录/` 做一个“对照分析”文档；
- 标注哪些逻辑可以直接迁移到心跳系统，哪些保持为插件级别的“内容生成器”。

---

### 五、小结（当前阶段的设计共识）

1. **配置同步是硬要求**：新增配置必须同步更新 `official_configs` + `config/*.toml` + `template/*.toml`。
2. **心跳系统应作为“主动行为总调度”**：
   - 吸收来自做梦 / 总结 / affinity 等系统的“主动意图”，
   - 统一考虑节奏、冷却和优先级，再决定真正的输出。
3. **时间段与频率必须可配置**：
   - 支持多层频率（fast / normal / slow）与活跃时段，
   - 配合每个 chat 的冷却时间，保证既“有人味”，又不打扰。
4. **fork 插件是很好的参考**：
   - 重点在于“如何基于亲密度发起主动聊天”，
   - 建议作为一个主动行为 source 接入到统一心跳 / 调度体系。



## 通知 & 心跳系统 - 阶段 0 基线检查记录（2026-02-27）

### 一、配置层现状（`official_configs.py`）

- **MessageReceiveConfig**
  - 当前仅包含：
    - `ban_words: set[str]`
    - `ban_msgs_regex: set[str]`
  - **尚未实现**：
    - V1 设计中的 `enable_input_status_tracking` / `input_status_dedup_window`
    - V2 设计中的 `enable_notice_tracking` / `notice_dedup_windows`
  - 结论：后续实现时，**直接落地 V2 的统一通知配置**（`enable_notice_tracking` + `notice_dedup_windows`），并在注释中说明兼容 V1 文档。

- **HeartbeatConfig**
  - 目前 **不存在** 对应配置类，`Config` 中也无 `[heartbeat]` 相关字段。
  - 结论：阶段 2 需要按 `notice_and_heartbeat_design.md` 中定义新增 `HeartbeatConfig`，并同步到 `bot_config.toml` / `template`。

### 二、数据库层现状（`database_model.py`）

- **Messages 模型**
  - 已有字段：
    - `is_notify: BooleanField`：表示“通知消息”但语义较粗。
  - **尚未实现**：
    - V1 设计：`is_input_status_notice` / `input_status_typing`
    - V2 设计：`is_notice` / `notice_type` / `notice_data`
  - 结论：
    - 当前任何通知类消息都不会以结构化形式入库。
    - 迁移时需要：在保留 `is_notify` 的前提下，新增 V2 的三个字段（可选再补兼容字段）。

### 三、消息接收层现状（`src/chat/message_receive/bot.py`）

- **handle_notice_message**
  - 已存在方法，但逻辑为：
    - 仅在 `message_id == "notice"` 时标记 `message.is_notify = True`，打印简单日志。
    - 不做类型解析、不做去重、不入库。
  - 与 V2 设计差异：
    - 没有 `_notice_dedup_cache`。
    - 没有 `_parse_notice_type` / `_should_record_notice` / `_store_notice_message` / `_log_notice_message`。

- **input_status_process**
  - 当前仅根据 `type == "input_status"` 打日志，不入库，不关联 `MessageReceiveConfig`。
  - 与早期 V1 文档的“输入状态专表 / 特殊消息入库”方案未对齐。

### 四、存储层现状（`src/chat/message_receive/storage.py`）

- **MessageStorage.store_message**
  - 关键逻辑：
    - 对 `MessageRecv` 且 `message.is_notify == True` 的情况，**直接跳过存储**：
      - `if isinstance(message, MessageRecv) and message.is_notify: logger.debug("通知消息，跳过存储"); return`
    - 之后所有 VAD / 关键词 / interest 的计算只针对“非通知消息”。
  - 与 V2 设计差异：
    - 没有区分 “需要入库的通知（`is_notice=True`）” 与 “纯日志通知（`is_notice=False`）”。
    - 未实现对通知类消息“跳过 VAD / 关键词 / 兴趣度但仍然入库”的分支。

### 五、主流程与心跳挂载现状（`src/main.py`）

- **初始化与任务调度**
  - 初始化阶段：
    - 启动在线时长统计、统计输出、表达方式自动检查、LPMM、情绪管理器、聊天管理器等。
    - 注册了：
      - `chat_bot.message_process`
      - `chat_bot.echo_message_process`
      - `chat_bot.input_status_process`
  - 调度阶段：
    - 调度的长期任务包括：
      - `OnlineTimeRecordTask` / `StatisticOutputTask`
      - 表情检查任务 / 做梦调度器 `start_dream_scheduler`
      - `self.app.run()` / `self.server.run()` / WebUI
  - **尚未集成**：
    - 任何 `heartbeat_system` 相关的导入与 `start()/stop()` 调用。

### 六、阶段 0 结论 & 后续落地提示

- **现状总结**：
  - 通知相关逻辑目前只做日志，不入库；`Messages` 中也无结构化字段。
  - 心跳系统尚未有任何代码或配置，只存在于设计文档与本文件说明中。
- **阶段 1/2 直接落地建议**：
  - 阶段 1（通知系统）：
    - 在 `MessageReceiveConfig` / `Messages` / `ChatBot.handle_notice_message` / `MessageStorage` 四处按 V2 方案直接落地。
  - 阶段 2（心跳系统）：
    - 按 `notice_and_heartbeat_design.md` 第 2 章 + 第 4 章规划，新建 `heartbeat_state.py` / `heartbeat_system.py`，并在 `main.py` 集成启动。
