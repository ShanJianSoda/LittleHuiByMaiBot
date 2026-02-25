import asyncio
import math
import random
import time

from src.plugin_system.apis.message_api import build_readable_messages_to_str, get_messages_by_time_in_chat_inclusive
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.common.data_models.database_data_model import DatabaseMessages
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest
from src.manager.async_task_manager import AsyncTask, async_task_manager
from src.mood.emotion_history_recorder import record_emotion_change

logger = get_logger("mood")


def init_prompt():
    Prompt(
        """
{chat_talking_prompt}
以上是群里正在进行的聊天记录

{identity_block}
你刚刚的情绪状态是：{mood_state}

现在，发送了消息，引起了你的注意，你对其进行了阅读和思考，请你输出一句话描述你新的情绪状态
请只输出情绪状态，不要输出其他内容：
""",
        "change_mood_prompt",
    )
    Prompt(
        """
{chat_talking_prompt}
以上是群里最近的聊天记录

{identity_block}
你之前的情绪状态是：{mood_state}

距离你上次关注群里消息已经过去了一段时间，你冷静了下来，请你输出一句话描述你现在的情绪状态
请只输出情绪状态，不要输出其他内容：
""",
        "regress_mood_prompt",
    )


class ChatMood:
    def __init__(self, chat_id: str):
        self.chat_id: str = chat_id

        # 这些将在异步初始化中设置
        self.chat_stream = None  # type: ignore
        self.log_prefix = f"[{chat_id}]"
        self._initialized = False

        self.mood_state: str = "感觉很平静"

    async def _initialize(self):
        """异步初始化方法"""
        if not self._initialized:
            try:
                from src.chat.message_receive.chat_stream import get_chat_manager

                chat_manager = get_chat_manager()
                self.chat_stream = chat_manager.get_stream(self.chat_id)

                if not self.chat_stream:
                    # 如果找不到聊天流，使用基础日志前缀但不抛出异常
                    self.log_prefix = f"[{self.chat_id}]"
                    logger.warning(f"Chat stream for chat_id {self.chat_id} not found during mood initialization")
                else:
                    self.log_prefix = f"[{self.chat_stream.group_info.group_name if self.chat_stream.group_info else self.chat_stream.user_info.user_nickname}]"

                # 初始化回归计数
                if not hasattr(self, "regression_count"):
                    self.regression_count = 0

                # 初始化情绪模型
                if not hasattr(self, "mood_model"):
                    self.mood_model = LLMRequest(model_set=model_config.model_task_config.emotion, request_type="mood")

                # 初始化最后变化时间
                if not hasattr(self, "last_change_time"):
                    self.last_change_time = 0

                self._initialized = True
                logger.debug(f"{self.log_prefix} 情绪系统初始化完成")

            except Exception as e:
                logger.error(f"情绪系统初始化失败: {e}")
                # 设置基础初始化状态，避免重复尝试
                self.log_prefix = f"[{self.chat_id}]"
                self._initialized = True
                if not hasattr(self, "regression_count"):
                    self.regression_count = 0
                if not hasattr(self, "mood_model"):
                    self.mood_model = LLMRequest(model_set=model_config.model_task_config.emotion, request_type="mood")
                if not hasattr(self, "last_change_time"):
                    self.last_change_time = 0

    async def update_mood_by_message(self, message: DatabaseMessages, interested_rate: float):
        # 确保异步初始化已完成
        await self._initialize()

        async with mood_manager._mood_lock:
            # 如果当前聊天处于失眠状态，则锁定情绪，不允许更新
            current_chat_id = getattr(message, "chat_id", self.chat_id)
            if current_chat_id in mood_manager.insomnia_chats:
                logger.debug(f"{self.log_prefix} 处于失眠状态，情绪已锁定，跳过更新。")
                return

            self.regression_count = 0

            # 使用 DatabaseMessages 的时间字段
            message_time = message.time

            # 防止负时间差
            during_last_time = max(0, message_time - self.last_change_time)

            base_probability = 0.05
            time_multiplier = 4 * (1 - math.exp(-0.01 * during_last_time))

            if interested_rate <= 0:
                interest_multiplier = 0
            else:
                interest_multiplier = 2 * math.pow(interested_rate, 0.25)

            # todo：增加关系权重elation_manager，关联发言人

            logger.debug(
                f"base_probability: {base_probability}, time_multiplier: {time_multiplier}, interest_multiplier: {interest_multiplier}"
            )
            update_probability = global_config.mood.mood_update_threshold * min(
                1.0, base_probability * time_multiplier * interest_multiplier
            )

            if random.random() > update_probability:
                logger.debug(f"{self.log_prefix} 情绪更新概率未达到阈值，跳过更新。概率: {update_probability:.3f}")
                return

            logger.debug(
                f"{self.log_prefix} 更新情绪状态，感兴趣度: {interested_rate:.2f}, 更新概率: {update_probability:.2f}"
            )

            # VAD 路径：全 bot 单一 VAD（AI-bot 一致性），词典+动力学更新，不调 LLM
            if getattr(global_config.mood, "use_vad_path", False):
                from src.mood.emotion_engine import compute_delta_with_framework
                from src.mood.mood_dynamics import vad_to_bucket

                user_id = ""
                if hasattr(message, "chat_info") and message.chat_info and hasattr(message.chat_info, "user_info"):
                    user_id = getattr(message.chat_info.user_info, "user_id", "") or ""

                # 优先使用消息已入库的 VAD（事实层），否则用正文现场估计
                message_vad = None
                ev = getattr(message, "emotion_v", None)
                ea = getattr(message, "emotion_a", None)
                ed = getattr(message, "emotion_d", None)
                if ev is not None and ea is not None and ed is not None:
                    try:
                        message_vad = (float(ev), float(ea), float(ed))
                    except (TypeError, ValueError):
                        pass
                text = getattr(message, "processed_plain_text", None) or "" if message_vad is None else ""
                new_v, new_a, new_d = compute_delta_with_framework(
                    current_chat_id,
                    text,
                    user_id=user_id,
                    message_count=1,
                    use_env_factors=True,
                    message_vad=message_vad,
                )
                new_state_text = vad_to_bucket(new_v, new_a, new_d)
                mood_manager.mood.mood_state = new_state_text
                mood_manager._last_chat_id = current_chat_id
                self.last_change_time = message_time
                record_emotion_change(current_chat_id, self.mood_state, "message", message_time, v=new_v, a=new_a, d=new_d)
                logger.info(f"{self.log_prefix} 情绪状态更新为(VAD): {self.mood_state}")
                return

            message_list_before_now = get_messages_by_time_in_chat_inclusive(
                chat_id=current_chat_id,
                start_time=self.last_change_time,
                end_time=message_time,
                limit=int(global_config.chat.max_context_size / 3),
                limit_mode="last",
            )
            chat_talking_prompt: str = build_readable_messages_to_str(
                message_list_before_now,
                replace_bot_name=True,
                # merge_messages=False,
                timestamp_mode="normal_no_YMD",
                read_mark=0.0,
                truncate=True,
                show_actions=True,
            )

            bot_name = global_config.bot.nickname
            if global_config.bot.alias_names:
                bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
            else:
                bot_nickname = ""

            prompt_personality = global_config.personality.personality
            identity_block = f"你的名字是{bot_name}{bot_nickname}，你{prompt_personality}："

            prompt = await global_prompt_manager.format_prompt(
                "change_mood_prompt",
                chat_talking_prompt=chat_talking_prompt,
                identity_block=identity_block,
                mood_state=self.mood_state,
            )

            response, (reasoning_content, _, _) = await self.mood_model.generate_response_async(
                prompt=prompt, temperature=0.7
            )
            if global_config.debug.show_prompt:
                logger.debug(f"{self.log_prefix} prompt: {prompt}")
                logger.debug(f"{self.log_prefix} response: {response}")
                logger.debug(f"{self.log_prefix} reasoning_content: {reasoning_content}")

            logger.info(f"{self.log_prefix} 情绪状态更新为: {response}")

            self.mood_state = response

            self.last_change_time = message_time

            mood_manager._last_chat_id = current_chat_id
            record_emotion_change(current_chat_id, self.mood_state, "message", message_time)

    async def regress_mood(self):
        async with mood_manager._mood_lock:
            message_time = time.time()

            # VAD 路径的回归由 MoodRegressionTask 统一做全局一次 tick，不在此处按 chat 执行
            if getattr(global_config.mood, "use_vad_path", False):
                return

            # 全 bot 单一 mood：回归时用最近触发更新的 chat 取上下文
            regress_chat_id = getattr(mood_manager, "_last_chat_id", None) or self.chat_id
            message_list_before_now = get_messages_by_time_in_chat_inclusive(
                chat_id=regress_chat_id,
                start_time=self.last_change_time,
                end_time=message_time,
                limit=15,
                limit_mode="last",
            )
            chat_talking_prompt: str = build_readable_messages_to_str(
                message_list_before_now,
                replace_bot_name=True,
                # merge_messages=False,
                timestamp_mode="normal_no_YMD",
                read_mark=0.0,
                truncate=True,
                show_actions=True,
            )

            bot_name = global_config.bot.nickname
            if global_config.bot.alias_names:
                bot_nickname = f",也有人叫你{','.join(global_config.bot.alias_names)}"
            else:
                bot_nickname = ""

            prompt_personality = global_config.personality.personality
            identity_block = f"你的名字是{bot_name}{bot_nickname}，你{prompt_personality}："

            prompt = await global_prompt_manager.format_prompt(
                "regress_mood_prompt",
                chat_talking_prompt=chat_talking_prompt,
                identity_block=identity_block,
                mood_state=self.mood_state,
            )

            response, (reasoning_content, _, _) = await self.mood_model.generate_response_async(
                prompt=prompt, temperature=0.7
            )

            if global_config.debug.show_prompt:
                logger.debug(f"{self.log_prefix} prompt: {prompt}")
                logger.debug(f"{self.log_prefix} response: {response}")
                logger.debug(f"{self.log_prefix} reasoning_content: {reasoning_content}")

            logger.info(f"{self.log_prefix} 情绪状态转变为: {response}")

            self.mood_state = response

            record_emotion_change(regress_chat_id, self.mood_state, "regress", message_time)

            self.regression_count += 1


