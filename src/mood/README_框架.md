# 情绪模块框架说明（效果优先版）

与 `情绪模块开发文档V2-效果优先版.md`、`fork版项目的mood_manager用法.md` 对应。**不修改 `mood_manager.py`**，在其外搭建可选的 VAD/动力学/环境适配层。

## 1. 现有流程（保持不变）

- **初始化**：`MoodManager.start()` 启动回归任务；`ChatMood` 懒加载。
- **写入**：`message_handler._preprocess_message()` → `chat_mood.update_mood_by_message()` → 更新 `mood_state`、`last_change_time`。
- **读取**：`mood_api.get_mood(chat_id)` 或各处直接读 `mood_manager.get_mood_by_chat_id(chat_id).mood_state`。
- **后台**：`MoodRegressionTask` 每 30 秒对超时未更新的 chat 调用 `regress_mood()`。

## 2. 新增文件与职责

| 文件 | 职责 |
|------|------|
| `vad_lexicon.py` | 加载 NRC-VAD 词典（`data/emotion/nrc_vad_internal.json`），无文件时为空表 |
| `mood_estimator.py` | `estimate_from_lexicon(text)` → (v, a, d, confidence) |
| `mood_dynamics.py` | `MoodDynamics` 三维指数衰减；`vad_to_bucket(v,a,d)` → 文本描述 |
| `relation_manager.py` | 关系权重 w_rel，放大 msg 影响；(chat_id, user_id) 维度，可配置+轻量学习 |
| `topic_tracker.py` | 话题因子 w_topic，切换惩罚；框架内为占位，可接 LLM 分类 |
| `context_smoother.py` | 频率/密度 EMA，输出 w_context |
| `complex_env_adapter.py` | 按 chat 聚合上述三者，输出 (w_rel, w_topic, w_context) |
| `emotion_engine.py` | 门面：`get_mood`/`get_mood_for_prompt` 委托 mood_manager；可选 `compute_delta_with_framework` 走 VAD+动力学 |

## 3. 使用方式

- **仅用现有逻辑**：无需改任何调用，继续用 `mood_manager` / `mood_api` 即可。
- **需要 Prompt 文案**：`from src.mood.emotion_engine import get_mood_for_prompt`，`get_mood_for_prompt(chat_id)` 或 `get_mood_for_prompt(chat_id, include_vad=True)`。
- **走 VAD+动力学**（可选）：在合适处（如预处理或单独任务）调用  
  `emotion_engine.compute_delta_with_framework(chat_id, message_text, user_id=...)`，  
  再按需 `mood_api.set_mood(chat_id, vad_to_bucket(v, a, d))` 写回文本，**不修改 mood_manager 内部**。

## 4. 数据与配置

- VAD 词典：可选。将 NRC-VAD 转为 `{ "word": { "v", "a", "d" } }` 存为 `data/emotion/nrc_vad_internal.json`；缺失时估计恒为 (0,0,0,0)。
- 复杂环境参数：见文档 3.6.6 节；当前实现用代码内默认值，未接 config。
