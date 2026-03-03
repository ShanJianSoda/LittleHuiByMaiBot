## 通知消息处理与心跳系统设计文档（工程版）

**适用范围**: `src/chat/message_receive/*`, `src/common/database/*`, `src/config/*`, `src/chat/heartbeat/*`, 相关插件与上层规划器/情绪系统。  
**当前状态**: 设计完成，待逐步实现与验证。  

---

## 一、通知（notice）消息处理设计

### 1.1 目标与场景

- **统一处理所有通知类消息**：
  - 撤回消息（recall）
  - 戳一戳 / poke
  - 输入状态（input_status / “对方正在输入”）
  - 未来可扩展的入群 / 退群 / 管理员变更 / 群名修改等。
- **将通知行为转化为可查询的结构化数据**，供以下系统使用：
  - 心跳系统（主动思考与主动发言决策）
  - 情绪系统（可选的情绪信号源）
  - 关系/亲密度系统（例如“经常戳你的人”）
  - 统计与可视化。
- **避免对正常聊天消息与情绪系统造成干扰**：
  - 可过滤、可关闭、可回滚。

### 1.2 输入输出与数据流

- **上游来源**：
  - NapCat / adapter 发来的 `notice` 类事件（如撤回、戳一戳、输入状态）。
  - main 初始化阶段通过 `register_message_handler` / `register_custom_message_handler` 接入。
- **处理链路（统一方案 V2）**：

```text
NapCat/Adapter
  ↓
main.py                # 注册消息与 notice 处理
  ↓
ChatBot.message_process
  ↓
ChatBot.handle_notice_message  # 统一 notice 入口
  ↓
  - 解析 notice 类型与场景（sub_type / scene）
  - 解析出 chat_id / user_id / group_id / platform
  - 按配置决定是否记录（enable_notice_tracking + 去重）
  ↓
_store_notice_message
  ↓
MessageStorage.store_message   # 带 is_notice / notice_type / notice_data
  ↓
Messages 表（结构化保存）
  ↓
下游：心跳系统 / 情绪系统 / 关系系统 / 统计
```

### 1.3 配置设计（MessageReceiveConfig）

**文件**: `src/config/official_configs.py` 中的 `MessageReceiveConfig`

- **新增字段（统一通知配置）**：

```python
@dataclass
class MessageReceiveConfig(ConfigBase):
    """消息接收配置类"""

    ban_words: set[str] = field(default_factory=lambda: set())
    ban_msgs_regex: list[str] = field(default_factory=lambda: [])

    # 通知消息跟踪
    enable_notice_tracking: bool = True
    """是否启用通知消息跟踪（撤回、戳一戳、输入状态等）"""

    notice_dedup_windows: Dict[str, int] = field(default_factory=lambda: {
        "recall": 0,        # 撤回消息不去重
        "poke": 10,         # 戳一戳 10 秒去重
        "input_status": 30, # 输入状态 30 秒去重
    })
    """各类通知消息的去重时间窗口（秒），0 表示不去重"""
```

- **配置文件同步**：
  - `config/bot_config.toml` 与 `template/bot_config_template.toml` 中增加：

```toml
[message_receive]
# ... 原有配置 ...

enable_notice_tracking = true

[message_receive.notice_dedup_windows]
recall = 0
poke = 10
input_status = 30
```

> 约定：后续所有新增配置字段，必须同步出现在  
> `official_configs.py` + `config/bot_config.toml` + `template/bot_config_template.toml`。

### 1.4 数据库模型设计（Messages 表）

**文件**: `src/common/database/database_model.py`

- **统一通知字段**：

```python
class Messages(BaseModel):
    # ... 现有字段 ...

    # 通知消息统一标识
    is_notice = BooleanField(default=False)
    """标识该记录为通知消息（撤回、戳一戳、输入状态等）"""

    notice_type = TextField(null=True)
    """通知类型: recall, poke, input_status, user_join, user_leave, ..."""

    notice_data = TextField(null=True)
    """通知消息的附加数据（JSON 字符串，保持原始字段，如 sub_type/scene 等）"""

    # （可选）兼容输入状态的旧字段
    is_input_status_notice = BooleanField(default=False)
    input_status_typing = BooleanField(null=True)
```

- **迁移建议**：
  - 编写一次性迁移脚本，向 Messages 表添加上述字段。

### 1.5 通知类型枚举（可选）

**文件**: `src/common/data_models/message_data_model.py`

```python
class NoticeType(Enum):
    """通知消息类型"""
    RECALL = "recall"
    POKE = "poke"
    INPUT_STATUS = "input_status"
    USER_JOIN = "user_join"
    USER_LEAVE = "user_leave"
    ADMIN_CHANGE = "admin_change"
    GROUP_NAME_CHANGE = "group_name_change"
    # 后续可扩展
```

> 实际实现时，可先在 `handle_notice_message` 内使用字符串常量，后续再引入 Enum 做重构。

### 1.6 ChatBot.notice 统一处理逻辑

**文件**: `src/chat/message_receive/bot.py`

核心思路：
- 所有 `message_id == "notice"` 的消息，统一走 `handle_notice_message`。
- 解析 `seg.type == "notify"` 以及内部 `data`。
- 基于 `sub_type` / `scene` 解析出 `notice_type`。
- 构造 `chat_id`（支持私聊与群聊）。
- 基于配置进行去重决策。
- 决定是否落库，并以统一格式写入。

伪代码结构：

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



### 1.7 MessageStorage 适配

**文件**: `src/chat/message_receive/storage.py`

要点：
- 对 `MessageRecv` 且 `is_notify` 为 True 的情况：
  - 若 `is_notice` 为 False，则保持老行为：**不存储**（兼容旧通知）。
  - 若 `is_notice` 为 True，则：
    - 使用 `processed_plain_text` / `notice_type` / `notice_data` 写入 Messages。
    - 不计算 VAD / 关键词 / 兴趣度。
- 普通消息不受影响。

目的：
- 避免通知刷 VAD 和关键词。
- 避免影响兴趣度统计。



代码参考

