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
class ChatBot:
    def __init__(self):
        self.bot = None
        self._started = False
        self.heartflow_message_receiver = HeartFCMessageReceiver()
        # 通知消息去重缓存 {(chat_id, notice_type): last_timestamp}
        self._notice_dedup_cache: Dict[Tuple[str, str], float] = {}

    async def handle_notice_message(self, message: MessageRecv):
        if message.message_info.message_id != "notice":
            return False

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

            notice_type = self._parse_notice_type(sub_type, scene)
            if not notice_type:
                logger.debug(f"[notice] 未识别的通知类型: sub_type={sub_type}, scene={scene}")
                return True

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

            # 去重
            if not self._should_record_notice(chat_id, notice_type):
                logger.debug(f"[notice] {notice_type} 去重，跳过记录")
                return True

            # 入库
            await self._store_notice_message(
                message=message,
                chat_id=chat_id,
                notice_type=notice_type,
                notice_data=notice_data,
            )

            # 日志输出
            self._log_notice_message(notice_type, notice_data, mi)

        except Exception as e:
            logger.error(f"[notice] 处理通知消息失败: {e}")
            logger.error(traceback.format_exc())

        return True
```

> 详细实现见 `dev.md` 中的 V2 方案片段，可在编码时直接参考。

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

- **全局视角的“主动行为调度中心”**：
  - 感知所有聊天流的状态与行为信号（包括通知、冷场、亲密度等）。
  - 统一调度主动思考与主动发言，而不是让各子系统“各自起定时器、自说自话”。
- **核心能力**：
  - 维护全局状态（当前关注 chat、活跃 chat 列表、注意力队列、统计）。
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

简化示意（与 `dev.md` 中版本一致）：

```text
Global Heartbeat System
├─ Heartbeat State Manager
│   ├─ current_chat_id
│   ├─ active_chats: {chat_id -> ChatInfo}
│   ├─ attention_queue: [chat_id ...]
│   └─ thinking_mode
│
├─ Heartbeat Scheduler
│   ├─ fast_heartbeat  (处理戳一戳、长时间输入等紧急事件)
│   ├─ normal_heartbeat(常规主动思考、切换聊天、主动发言)
│   └─ slow_heartbeat  (全局整理与统计)
│
├─ Decision Engine
│   ├─ _update_chat_states      # 从 Messages 表拉取最近消息与通知
│   ├─ _calculate_attention_score
│   ├─ _check_urgent_events
│   ├─ _decide_and_act
│   ├─ _consider_switch_chat
│   ├─ _consider_proactive_speak
│   └─ _consider_tool_call      # 预留，未来调用记忆/联网等工具
│
└─ Action Executor
    ├─ enter_chat / exit_chat
    ├─ _proactive_speak
    ├─ _handle_urgent_event
    └─ 公共接口：get_chat_list / get_current_chat_id / get_state
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
  - `current_chat_id` + `thinking_mode`。
  - `active_chats` 与 `attention_queue`。
  - 统计字段（总思考次数、主动发言次数、工具调用次数等）。

> 详细字段设计已在 `dev.md` 中给出，可直接作为实现参考。

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

- 切换当前关注聊天：

