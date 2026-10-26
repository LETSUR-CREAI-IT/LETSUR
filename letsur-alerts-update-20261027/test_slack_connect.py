from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import urllib.error
from email.message import Message

from delivery import DefiniteFailure
from slack_connect import SlackClient, OutcomeUnknown, load_settings, smoke_test


class SlackConnectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)
        self.settings = {"SLACK_BOT_TOKEN": "xoxb-local-test-only", "SLACK_TEAM_ID": "T_TEST", "SLACK_CHANNEL_ID": "C_TEST"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_loader_rejects_empty_token_without_disclosure(self):
        path = self.root / ".env"
        path.write_text("SLACK_BOT_TOKEN=\nSLACK_TEAM_ID=T123\nSLACK_CHANNEL_ID=C123\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_settings(path)

    def test_success_sends_one_message_even_if_repeated(self):
        client = Mock()
        client.authenticate.return_value = {"team_id": "T_TEST"}
        client.send.return_value = {"channel": "C_TEST", "ts": "123.456"}
        path = self.root / "receipt.sqlite"
        self.assertEqual(smoke_test(client, self.settings, path)["status"], "sent")
        self.assertEqual(smoke_test(client, self.settings, path)["status"], "already_sent")
        self.assertEqual(client.send.call_count, 1)

    def test_ambiguous_send_is_not_retried(self):
        client = Mock()
        client.authenticate.return_value = {}
        client.send.side_effect = TimeoutError("secret")
        for _ in range(2):
            with self.assertRaises(OutcomeUnknown):
                smoke_test(client, self.settings, self.root / "receipt.sqlite")
        self.assertEqual(client.send.call_count, 1)

    def test_authentication_checks_expected_workspace(self):
        client = SlackClient("xoxb-local-test-only")
        client.call = Mock(return_value={"ok": True, "team_id": "T_OTHER", "bot_id": "B_TEST"})
        with self.assertRaises(ValueError):
            client.authenticate("T_TEST")

    def test_network_error_never_echoes_credentials(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.URLError("xoxb-local-test-only")
        with self.assertRaises(OutcomeUnknown) as result:
            SlackClient("xoxb-local-test-only", opener).call("auth.test", {})
        self.assertNotIn("xoxb", str(result.exception))

    def test_rate_limit_is_definite_rejection_with_retry_after(self):
        headers = Message()
        headers["Retry-After"] = "17"
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://slack.com/api/chat.postMessage", 429, "rate", headers, None)
        with self.assertRaises(DefiniteFailure) as result:
            SlackClient("xoxb-local-test-only", opener).call("chat.postMessage", {})
        self.assertTrue(result.exception.retryable)
        self.assertEqual(result.exception.retry_after_seconds, 17)


if __name__ == "__main__":
    unittest.main()
