import json
import re
import traceback
from typing import Optional, Tuple, Union

from src.common.database.database_model import Messages, Images
from src.common.logger import get_logger
from .chat_stream import ChatStream
from .message import MessageSending, MessageRecv

logger = get_logger("message_storage")


def _compute_message_vad(text: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """对消息正文计算 VAD（事实层），供情绪模块与检索使用。无有效文本或置信度为 0 时返回 (None, None, None)。"""
    if not (text and str(text).strip()):
        return None, None, None
    try:
        from src.mood.mood_estimator import estimate_from_lexicon

        v, a, d, conf = estimate_from_lexicon(str(text).strip()[:2000])
        if conf <= 0 or not all(isinstance(x, (int, float)) for x in (v, a, d)):
            return None, None, None
        return (
            round(max(-1.0, min(1.0, float(v))), 4),
            round(max(-1.0, min(1.0, float(a))), 4),
            round(max(-1.0, min(1.0, float(d))), 4),
        )
    except Exception:
        return None, None, None


class MessageStorage:
    @staticmethod
    def _serialize_keywords(keywords) -> str:
        """将关键词列表序列化为JSON字符串"""
        if isinstance(keywords, list):
            return json.dumps(keywords, ensure_ascii=False)
        return "[]"

    @staticmethod
    def _deserialize_keywords(keywords_str: str) -> list:
        """将JSON字符串反序列化为关键词列表"""
        if not keywords_str:
            return []
        try:
            return json.loads(keywords_str)
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    async def store_message(message: Union[MessageSending, MessageRecv], chat_stream: ChatStream) -> None:
        """存储消息到数据库"""
        try:
            # 通知消息默认不存储；仅当 is_notice=True 时按通知记录入库
            if isinstance(message, MessageRecv) and message.is_notify and not getattr(message, "is_notice", False):
                logger.debug("通知消息（未标记为持久化通知），跳过存储")
                return

            pattern = r"<MainRule>.*?</MainRule>|<schedule>.*?</schedule>|<UserMessage>.*?</UserMessage>"

            # print(message)

            processed_plain_text = message.processed_plain_text

            # print(processed_plain_text)

            if processed_plain_text:
                processed_plain_text = MessageStorage.replace_image_descriptions(processed_plain_text)
                filtered_processed_plain_text = re.sub(pattern, "", processed_plain_text, flags=re.DOTALL)
            else:
                filtered_processed_plain_text = ""

            if isinstance(message, MessageSending):
                display_message = message.display_message
                if display_message:
                    filtered_display_message = re.sub(pattern, "", display_message, flags=re.DOTALL)
                else:
                    filtered_display_message = ""
                interest_value = 0
                is_mentioned = False
                is_at = False
                reply_probability_boost = 0.0
                reply_to = message.reply_to
                priority_mode = ""
                priority_info = {}
                is_emoji = False
                is_picid = False
                is_notify = False
                is_command = False
                key_words = ""
                key_words_lite = ""
                selected_expressions = message.selected_expressions
                intercept_message_level = 0
                is_notice = False
                notice_type = None
                notice_data = None
            else:
                filtered_display_message = ""
                interest_value = message.interest_value
                is_mentioned = message.is_mentioned
                is_at = message.is_at
                reply_probability_boost = message.reply_probability_boost
                reply_to = ""
                priority_mode = message.priority_mode
                priority_info = message.priority_info
                is_emoji = message.is_emoji
                is_picid = message.is_picid
                is_notify = message.is_notify
                is_command = message.is_command
                intercept_message_level = getattr(message, "intercept_message_level", 0)
                # 序列化关键词列表为JSON字符串
                key_words = MessageStorage._serialize_keywords(message.key_words)
                key_words_lite = MessageStorage._serialize_keywords(message.key_words_lite)
                selected_expressions = ""
                # 通知相关字段（由上游 ChatBot 标记）
                is_notice = getattr(message, "is_notice", False)
                notice_type = getattr(message, "notice_type", None)
                notice_data = getattr(message, "notice_data", None)

            chat_info_dict = chat_stream.to_dict()
            user_info_dict = message.message_info.user_info.to_dict()  # type: ignore

            # message_id 现在是 TextField，直接使用字符串值
            msg_id = message.message_info.message_id

            # 消息级 VAD（事实层）：入库时计算，供情绪更新与检索使用
            # 对于通知类持久化记录（is_notice=True），不计算 VAD / 关键词 / 兴趣度
            if isinstance(message, MessageRecv) and getattr(message, "is_notice", False):
                emotion_v, emotion_a, emotion_d = None, None, None
                interest_value = 0
                key_words = ""
                key_words_lite = ""
            else:
                emotion_v, emotion_a, emotion_d = _compute_message_vad(filtered_processed_plain_text or "")

            # 安全地获取 group_info, 如果为 None 则视为空字典
            group_info_from_chat = chat_info_dict.get("group_info") or {}
            # 安全地获取 user_info, 如果为 None 则视为空字典 (以防万一)
            user_info_from_chat = chat_info_dict.get("user_info") or {}

            Messages.create(
                message_id=msg_id,
                time=float(message.message_info.time),  # type: ignore
                chat_id=chat_stream.stream_id,
                # Flattened chat_info
                reply_to=reply_to,
                is_mentioned=is_mentioned,
                is_at=is_at,
                reply_probability_boost=reply_probability_boost,
                chat_info_stream_id=chat_info_dict.get("stream_id"),
                chat_info_platform=chat_info_dict.get("platform"),
                chat_info_user_platform=user_info_from_chat.get("platform"),
                chat_info_user_id=user_info_from_chat.get("user_id"),
                chat_info_user_nickname=user_info_from_chat.get("user_nickname"),
                chat_info_user_cardname=user_info_from_chat.get("user_cardname"),
                chat_info_group_platform=group_info_from_chat.get("platform"),
                chat_info_group_id=group_info_from_chat.get("group_id"),
                chat_info_group_name=group_info_from_chat.get("group_name"),
                chat_info_create_time=float(chat_info_dict.get("create_time", 0.0)),
                chat_info_last_active_time=float(chat_info_dict.get("last_active_time", 0.0)),
                # Flattened user_info (message sender)
                user_platform=user_info_dict.get("platform"),
                user_id=user_info_dict.get("user_id"),
                user_nickname=user_info_dict.get("user_nickname"),
                user_cardname=user_info_dict.get("user_cardname"),
                # Text content
                processed_plain_text=filtered_processed_plain_text,
                display_message=filtered_display_message,
                interest_value=interest_value,
                emotion_v=emotion_v,
                emotion_a=emotion_a,
                emotion_d=emotion_d,
                priority_mode=priority_mode,
                priority_info=priority_info,
                is_emoji=is_emoji,
                is_picid=is_picid,
                is_notify=is_notify,
                is_notice=is_notice if isinstance(message, MessageRecv) else False,
                notice_type=notice_type if isinstance(message, MessageRecv) else None,
                notice_data=notice_data if isinstance(message, MessageRecv) else None,
                is_command=is_command,
                intercept_message_level=intercept_message_level,
                key_words=key_words,
                key_words_lite=key_words_lite,
                selected_expressions=selected_expressions,
            )
        except Exception:
            logger.exception("存储消息失败")
            logger.error(f"消息：{message}")
            traceback.print_exc()

    # 如果需要其他存储相关的函数，可以在这里添加
    @staticmethod
    def update_message(mmc_message_id: str | None, qq_message_id: str | None) -> bool:
        """实时更新数据库的自身发送消息ID"""
        try:
            if not qq_message_id:
                logger.info("消息不存在message_id，无法更新")
                return False
            if matched_message := (
                Messages.select().where((Messages.message_id == mmc_message_id)).order_by(Messages.time.desc()).first()
            ):
                # 更新找到的消息记录
                Messages.update(message_id=qq_message_id).where(Messages.id == matched_message.id).execute()  # type: ignore
                logger.debug(f"更新消息ID成功: {matched_message.message_id} -> {qq_message_id}")
                return True
            else:
                logger.debug("未找到匹配的消息")
                return False

        except Exception as e:
            logger.error(f"更新消息ID失败: {e}")
            return False

    @staticmethod
    def replace_image_descriptions(text: str) -> str:
        """将[图片：描述]替换为[picid:image_id]"""
        # 先检查文本中是否有图片标记
        pattern = r"\[图片：([^\]]+)\]"
        matches = re.findall(pattern, text)

        if not matches:
            logger.debug("文本中没有图片标记，直接返回原文本")
            return text

        def replace_match(match):
            description = match.group(1).strip()
            try:
                image_record = (
                    Images.select().where(Images.description == description).order_by(Images.timestamp.desc()).first()
                )
                return f"[picid:{image_record.image_id}]" if image_record else match.group(0)
            except Exception:
                return match.group(0)

        return re.sub(r"\[图片：([^\]]+)\]", replace_match, text)