```
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

### 1.8 与情绪系统的交互

**文件**: `src/chat/heart_flow/heartflow_message_processor.py`

建议策略：
- **接收层**：`message.is_notify` 的消息仍然**不走心流主流程**（避免逻辑污染）。
- **情绪更新层**：
  - 心跳 / 通知持久化后，如果需要将通知作为情绪信号：
    - 可以在查询时显式地包含 `is_notice == True` 的记录；
    - 或者在专门的“情绪观测任务”中，根据 `notice_type` 赋予轻量权重。
- 当前阶段设计偏向：**通知默认不影响情绪值**，仅作为行为观察信号。

### 1.9 测试与风险

- **单元测试**：
  - 去重逻辑：同一 `chat_id + notice_type` 在窗口内只落库一次。
  - 通知类型解析：不同 `sub_type` / `scene` 能正确映射到 `notice_type`。
  - 存储逻辑：`is_notice` 消息不触发 VAD 与关键词提取。
- **集成测试**：
  - “戳一戳”后是否有一条对应的 notice 记录。
  - “对方正在输入”多次出现，是否按配置频率落库。
  - 与心跳系统联动：notice 能被心跳查询到。
- **风险与缓解**：
  - 数据量增加 → 使用去重与时间窗口限制；必要时增加索引。
  - 逻辑回滚 → 通过 `enable_notice_tracking` 一键关闭 + SQL 删除 notice 记录。

---

## 二、心跳系统（Heartbeat System）设计

### 2.1 目标与定位

构建一个 **全局状态管理 + 主动思考决策** 的心跳系统，让 Bot 具有:

1. 🧠 **全局视野**: 能够感知所有聊天流的状态
2. 🎯 **主动切换**: 可以在不同聊天流之间切换注意力
3. 💭 **自主思考**: 定时 + 随机触发的主动思考
4. 🔧 **工具调用**: 主动联网搜索、记忆检索等
5. 🤖 **真实体验**: 模拟真人的多任务处理和主动关心

定位：

- **全局视角的“主动行为调度中心”**：
  - 感知所有聊天流的状态与行为信号（包括通知、冷场、亲密度等）。
  - 统一调度主动思考与主动发言，而不是让各子系统“各自起定时器、自说自话”。
- **核心能力**：
  - 维护全局状态（活跃 chat 列表、注意力队列、统计）。
  - 定时 + 随机触发的心跳循环（多级频率：fast/normal/slow）。
  - 接收来自不同 source（做梦、affinity、总结等）的“主动意图”，统一决策。
  - 在恰当的时机，以恰当的频率，对恰当的对象说出“看起来很自然”的话。

### 2.2 配置设计（HeartbeatConfig）

建议在 `official_configs.py` 中新增：

```python
@dataclass
class HeartbeatConfig(ConfigBase):
    """心跳系统配置"""

    enable: bool = True
    """是否启用心跳系统"""

    fast_interval: int = 5
    """快速心跳周期（秒），用于处理紧急事件"""

    normal_interval: int = 30
    """常规心跳周期（秒），用于主动思考与决策"""

    slow_interval: int = 300
    """慢速心跳周期（秒），用于全局清理与统计"""

    active_time_ranges: list[str] = field(default_factory=lambda: ["08:00-23:30"])
    """心跳系统“活跃”的时间段，格式如 'HH:MM-HH:MM'"""

    min_proactive_interval_seconds: int = 600
    """同一 chat 最小主动发言间隔（秒）"""
```

对应 `bot_config.toml` / `bot_config_template.toml` 示例：

```toml
[heartbeat]
enable = true
fast_interval = 5
normal_interval = 30
slow_interval = 300
active_time_ranges = ["08:00-23:30"]
min_proactive_interval_seconds = 600
```

### 2.3 核心架构

简化示意

```text
Global Heartbeat System
├─ Heartbeat State Manager
│   ├─ active_chats: {chat_id -> ChatInfo}
│   ├─ attention_queue: [chat_id ...]
│   └─ thinking_mode（可选：idle / observing 等，无“当前关注”）
│
├─ Heartbeat Scheduler
│   ├─ fast_heartbeat  (处理戳一戳、长时间输入等紧急事件)
│   ├─ normal_heartbeat(常规主动思考、从注意力队列选目标、主动发言)
│   └─ slow_heartbeat  (全局整理与统计)
│
├─ Decision Engine
│   ├─ _update_chat_states      # 从 Messages 表拉取最近消息与通知
│   ├─ _calculate_attention_score
│   ├─ _check_urgent_events
│   ├─ _decide_and_act
│   ├─ _consider_proactive_speak   # 从 attention_queue 选目标后决策是否发言
│   └─ _consider_tool_call      # 预留，未来调用记忆/联网等工具
│
└─ Action Executor
    ├─ _proactive_speak
    ├─ _handle_urgent_event
    └─ 公共接口：get_chat_list / get_state
```

### 2.4 全局状态结构（HeartbeatState / ChatInfo）

**文件**: `src/chat/heartbeat/heartbeat_state.py`（规划）

关键字段：

- `ChatInfo`:
  - 最近一条消息时间 / 内容。
  - 是否冷场、冷场时长。
  - 最近是否有输入状态 / 戳一戳 / 撤回（由 notice 记录推断）。
  - 计算后的 `attention_score`（注意力得分）。
- `HeartbeatState`:
  - `active_chats` 与 `attention_queue`（无“当前关注”字段）。
  - 可选：`thinking_mode` 仅表示 idle / observing 等，不绑定具体 chat。
  - 统计字段（总思考次数、主动发言次数、工具调用次数等）。
  - 每 chat 的上次主动发言时间（用于冷却）。

代码示例：
```
# src/chat/heartbeat/heartbeat_state.py

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import time

class ThinkingMode(Enum):
    """思考模式（不绑定“当前关注”的 chat）"""
    IDLE = "idle"              # 空闲，无活跃聊天或未在决策
    OBSERVING = "observing"     # 观察多个聊天流（按 attention_queue 决策）
    SEARCHING = "searching"     # 正在搜索/检索信息
    REFLECTING = "reflecting"   # 反思/整理记忆

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
    """心跳系统全局状态（无 current_chat_id，不做进入/退出）"""
    
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
    total_proactive_messages: int = 0
    total_tool_calls: int = 0
    
    # 每个 chat 的上次主动发言时间（用于冷却）
    last_proactive_at: Dict[str, float] = field(default_factory=dict)
    
    def is_idle(self) -> bool:
        """是否处于空闲（无活跃聊天）"""
        return len(self.attention_queue) == 0


# 全局状态实例
_global_heartbeat_state = HeartbeatState()

def get_heartbeat_state() -> HeartbeatState:
    """获取全局心跳状态"""
    return _global_heartbeat_state
