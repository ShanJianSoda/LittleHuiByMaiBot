# VTuber 与 MaiBot 双向接入：格式与实现

> 结论：**Open-LLM-VTuber 的发送内容只需构造成与 Napcat-adapter 相同的 API 格式（即 MaiBot 当前收到的 `message_data` 结构），重构成该格式后通过 maim_message 的 WebSocket 客户端发往 MaiBot 即可。**  
> 反向：在 MaiBot 中为 Open-LLM-VTuber 定义新 platform（如 `openllm_vtuber`），由 VTuber 侧维持一条连接并注册该 platform，MaiBot 回复时即会路由到该连接。

---

## 一、表述是否正确

**「open-llm-vtuber 的发送内容就是构造成 api 接口的样子，只需要重构成 Napcat-adapter 发送的格式就行了」—— 正确。**

依据：

- MaiBot 侧接收逻辑：`maim_message` 的 WebSocket 服务端在收到客户端发来的 **任意 JSON** 后，直接调用 `process_message(message)`，其中 `message` 即为该 JSON 字典（见 `ws_connection.py`：`message = await websocket.receive_json()` → `task = asyncio.create_task(self.process_message(message))`）。
- MaiBot 对消息的解析：`MessageRecv(message_dict)` 使用 `message_info`、`message_segment`、`raw_message` 等键（见 `message.py` 的 `MessageRecv.__init__`），与 Napcat-adapter 发来的结构一致。
- 因此：只要 VTuber 侧（或中间桥接服务）**构造与 Napcat-adapter 同形的字典**，通过**同一套 WebSocket 客户端**发往 MaiBot，就会被当作「来自该 platform 的一条用户消息」处理，无需改 MaiBot 收消息的接口。

**边界与注意：**

- 必填/建议字段见下节「三种 message_data 格式」；`message_info` 中 `platform` 建议设为 `openllm_vtuber`（或你定义的新平台名），以便与 QQ 等区分并用于反向发送路由。
- 若 VTuber 仅发文本，可只构造文本段；若需支持图片/表情，需按 MaiBot 现有约定构造 `message_segment`（如 `type: "image"` / `"emoji"`，`data` 为 base64 等）。

---

## 二、三种 message_data 格式（来自 MaiBot 日志）

以下为从 `message_process` 前打印的 `message_data`（即 `logger.debug(str(message_data))`）中提炼的**最小可用结构**，便于 VTuber 侧按需构造。

### 2.1 文本消息

```json
{
  "message_info": {
    "platform": "qq",
    "message_id": 1603101368,
    "time": 1773737799.6023998,
    "user_info": {
      "platform": "qq",
      "user_id": "1962560763",
      "user_nickname": "空梦",
      "user_cardname": ""
    },
    "format_info": {
      "content_format": ["text", "image", "emoji", "voice"],
      "accept_format": ["text", "image", "emoji", "reply", "voice", "command", "voiceurl", "music", "videourl", "file", "imageurl", "forward", "video"]
    },
    "additional_config": {}
  },
  "message_segment": {
    "type": "seglist",
    "data": [
      { "type": "text", "data": "嗯哼" }
    ]
  },
  "raw_message": "嗯哼"
}
```

- **必填**：`message_info.platform`、`message_info.user_info`（至少 `user_id`）、`message_segment`（seglist + 一条 `type: "text"`）、`raw_message`（与文本一致即可）。
- **可选**：`message_id`、`time`、`format_info`、`group_info`（私聊可省略）、`additional_config`。

### 2.2 表情包（emoji）

- `message_segment.data` 中为**一条** `type: "emoji"`，`data` 为**该表情图片的 base64 字符串**。
- `raw_message` 为 CQ 码形式，例如：`[CQ:image,summary=[动画表情],file=...,url=...,sub_type=1,...]`（若 VTuber 不关心 CQ 码，可给一占位或与 Napcat 一致的最小 CQ 串）。
- 其他与文本消息相同：`message_info` 含 `platform`、`user_info` 等。

（日志中 emoji 的 `data` 为很长一段 base64，此处不贴；VTuber 若发表情，需把表情图转成 base64 放入 `message_segment.data[].data`。）

### 2.3 图片

- `message_segment.data` 中为**一条** `type: "image"`，`data` 为**图片 base64 字符串**。
- `raw_message` 为 CQ 码，例如：`[CQ:image,file=xxx.jpeg,sub_type=0,url=...,file_size=55701]`。
- 其余同文本：`message_info` 结构一致。

**小结**：三种格式共用同一 `message_info` 结构；区别仅在 `message_segment.data` 中段的 `type`（`text` / `emoji` / `image`）和 `data`（文本或 base64），以及 `raw_message` 的 CQ 形式。VTuber 侧只需按需组这三种之一即可。

### 2.4 VTuber 上行 payload 示例（可直接复制粘贴）

以下为 **platform 已设为 `openllm_vtuber`** 的最小可发 payload，按需替换占位后 `send_json(...)` 即可。

