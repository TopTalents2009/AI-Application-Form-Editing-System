import unittest

from api.server import _enrich_local_source, _is_this_pc, _same_machine


class SourceMergeTests(unittest.TestCase):
    def test_same_machine_strips_local_suffix(self):
        self.assertTrue(_same_machine(
            "DESKTOP-TTCUQMG（本机）",
            "",
            "DESKTOP-TTCUQMG",
            "192.168.2.25",
        ))

    def test_this_pc_matches_lan_ip(self):
        local = {
            "id": "local",
            "computer_name": "DESKTOP-TTCUQMG（本机）",
            "host": "",
        }
        self.assertTrue(_is_this_pc(
            "DESKTOP-TTCUQMG",
            "192.168.2.25",
            local,
            {"192.168.2.25"},
        ))
        self.assertFalse(_is_this_pc(
            "machefu003",
            "192.168.2.14",
            local,
            {"192.168.2.25"},
        ))

    def test_enrich_local_copies_operator_name(self):
        local = {
            "id": "local",
            "kind": "local",
            "computer_name": "DESKTOP-TTCUQMG（本机）",
            "operator_name": "",
            "host": "",
        }
        clients = [{
            "source_id": "DESKTOP-TTCUQMG-1",
            "operator_name": "吴贤腾",
            "computer_name": "DESKTOP-TTCUQMG",
            "host": "192.168.2.25",
        }]
        _enrich_local_source(local, clients)
        self.assertEqual(local["operator_name"], "吴贤腾")
        self.assertEqual(local["host"], "192.168.2.25")


if __name__ == "__main__":
    unittest.main()
