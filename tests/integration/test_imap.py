import imaplib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inboxed.config import AccountConfig
from inboxed.domain.actions import Approval, Execution
from inboxed.domain.errors import CapabilityError, ProviderError
from inboxed.providers.imap.config import IMAPConfig
from inboxed.providers.imap.parsing import (
    decode_ref,
    keyword_label,
    label_keyword,
    mailbox_text,
    mailbox_wire,
)
from inboxed.providers.imap.provider import IMAPProvider
from inboxed.services.execution import ExecutionService
from inboxed.services.review import ReviewService
from inboxed.services.scanning import ScanService
from inboxed.storage.sqlite import SQLiteRunRepository
from tests.helpers import FakeClassifier, settings

RAW = b"From: Shop <shop@example.com>\r\nSubject: Sale\r\nMessage-ID: <one@example.com>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSale on bicycles!"


class FakeIMAP:
    def __init__(self, keywords=True, move=True, uidplus=True):
        self.capabilities = (
            (b"IMAP4rev1",) + ((b"MOVE",) if move else ()) + ((b"UIDPLUS",) if uidplus else ())
        )
        self.keywords = keywords
        self.folder = "INBOX"
        self.readonly = False
        self.validity = "123"
        self.folders = {"INBOX": {"1": [RAW, set()]}, "Archive": {}, "Trash": {}}
        self.calls = []
        self.lost_move_response = False
        self.lost_copy_response = False

    def list(self):
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\Archive) "/" "Archive"',
            b'(\\Trash) "/" "Trash"',
        ]

    def select(self, folder, readonly=False):
        self.folder = folder.strip('"')
        self.readonly = readonly
        return "OK", [str(len(self.folders[self.folder])).encode()]

    def response(self, key):
        if key == "UIDVALIDITY":
            return key, [self.validity.encode()]
        if key == "PERMANENTFLAGS":
            return key, [b"(\\Seen \\Deleted \\*)" if self.keywords else b"(\\Seen \\Deleted)"]
        return key, [b"(\\Seen \\Deleted)"]

    def uid(self, command, *args):
        self.calls.append((command, self.folder, *args))
        messages = self.folders[self.folder]
        if command == "SEARCH":
            uids = list(messages)
            if "HEADER" in args:
                value = args[-1].strip('"').encode()
                uids = [u for u in uids if value in messages[u][0]]
            if "UNDELETED" in args:
                uids = [u for u in uids if "\\Deleted" not in messages[u][1]]
            return "OK", [" ".join(uids).encode()]
        if command == "FETCH":
            values = []
            for uid in args[0].split(","):
                if uid not in messages:
                    continue
                raw, flags = messages[uid]
                metadata = f"1 (UID {uid} FLAGS ({' '.join(sorted(flags))}))".encode()
                values.append((metadata, raw) if "BODY.PEEK[]" in args[1] else metadata)
            return "OK", values or [None]
        assert not self.readonly, "Write on a read-only connection"
        if command == "STORE":
            messages[args[0]][1].update(args[2].strip("()").split())
            return "OK", [b"updated"]
        if command in ("MOVE", "COPY"):
            destination = self.folders[args[1].strip('"')]
            raw, flags = messages[args[0]]
            uid = str(100 + len(destination))
            destination[uid] = [raw, set(flags)]
            if command == "MOVE":
                del messages[args[0]]
            if self.lost_move_response and command == "MOVE":
                self.lost_move_response = False
                raise ConnectionError("lost MOVE response")
            if self.lost_copy_response and command == "COPY":
                self.lost_copy_response = False
                raise ConnectionError("lost COPY response")
            return "OK", [b"moved"]
        if command == "EXPUNGE":
            # Only remove the specified UID, never other deleted messages.
            del messages[args[0]]
            return "OK", [b"expunged"]
        raise AssertionError(command)

    def capability(self):
        return "OK", [b" ".join(self.capabilities)]

    def login(self, username, password):
        self.login_args = (username, password)
        return "OK", [b"authenticated"]

    def authenticate(self, mechanism, callback):
        self.oauth_args = (mechanism, callback(b""))
        return "OK", [b"authenticated"]

    def logout(self):
        self.calls.append(("LOGOUT",))
        return "BYE", []


