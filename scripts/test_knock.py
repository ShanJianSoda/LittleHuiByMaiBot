import os
import sys
import time
import json
import unittest
from pprint import pformat


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.common.database.database import db
from src.common.database.database_model import ChatStreams, Knock
from src.common.knock import knock_manager


class KnockManagerTestCase(unittest.TestCase):
    TEST_PRIVATE_CHAT_ID = "test_knock_private_chat"
    TEST_GROUP_CHAT_ID = "test_knock_group_chat"
    TEST_FALLBACK_CHAT_ID = "test_knock_fallback_chat"

    @classmethod
    def setUpClass(cls) -> None:
        cls._print_banner("Knock 测试开始")
        db.connect(reuse_if_open=True)
        print("[准备] 清理旧的测试数据")
        cls._cleanup_test_data()
        print("[准备] 插入新的测试数据")
        cls._seed_test_data()
        cls._print_seed_summary()

    @classmethod
    def tearDownClass(cls) -> None:
        print("[清理] 删除测试数据")
        cls._cleanup_test_data()
        if not db.is_closed():
            db.close()
        cls._print_banner("Knock 测试结束")

    def setUp(self) -> None:
        print(f"\n[用例] {self._testMethodName}")

    @classmethod
    def _print_banner(cls, title: str) -> None:
        line = "=" * 80
        print(f"\n{line}\n{title}\n{line}")

    @classmethod
    def _pretty_print(cls, label: str, value) -> None:
        print(f"{label}:\n{pformat(value, width=100, sort_dicts=False)}")

    @classmethod
    def _cleanup_test_data(cls) -> None:
        Knock.delete().where(
            Knock.chat_id.in_(
                [
                    cls.TEST_PRIVATE_CHAT_ID,
                    cls.TEST_GROUP_CHAT_ID,
                    cls.TEST_FALLBACK_CHAT_ID,
                ]
            )
            | Knock.knock_id.in_(["1000000000001", "2000000000123"])
        ).execute()
        ChatStreams.delete().where(
            ChatStreams.stream_id.in_(
                [
                    cls.TEST_PRIVATE_CHAT_ID,
                    cls.TEST_GROUP_CHAT_ID,
                    cls.TEST_FALLBACK_CHAT_ID,
                ]
            )
        ).execute()

    @classmethod
    def _seed_test_data(cls) -> None:
        now = time.time()
        ChatStreams.create(
            stream_id=cls.TEST_PRIVATE_CHAT_ID,
            create_time=now,
            last_active_time=now,
            platform="qq",
            user_platform="qq",
            user_id="1234567890123",
            user_nickname="原始私聊用户",
            user_cardname="",
            group_platform=None,
            group_id=None,
            group_name=None,
        )
        ChatStreams.create(
            stream_id=cls.TEST_GROUP_CHAT_ID,
            create_time=now,
            last_active_time=now,
            platform="qq",
            user_platform="qq",
            user_id="2234567890123",
            user_nickname="群消息发送者",
            user_cardname="",
            group_platform="qq",
            group_id="9876543210123",
            group_name="原始测试群",
        )
        ChatStreams.create(
            stream_id=cls.TEST_FALLBACK_CHAT_ID,
            create_time=now,
            last_active_time=now,
            platform="qq",
            user_platform="qq",
            user_id="3234567890123",
            user_nickname="仅流回退用户",
            user_cardname="",
            group_platform=None,
            group_id=None,
            group_name=None,
        )
        Knock.create(
            chat_id=cls.TEST_PRIVATE_CHAT_ID,
            knock_id="1000000000001",
            platform="qq",
            knock_type="private",
            user_id="1234567890123",
            group_id=None,
            display_name="小明",
            remark_name="明明",
            aliases=json.dumps(["xm", "测试私聊"], ensure_ascii=False),
            source="test",
            is_enabled=True,
            meta_json=json.dumps({"note": "private test contact"}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        Knock.create(
            chat_id=cls.TEST_GROUP_CHAT_ID,
            knock_id="2000000000123",
            platform="qq",
            knock_type="group",
            user_id=None,
            group_id="9876543210123",
            display_name="测试群聊",
            remark_name="项目群",
            aliases=json.dumps(["tg", "开发群"], ensure_ascii=False),
            source="test",
            is_enabled=True,
            meta_json=json.dumps({"note": "group test contact"}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def _print_seed_summary(cls) -> None:
        print("[数据] 已插入测试 ChatStreams:")
        for stream_id in [
            cls.TEST_PRIVATE_CHAT_ID,
            cls.TEST_GROUP_CHAT_ID,
            cls.TEST_FALLBACK_CHAT_ID,
        ]:
            stream = ChatStreams.get_or_none(ChatStreams.stream_id == stream_id)
            if stream:
                cls._pretty_print(
                    f"  - ChatStream {stream_id}",
                    {
                        "stream_id": stream.stream_id,
                        "platform": stream.platform,
                        "user_id": stream.user_id,
                        "user_nickname": stream.user_nickname,
                        "group_id": stream.group_id,
                        "group_name": stream.group_name,
                    },
                )

        print("[数据] 已插入测试 Knock:")
        for chat_id in [cls.TEST_PRIVATE_CHAT_ID, cls.TEST_GROUP_CHAT_ID]:
            item = Knock.get_or_none(Knock.chat_id == chat_id)
            if item:
                cls._pretty_print(
                    f"  - Knock {chat_id}",
                    {
                        "chat_id": item.chat_id,
                        "knock_id": item.knock_id,
                        "display_name": item.display_name,
                        "remark_name": item.remark_name,
                        "aliases": json.loads(item.aliases or "[]"),
                        "type": item.knock_type,
                    },
                )

    def test_get_contact_by_chat_id_prefers_knock_data(self) -> None:
        contact = knock_manager.get_contact_by_chat_id(self.TEST_PRIVATE_CHAT_ID)
        self._pretty_print("[结果] get_contact_by_chat_id", contact.to_dict() if contact else None)
        self.assertIsNotNone(contact)
        assert contact is not None
        self.assertEqual(contact.knock_id, "1000000000001")
        self.assertEqual(contact.primary_name, "明明")
        self.assertIn("xm", contact.aliases)

    def test_resolve_chat_id_by_knock_id_and_alias(self) -> None:
        results = {
            "1000000000001": knock_manager.resolve_chat_id("1000000000001"),
            "明明": knock_manager.resolve_chat_id("明明"),
            "开发群": knock_manager.resolve_chat_id("开发群"),
        }
        self._pretty_print("[结果] resolve_chat_id", results)
        self.assertEqual(results["1000000000001"], self.TEST_PRIVATE_CHAT_ID)
        self.assertEqual(results["明明"], self.TEST_PRIVATE_CHAT_ID)
        self.assertEqual(results["开发群"], self.TEST_GROUP_CHAT_ID)

    def test_fallback_to_chat_stream_when_knock_missing(self) -> None:
        contact = knock_manager.get_contact_by_chat_id(self.TEST_FALLBACK_CHAT_ID)
        self._pretty_print("[结果] fallback_contact", contact.to_dict() if contact else None)
        self.assertIsNotNone(contact)
        assert contact is not None
        self.assertEqual(contact.chat_id, self.TEST_FALLBACK_CHAT_ID)
        self.assertEqual(contact.source, "chat_stream_fallback")
        self.assertEqual(contact.display_name, "仅流回退用户")

    def test_humanize_structure_replaces_chat_refs(self) -> None:
        payload = {
            "target_chat_id": self.TEST_PRIVATE_CHAT_ID,
            "payload": {
                "chat_id": self.TEST_GROUP_CHAT_ID,
                "source_chat_id": self.TEST_PRIVATE_CHAT_ID,
            },
        }
        humanized = knock_manager.humanize_structure(payload)
        self._pretty_print("[输入] humanize_structure", payload)
        self._pretty_print("[输出] humanize_structure", humanized)
        self.assertIsInstance(humanized["target_chat_id"], dict)
        self.assertEqual(humanized["target_chat_id"]["name"], "明明")
        self.assertEqual(humanized["payload"]["chat_id"]["name"], "项目群")

    def test_resolve_structure_chat_refs_restores_chat_ids(self) -> None:
        payload = {
            "target_ref": "明明",
            "payload": {
                "chat_id": "项目群",
                "source_chat_id": "1000000000001",
                "share_target_chat_id": "开发群",
            },
        }
        resolved = knock_manager.resolve_structure_chat_refs(payload)
        self._pretty_print("[输入] resolve_structure_chat_refs", payload)
        self._pretty_print("[输出] resolve_structure_chat_refs", resolved)
        self.assertEqual(resolved["target_chat_id"], self.TEST_PRIVATE_CHAT_ID)
        self.assertEqual(resolved["payload"]["chat_id"], self.TEST_GROUP_CHAT_ID)
        self.assertEqual(resolved["payload"]["source_chat_id"], self.TEST_PRIVATE_CHAT_ID)
        self.assertEqual(resolved["payload"]["share_target_chat_id"], self.TEST_GROUP_CHAT_ID)


if __name__ == "__main__":
    unittest.main(verbosity=2)
