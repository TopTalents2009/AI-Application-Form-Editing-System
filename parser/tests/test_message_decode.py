import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "message_decode", os.path.join(ROOT, "core-wecom", "message_decode.py")
)
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)


class MessageDecodeTests(unittest.TestCase):
    def test_recovers_type2_ssh_command(self):
        raw = '/\x08\x00\x12+\n)ssh -i "私钥路径" root@43.160.235.132'
        text = md.recover_display_text(2, raw)
        self.assertIn("ssh -i", text)
        self.assertNotIn("\x08", text)

    def test_recovers_toml_blob(self):
        raw = '\x06\x08\x00\x12\x06\n\x06[cli]\ninstaller = "internal"\n'
        text = md.recover_display_text(2, raw)
        self.assertIn("installer", text)

    def test_image_falls_back_to_url(self):
        raw = "\x07\x08x\x03BChttps://wework.qpic.cn/wwpic3az/demo/0"
        text = md.recover_display_text(4, raw)
        self.assertIn("wework.qpic.cn", text)
        self.assertIn("[图片]", text)

    def test_image_fields_split_url_from_caption(self):
        raw = "[图片] https://wework.qpic.cn/wwpic3az/163172_demo_1767679929/0P"
        text, url = md.recover_display_fields(4, raw)[:2]
        self.assertEqual(text, "[图片]")
        self.assertEqual(url, "https://wework.qpic.cn/wwpic3az/163172_demo_1767679929/0")

    def test_ignores_doc_icon_png(self):
        raw = "https://wwcdn.weixin.qq.com/node/wework/images/sheet_tsp@3x.png"
        self.assertEqual(md.extract_image_urls(raw), [])

    def test_plain_chinese_stays(self):
        self.assertEqual(md.recover_display_text(2, "你好世界"), "你好世界")

    def test_extracts_zip_from_garbled_file_text(self):
        raw = "[图片/文件] 文件] 文件] 文件] AiSession-client-win.zip"
        text, url, name = md.recover_display_fields(15, raw)
        self.assertEqual(name, "AiSession-client-win.zip")
        self.assertIn("AiSession-client-win.zip", text)
        self.assertEqual(url, "")

    def test_extracts_hash_named_jpg(self):
        raw = "[文件] ] ] ] ] ] -045c4806893b4f79b2736d9491b28aa5_compress.jpg"
        self.assertEqual(
            md.extract_attachment_name(raw),
            "-045c4806893b4f79b2736d9491b28aa5_compress.jpg",
        )

    def test_plain_text_does_not_become_file(self):
        text, url, name = md.recover_display_fields(2, "请看 prompt_content.txt")
        self.assertEqual(name, "")
        self.assertIn("prompt_content.txt", text)

    def test_strips_protobuf_prefix_from_synced_text(self):
        text = md.recover_display_text(2, "M I\nG试用期全部用这个 过了试用期公司会分配账号使用grok")
        self.assertTrue(text.startswith("试用期"))
        self.assertIn("grok", text)

    def test_drops_random_id_tokens(self):
        self.assertTrue(md._is_id_token("dt2nbZncRN6ajH8"))
        self.assertFalse(md._is_id_token("qodercli"))
        self.assertEqual(md.recover_display_text(2, "dt2nbZncRN6ajH8"), "[文本]")

    def test_strips_leading_junk_before_chinese(self):
        self.assertEqual(md.recover_display_text(2, "'肯定是王伦那边同意了才能接"), "肯定是王伦那边同意了才能接")
        self.assertIn("汪伦", md.recover_display_text(2, "6你有汪伦同意吗？还是自己直接接入进去"))

    def test_drops_wecom_app_version(self):
        self.assertEqual(md.recover_display_text(2, "2.8.18"), "[文本]")
        text = md.recover_display_text(2, "目前缺失信息：无\n2.8.18")
        self.assertEqual(text, "目前缺失信息：无")
        self.assertNotIn("2.8.18", md.recover_display_text(
            2, "群聊的聊天记录\n2.8.18\n518 经开区"
        ))
        parts = [
            "【工作进度播报】\n汇报人：吴贤腾",
            "节点：下午进度",
            "人才库修改模板系统80%：针对用户反馈，生成 _修改后.docx",
        ]
        text = md._pick_best_text(parts, ["_修改后.docx"])
        self.assertIn("汇报人：吴贤腾", text)
        self.assertNotEqual(text, "_修改后.docx")

    def test_keeps_short_replies(self):
        for raw in ("好", "嗯", "好的", "收到", "谢谢", "可以", "哈哈", "ok", "OK", "？"):
            self.assertFalse(md._is_garbage_text(raw), raw)
            self.assertEqual(md.recover_display_text(2, raw), raw)
            self.assertEqual(md.display_message_content(2, raw), raw)

    def test_decodes_wecom_short_protobuf(self):
        ok_blob = bytes.fromhex("0a08080012040a026f6b")
        thanks_blob = bytes.fromhex("0a0c080012080a06e5a5bde79a84")
        recv_blob = bytes.fromhex("0a0c080012080a06e694b6e588b0")
        self.assertEqual(md.display_message_content(2, ok_blob), "ok")
        self.assertEqual(md.display_message_content(2, thanks_blob), "好的")
        self.assertEqual(md.display_message_content(2, recv_blob), "收到")

    def test_keeps_wecom_bracket_emoji(self):
        blob = bytes.fromhex("0a0e0803120a0a085be68ab1e68bb35d")
        self.assertEqual(md.display_message_content(2, blob), "[抱拳]")

    def test_still_drops_single_ascii_leftover(self):
        self.assertTrue(md._is_garbage_text("A"))
        self.assertEqual(md.recover_display_text(2, "A"), "[文本]")

    def test_keeps_mac_address_and_user_path(self):
        mac = bytes.fromhex("0a17080012130a1133432d37412d41412d36312d46302d4535")
        self.assertEqual(md.display_message_content(2, mac), "3C-7A-AA-61-F0-E5")
        path_blob = bytes.fromhex("0a17080012130a11433a5c55736572735c77755c2e67726f6b")
        self.assertIn(".grok", md.display_message_content(2, path_blob))
        self.assertTrue(md._is_noise_text(r"C:\Users\wu\AppData\Local\WXWork\cache"))

    def test_keeps_url_and_id_list(self):
        url_blob = b"\n/\x08\x00\x12+\n)https://github.com/accten/agent-/issues/1"
        text = md.display_message_content(2, url_blob)
        self.assertIn("github.com", text)
        self.assertFalse(text.startswith(")"))
        id_list = "23164、50203、53590、51268、40066、54903，40302、30708"
        self.assertFalse(md._is_garbage_text(id_list))
        self.assertIn("23164", md.recover_display_text(2, id_list))

    def test_keeps_ipv4_and_emoji(self):
        self.assertFalse(md._is_app_version("192.168.2.130"))
        self.assertTrue(md._is_app_version("2.8.18"))
        self.assertEqual(md.recover_display_text(2, "192.168.2.130"), "192.168.2.130")
        self.assertIn("👌", md.recover_display_text(2, "👌"))

    def test_known_type_names_cover_common_unknowns(self):
        self.assertEqual(md.message_type_name(1002), "名片")
        self.assertEqual(md.message_type_name(29), "动画表情")
        self.assertEqual(md.message_type_name(123), "图文混排")
        self.assertEqual(md.message_type_name(13), "在线文档")
        self.assertEqual(md.message_type_name(6), "位置")
        self.assertEqual(md.message_type_name(23), "视频")

    def test_rewrites_legacy_unknown_placeholder(self):
        self.assertEqual(md.recover_display_text(1002, "[未知(1002)]"), "[名片]")
        self.assertEqual(md.recover_display_text(1052, "[未知(1052)]"), "[置顶消息]")
        self.assertEqual(
            md.recover_display_text(123, "[未知(123)] 企业微信截图_17651630656241.png"),
            "[图文混排] 企业微信截图_17651630656241.png",
        )
        self.assertEqual(md.display_message_content(1002, "1688854750794009"), "[名片]")
        self.assertEqual(
            md.display_message_content(1006, "1688854750794009;1688856511813320"),
            "[邀请入群]",
        )
        self.assertEqual(md.display_message_content(1001, "东莞大朗"), "东莞大朗")


if __name__ == "__main__":
    unittest.main()