**（1）纯文本 — 最小必填**

```json
{
  "message_info": {
    "platform": "openllm_vtuber",
    "message_id": "1",
    "time": 0,
    "user_info": {
      "platform": "openllm_vtuber",
      "user_id": "vtuber_user_1",
      "user_nickname": "观众",
      "user_cardname": ""
    },
    "format_info": {
      "content_format": ["text", "image", "emoji"],
      "accept_format": ["text", "image", "emoji"]
    },
    "additional_config": {}
  },
  "message_segment": {
    "type": "seglist",
    "data": [
      { "type": "text", "data": "这里填用户说的话" }
    ]
  },
  "raw_message": "这里填用户说的话"
}
```

**（2）表情包 — 将 `<BASE64_EMOJI>` 换成实际 base64 字符串**

```json
{
  "message_info": {
    "platform": "openllm_vtuber",
    "message_id": "2",
    "time": 0,
    "user_info": {
      "platform": "openllm_vtuber",
      "user_id": "vtuber_user_1",
      "user_nickname": "观众",
      "user_cardname": ""
    },
    "format_info": {
      "content_format": ["text", "image", "emoji"],
      "accept_format": ["text", "image", "emoji"]
    },
    "additional_config": {}
  },
  "message_segment": {
    "type": "seglist",
    "data": [
      { "type": "emoji", "data": "<BASE64_EMOJI>" }
    ]
  },
  "raw_message": "[CQ:image,summary=[动画表情],sub_type=1]"
}
```

**（3）图片 — 将 `<BASE64_IMAGE>` 换成实际 base64 字符串**

```json
{
  "message_info": {
    "platform": "openllm_vtuber",
    "message_id": "3",
    "time": 0,
    "user_info": {
      "platform": "openllm_vtuber",
      "user_id": "vtuber_user_1",
      "user_nickname": "观众",
      "user_cardname": ""
    },
    "format_info": {
      "content_format": ["text", "image", "emoji"],
      "accept_format": ["text", "image", "emoji"]
    },
    "additional_config": {}
  },
  "message_segment": {
    "type": "seglist",
    "data": [
      { "type": "image", "data": "<BASE64_IMAGE>" }
    ]
  },
  "raw_message": "[CQ:image,file=image.jpeg,sub_type=0]"
}
```

- 私聊可不传 `message_info.group_info`；若传群聊，则增加 `"group_info": { "platform": "openllm_vtuber", "group_id": "xxx", "group_name": "" }`。
- `message_id`、`time` 可按需改为唯一值、当前时间戳，避免冲突或用于去重。

---

## 三、新 platform：openllm_vtuber

### 3.1 为何要新 platform

- **发送到 VTuber（MaiBot → Open-LLM-VTuber）**：MaiBot 发送时根据 `message.message_info.platform` 选择连接（见 `uni_message_sender._send_message` → `get_global_api().send_message(message)`；maim_message 服务端用 `platform_websockets[platform]` 发到对应 WebSocket）。若没有「专属于 VTuber」的 platform，就无法把回复单独路由到 VTuber 连接。
- **接收来自 VTuber（Open-LLM-VTuber → MaiBot）**：用统一 platform（如 `openllm_vtuber`）便于在 MaiBot 内做 stream_id、限流、存储等时与 QQ 等区分。

因此：**在 MaiBot 侧将 Open-LLM-VTuber 视为一个新 platform（如 `openllm_vtuber`）是可行且推荐的做法。**

### 3.2 服务端如何识别 platform

- maim_message 的 WebSocket 服务端在**客户端连接时**从请求头读取 `platform`（`ws_connection.py`：`platform = websocket.headers.get("platform", "unknown")`），并把该连接存入 `platform_websockets[platform]`。
- 客户端连接时需在 **headers 里带上 `platform: openllm_vtuber`**（以及若启用鉴权则带 `Authorization`），则后续 MaiBot 发往 `platform == "openllm_vtuber"` 的消息都会从 `send_message(target=platform, message)` 发到该连接。

无需在 MaiBot 业务代码里「注册」platform 名称；只要客户端以该 platform 连接，即自动完成「注册」。

---

## 四、双向实现思路

### 4.1 VTuber → MaiBot（用户输入进入 MaiBot）

1. **连接**：在 Open-LLM-VTuber 侧（或独立桥接进程）使用 maim_message 的 **WebSocket 客户端**（如 `WebSocketClient`）连接 MaiBot 的 WebSocket 地址（与 Napcat 相同），并在连接时设置 `platform="openllm_vtuber"`（及可选 token）。
2. **构造 payload**：根据用户输入类型（文本/表情/图片）按上节三种格式之一构造 `message_data` 字典（`message_info`、`message_segment`、`raw_message`），其中 `message_info.platform` 设为 `openllm_vtuber`，`user_info` 可固定为一个虚拟用户或当前 VTuber 用户。
3. **发送**：客户端对该 WebSocket 执行 **send_json(message_data)**；服务端收到后调用 `process_message(message_data)`，MaiBot 即按现有流程处理（`MessageRecv.from_dict`、`message_process`、chat_stream 等）。

