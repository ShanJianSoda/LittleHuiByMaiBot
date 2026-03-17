# MaiBot 如何接收 NapCat-Adapter 的消息

> 简要说明：是**监听端口**（WebSocket 服务端），不是 MaiBot 去连 NapCat。

---

## 1. 谁监听、谁连接

- **MaiBot** 作为 **服务端**，在启动时**监听一个端口**（WebSocket，默认路径 `/ws`）。
- **NapCat-Adapter**（或其它适配器）作为 **客户端**，**主动连接** MaiBot 的 WebSocket 地址，连接成功后**向 MaiBot 推送消息**。

也就是说：是适配器连 MaiBot，不是 MaiBot 连适配器。

---

## 2. 端口与配置从哪来

- **主通道**：`HOST`、`PORT` 来自**环境变量**（通常在 `.env` 或启动脚本里设置）。
- 在 `src/common/message/api.py` 里创建 `MessageServer` 时使用：
  ```python
  kwargs = {
      "host": os.environ["HOST"],
      "port": int(os.environ["PORT"]),
      "app": get_global_server().get_app(),
  }
  global_api = MessageServer(**kwargs)
  ```
- 若未单独设置，一般会在文档或启动脚本里约定默认值（如 `0.0.0.0:18000` 等，以你项目为准）。

可选：`config/bot_config.toml` 里 `[maim_message]` 下的 `enable_api_server`、`api_server_port` 等是**另一套**「新版 API Server」的配置，会再监听一个端口（如 8090），用于新版协议；主消息通道仍是上面的 `HOST`/`PORT`。

---

## 3. 底层实现（maim_message 库）

- **MessageServer**（`maim_message.api`）默认使用 **WebSocket 模式**（`mode="ws"`），内部是 `WebSocketServer`（`maim_message.ws_connection`）。
- `WebSocketServer` 用 **FastAPI + Uvicorn** 在 `host:port` 上起 HTTP 服务，并挂载 **WebSocket 路径**（默认 `/ws`）。
- 适配器连接 `ws://<HOST>:<PORT>/ws`（或 `wss://` 若配置了 SSL），通过该 WebSocket **发送 JSON 消息**。
- 服务端收到一条消息后，会调用已注册的 **消息处理函数**（见下）。

---

## 4. 消息如何交到你的逻辑

1. **注册处理器**（`src/main.py`）：
   ```python
   self.app.register_message_handler(chat_bot.message_process)
   self.app.register_custom_message_handler("message_id_echo", chat_bot.echo_message_process)
   ```
   - `self.app` 即 `MessageServer`（`get_global_api()`）。
   - 每条**普通消息**会交给 `chat_bot.message_process`；带 `message_type_name == "message_id_echo"` 的自定义消息会交给 `chat_bot.echo_message_process`。

2. **服务端收到 WebSocket 消息后**（在 maim_message 内部）：
   - 解析为字典，调用 `MessageServer.process_message(message)`。
   - `process_message` 会依次调用所有通过 `register_message_handler` 注册的 handler（如 `chat_bot.message_process`），或根据 `message_type_name` 调用 `register_custom_message_handler` 注册的 handler。

3. **启动时**（`main.py` 的 `schedule_tasks`）：
   ```python
   await asyncio.gather(..., self.app.run(), ...)
   ```
   - `self.app.run()` 内部会 `await self.connection.start()`，即**启动 WebSocket 服务并开始监听**，等待适配器连接并收发消息。

---

## 5. 小结

| 问题 | 答案 |
|------|------|
| 是监听端口还是 MaiBot 去连别人？ | **监听端口**。MaiBot 是 WebSocket **服务端**。 |
| 端口与地址？ | 由环境变量 **HOST**、**PORT** 决定；WebSocket 路径默认 **/ws**。 |
| 谁连谁？ | **NapCat-Adapter 连接 MaiBot**（`ws://<HOST>:<PORT>/ws`），连接后向 MaiBot 推送消息。 |
| 消息如何进入业务？ | 适配器发来的每条消息触发 `MessageServer.process_message`，再分发给 `register_message_handler` / `register_custom_message_handler` 注册的回调（如 `chat_bot.message_process`）。 |
