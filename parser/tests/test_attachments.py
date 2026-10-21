import importlib.util
import os
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "core_wecom_attachments",
    os.path.join(ROOT, "core-wecom", "attachments.py"),
)
attachments = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attachments)


class WeComAttachmentResolverTests(unittest.TestCase):
    def test_resolve_by_filename_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "1688", "Cache", "File")
            os.makedirs(cache)
            path = os.path.join(cache, "report.zip")
            with open(path, "wb") as f:
                f.write(b"zip")
            resolver = attachments.WeComAttachmentResolver(
                "", [os.path.join(tmp, "1688")]
            )
            found = resolver.resolve_file(123, filename_hint="report.zip")
            self.assertEqual(os.path.realpath(found), os.path.realpath(path))
            info = resolver.resolve(123, filename_hint="report.zip")
            self.assertTrue(info.available)
            self.assertEqual(info.kind, "file")


if __name__ == "__main__":
    unittest.main()
