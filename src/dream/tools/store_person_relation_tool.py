"""
Dream 工具：将整理出的人物相关记忆写入 Person.memory_points（带可选 category）。
与概括器自动写关系互补：dream 在整理时可主动沉淀「关于某人的精炼事实」。
"""

from src.common.logger import get_logger
from src.person_info.person_info import store_person_memory_from_answer

logger = get_logger("dream_agent")

# 与 chat_history_summarizer.MEMORY_CATEGORY_OPTIONS 一致
ALLOWED_CATEGORIES = ("工作", "生活", "偏好", "情感", "娱乐", "学习", "其他")


def make_store_person_relation(chat_id: str):
    async def store_person_relation(
        person_name: str,
        memory_content: str,
        category: str = "其他",
    ) -> str:
        """将关于某人的一条记忆写入其 Person 档案（用于整理时沉淀精炼关系）。"""
        try:
            cat = (category or "其他").strip()
            if cat not in ALLOWED_CATEGORIES:
                cat = "其他"
            await store_person_memory_from_answer(
                person_name=person_name.strip(),
                memory_content=(memory_content or "")[:500],
                chat_id=chat_id,
                category=cat,
            )
            msg = f"已为 {person_name} 写入一条记忆（分类: {cat}）"
            logger.info(f"[dream][tool] store_person_relation 完成: {msg}")
            return msg
        except Exception as e:
            logger.error(f"store_person_relation 失败: {e}")
            return f"store_person_relation 执行失败: {e}"

    return store_person_relation
