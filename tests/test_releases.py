import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

import releases  # noqa: E402


class ParseReleasesTests(unittest.TestCase):
    def test_keeps_published_releases_with_the_zip(self):
        data = [
            {"tag_name": "v0.2.1", "assets": [
                {"name": "claude-sessions.zip", "browser_download_url": "https://x/0.2.1.zip", "digest": "sha256:abc"},
            ]},
            {"tag_name": "v0.3.0", "draft": True, "assets": [{"name": "claude-sessions.zip"}]},
            {"tag_name": "v0.1.9", "assets": [{"name": "other.zip"}]},
            {"tag_name": "v0.2.0", "assets": [
                {"name": "claude-sessions.zip", "browser_download_url": "https://x/0.2.0.zip", "digest": None},
            ]},
        ]
        self.assertEqual(releases.parse_releases(data), [
            {"version": "0.2.1", "url": "https://x/0.2.1.zip", "hash": "abc"},
            {"version": "0.2.0", "url": "https://x/0.2.0.zip", "hash": ""},
        ])

    def test_unreachable_host_raises_quickly(self):
        with self.assertRaises(RuntimeError):
            releases.fetch_releases("https://127.0.0.1:9/none", timeout=2)


if __name__ == "__main__":
    unittest.main()