```python
await heartbeat.enter_chat("qq_private_123456")
current_id = heartbeat.get_current_chat_id()
await heartbeat.exit_chat()
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

---

## 三、实现优先级与检查清单（工程视角）

### 3.1 通知系统

- [ ] 在 `official_configs.py` 中扩展 `MessageReceiveConfig`。
- [ ] 更新 `bot_config.toml` / 模板中的通知配置。
- [ ] 为 `Messages` 表添加 `is_notice` / `notice_type` / `notice_data` 字段（及迁移脚本）。
- [ ] 在 `ChatBot.handle_notice_message` 中实现统一解析与入库逻辑。
- [ ] 在 `MessageStorage.store_message` 中适配通知消息的 VAD / 关键词跳过逻辑。
- [ ] 针对撤回 / 戳一戳 / 输入状态分别编写基本集成测试。

### 3.2 心跳系统

- [ ] 在 `official_configs.py` 中新增 `HeartbeatConfig` 并挂入总 `Config`。
- [ ] 更新 `bot_config.toml` / 模板中的 `[heartbeat]` 配置块。
- [ ] 新建 `src/chat/heartbeat/heartbeat_state.py` 与 `heartbeat_system.py`，实现基础骨架：
  - [ ] `HeartbeatState` 与 `ChatInfo` 数据结构。
  - [ ] fast/normal/slow 三级心跳任务框架。
  - [ ] `_update_chat_states` 从 Messages 查询最近状态。
  - [ ] `_calculate_attention_score` 的基础实现。
  - [ ] `_check_urgent_events` / `_decide_and_act` 的最小实现（可以先只 log，不发言）。
- [ ] 在 `main.py` 中集成心跳系统启动（可受配置控制）。

### 3.3 与其他系统的对接（后续）

- [ ] 定义统一的 `request_proactive_action` 接口。
- [ ] 为做梦系统 / affinity 插件提供对接样例。
- [ ] 在心跳系统内实现对外源请求的排队与冷却控制。
- [ ] 将当前“简单模板回复”的 `_proactive_speak` 替换为 LLM 生成方案，并与情绪 / 关系系统打通。

---

## 四、开发计划规划（一步一步来）

本节把上面的“实现清单”展开成**可直接落地的迭代计划**，每一步都给出：目标、改动点、产出与验收方式。建议严格按顺序推进：先让通知稳定入库，再做心跳骨架与集成，最后再接主动行为与外部系统。

### 4.1 阶段 0：代码基线与联调准备（只读检查）

- **目标**：确认当前工程的真实现状（已有实现/字段/入口），避免重复造轮子或改错位置。
- **检查点**：
  - `src/config/official_configs.py`：`MessageReceiveConfig` 当前结构与加载逻辑。
  - `src/common/database/database_model.py`：`Messages` 表已有字段与初始化/迁移方式。
  - `src/chat/message_receive/bot.py`：是否已有 notice 相关入口与消息分发策略。
  - `src/chat/message_receive/storage.py`：存储链路、VAD/关键词/兴趣度处理位置。
  - `src/main.py`：消息处理注册点与未来心跳系统的启动挂载点。
- **产出**：记录“需要改的文件清单 + 现有实现差异点”（可写到 `开发记录/dev.md` 里）。
- **验收**：不改代码，仅能清晰回答“通知从哪进、存储在哪做、心跳在哪启动”。

### 4.2 阶段 1：通知系统落地（Notice Tracking）

#### 4.2.1 第 1 步：配置层（MessageReceiveConfig + TOML）

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

#### 4.2.2 第 2 步：数据库模型（Messages 表新增 notice 字段 + 迁移）

- **目标**：以统一结构存储所有通知，供心跳/统计复用。
- **改动点**：
  - `src/common/database/database_model.py` 的 `Messages` 增加字段：
    - `is_notice`、`notice_type`、`notice_data`
    - （可选兼容）`is_input_status_notice`、`input_status_typing`
  - 增加一次性迁移脚本/迁移逻辑（按项目惯例落地）。
- **产出**：数据库表结构升级完成。
- **验收**：程序能正常读写普通消息；新字段存在且默认值正确。

#### 4.2.3 第 3 步：ChatBot 统一 notice 入口（解析 + 去重 + 入库）

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

#### 4.2.4 第 4 步：MessageStorage 适配（跳过 VAD/关键词/兴趣度）

- **目标**：通知只作为“行为记录”，不污染正常聊天分析统计。
- **改动点**：
  - `src/chat/message_receive/storage.py` 的 `MessageStorage.store_message`：
    - 对 `message.is_notify == True` 且 `is_notice == True` 的记录：
      - 写入 `Messages`（带 `is_notice/notice_type/notice_data`）
      - 明确跳过 VAD / 关键词 / 兴趣度等处理
    - 其他普通消息保持不变
- **产出**：通知记录与普通消息处理完全隔离。
- **验收**：通知记录存在，但不会触发 VAD/关键词相关日志或字段更新（视项目实现而定）。

### 4.3 阶段 2：心跳系统骨架与集成（Heartbeat Skeleton）

#### 4.3.1 第 5 步：HeartbeatConfig 配置接入

- **目标**：心跳系统可通过配置启停，并具备 fast/normal/slow 三档间隔与时间段控制。
- **改动点**：
  - `src/config/official_configs.py`：新增 `HeartbeatConfig` 并挂入总配置。
  - `config/bot_config.toml`、`template/bot_config_template.toml`：新增 `[heartbeat]` 配置块。
- **产出**：配置可加载；关闭后不启动任何心跳任务。
- **验收**：`enable=false` 时不产生心跳日志；`enable=true` 时按 interval 产生日志（下一步实现后验证）。

#### 4.3.2 第 6 步：实现心跳系统最小可运行骨架（先只 log）

- **目标**：心跳系统跑起来，但不主动发言（降低联调风险）。
- **改动点**：
  - 新建 `src/chat/heartbeat/heartbeat_state.py`：定义 `ChatInfo` / `HeartbeatState`。
  - 新建 `src/chat/heartbeat/heartbeat_system.py`：
    - `start()/stop()` 启动 fast/normal/slow 三个周期任务
    - `_update_chat_states()` 从 `Messages` 拉取最近消息与近期 notice（input_status/poke/recall）
    - `_calculate_attention_score()` 基础实现（先用规则法）
    - `_check_urgent_events()` / `_decide_and_act()`：先仅日志输出
    - 对外接口：`get_chat_list()` / `get_current_chat_id()` / `enter_chat()` / `exit_chat()`
- **产出**：可观测的全局状态更新循环。
- **验收**：程序启动后稳定输出心跳日志；无异常堆栈；不发任何主动消息。

#### 4.3.3 第 7 步：在 main 中集成启动

- **目标**：心跳系统成为机器人生命周期的一部分，可配置控制。
- **改动点**：
  - `src/main.py`：在初始化完成后按配置 `await heartbeat.start()`；在退出路径中 `await heartbeat.stop()`（若有）。
- **产出**：无需额外脚本即可运行心跳系统。
- **验收**：与原有聊天/心流逻辑不冲突；关开配置可控。

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

#### 4.4.2 第 9 步：实现真实 `_proactive_speak`（LLM + 情绪/关系联动）

- **目标**：从“观测与调度”升级为“自然的主动发言”。
- **改动点（建议单独迭代）**：
  - 心跳系统内实现 `_proactive_speak`，将 attention/request/meta 等转换为可控 prompt
  - 接入情绪/关系系统用于语气与频率调节
  - 完善观测与统计（slow_heartbeat 汇总）
- **验收**：主动发言自然、频率可控、不打扰；可通过配置快速关闭。

本文件作为 `dev.md` 的工程化提炼版，主要面向“真正要落代码与联调”的阶段使用。  
详细的思路推演、历史记录和更完整的代码片段可继续参考 `开发记录/dev.md` 与 `docs-src/` 中相关文档。 

