import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from inboxed.domain.errors import ProviderError
from inboxed.providers.gmail.provider import GmailProvider


class FakeHttpError(Exception):
    def __init__(self, status, message="API failure"):
        self.resp = SimpleNamespace(status=status)
        super().__init__(message)


class GmailTests(unittest.TestCase):
    def setUp(self):
        package = ModuleType("googleapiclient")
        errors = ModuleType("googleapiclient.errors")
        errors.HttpError = FakeHttpError
        self.patcher = patch.dict(
            sys.modules, {"googleapiclient": package, "googleapiclient.errors": errors}
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.service = Mock()
        self.service.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "Test@Example.com"
        }
        self.provider = GmailProvider(service=self.service)

    def test_pagination_and_inbox_query(self):
        request = self.service.users.return_value.messages.return_value.list.return_value
        request.execute.return_value = {"messages": [{"id": "1"}], "nextPageToken": "next"}
        page = self.provider.list_inbox(10, "cursor", "older_than:30d")
        self.assertEqual(page.next_cursor, "next")
        self.assertEqual(page.messages[0].account, "test@example.com")
        self.service.users.return_value.messages.return_value.list.assert_called_once_with(
            userId="me",
            labelIds=["INBOX"],
            maxResults=10,
            includeSpamTrash=False,
            pageToken="cursor",
            q="older_than:30d",
        )

    def test_retry_bound_and_nonretryable_error(self):
        request = Mock()
        request.execute.side_effect = [FakeHttpError(429), FakeHttpError(503), {"ok": True}]
        with patch("inboxed.providers.gmail.provider.time.sleep"):
            self.assertEqual(self.provider._execute(request), {"ok": True})
        self.assertEqual(request.execute.call_count, 3)
        request.execute.reset_mock()
        request.execute.side_effect = FakeHttpError(503)
        with patch("inboxed.providers.gmail.provider.time.sleep"), self.assertRaises(ProviderError):
            self.provider._execute(request)
        self.assertEqual(request.execute.call_count, 4)
        request.execute.reset_mock()
        request.execute.side_effect = FakeHttpError(403)
        with self.assertRaises(ProviderError):
            self.provider._execute(request)
        self.assertEqual(request.execute.call_count, 1)

    def test_not_found_get_vs_write(self):
        from inboxed.domain.entities import MessageRef

        ref = MessageRef("gmail", self.provider.account_identity(), "gone")
        self.service.users.return_value.messages.return_value.get.return_value.execute.side_effect = FakeHttpError(
            404
        )
        self.assertIsNone(self.provider.get_message(ref))
        self.service.users.return_value.messages.return_value.trash.return_value.execute.side_effect = FakeHttpError(
            404
        )
        with self.assertRaises(ProviderError):
            self.provider.trash(ref)

    def test_label_creation_lost_response_is_reconciled(self):
        self.service.users.return_value.labels.return_value.create.return_value.execute.side_effect = FakeHttpError(
            503
        )
        self.service.users.return_value.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"name": "Inboxed/Class/Work", "id": "label-1"}]
        }
        self.assertEqual(self.provider.create_label("Inboxed/Class/Work"), "label-1")