### 4.2 MaiBot → VTuber（回复下发到 VTuber）

1. **维持连接**：同上，VTuber 侧必须**长期维持**一条以 `platform=openllm_vtuber` 连接的 WebSocket，这样 `platform_websockets["openllm_vtuber"]` 才存在。
2. **回复时的 platform**：MaiBot 生成回复时，该回复对应的 `message_info.platform` 会来自触发对话的那条消息的 platform（即若用户消息是 `openllm_vtuber`，则回复也会是 `openllm_vtuber`），因此 `_send_message` → `get_global_api().send_message(message)` 会自然走到 maim_message 的 `send_message(target="openllm_vtuber", message)`。
3. **VTuber 侧收包**：maim_message 服务端向该连接 `send_json(message)` 发送的是**服务端定义的发送结构**（一般为消息的 to_dict 或等价 JSON）。VTuber 侧客户端在 **receive** 循环里收到 JSON 后，解析为「机器人回复」：取文本/段展示、或送入 TTS、驱动立绘等。

若 MaiBot 侧发送的 JSON 结构与 VTuber 期望的不完全一致，可在 VTuber 侧做一层薄适配（只取 `processed_plain_text` 或指定字段即可）。

---

## 五、实现检查清单（简要）

| 项目 | 说明 |
|------|------|
| VTuber 侧 WebSocket 客户端 | 使用 maim_message 的 WebSocketClient，连接 MaiBot 的 ws 地址，headers 带 `platform: openllm_vtuber`（及 token 若需要）。 |
| 上行（VTuber→MaiBot） | 按文本/表情/图片三种之一构造 `message_info` + `message_segment` + `raw_message`，`platform` 统一为 `openllm_vtuber`，然后 `send_json(message_data)`。 |
| 下行（MaiBot→VTuber） | 保持连接；在客户端 `receive` 循环中处理收到的 JSON，解析为 bot 回复并做 TTS/展示。 |
| MaiBot 侧 | 无需改收消息格式；若需在 UI 或存储里区分 VTuber，可依赖 `message_info.platform == "openllm_vtuber"`。 |

---

## 六、参考代码位置（MaiBot）

- 收消息构造：`src/chat/message_receive/message.py` — `MessageRecv.__init__`、`BaseMessageInfo.from_dict`、`Seg.from_dict`。
- 收消息入口：`src/chat/message_receive/bot.py` — `message_process` 使用的 `message_data`（如 428 行附近 debug 打印）。
- 发消息路由：`src/chat/message_receive/uni_message_sender.py` — `_send_message`、`get_global_api().send_message(message)`；maim_message 中 `platform_websockets[platform]` 与 `send_message(target, message)`。

以上即可支撑「Open-LLM-VTuber 仅需重构成 Napcat-adapter 格式 + 新 platform 双向收发」的可行性与实现方式。

---

## 七、MaiBot 回复内容到适配器的函数（补充）

**结论：MaiBot 把回复发到某个 platform 时，对应适配器上「接收该回复」的函数如下。**

| 端 | 函数 / 位置 | 说明 |
|----|-------------|------|
| **MaiBot 侧（发出）** | `src/chat/message_receive/uni_message_sender.py` 中的 `_send_message(message)` → `get_global_api().send_message(message)` | 业务层调用 `UniversalMessageSender.send_message(message)` 后，最终通过 `get_global_api().send_message(message)` 交给 maim_message 服务端；服务端根据 `message.message_info.platform` 找到对应 WebSocket 连接并 `send_json(message)`。 |
| **Napcat-Adapter 侧（接收）** | `MaiBot-Napcat-Adapter/src/send_handler/main_send_handler.py` 中的 **`SendHandler.handle_message(raw_message_base_dict)`** | 适配器用 `router.register_class_handler(send_handler.handle_message)` 注册；maim_message 客户端收到服务端 push 的 JSON 后，反序列化为 `MessageBase` 并回调 `handle_message`。入参为消息的字典形式（含 `message_info`、`message_segment`、`raw_message` 等）。 |
| **Open-LLM-VTuber 侧（若同样以客户端连 MaiBot）** | 需自建接收循环：连接 MaiBot 的 WebSocket 后，在 `receive` 循环里解析收到的 JSON，即为 MaiBot 的回复消息；可从中取 `processed_plain_text` 或 `message_segment` 做 TTS/展示。 | 与 Napcat 不同，VTuber 可不使用 maim_message 的 Router，仅用原生 WebSocket 连接 `ws://<HOST>:<PORT>/ws` 并带 header `platform: openllm_vtuber`，则 MaiBot 回复时会向该连接推送同结构的消息。 |

因此：**「MaiBot 返回内容到 napcat-adapter」的入口函数就是 `SendHandler.handle_message`**；若 VTuber 自己连 MaiBot，则在自己的 WebSocket 收包循环里处理收到的 JSON 即可。