```

心跳系统核心

```
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
        
        # 决策1: 从注意力队列选出一个或多个目标 chat，考虑是否主动发言（无“切换/进入”）
        await self._consider_proactive_speak()
        
        # 决策2: 是否需要调用工具（随机触发）
        if random.random() < 0.1:  # 10% 概率
            await self._consider_tool_call()
    
    async def _consider_proactive_speak(self):
        """从 attention_queue 选目标 chat，决定是否主动发言（无 current_chat_id / enter/exit）"""
        if not self.state.attention_queue:
            logger.debug("[心跳] 没有活跃聊天流")
            return
        
        # 从注意力队列取当前最值得关注的 chat（或按策略抽样）
        top_chat_id = self.state.attention_queue[0]
        top_chat = self.state.active_chats.get(top_chat_id)
        if not top_chat:
            return
        
        # 主动发言条件
        should_speak = False
        reason = ""
        
        if top_chat.has_poke and top_chat.age() < 30:
            should_speak = True
            reason = "回应戳一戳"
        elif top_chat.has_input_status and top_chat.age() > 60:
            should_speak = True
            reason = "关心长时间输入"
        elif top_chat.is_cold and top_chat.cold_duration > 600 and random.random() < 0.2:
            should_speak = True
            reason = "打破冷场"
        
        if should_speak:
            logger.info(f"[心跳] 决定主动发言: {reason} @ {top_chat.chat_name}")
            await self._proactive_speak(top_chat.chat_id, reason)
    
    async def _consider_tool_call(self):
        """考虑是否调用工具"""
        # TODO: 实现工具调用决策
        # 例如: 随机检索记忆、联网搜索感兴趣的话题等
        logger.debug("[心跳] 考虑工具调用（暂未实现）")
    
    # ========== 行动执行 ==========
    
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
        """处理紧急事件（直接对目标 chat 行动，无 enter/exit）"""
        logger.info(f"[心跳] 处理紧急事件: {event_type} @ {chat_id}")
        
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

### 2.5 与 Messages / 通知系统的集成

- 心跳系统在 `_update_chat_states` 中：
  - 从 `Messages` 表中获取每个 `chat_id` 的最后一条消息。
  - 在一定时间窗口内（如 120 秒）查询对应 `is_notice == True` 的记录。
  - 从中判断：
    - 是否有近期输入状态（`notice_type == "input_status"`）。
    - 是否有近期戳一戳（`notice_type == "poke"`）。
    - 是否有撤回行为（`notice_type == "recall"`）。
  - 用这些信号一起参与 `attention_score` 计算。

### 2.6 与“做梦 / 总结 / affinity”等系统的关系

注意：affinity 是同项目的fork版本中的一个插件，基于亲和力流，路径 `D:\AI\little-hui-by-maimai\MaiBot\src\plugins\built_in\affinity_flow_chatter`， 



- 统一约定：**这些系统不直接发消息**，而是向心跳系统/调度中心提交“主动行为请求”：

```python
request_proactive_action(
    source: str,          # "dream" | "affinity" | "summary" | ...
    chat_id: str,
    intent_type: str,     # "share_dream" | "small_talk" | ...
    meta: Dict[str, Any], # 附加上下文，例如摘要/记忆引用
)
```

- 心跳系统内部：
  - 结合 `HeartbeatConfig` 的冷却逻辑和当前状态：
    - 判断是否接受该请求；
    - 何时执行（可以在 normal_heartbeat 中统一处理）。
- 好处：
  - 统一控制“主动行为的频率”和“全局节奏”；
  - 避免多系统各自起定时器、互不知情。

### 2.7 时间段与频率控制

- 通过 `HeartbeatConfig` 的三个 interval + `active_time_ranges` 实现：
  - `fast_interval`: 保持小，逻辑轻，不做 LLM 调用。
  - `normal_interval`: 核心决策时钟，可做有限 LLM 调用。
  - `slow_interval`: 维护性任务（清理状态、打点统计）。
  - `active_time_ranges`: 在正常工作时段内执行 full 逻辑，其他时间可降级。
- 对单个 chat 的 `min_proactive_interval_seconds`：
  - 在 `_consider_proactive_speak` / 处理外部 `request_proactive_action` 前统一检查；
  - 防止对单个用户过度“搭话”导致打扰感。

### 2.8 对外接口与使用示例

- 获取聊天列表（按关注度排序）：

```python
from src.chat.heartbeat.heartbeat_system import get_heartbeat_system

heartbeat = get_heartbeat_system()
chat_list = heartbeat.get_chat_list()
for chat in chat_list[:5]:
    print(chat.chat_name, chat.attention_score)
```

- 启动集成（在 `main.py` 中）：

```python
from src.chat.heartbeat.heartbeat_system import get_heartbeat_system

heartbeat = get_heartbeat_system()
await heartbeat.start()
```

### 2.9 风险与控制

- **资源占用**：
  - 通过多级间隔 + 时间段控制，限制心跳频率。
  - fast 心跳不做重操作，normal 心跳内限制 LLM 调用次数。
- **行为冲突**：
  - 通过“统一调度”的模式，避免做梦 / 总结 / affinity 等系统各自发声。
  - 引入 per-chat 冷却，防止过度主动。
- **调试与观测**：
  - 心跳系统可在 slow 心跳中输出汇总日志（总思考次数 / 主动发言次数等）。
  - 未来可在 WebUI 中可视化当前 `HeartbeatState`。

### 2.10 与 heart_flow / HeartFCChat 的关系（执行层对接方案 A）

从项目架构角度，推荐采用 **“HeartbeatSystem 只做调度与意图，heart_flow 负责具体对话执行”** 的分层：

- **HeartbeatSystem（调度层）**：
  - 全局视角地维护 `HeartbeatState`（active_chats / attention_queue / 统计），无“当前关注 chat”概念。
  - 基于多级心跳 + 通知信号 + 冷场情况，**决定“何时、对谁、以什么意图”发起主动行为**。
  - 在 normal 心跳中，从 attention_queue 选定的 `chat_id` 构造“主动行为请求对象”（见下文），而不直接拼 prompt 或发送消息。
  - 统一接入“做梦 / 总结 / affinity”等外部 source 的请求，按频率 / 冷却 / 优先级做调度。

- **heart_flow / HeartFCChat（执行层）**：
  - 继续作为**唯一的对话执行引擎**：无论是被动回复还是主动发言，最终都由 heart_flow 决定“怎么说”。
  - 基于当前 chat 的历史、情绪（`mood_manager`）、关系系统与请求 meta，组织 prompt → 调 LLM → 发送消息。
  - 通过新增一个统一入口（例如 `handle_proactive_request` 或 `run_chat(trigger=..., intent_type=..., meta=...)`）接收来自 HeartbeatSystem 的主动行为请求。

