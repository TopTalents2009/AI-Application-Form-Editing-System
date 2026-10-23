import importlib.util
import os
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "wecom_sync_uninstall", os.path.join(ROOT, "agent", "wecom_sync_uninstall.py")
)
uninstall = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uninstall)


class WecomSyncUninstallTests(unittest.TestCase):
    def test_parse_quoted_exe(self):
        self.assertEqual(
            uninstall.parse_quoted_exe(r'"C:\Apps\WeComSyncAgent.exe" --background'),
            r"C:\Apps\WeComSyncAgent.exe",
        )
        self.assertEqual(
            uninstall.parse_quoted_exe(r"C:\Apps\WeComSyncAgent.exe --background"),
            r"C:\Apps\WeComSyncAgent.exe",
        )
        self.assertEqual(uninstall.parse_quoted_exe(""), "")

    def test_work_dir_uses_appdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                uninstall.work_dir({"APPDATA": tmp}),
                os.path.join(tmp, "WeComSyncAgent"),
            )

    def test_uninstall_stops_and_clears_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            appdata = os.path.join(tmp, "appdata")
            work = os.path.join(appdata, "WeComSyncAgent")
            os.makedirs(work)
            with open(os.path.join(work, "settings.json"), "w", encoding="utf-8") as f:
                f.write("{}")
            with open(os.path.join(work, "agent.lock"), "w", encoding="utf-8") as f:
                f.write("4242")
            exe = os.path.join(tmp, "WeComSyncAgent.exe")
            with open(exe, "wb") as f:
                f.write(b"MZ")
            stopped = []
            disabled = []

            def stop_fn(root, skip_pids=None):
                stopped.append((root, skip_pids))
                return ["已结束助手进程 PID 4242"]

            def disable_fn():
                disabled.append(True)
                return True

            result = uninstall.run_uninstall(
                environ={"APPDATA": appdata},
                autostart_cmd='"' + exe + '" --background',
                disable_autostart_fn=disable_fn,
                stop_fn=stop_fn,
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(stopped[0][0], work)
            self.assertTrue(disabled)
            self.assertFalse(os.path.exists(work))
            self.assertFalse(os.path.exists(exe))
            self.assertIn("已关闭开机自启", result["logs"])
            self.assertIn("已删除数据目录 " + work, result["logs"])
            self.assertIn("已删除助手程序 " + exe, result["logs"])

    def test_uninstall_ok_when_nothing_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            appdata = os.path.join(tmp, "empty")
            os.makedirs(appdata)
            result = uninstall.run_uninstall(
                environ={"APPDATA": appdata},
                autostart_cmd="",
                disable_autostart_fn=lambda: False,
                stop_fn=lambda root, skip_pids=None: ["助手当前没有在运行"],
            )
            self.assertTrue(result["ok"], result)
            self.assertIn("没有数据目录", "".join(result["logs"]))


if __name__ == "__main__":
    unittest.main()
