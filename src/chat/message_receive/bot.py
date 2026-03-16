import traceback
import os
import re
import json
import time

from typing import Dict, Any, Optional, Tuple
from maim_message import UserInfo, Seg, GroupInfo

from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.message_receive.message import MessageRecv
from src.chat.message_receive.storage import MessageStorage
from src.chat.heartbeat_v2.system import get_heartbeat_v2_system
from src.chat.heart_flow.heartflow_message_processor import HeartFCMessageReceiver
from src.chat.utils.prompt_builder import Prompt, global_prompt_manager
from src.plugin_system.core import component_registry, events_manager, global_announcement_manager
from src.plugin_system.base import BaseCommand, EventType

# 定义日志配置

# 获取项目根目录（假设本文件在src/chat/message_receive/下，根目录为上上上级目录）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))

# 配置主程序日志格式
logger = get_logger("chat")


def _check_ban_words(text: str, userinfo: UserInfo, group_info: Optional[GroupInfo] = None) -> bool:
    """检查消息是否包含过滤词

    Args:
        text: 待检查的文本
        chat: 聊天对象
        userinfo: 用户信息

    Returns:
        bool: 是否包含过滤词
    """
    for word in global_config.message_receive.ban_words:
        if word in text:
            chat_name = group_info.group_name if group_info else "私聊"
            logger.info(f"[{chat_name}]{userinfo.user_nickname}:{text}")
            logger.info(f"[过滤词识别]消息中含有{word}，filtered")
            return True
    return False


def _check_ban_regex(text: str, userinfo: UserInfo, group_info: Optional[GroupInfo] = None) -> bool:
    """检查消息是否匹配过滤正则表达式

    Args:
        text: 待检查的文本
        chat: 聊天对象
        userinfo: 用户信息

    Returns:
        bool: 是否匹配过滤正则
    """
    # 检查text是否为None或空字符串
    if text is None or not text:
        return False

    for pattern in global_config.message_receive.ban_msgs_regex:
        if re.search(pattern, text):
            chat_name = group_info.group_name if group_info else "私聊"
            logger.info(f"[{chat_name}]{userinfo.user_nickname}:{text}")
            logger.info(f"[正则表达式过滤]消息匹配到{pattern}，filtered")
            return True
    return False