> 这样，“用户发消息”时仍走现有的 heart_flow 链路；而“我想主动说点什么”时，由 HeartbeatSystem 决策并把意图交给 heart_flow 执行，既保留全局心跳的调度能力，又避免出现“两套人格”的割裂。

#### 2.10.1 主动行为请求对象（ProactiveActionRequest，示意）

HeartbeatSystem 在 normal 心跳中，针对选定的 `chat_id` 构造一个标准化的请求对象，例如：

```python
@dataclass
class ProactiveActionRequest:
    source: str              # "heartbeat" | "dream" | "affinity" | "summary" | ...
    action_type: str         # "query_history" | "web_search" | "plan_message" | ...
    chat_id: str
    weight: float            # 本次心跳内对此请求的优先级（可用于调度）
    meta: Dict[str, Any]     # 附加上下文：抽样历史、建议搜索词、最近通知信号等
    recent_ticks: list[Any]  # 最近若干次心跳摘要，用于构造“内在活动感”
```

- **meta.history_samples**（可选）：
  - HeartbeatSystem 在 `_update_chat_states` 或 `_decide_and_act` 中，从 `Messages` 抽样最近一段历史：
    - 先取最近 `N` 条消息；
    - 为每条构造“**指数衰减 + 随机扰动**”的权重（越新越重，越旧权重指数下降，再乘以少量随机因子）；
    - 选出若干条代表性消息作为 `history_samples`，供 heart_flow 直接阅读。
- **meta.web_search_query**（可选）：
  - 由 HeartbeatSystem 根据最近话题 / 关键词，构造一个简单的联网搜索 query，让 heart_flow 决定是否真的发起搜索。
- **recent_ticks**：
  - HeartbeatSystem 在 fast/normal/slow 心跳结束时记录一条 `HeartbeatTick`（例如：时间戳、心跳类型、当前 active_chats 数量、是否检测到紧急事件等），
  - 在构造请求时附上最近若干条，用于让 heart_flow 在 prompt 中加入“最近这段时间我在做什么”的内在活动感，例如：
    - “刚刚在几个群里巡了一圈，觉得这里有点冷清……”  
    - “我看到你这会儿输入了又删掉几次，好像在犹豫些什么。”

HeartBeatSystem 不负责解释这些 tick，只负责**把原始“心跳轨迹”传给 heart_flow**，由 heart_flow 在自然语言层面进行消化与呈现。

#### 2.10.2 随机、可重复、顺序不固定的行为选择

在 Decision Engine 中，HeartbeatSystem 可以用简单、可控的随机策略选择行为类型和目标 chat：

- **目标聊天选择**：
  - 在 attention_queue 的前若干名中，按注意力得分 + 少量随机扰动抽样一个 `chat_id`。
- **行为类型选择**：
  - 在 `["query_history", "web_search", "plan_message", ...]` 中随机选择一个或几个（可允许同类行为重复发生，不强制去重）。
- **样本选择**：
  - 历史消息、通知信号等本身也通过“指数衰减 + 随机扰动”的方式抽样，使得**近期更可能被选中，但旧片段也有小概率被翻出来**，让行为显得更自然。

心跳系统不追求严格的“行为覆盖”，而是模拟一种“有偏好的随机注意力”：  
近期活跃的聊天更容易被选中，但偶尔也会“翻旧账”；某种行为类型可能连续出现几次，也可能长时间不出现，从而避免机械感。

### 2.11 记忆系统与心跳/心流的协作（拓展）

本节约定：**心跳系统**与**心流系统**均可访问记忆系统接口（查询或 prompt 拼接），二者职责不同、互补使用。

#### 2.11.1 原则说明

- **“十分钟后提醒”仅为举例**：主动行为不一定要由定时器构造。只要心跳系统在决策时通过调用记忆（或其它）API 得到“该做什么”的结论，并将**决策结果（内容/意图）**通过 `ProactiveActionRequest` 交给心流系统即可。
- **心跳系统**：负责**是否执行、对谁执行、执行何种意图**的决策；决策过程中可调用记忆系统 API 做查询（例如到期提醒、待办、短期记忆中的约定等），得到的结果作为 `meta` 传给心流。
- **心流系统**：负责**怎么说、怎么生成回复**；既可沿用现有“被动回复时”的记忆检索与 prompt 拼接，也可在处理主动行为请求时使用心跳传入的 `meta`（如 `meta.reminders`、`meta.memory_snippets`）做 prompt 注入。

#### 2.11.2 记忆访问方式约定

| 使用方     | 使用场景                     | 记忆访问方式 |
|------------|------------------------------|--------------|
| 心跳系统   | 决策阶段：判断是否主动行为、对谁、带什么内容 | 调用记忆系统 API 查询（如到期短期记忆、待办、约定等），结果写入 `ProactiveActionRequest.meta` |
| 心流系统   | 被动回复：生成对用户消息的回复 | 现有流程：`build_memory_retrieval_prompt` 等，即记忆检索 + prompt 拼接 |
| 心流系统   | 主动发言：执行心跳下发的主动行为 | 使用 `request.meta` 中的内容（如提醒列表、记忆片段）做 prompt 拼接；可选再调用记忆 API 做补充检索 |

#### 2.11.3 数据流示意

```text
记忆系统（三层架构预留）
├─ 短期记忆（如待办、提醒、约定）
├─ 中期/长期记忆（人物、关系、历史）
└─ 统一查询/写入 API
        ↑                    ↑
        │ 决策时查询          │ 回复/主动发言时查询或拼接
        │                    │
  心跳系统 _decide_and_act    心流系统（HeartFChatting / 回复生成）
        │                    │
        └── ProactiveActionRequest(meta=...) ──→ 心流执行
```

约定：心跳与心流**都应能**获取记忆系统接口进行查询或 prompt 拼接；具体 API 形态由记忆系统升级（如三层记忆）时统一暴露，此处仅约定调用关系与数据流。

### 2.12 心跳系统决策 Prompt 与模型选型

心跳在 **normal 心跳**中做“是否主动行为、对谁、用什么工具/意图”的决策时，会构造一段**面向 LLM 的 system/决策 prompt**，再调用模型得到结构化输出（如：`action_type`、`chat_id`、简要理由）。本节约定该 prompt 的结构与模型选型原则，以便实现时**快而省、效果可接受**。

#### 2.12.1 决策 Prompt 模板（初版）