class IMAPTests(unittest.TestCase):
    def provider(self, connection=None, write=True):
        return IMAPProvider(
            IMAPConfig(host="imap.example.com", username="user@example.com"),
            write=write,
            connection=connection or FakeIMAP(),
        )

    def test_ssl_auth_reads_environment_and_closes_on_missing_secret(self):
        connection = FakeIMAP()
        config = IMAPConfig(host="imap.example.com", username="user@example.com")
        with (
            patch.dict("os.environ", {"INBOXED_IMAP_PASSWORD": "test-password"}, clear=True),
            patch(
                "inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=connection
            ) as connect,
        ):
            provider = IMAPProvider(config)
            self.assertEqual(connection.login_args, ("user@example.com", "test-password"))
            self.assertEqual(connect.call_args.kwargs["timeout"], 30)
            self.assertIsNotNone(connect.call_args.kwargs["ssl_context"])
            provider.close()
        connection = FakeIMAP()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=connection),
        ):
            with self.assertRaises(ProviderError):
                IMAPProvider(config)
            self.assertIn(("LOGOUT",), connection.calls)

    def test_oauth_environment_token(self):
        connection = FakeIMAP()
        config = IMAPConfig(
            host="imap.example.com", username="user@example.com", oauth_token_env="TEST_IMAP_TOKEN"
        )
        with (
            patch.dict("os.environ", {"TEST_IMAP_TOKEN": "test-token"}, clear=True),
            patch("inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=connection),
        ):
            provider = IMAPProvider(config)
            self.assertEqual(connection.oauth_args[0], "XOAUTH2")
            self.assertIn(b"Bearer test-token", connection.oauth_args[1])
            provider.close()

    def test_direct_password_auth(self):
        connection = FakeIMAP()
        config = IMAPConfig(
            host="imap.example.com",
            username="user@example.com",
            password="my-direct-secret",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=connection),
        ):
            provider = IMAPProvider(config)
            self.assertEqual(connection.login_args, ("user@example.com", "my-direct-secret"))
            provider.close()

    def test_password_env_fallback_and_error_sanitization(self):
        # Fallback when env var missing and user typed password into password_env
        connection = FakeIMAP()
        config = IMAPConfig(
            host="imap.example.com",
            username="user@example.com",
            password_env="fallback_password_value",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=connection),
        ):
            provider = IMAPProvider(config)
            self.assertEqual(connection.login_args, ("user@example.com", "fallback_password_value"))
            provider.close()

        # Login failure doesn't leak password in error message
        failing_conn = FakeIMAP()
        def fail_login(user, pwd):
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        failing_conn.login = fail_login
        bad_config = IMAPConfig(
            host="imap.example.com",
            username="user@example.com",
            password="super_secret_pwd",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=failing_conn),
        ):
            with self.assertRaises(ProviderError) as ctx:
                IMAPProvider(bad_config)
            self.assertIn("IMAP login failed", str(ctx.exception))
            self.assertNotIn("super_secret_pwd", str(ctx.exception))

        # Missing password error sanitizes env name containing special chars
        empty_conn = FakeIMAP()
        def fail_empty(user, pwd):
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        empty_conn.login = fail_empty
        sym_config = IMAPConfig(
            host="imap.example.com",
            username="user@example.com",
            password=None,
            password_env="p@$$w0rd!",
        )
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("inboxed.providers.imap.provider.imaplib.IMAP4_SSL", return_value=empty_conn),
        ):
            with self.assertRaises(ProviderError) as ctx:
                IMAPProvider(sym_config)
            self.assertNotIn("p@$$w0rd!", str(ctx.exception))

    def test_scan_is_read_only_and_canonical_ref_has_stable_identity(self):
        connection = FakeIMAP()
        provider = self.provider(connection, write=False)
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteRunRepository(Path(directory) / "runs.sqlite3")
            config = settings()
            config.accounts = {
                "personal": AccountConfig(
                    provider="imap",
                    options={"host": "imap.example.com", "username": "user@example.com"},
                )
            }
            run = ScanService(provider, FakeClassifier(), repo).scan(config)
            data = decode_ref(run.messages[0].ref.message_id)
            self.assertEqual(data["rfc_id"], "<one@example.com>")
            self.assertTrue(data["fingerprint"])
            self.assertFalse(
                any(c[0] in ("STORE", "MOVE", "COPY", "EXPUNGE") for c in connection.calls)
            )
            self.assertTrue(
                all("BODY.PEEK[]" in c[-1] for c in connection.calls if c[0] == "FETCH")
            )

    def test_keywords_and_move(self):
        provider = self.provider()
        ref = provider.get_message(provider.list_inbox(1).messages[0]).ref
        name = "Inboxed/Tag/Cycling tours"
        identifier = provider.create_label(name)
        provider.apply_label(ref, identifier)
        self.assertIn(identifier, provider.get_message(ref).labels)
        self.assertEqual(provider.list_labels()[name], identifier)
        provider.archive(ref)
        archived = provider.get_message(ref)
        self.assertFalse(archived.in_inbox)
        self.assertIn(identifier, archived.labels)
        provider.trash(ref)
        self.assertIn("TRASH", provider.get_message(ref).labels)

    def test_uidvalidity_change_refuses_old_uid(self):
        provider = self.provider()
        ref = provider.get_message(provider.list_inbox(1).messages[0]).ref
        provider.connection.validity = "456"
        with self.assertRaises(ProviderError):
            provider.trash(ref)
        self.assertFalse(any(c[0] == "MOVE" for c in provider.connection.calls))

    def test_uidplus_fallback_never_globally_expunge(self):
        connection = FakeIMAP(move=False)
        connection.folders["INBOX"]["2"] = [b"Other mail", {"\\Deleted"}]
        provider = self.provider(connection)
        ref = provider.get_message(provider.list_inbox(1).messages[0]).ref
        provider.trash(ref)
        self.assertIn("2", connection.folders["INBOX"])
        self.assertIn("TRASH", provider.get_message(ref).labels)
        self.assertIn(("EXPUNGE", "INBOX", "1"), connection.calls)

    def test_lost_copy_response_does_not_duplicate_copy(self):
        connection = FakeIMAP(move=False)
        provider = self.provider(connection)
        ref = provider.get_message(provider.list_inbox(1).messages[0]).ref
        connection.lost_copy_response = True
        with self.assertRaises(ProviderError):
            provider.trash(ref)
        provider.trash(ref)
        self.assertEqual(len(connection.folders["Trash"]), 1)
        self.assertEqual(len([c for c in connection.calls if c[0] == "COPY"]), 1)

    def test_lost_move_response_reconciled_by_execution(self):
        connection = FakeIMAP()
        provider = self.provider(connection)
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteRunRepository(Path(directory) / "runs.sqlite3")
            run = ScanService(provider, FakeClassifier(), repo).scan(settings())
            for action in run.messages[0].actions:
                ReviewService(repo).decide(run, 0, action.proposal.id, Approval.APPROVED)
            connection.lost_move_response = True
            executor = ExecutionService(provider, repo)
            self.assertEqual(executor.apply(run)["failed"], 1)
            # Use a fresh provider instance to simulate CLI restart.
            executor = ExecutionService(self.provider(connection), repo)
            executor.apply(repo.load(run.id))
            self.assertEqual(
                repo.load(run.id).messages[0].actions[-1].execution, Execution.SUCCEEDED
            )
            self.assertEqual(len([c for c in connection.calls if c[0] == "MOVE"]), 1)

    def test_capabilities_and_readonly_guards(self):
        provider = self.provider(FakeIMAP(keywords=False, move=False, uidplus=False))
        self.assertFalse(provider.capabilities.labels)
        self.assertFalse(provider.capabilities.trash)
        with self.assertRaises(CapabilityError):
            provider.create_label("Inboxed/Class/Work")
        provider = self.provider(write=False)
        with self.assertRaises(ProviderError):
            provider.create_label("Inboxed/Class/Work")

    def test_newest_first_pagination_and_query(self):
        connection = FakeIMAP()
        connection.folders["INBOX"]["2"] = [RAW.replace(b"one@", b"two@"), set()]
        provider = self.provider(connection)
        page = provider.list_inbox(1, query="UNSEEN")
        self.assertEqual(decode_ref(page.messages[0].message_id)["uid"], "2")
        self.assertEqual(
            decode_ref(provider.list_inbox(1, page.next_cursor).messages[0].message_id)["uid"], "1"
        )
        with self.assertRaises(ProviderError):
            provider.list_inbox(1, query="ALL\r\nLOGOUT")

    def test_unicode_mailboxes_and_labels_round_trip(self):
        for value in ["Archiv & koš", "旅行", "Archive"]:
            self.assertEqual(mailbox_text(mailbox_wire(value)), value)
            self.assertEqual(keyword_label(label_keyword(value)), value)

    def test_ambiguous_copies_refuse_recovery(self):
        connection = FakeIMAP()
        provider = self.provider(connection)
        ref = provider.get_message(provider.list_inbox(1).messages[0]).ref
        del connection.folders["INBOX"]["1"]
        connection.folders["Trash"] = {"100": [RAW, set()], "101": [RAW, set()]}
        with self.assertRaises(ProviderError):
            provider.get_message(ref)
