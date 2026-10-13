import os
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get("INBOXED_LIVE_GMAIL") == "1", "Opt-in Gmail smoke test")
class LiveGmailTests(unittest.TestCase):
    def test_read_one_message(self):
        from platformdirs import user_data_path
        from inboxed.bootstrap import provider_session
        from inboxed.config import load_settings

        settings = load_settings(Path(os.environ.get("INBOXED_CONFIG", "config.toml")))
        alias = os.environ.get("INBOXED_ACCOUNT", "personal")
        self.assertEqual(settings.accounts[alias].provider, "gmail")
        directory = Path(os.environ.get("INBOXED_DATA_DIR", str(user_data_path("inboxed"))))
        with provider_session(settings, alias, directory, write=False) as provider:
            page = provider.list_inbox(limit=1)
            for ref in page.messages:
                message = provider.get_message(ref)
                self.assertIsNotNone(message)
                self.assertEqual(message.ref.account, provider.account_identity())