每次 normal 心跳决策时，可组装如下结构的 prompt（具体占位由心跳系统从状态与记忆系统填充）：

```text
时间：{当前时间，如 2025-02-28 14:30}
人格：{从 bot/人格配置获取的简短描述，如身份、说话风格、约束}

情绪：{从情绪系统获取}

前 n 次心跳摘要：
{最近若干次 HeartbeatTick 的简短摘要：时间、类型(fast/normal/slow)、执行策略、内容等，条数可配置，如 3～5 条}

可选工具：{当前可用的工具/行为类型列表，如 query_history, web_search, plan_message, send_reminder, ...}

目前活跃记忆：{从记忆系统 API 获取的、与当前决策相关的记忆片段：到期提醒、待办、近期约定等，条数上限控制 token}

当前活跃聊天与注意力（简要）：
{每个 chat_id 的 last_message_time、冷场时长、近期 notice 信号(poke/input_status/recall)、attention_score，仅摘要列表}

其他上下文（可选）：
- 各 chat 的 min_proactive_interval 是否已过冷却
- 外部待执行的 request_proactive_action 队列长度
```

- **时间**：便于模型理解“当前时刻”，避免与历史 tick 混淆。
- **人格**：与主对话人格一致，保证决策风格不割裂；此处仅简短摘要，不展开长设定。
- **前 n 次心跳**：用**摘要**而非原始日志，控制 token；实现时可由心跳在 slow/fast 中维护一个 `recent_tick_summaries` 列表（如最近 5 条，每条约 1～2 句）。
- **可选工具**：当前心跳周期允许的行为类型（可与配置或插件注册的 action 对齐），便于模型输出合规的 `action_type`。
- **目前活跃记忆**：来自记忆系统 API 的查询结果（如“到期短期记忆/待办”），条数上限（如 5～10 条）由配置或默认值限制，避免 token 爆炸。
- **当前活跃聊天与注意力**：来自 `HeartbeatState.active_chats` 与 `attention_queue` 的摘要，只列关键字段，不贴完整历史。
- **其他**：冷却状态、外部请求队列等，按需简短补充。

输出格式需约定（如 JSON 或固定 schema）：`action_type`、`chat_id`（可选）、`reason_brief`（可选）、`skip`（本轮不执行主动行为）等，便于心跳系统解析后构造 `ProactiveActionRequest` 或直接跳过。

#### 2.12.2 触发量与 Token 约束

- **触发量大且频繁**：normal 心跳按配置间隔（如 30s～60s）执行，一天内决策次数高，单次 prompt 的 token 必须严格控制。
- **单轮 token 上限建议**：
  - 输入：人格 1～2 句 + 前 n 次心跳摘要（n≤5，每条约 1 句）+ 工具列表 1 行 + 活跃记忆 N 条（N 可配置，如 5）+ 活跃聊天摘要（每 chat 1 行）。总输入建议控制在 **1k～2k token** 内（视模型 context 可再收紧）。
  - 输出：仅需结构化字段（action_type、chat_id、reason_brief 等），**几十 token 即可**。
- **实现要点**：
  - “前 n 次心跳”不塞原始日志，只塞**预先写好的短摘要**（心跳自己在 tick 结束时写一句总结）。
  - 活跃记忆由记忆系统 API 返回时即做**条数/长度上限**（或在心跳侧截断）。
  - 活跃聊天列表只保留当前 `attention_queue` 前 K 个（如 5～10），每条仅（未定，需要询问设计）：chat_id、last_message_time、冷场时长、notice 标志、score。

#### 2.12.3 模型选型：快、省、性价比

- **目标**：心跳调用频率高、单次 token 量中等，模型应**响应快、单价低、效果不拉胯**，优先性价比。
- **建议**：
  - 使用**独立于主对话的模型配置**（如 `model_task_config.heartbeat` 或 `heartbeat_decision`），与 `replyer` / `tool_use` 分离，便于单独选小模型或便宜 API。
  - 模型能力要求：能理解上述结构化 prompt、输出约定 schema（如 JSON）；不需要长上下文、不需要复杂推理，**小模型或轻量 API 即可**。
  - 在 `official_configs` / `bot_config.toml` 中为心跳决策预留 `model_set` 或等价配置，默认指向“快而便宜”的模型；若未配置则可在初版回退为**纯规则决策**（不调 LLM），避免强依赖。
- **与心流/做梦的对比**：
  - 心流（主回复）：偏质量与一致性，可用更强模型、更长上下文。
  - 做梦（总结/工具）：偏任务完成与工具调用，模型配置已独立（如 `tool_use`）。
  - 心跳（调度决策）：偏“选谁、选什么行为”，轻量结构化输出，故**更看重延迟与成本**。

#### 2.12.4 实现检查清单（后续迭代）

- [ ] 在配置中新增心跳决策用模型配置（如 `heartbeat` / `heartbeat_decision`），并在 prompt 组装处使用。
- [ ] 实现“前 n 次心跳”的摘要维护（在 fast/normal/slow tick 结束时写入短句），并在 prompt 中只填摘要。
- [ ] 与记忆系统 API 对接：获取“当前活跃记忆”并做条数/长度上限。
- [ ] 约定并实现模型输出解析（JSON/schema），驱动 `ProactiveActionRequest` 构造或 skip。
- [ ] 单轮输入 token 上限配置化，并在组装 prompt 时做截断/省略。

---

## 三、实现优先级与检查清单（工程视角）

### 3.1 通知系统 ✅

- [x] 在 `official_configs.py` 中扩展 `MessageReceiveConfig`。
- [x] 更新 `bot_config.toml` / 模板中的通知配置。
- [x] 为 `Messages` 表添加 `is_notice` / `notice_type` / `notice_data` 字段（及迁移脚本）。
- [x] 在 `ChatBot.handle_notice_message` 中实现统一解析与入库逻辑。
- [x] 在 `MessageStorage.store_message` 中适配通知消息的 VAD / 关键词跳过逻辑。
- [ ] 针对撤回 / 戳一戳 / 输入状态分别编写基本集成测试。

### 3.2 心跳系统

- [x] 在 `official_configs.py` 中新增 `HeartbeatConfig` 并挂入总 `Config`。
- [x] 更新 `bot_config.toml` / 模板中的 `[heartbeat]` 配置块。
- [x] 新建 `src/chat/heartbeat/heartbeat_state.py` 与 `heartbeat_system.py`，实现基础骨架：
  - [x] `HeartbeatState` 与 `ChatInfo` 数据结构。
  - [x] fast/normal/slow 三级心跳任务框架。
  - [x] `_update_chat_states` 从 Messages 查询最近状态。
  - [ ] `_calculate_attention_score` 的基础实现（当前为占位：`attention_queue` 按 chat_id 排序，notice 与冷场/得分在后续迭代补充）。
  - [x] `_check_urgent_events` / `_decide_and_act` 的最小实现（可以先只 log，不发言）。