class MoodRegressionTask(AsyncTask):
    def __init__(self, mood_manager: "MoodManager"):
        super().__init__(task_name="MoodRegressionTask", run_interval=30)
        self.mood_manager = mood_manager

    async def run(self):
        # 不在此处加锁，由 regress_mood() 与 update_mood_by_message() 内 _mood_lock 串行化
        logger.debug("开始情绪回归任务...")
        now = time.time()

        # VAD 路径：全 bot 单一 VAD，每周期仅做一次全局衰减并同步到所有 chat
        if getattr(global_config.mood, "use_vad_path", False):
            async with self.mood_manager._mood_lock:
                from src.mood.emotion_engine import get_global_dynamics
                from src.mood.mood_dynamics import vad_to_bucket

                dyn = get_global_dynamics()
                new_v, new_a, new_d = dyn.tick_only()
                new_state_text = vad_to_bucket(new_v, new_a, new_d)
                self.mood_manager.mood.mood_state = new_state_text
                record_emotion_change("global", new_state_text, "regress", now, v=new_v, a=new_a, d=new_d)
                logger.debug("情绪回归(VAD) 全局 tick 完成: %s", new_state_text)
            return

        mood = self.mood_manager.mood
        if mood.last_change_time == 0:
            return
        if now - mood.last_change_time <= 180:
            return
        if mood.regression_count >= 3:
            return
        logger.debug(f"{mood.log_prefix} 开始情绪回归, 第 {mood.regression_count + 1} 次")
        await mood.regress_mood()


class MoodManager:
    def __init__(self):
        # 全 bot 单一 ChatMood（设计原则：AI-bot 一致性）
        self.mood: ChatMood = ChatMood("global")
        self.task_started: bool = False
        self.insomnia_chats: set[str] = set()  # 正在失眠的聊天ID列表
        self._last_chat_id: str = ""  # 最近触发情绪更新的 chat_id，供回归时取上下文
        self._mood_lock = asyncio.Lock()  # 串行化 mood 更新/回归，避免与 MoodRegressionTask 竞态

    async def start(self):
        """启动情绪回归后台任务"""
        if self.task_started:
            return

        logger.info("启动情绪回归任务...")
        task = MoodRegressionTask(self)
        await async_task_manager.add_task(task)
        self.task_started = True
        logger.info("情绪回归任务已启动")

    def get_mood_by_chat_id(self, chat_id: str) -> ChatMood:
        """返回全 bot 唯一的 ChatMood（AI-bot 一致性），chat_id 仅保留接口兼容。"""
        return self.mood

init_prompt()

mood_manager = MoodManager()
"""全局情绪管理器"""
