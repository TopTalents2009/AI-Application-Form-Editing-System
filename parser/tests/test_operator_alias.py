import os
import tempfile
import unittest
from unittest import mock

from shared import operator_alias


class OperatorAliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "operator_aliases.json")
        self.patcher = mock.patch.object(operator_alias, "ALIAS_FILE", self.path)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_resolve_prefers_host(self):
        operator_alias.set_alias(host="192.168.2.31", computer="DESKTOP-ONNAGDC", name="彭映霞")
        operator_alias.set_alias(host="192.168.2.38", name="王康群")
        self.assertEqual(
            operator_alias.resolve_operator_name("192.168.2.31", "DESKTOP-ONNAGDC", "SHEN"),
            "彭映霞",
        )
        self.assertEqual(
            operator_alias.resolve_operator_name("192.168.2.38", "PC-NEW", ""),
            "王康群",
        )
        self.assertEqual(
            operator_alias.resolve_operator_name("192.168.2.99", "OTHER", "张三"),
            "张三",
        )