- [ ] 在 `main.py` 中集成心跳系统启动（可受配置控制）。

**目录与职责约定（当前阶段）**：

- 心跳系统仅在 `src/chat/heartbeat/` 目录下维护：
  - `heartbeat_state.py`：纯状态容器（数据结构）。
  - `heartbeat_system.py`：调度与规则骨架（不直接负责 LLM 调用）。
- **不额外新增** `actions/`、`core/`、`planner/`、`proactive/`、`tools/` 等子目录：
  - 心跳系统聚焦于「注意力/节奏调度」，不重复实现一套完整的心流/规划框架。
  - 主动行为的真正执行（构造 prompt、调用 LLM、落地回复）仍由心流系统 / 做梦系统等已有模块负责。
- 在完成「初版心跳系统」（至少包含 `_calculate_attention_score` 的基础实现 + 简单决策骨架）之前，**不接入 `main.py`** 启动，只在单元/集成环境下自测。

### 3.3 与其他系统的对接（后续）

- [ ] 定义统一的 `request_proactive_action` 接口。
- [ ] 为做梦系统 / affinity 插件提供对接样例。
- [ ] 在心跳系统内实现对外源请求的排队与冷却控制。
- [ ] 将当前“简单模板回复”的 `_proactive_speak` 替换为 LLM 生成方案，并与情绪 / 关系系统打通。
- [ ] **心跳决策 Prompt 与模型**：按 2.12 实现决策 prompt 模板、前 n 次心跳摘要、活跃记忆注入、独立模型配置（快/省/性价比），及输出解析与 token 上限控制。

### 3.4 记忆系统对接（后续，见 2.11）

- [ ] 记忆系统对外提供统一查询/写入 API（兼容未来三层记忆架构）。
- [ ] 心跳系统在决策流程中可调用记忆 API，并将结果写入 `ProactiveActionRequest.meta`。
- [ ] 心流系统在处理主动行为请求时，使用 `meta` 中的记忆/提醒等内容做 prompt 拼接；保留现有被动回复时的记忆检索与拼接逻辑。

---

## 四、开发计划规划（一步一步来）

本节把上面的“实现清单”展开成**可直接落地的迭代计划**，每一步都给出：目标、改动点、产出与验收方式。建议严格按顺序推进：先让通知稳定入库，再做心跳骨架与集成，最后再接主动行为与外部系统。

**说明**：主动行为（例如“十分钟后提醒”）不一定要由定时器构造，只要心跳在决策时通过调用 API（如记忆系统）得到执行依据，并将**决策结果**提供给心流系统即可。心跳系统与心流系统**都应能**访问记忆系统接口进行查询或 prompt 拼接（详见 2.11）。

### 4.1 阶段 0：代码基线与联调准备（只读检查）✅ 已完成

- **目标**：确认当前工程的真实现状（已有实现/字段/入口），避免重复造轮子或改错位置。
- **检查点**：
  - `src/config/official_configs.py`：`MessageReceiveConfig` 当前结构与加载逻辑。
  - `src/common/database/database_model.py`：`Messages` 表已有字段与初始化/迁移方式。
  - `src/chat/message_receive/bot.py`：是否已有 notice 相关入口与消息分发策略。
  - `src/chat/message_receive/storage.py`：存储链路、VAD/关键词/兴趣度处理位置。
  - `src/main.py`：消息处理注册点与未来心跳系统的启动挂载点。
- **产出**：记录“需要改的文件清单 + 现有实现差异点”（更新到本文档）。
- **验收**：不改代码，仅能清晰回答“通知从哪进、存储在哪做、心跳在哪启动”。

**验收记录**：通知入口：`main.py` 注册 `chat_bot.message_process` → 内先调 `handle_notice_message`，`message_id == "notice"` 时走统一 notice 处理。存储：notice 在 `_store_notice_message` 中调 `MessageStorage.store_message`；`storage.py` 对 `is_notify and not is_notice` 跳过存储，对 `is_notice == True` 写入 Messages 并跳过 VAD/关键词/兴趣度。心跳挂载点：`main.py` 的 `initialize()` 可在此后增加 `get_heartbeat_system().start()`（阶段 2 第 7 步实现）。

### 4.2 阶段 1：通知系统落地（Notice Tracking）✅ 已完成

#### 4.2.1 第 1 步：配置层（MessageReceiveConfig + TOML）✅

- **目标**：通知跟踪开关、去重窗口可配置，可随时关闭回滚。
- **改动点**：
  - `src/config/official_configs.py`：给 `MessageReceiveConfig` 增加：
    - `enable_notice_tracking: bool`
    - `notice_dedup_windows: Dict[str, int]`
  - `config/bot_config.toml`、`template/bot_config_template.toml`：同步新增：
    - `enable_notice_tracking = true`
    - `[message_receive.notice_dedup_windows]` 下的 `recall/poke/input_status`
- **产出**：配置字段可读写，默认值与文档一致。
- **验收**：启动不报错；改 `enable_notice_tracking=false` 后 notice 不再落库（后续步骤实现后验证）。
- **验收记录**：`MessageReceiveConfig` 已含 `enable_notice_tracking`、`notice_dedup_windows`（recall=0, poke=10, input_status=30）；`Config` 含 `message_receive`；`bot_config.toml` 与模板已含对应配置。

#### 4.2.2 第 2 步：数据库模型（Messages 表新增 notice 字段 + 迁移）✅

- **目标**：以统一结构存储所有通知，供心跳/统计复用。
- **改动点**：
  - `src/common/database/database_model.py` 的 `Messages` 增加字段：
    - `is_notice`、`notice_type`、`notice_data`
    - （可选兼容）`is_input_status_notice`、`input_status_typing`
  - 增加一次性迁移脚本/迁移逻辑（按项目惯例落地）。
- **产出**：数据库表结构升级完成。
- **验收**：程序能正常读写普通消息；新字段存在且默认值正确。
- **验收记录**：`Messages` 已含 `is_notify`、`is_notice`、`notice_type`、`notice_data`；`initialize_database()` 自动补缺失列，符合项目惯例。

#### 4.2.3 第 3 步：ChatBot 统一 notice 入口（解析 + 去重 + 入库）✅