class ChatBot:
    def __init__(self):
        self.bot = None  # bot 实例引用
        self._started = False
        self.heartflow_message_receiver = HeartFCMessageReceiver()  # 新增
        # 通知消息去重缓存 {(chat_id, notice_type): last_timestamp}
        self._notice_dedup_cache: Dict[Tuple[str, str], float] = {}

    async def _ensure_started(self):
        """确保所有任务已启动"""
        if not self._started:
            logger.debug("确保ChatBot所有任务已启动")

            self._started = True

    async def _process_commands(self, message: MessageRecv):
        # sourcery skip: use-named-expression
        """使用新插件系统处理命令"""
        try:
            text = message.processed_plain_text

            # 使用新的组件注册中心查找命令
            command_result = component_registry.find_command_by_text(text)
            if command_result:
                command_class, matched_groups, command_info = command_result
                plugin_name = command_info.plugin_name
                command_name = command_info.name
                if (
                    message.chat_stream
                    and message.chat_stream.stream_id
                    and command_name
                    in global_announcement_manager.get_disabled_chat_commands(message.chat_stream.stream_id)
                ):
                    logger.info("用户禁用的命令，跳过处理")
                    return False, None, True

                message.is_command = True

                # 获取插件配置
                plugin_config = component_registry.get_plugin_config(plugin_name)

                # 创建命令实例
                command_instance: BaseCommand = command_class(message, plugin_config)
                command_instance.set_matched_groups(matched_groups)

                try:
                    # 执行命令
                    success, response, intercept_message_level = await command_instance.execute()
                    message.intercept_message_level = intercept_message_level

                    # 记录命令执行结果
                    if success:
                        logger.info(f"命令执行成功: {command_class.__name__} (拦截等级: {intercept_message_level})")
                    else:
                        logger.warning(f"命令执行失败: {command_class.__name__} - {response}")

                    # 根据命令的拦截设置决定是否继续处理消息
                    return (
                        True,
                        response,
                        not bool(intercept_message_level),
                    )  # 找到命令，根据intercept_message决定是否继续

                except Exception as e:
                    logger.error(f"执行命令时出错: {command_class.__name__} - {e}")
                    logger.error(traceback.format_exc())

                    try:
                        await command_instance.send_text(f"命令执行出错: {str(e)}")
                    except Exception as send_error:
                        logger.error(f"发送错误消息失败: {send_error}")

                    # 命令出错时，根据命令的拦截设置决定是否继续处理消息
                    return True, str(e), False  # 出错时继续处理消息

            # 没有找到命令，继续处理消息
            return False, None, True

        except Exception as e:
            logger.error(f"处理命令时出错: {e}")
            return False, None, True  # 出错时继续处理消息

    async def handle_notice_message(self, message: MessageRecv) -> bool:
        """
        统一处理 NapCat/Adapter 发来的 notice 消息：撤回 / 戳一戳 / 输入状态 等。

        - 解析通知类型与场景（sub_type / scene）
        - 解析 chat_id（平台 + 群/私聊）
        - 按配置进行去重
        - 需要时写入 Messages 表（is_notice / notice_type / notice_data）
        """
        # 非 notice 消息直接跳过
        if message.message_info.message_id != "notice":
            return False

        message.is_notify = True
        logger.debug("notice消息")

        try:
            seg = message.message_segment
            mi = message.message_info

            # 仅处理 type == notify 且 data 为 dict 的情况
            if getattr(seg, "type", None) != "notify":
                return True

            notice_data = getattr(seg, "data", {})
            if not isinstance(notice_data, dict):
                return True

            sub_type = notice_data.get("sub_type")
            scene = notice_data.get("scene")

            # 解析通知类型（字符串）
            notice_type = self._parse_notice_type(sub_type, scene)
            if not notice_type:
                logger.debug(f"[notice] 未识别的通知类型: sub_type={sub_type}, scene={scene}")
                return True

            # 解析 chat_id
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

            # 去重判断（根据配置）
            if not self._should_record_notice(chat_id, notice_type):
                logger.debug(f"[notice] {notice_type} 去重，跳过记录")
                return True

            # 持久化记录
            await self._store_notice_message(
                message=message,
                chat_id=chat_id,
                notice_type=notice_type,
                notice_data=notice_data,
            )

            # 日志输出（人类可读）
            self._log_notice_message(notice_type, notice_data, mi)

        except Exception as e:
            logger.error(f"[notice] 处理通知消息失败: {e}")
            logger.error(traceback.format_exc())

        return True

    def _parse_notice_type(self, sub_type: Optional[str], scene: Optional[str]) -> Optional[str]:
        """根据 sub_type / scene 解析统一的通知类型字符串。"""
        if sub_type == "recall":
            return "recall"
        # 不同适配器可能把“戳一戳”放在 sub_type 或 scene 中
        if sub_type == "poke" or scene == "poke":
            return "poke"
        # 输入状态（未来会从 input_status_process 并入这里）
        if sub_type == "input_status" or scene == "input_status":
            return "input_status"
        return None

    def _should_record_notice(self, chat_id: str, notice_type: str) -> bool:
        """基于配置与去重缓存，判断是否需要记录该通知。"""
        # 全局开关
        if not getattr(global_config.message_receive, "enable_notice_tracking", True):
            return False

        # 获取各类型去重窗口
        dedup_windows = getattr(global_config.message_receive, "notice_dedup_windows", {}) or {}
        dedup_window = int(dedup_windows.get(notice_type, 30))

        # 0 表示不去重
        if dedup_window == 0:
            return True

        key: Tuple[str, str] = (chat_id, notice_type)
        now = time.time()

        last_ts = self._notice_dedup_cache.get(key)
        if last_ts is not None and now - last_ts < dedup_window:
            return False

        # 更新缓存
        self._notice_dedup_cache[key] = now

        # 简单清理过期缓存，避免无限增长
        # 使用最大窗口的 2 倍作为保留时间
        max_window = max(dedup_windows.values(), default=dedup_window)
        expire_after = max_window * 2 if max_window > 0 else 60
        to_delete = [k for k, ts in self._notice_dedup_cache.items() if now - ts > expire_after]
        for k in to_delete:
            self._notice_dedup_cache.pop(k, None)

        return True

    async def _store_notice_message(
        self,
        message: MessageRecv,
        chat_id: str,
        notice_type: str,
        notice_data: Dict[str, Any],
    ) -> None:
        """将通知消息以统一结构写入 Messages 表。"""
        try:
            chat = get_chat_manager().get_stream(chat_id)
            if not chat:
                logger.warning(f"[notice] 未找到聊天流: {chat_id}")
                return

            # 构造可读文本
            readable_text = self._format_notice_text(notice_type, notice_data, message.message_info)

            # 标记消息属性，供存储层使用
            message.is_notice = True
            message.notice_type = notice_type
            message.notice_data = json.dumps(notice_data, ensure_ascii=False)
            message.processed_plain_text = readable_text
            message.chat_stream = chat

            await MessageStorage.store_message(message, chat)

            logger.info(f"[notice] 持久化通知: chat_id={chat_id}, type={notice_type}")

        except Exception as e:
            logger.error(f"[notice] 存储通知消息失败: {e}")
            logger.error(traceback.format_exc())

    def _format_notice_text(self, notice_type: str, notice_data: Dict[str, Any], message_info: Any) -> str:
        """将通知格式化为人类可读的简短文本，便于调试与统计。"""
        user_info = getattr(message_info, "user_info", None)
        user_name = (
            getattr(user_info, "user_cardname", None)
            or getattr(user_info, "user_nickname", None)
            or str(getattr(user_info, "user_id", ""))
            or "未知用户"
        )

        if notice_type == "recall":
            recalled = notice_data.get("recalled_user_info") or {}
            if isinstance(recalled, dict):
                recalled_name = (
                    recalled.get("user_cardname")
                    or recalled.get("user_nickname")
                    or str(recalled.get("user_id", ""))
                    or "某人"
                )
                if str(recalled.get("user_id", "")) != str(getattr(user_info, "user_id", "")):
                    return f"[通知] {user_name} 撤回了 {recalled_name} 的消息"
            return f"[通知] {user_name} 撤回了消息"

        if notice_type == "poke":
            return f"[通知] {user_name} 戳了戳你"

        if notice_type == "input_status":
            return f"[通知] {user_name} 正在输入..."

        return f"[通知] {notice_type}"

    def _log_notice_message(self, notice_type: str, notice_data: Dict[str, Any], message_info: Any) -> None:
        """输出稍微详细一点的通知日志，便于在日志中观察行为。"""
        user_info = getattr(message_info, "user_info", None)
        user_name = (
            getattr(user_info, "user_cardname", None)
            or getattr(user_info, "user_nickname", None)
            or str(getattr(user_info, "user_id", ""))
            or "未知"
        )

        if notice_type == "recall":
            recalled = notice_data.get("recalled_user_info") or {}
            if isinstance(recalled, dict):
                recalled_name = (
                    recalled.get("user_cardname")
                    or recalled.get("user_nickname")
                    or str(recalled.get("user_id", ""))
                    or "某人"
                )
                logger.info(f"{user_name} 撤回了 {recalled_name} 的消息")
            else:
                logger.info(f"{user_name} 撤回了消息")
        elif notice_type == "poke":
            logger.info(f"{user_name} 戳了戳你")
        else:
            logger.debug(f"[notice] type={notice_type}, data={notice_data}")

    async def echo_message_process(self, raw_data: Dict[str, Any]) -> None:
        """
        用于专门处理回送消息ID的函数
        """
        message_data: Dict[str, Any] = raw_data.get("content", {})
        if not message_data:
            return
        message_type = message_data.get("type")
        if message_type != "echo":
            return
        mmc_message_id = message_data.get("echo")
        actual_message_id = message_data.get("actual_id")
        if MessageStorage.update_message(mmc_message_id, actual_message_id):
            logger.debug(f"更新消息ID成功: {mmc_message_id} -> {actual_message_id}")
        else:
            logger.warning(f"更新消息ID失败: {mmc_message_id} -> {actual_message_id}")

    # async def input_status_process(self, raw_data: Dict[str, Any]) -> None:
    #     """
    #     处理输入状态通知
    #     """
    #     message_data: Dict[str, Any] = raw_data.get("content", {})
    #     if not message_data:
    #         return
    #     message_type = message_data.get("type")
    #     if message_type != "input_status":
    #         return
        
    #     user_id = message_data.get("user_id")
    #     group_id = message_data.get("group_id")
    #     is_typing = message_data.get("is_typing", False)
    #     status_text = message_data.get("status_text", "")
        
    #     if is_typing:
    #         logger.info(f"[输入状态] 用户 {user_id} 正在输入..." + (f" (群: {group_id})" if group_id else ""))
    #     else:
    #         logger.debug(f"[输入状态] 用户 {user_id} 停止输入" + (f" (群: {group_id})" if group_id else ""))

    async def message_process(self, message_data: Dict[str, Any]) -> None:
        """处理转化后的统一格式消息
        这个函数本质是预处理一些数据，根据配置信息和消息内容，预处理消息，并分发到合适的消息处理器中
        heart_flow模式：使用思维流系统进行回复
        - 包含思维流状态管理
        - 在回复前进行观察和状态更新
        - 回复后更新思维流状态
        - 消息过滤
        - 记忆激活
        - 意愿计算
        - 消息生成和发送
        - 表情包处理
        - 性能计时
        """
        try:
            # 确保所有任务已启动
            await self._ensure_started()

            if message_data["message_info"].get("group_info") is not None:
                message_data["message_info"]["group_info"]["group_id"] = str(
                    message_data["message_info"]["group_info"]["group_id"]
                )
            if message_data["message_info"].get("user_info") is not None:
                message_data["message_info"]["user_info"]["user_id"] = str(
                    message_data["message_info"]["user_info"]["user_id"]
                )
            # print(message_data)
            # logger.debug(str(message_data))
            message = MessageRecv(message_data)
            group_info = message.message_info.group_info
            user_info = message.message_info.user_info

            continue_flag, modified_message = await events_manager.handle_mai_events(
                EventType.ON_MESSAGE_PRE_PROCESS, message
            )
            if not continue_flag:
                return
            if modified_message and modified_message._modify_flags.modify_message_segments:
                message.message_segment = Seg(type="seglist", data=modified_message.message_segments)

            if await self.handle_notice_message(message):
                pass

            # 处理消息内容，生成纯文本
            await message.process()

            # 平台层的 @ 检测由底层 is_mentioned_bot_in_message 统一处理；此处不做用户名硬编码匹配

            # 过滤检查
            if _check_ban_words(
                message.processed_plain_text,
                user_info,  # type: ignore
                group_info,
            ) or _check_ban_regex(
                message.raw_message,  # type: ignore
                user_info,  # type: ignore
                group_info,
            ):
                return

            get_chat_manager().register_message(message)

            chat = await get_chat_manager().get_or_create_stream(
                platform=message.message_info.platform,  # type: ignore
                user_info=user_info,  # type: ignore
                group_info=group_info,
            )

            message.update_chat_stream(chat)

            # if await self.check_ban_content(message):
            #     logger.warning(f"检测到消息中含有违法，色情，暴力，反动，敏感内容，消息内容：{message.processed_plain_text}，发送者：{message.message_info.user_info.user_nickname}")
            #     return

            # 命令处理 - 使用新插件系统检查并处理命令
            is_command, cmd_result, continue_process = await self._process_commands(message)

            # 如果是命令且不需要继续处理，则直接返回
            if is_command and not continue_process:
                await MessageStorage.store_message(message, chat)
                logger.info(f"命令处理完成，跳过后续消息处理: {cmd_result}")
                return

            continue_flag, modified_message = await events_manager.handle_mai_events(EventType.ON_MESSAGE, message)
            if not continue_flag:
                return
            if modified_message and modified_message._modify_flags.modify_plain_text:
                message.processed_plain_text = modified_message.plain_text

            # 确认从接口发来的message是否有自定义的prompt模板信息
            if message.message_info.template_info and not message.message_info.template_info.template_default:
                template_group_name: Optional[str] = message.message_info.template_info.template_name  # type: ignore
                template_items = message.message_info.template_info.template_items
                async with global_prompt_manager.async_message_scope(template_group_name):
                    if isinstance(template_items, dict):
                        for k in template_items.keys():
                            await Prompt.create_async(template_items[k], k)
                            logger.debug(f"注册{template_items[k]},{k}")
            else:
                template_group_name = None

            # async def preprocess():
            #     await self.heartflow_message_receiver.process_message(message)

            # if template_group_name:
            #     async with global_prompt_manager.async_message_scope(template_group_name):
            #         await preprocess()
            # else:
            #     await preprocess()

            try:
                get_heartbeat_v2_system().ingest_message(message)
            except Exception as heartbeat_error:  # noqa: BLE001
                logger.warning(f"heartbeat ingress 投递失败: {heartbeat_error}")

        except Exception as e:
            logger.error(f"预处理消息失败: {e}")
            traceback.print_exc()


# 创建全局ChatBot实例
chat_bot = ChatBot()
