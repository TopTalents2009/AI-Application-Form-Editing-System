from datetime import datetime, timedelta
import os
import tempfile
import unittest

from shared.presence import PresenceHub, display_who


class Clock:
    def __init__(self):
        self.t = datetime(2026, 9, 16, 12, 0, 0)

    def now(self):
        return self.t

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)


class PresenceHubTests(unittest.TestCase):
    def test_display_who_prefers_operator_name(self):
        self.assertEqual(display_who("张三", "PC-A"), "张三")
        self.assertEqual(display_who("", "PC-A"), "PC-A")

    def test_first_heartbeat_emits_online_alert(self):
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        result = hub.heartbeat("pc-a", "张三", "PC-A")
        self.assertTrue(result["online"])
        self.assertEqual(result["alert"]["kind"], "online")
        self.assertIn("张三", result["alert"]["title"])
        hub.heartbeat("pc-a", "张三", "PC-A")
        snapshot = hub.snapshot()
        self.assertEqual(len(snapshot["online"]), 1)
        self.assertEqual(len([a for a in snapshot["alerts"] if a["kind"] == "online"]), 1)

    def test_offline_then_online_alerts_again(self):
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        hub.heartbeat("pc-a", "张三", "PC-A")
        hub.mark_offline("pc-a")
        result = hub.heartbeat("pc-a", "张三", "PC-A")
        self.assertEqual(result["alert"]["kind"], "online")

    def test_expired_presence_drops_offline(self):
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        hub.heartbeat("pc-a", "张三", "PC-A")
        clock.advance(120)
        snapshot = hub.snapshot()
        self.assertEqual(snapshot["online"], [])

    def test_note_sync_appends_sync_alert(self):
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        hub.note_sync("pc-a", "张三", "PC-A", ["家", "工作"], 2)
        kinds = [a["kind"] for a in hub.snapshot()["alerts"]]
        self.assertIn("online", kinds)
        self.assertIn("sync", kinds)
        sync = [a for a in hub.snapshot()["alerts"] if a["kind"] == "sync"][0]
        self.assertIn("家", sync["message"])

    def test_presence_survives_restart(self):
        clock = Clock()
        folder = tempfile.TemporaryDirectory()
        path = os.path.join(folder.name, "presence.json")
        hub = PresenceHub(now=clock.now, persist_path=path)
        hub.heartbeat("pc-a", "张三", "PC-A", "192.168.2.14")
        restored = PresenceHub(now=clock.now, persist_path=path)
        snapshot = restored.snapshot()
        self.assertEqual(snapshot["online"][0]["source_id"], "pc-a")
        self.assertEqual(snapshot["online"][0]["operator_name"], "张三")
        folder.cleanup()

    def test_known_clients_keep_offline_machine(self):
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        hub.heartbeat("sky", "唐利萍", "SKY-PC", "192.168.2.11")
        clock.advance(120)
        self.assertEqual(hub.snapshot()["online"], [])
        known = hub.known_clients()
        self.assertEqual(known[0]["source_id"], "sky")
        self.assertEqual(known[0]["operator_name"], "唐利萍")

    def test_heartbeat_keeps_agent_version(self):
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        hub.heartbeat("pc-a", "张三", "PC-A", "192.168.2.14", "online", "2026.09.21.1")
        self.assertEqual(hub.snapshot()["online"][0]["agent_version"], "2026.09.21.1")
        hub.heartbeat("pc-a", "张三", "PC-A", "192.168.2.14")
        self.assertEqual(hub.snapshot()["online"][0]["agent_version"], "2026.09.21.1")
        clock = Clock()
        hub = PresenceHub(now=clock.now)
        hub.heartbeat("pc-a", "张三", "PC-A", "192.168.2.14")
        self.assertEqual(hub.mark_updating_by_host("192.168.2.14"), 1)
        clock.advance(120)
        snapshot = hub.snapshot()
        self.assertEqual(len(snapshot["online"]), 1)
        self.assertEqual(snapshot["online"][0]["status"], "updating")
        clock.advance(15 * 60)
        self.assertEqual(hub.snapshot()["online"], [])


if __name__ == "__main__":
    unittest.main()