- **目标**：所有 notice 事件统一解析为 `notice_type` 并写入 `Messages`。
- **改动点**：
  - `src/chat/message_receive/bot.py`：
    - 增加 `_notice_dedup_cache: Dict[(chat_id, notice_type), timestamp]`
    - 实现 `handle_notice_message`：
      - 识别 `message_id == "notice"` 且 `seg.type == "notify"`
      - 从 `seg.data` 解析 `sub_type` / `scene`
      - `_parse_notice_type(sub_type, scene)`（至少支持 recall/poke/input_status）
      - 计算 `chat_id`（group/private）
      - `_should_record_notice(chat_id, notice_type)`（按配置窗口去重）
      - `_store_notice_message(...)`（携带 `is_notice/notice_type/notice_data`）
- **产出**：notice 解析、去重、落库链路完成，并有可读日志。
- **验收**：
  - 触发戳一戳/输入状态等后，日志显示识别成功；
  - 同 chat 同 notice_type 在窗口内触发多次，只落库一次（poke/input_status）。
- **验收记录**：`_notice_dedup_cache`、`handle_notice_message`、`_parse_notice_type`（recall/poke/input_status）、`_should_record_notice`、`_store_notice_message` 已完整实现，逻辑与文档一致。

#### 4.2.4 第 4 步：MessageStorage 适配（跳过 VAD/关键词/兴趣度）✅

- **目标**：通知只作为“行为记录”，不污染正常聊天分析统计。
- **改动点**：
  - `src/chat/message_receive/storage.py` 的 `MessageStorage.store_message`：
    - 对 `message.is_notify == True` 且 `is_notice == True` 的记录：
      - 写入 `Messages`（带 `is_notice/notice_type/notice_data`）
      - 明确跳过 VAD / 关键词 / 兴趣度等处理
    - 其他普通消息保持不变
- **产出**：通知记录与普通消息处理完全隔离。
- **验收**：通知记录存在，但不会触发 VAD/关键词相关日志或字段更新（视项目实现而定）。
- **验收记录**：`store_message` 对 `is_notice == True` 的 MessageRecv 设 `emotion_v/a/d=None`、`interest_value=0`、`key_words=""`，且写入 `is_notice/notice_type/notice_data`；VAD 仅对非 is_notice 调用，符合设计。

### 4.3 阶段 2：心跳系统骨架与集成（Heartbeat Skeleton）

#### 4.3.1 第 5 步：HeartbeatConfig 配置接入

- **目标**：心跳系统可通过配置启停，并具备 fast/normal/slow 三档间隔与时间段控制。
- **改动点**：
  - `src/config/official_configs.py`：新增 `HeartbeatConfig` 并挂入总配置。
  - `config/bot_config.toml`、`template/bot_config_template.toml`：新增 `[heartbeat]` 配置块。
- **产出**：配置可加载；关闭后不启动任何心跳任务。
- **验收**：`enable=false` 时不产生心跳日志；`enable=true` 时按 interval 产生日志（下一步实现后验证）。

#### 4.3.2 第 6 步：实现心跳系统骨架（项目级框架）

- **目标**：按照项目规范搭建完整的心跳系统框架（State + System + 单例获取），暂不实现复杂决策，只提供清晰的扩展点。
- **改动点**：
  - 新建 `src/chat/heartbeat/heartbeat_state.py`：定义 `ChatInfo` / `HeartbeatState` 与 `get_heartbeat_state()`：
    - `ChatInfo`：封装单个聊天流的最近消息时间/内容、冷场状态、近期通知信号（`has_recent_input_status` / `has_recent_poke` / `has_recent_recall`）、`attention_score` 等；
    - `HeartbeatState`：保存 `active_chats` / `attention_queue` / 统计字段与每 chat 的上次主动发言时间（无 current_chat_id）；
    - 使用 `get_heartbeat_state()` 提供全局单例（仿照 `mood_manager` / `chat_manager` 风格，仅做状态容器，不负责逻辑）。
  - 新建 `src/chat/heartbeat/heartbeat_system.py`：
    - 定义 `HeartbeatSystem` 类，持有 `HeartbeatState` 引用，负责：
      - `start()/stop()`：依据 `HeartbeatConfig` 启动 / 停止 fast / normal / slow 三个心跳协程（内部使用 `asyncio.create_task`）；
      - `_fast_heartbeat()`：按 `fast_interval` 轮询，调用 `_check_urgent_events()`（当前阶段仅日志占位）；
      - `_normal_heartbeat()`：按 `normal_interval` 轮询，调用 `_update_chat_states()` 与 `_decide_and_act()`；
      - `_slow_heartbeat()`：按 `slow_interval` 轮询，调用 `_global_cleanup()` 输出统计与做轻量清理；
      - `_update_chat_states()`：从 `ChatManager.streams` 与 `Messages` 拉取每个 `chat_id` 最近一条消息，构造或更新 `ChatInfo`；近期 notice 查询与注意力得分计算在后续迭代中补充；
      - `_decide_and_act()` / `_check_urgent_events()` / `_global_cleanup()`：当前仅输出结构化日志，作为后续规则实现的挂载点；
      - 对外接口：`get_chat_list()` / `get_state()`。
    - 定义 `get_heartbeat_system()`：提供全局单例 HeartbeatSystem，供 `main.py` 或其他模块按需获取。
  - 约定：
    - 心跳系统采用“**System + State + get_xxx() 单例**”模式，而非再额外添加 `manager/processor` 层，以减少心智负担；
    - fast/normal/slow 心跳协程由 HeartbeatSystem 自行管理，**不接入 `async_task_manager`**，避免与插件/统计任务调度混淆。
- **产出**：一个结构清晰、职责边界明确的心跳系统代码框架，具备：
  - 可配置的启停和三档频率（由 `HeartbeatConfig` 控制）；
  - 全局状态访问入口（`get_heartbeat_state()` / `get_heartbeat_system()`）；
  - 完整的扩展点（状态更新、注意力计算、紧急事件、决策与全局清理）。
- **验收**：代码结构符合项目现有 `*_manager` / `*_system` 的风格，后续只需在标记好的方法内填充业务逻辑即可，无需再调整整体架构。

#### 4.3.3 第 7 步：在 main 中集成启动

- **目标**：心跳系统成为机器人生命周期的一部分，可配置控制。
- **改动点**：
  - `src/main.py`：在初始化完成后按配置 `await heartbeat.start()`；在退出路径中 `await heartbeat.stop()`（若有）。
- **产出**：无需额外脚本即可运行心跳系统。
- **验收**：
  - 与原有聊天/心流逻辑不冲突；关开配置可控。
  - 仅在「初版心跳系统」完成后执行本步骤（即：`_calculate_attention_score` 已有可用基础实现，normal/fast/slow 心跳在测试环境中表现稳定）。

### 4.4 阶段 3：对外主动行为请求与后续增强（迭代项）

#### 4.4.1 第 8 步：统一 `request_proactive_action` 接口（只入队 + 冷却）

- **目标**：做梦/总结/affinity 等系统不直接发消息，而是向心跳系统提交“主动行为请求”。
- **改动点**：
  - 在心跳系统暴露 `request_proactive_action(source, chat_id, intent_type, meta)`：
    - 进入队列或缓存结构
    - `normal_heartbeat` 中消费，并做 `min_proactive_interval_seconds` 冷却检查
    - 当前阶段只 log，不真正发言
- **产出**：外部系统可无侵入接入调度中心。
- **验收**：调用接口后，心跳日志可看到请求被接收/排队/忽略（因冷却）等结果。

**设计参考与边界说明**：

- **提示词与多轮决策**：
  - 心跳系统本身**不直接持有复杂 LLM 提示词**，只是根据状态/记忆结果决定「是否需要主动行为」「大致意图类型」。
  - 具体的 prompt 设计、多轮工具调用与规划流程，可参考并复用：
    - `src/chat/heart_flow/` 中现有的心流提示词与对话规划模式；
    - `src/dream/` 中的做梦/总结调度策略；
    - `src/memory_system/chat_history_summarizer.py` 中的记忆/总结调用方式。
- **职责划分**：
  - 心跳系统：做「注意力与主动行为调度器」（决定 *何时*、对 *哪个 chat*、以 *什么 intent_type* 发起请求），并通过 `request_proactive_action` / `ProactiveActionRequest.meta` 传递必要上下文（如冷场时长、最近 notice、相关记忆片段标识等）。
  - 心流系统 / 做梦系统 / 记忆系统：继续负责「如何说」「说什么」，包括 prompt 设计、工具调用编排、多轮决策等。
  - 通过这种拆分，心跳可以轻量复用上述模块的能力，而不在 `src/chat/heartbeat/` 下再构造一套 `actions/core/planner/tools` 目录结构。

#### 4.4.2 第 9 步：实现真实 `_proactive_speak`（LLM + 情绪/关系联动）

- **目标**：从“观测与调度”升级为“自然的主动发言”；心跳将决策内容通过 `ProactiveActionRequest.meta` 提供给心流，心流负责生成与发送。
- **改动点（建议单独迭代）**：
  - 心跳系统：`_proactive_speak` 或统一入口构造 `ProactiveActionRequest(chat_id, intent_type, meta=...)`，其中 `meta` 可含提醒内容、记忆片段等（来自心跳侧记忆 API 调用或其它决策结果）。
  - 心流系统：新增或复用“主动行为”入口（如 `handle_proactive_request`），接收请求后使用 `meta` 做 prompt 拼接并调用 LLM 生成回复、发送。
  - 接入情绪/关系系统用于语气与频率调节。
  - 完善观测与统计（slow_heartbeat 汇总）。
- **验收**：主动发言自然、频率可控、不打扰；心跳决策结果能通过 meta 传递至心流并影响回复内容；可通过配置快速关闭。

### 4.5 阶段 4：记忆系统对接（迭代项，依赖三层记忆架构）

本阶段在记忆系统升级为三层架构并暴露统一 API 后推进。心跳与心流均可调用记忆接口（查询或 prompt 拼接），职责见 2.11。

#### 4.5.1 第 10 步：记忆系统统一 API（记忆侧）

- **目标**：记忆系统对外提供统一查询/写入 API，供心跳与心流调用；API 形态兼容短期/中期/长期记忆（如按类型或接口区分）。
- **改动点**：
  - 在 `src/memory_system/` 或约定模块中定义并实现：
    - 查询接口：按 chat_id、类型、时间范围等查询记忆（含到期提醒、待办等）；
    - 写入接口：供心流或上游在对话中写入短期/长期记忆（如用户说“十分钟后提醒我”时由心流或规划器写入短期记忆）。
  - 文档化 API 入参、出参及使用场景。
- **产出**：记忆 API 可被心跳、心流以同步或异步方式调用。
- **验收**：心跳/心流模块能成功调用 API 并拿到约定格式结果；不破坏现有 `build_memory_retrieval_prompt` 等用法。

#### 4.5.2 第 11 步：心跳系统接入记忆 API（决策阶段）

- **目标**：心跳在 `_decide_and_act` 或 `_consider_tool_call` 中调用记忆 API，根据查询结果决定是否发起主动行为，并将结果写入 `ProactiveActionRequest.meta`。
- **改动点**：
  - 在 `HeartbeatSystem` 决策流程中调用记忆系统 API（例如查询“当前到期的短期记忆/待办”）。
  - 若存在需执行的项（如到期提醒），构造 `ProactiveActionRequest(chat_id, intent_type="reminder" 等, meta={ "reminders": [...] })` 并交给心流执行。
  - 不做定时器强绑定：决策逻辑可基于 API 返回的“到期项”即可，具体触发时机由心跳周期与 API 结果决定。
- **产出**：心跳决策可依赖记忆查询结果；主动行为请求的 meta 中可携带提醒/记忆片段等内容。
- **验收**：记忆中有到期项时，心跳能生成对应主动行为请求并传递 meta；心流侧能收到并使用。

#### 4.5.3 第 12 步：心流系统使用 meta 与记忆 API（执行阶段）

- **目标**：心流在处理主动行为请求时，使用心跳传入的 `meta`（如 `meta.reminders`）做 prompt 拼接；必要时再调用记忆 API 做补充检索。
- **改动点**：
  - 心流“主动行为”入口：读取 `ProactiveActionRequest.meta`，将提醒内容、记忆片段等注入当前轮次的 prompt。
  - 可选：对主动发言场景增加一次记忆检索或仅使用 meta 中的内容，避免重复查询。
  - 保持被动回复时现有记忆检索与 prompt 拼接逻辑不变。
- **产出**：主动发言的回复能体现心跳下发的提醒/记忆内容；心流仍能独立调用记忆 API 做检索或拼接。
- **验收**：端到端测试：写入一条“N 分钟后提醒”的短期记忆，心跳在决策时查到并下发，心流生成并发送提醒消息。

