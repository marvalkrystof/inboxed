import hashlib
import imaplib
import os
import re
import ssl

from inboxed.config import validate_folder_name
from inboxed.domain.entities import MessageRef
from inboxed.domain.errors import CapabilityError, ProviderError
from inboxed.ports.mail_provider import InboxPage, ProviderCapabilities

from .config import IMAPConfig
from .parsing import (
    decode_ref,
    encode_ref,
    gmail_labels,
    keyword_label,
    label_keyword,
    mailbox_text,
    mailbox_wire,
    parse_email,
)


def quote(value: str) -> str:
    if "\r" in value or "\n" in value or "\x00" in value:
        raise ProviderError("IMAP values cannot contain newlines or null bytes")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class IMAPProvider:
    name = "imap"

    def __init__(self, config: IMAPConfig, write: bool = False, connection=None):
        self.config, self.write = config, write
        self.connection = connection
        self._uids = None
        self._labels = {}
        self._folder_hints = {}
        self.archive_folder = config.archive_folder
        self.trash_folder = config.trash_folder
        try:
            if self.connection is None:
                context = ssl.create_default_context()
                if config.security == "ssl":
                    self.connection = imaplib.IMAP4_SSL(
                        config.host, config.port, ssl_context=context, timeout=config.timeout
                    )
                else:
                    self.connection = imaplib.IMAP4(
                        config.host, config.port, timeout=config.timeout
                    )
                    self._ok(self.connection.starttls(ssl_context=context))
                if config.oauth_token_env:
                    token = os.environ.get(config.oauth_token_env)
                    if not token:
                        env_hint = (
                            config.oauth_token_env
                            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.oauth_token_env)
                            else "oauth token"
                        )
                        raise ProviderError(f"Set environment variable {env_hint}")
                    self._ok(
                        self.connection.authenticate(
                            "XOAUTH2",
                            lambda _: (
                                f"user={config.username}\x01auth=Bearer {token}\x01\x01"
                            ).encode(),
                        )
                    )
                else:
                    password = config.password
                    if not password and config.password_env:
                        password = os.environ.get(config.password_env)
                        # If the environment variable was not found, check if the user mistakenly
                        # typed their actual password directly into password_env
                        if not password and config.password_env != "INBOXED_IMAP_PASSWORD":
                            password = config.password_env
                    if not password:
                        password = os.environ.get("INBOXED_IMAP_PASSWORD")
                    if not password:
                        env_hint = (
                            config.password_env
                            if (
                                config.password_env
                                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config.password_env)
                            )
                            else "INBOXED_IMAP_PASSWORD"
                        )
                        raise ProviderError(f"Set environment variable {env_hint}")
                    try:
                        self._ok(self.connection.login(config.username, password))
                    except (imaplib.IMAP4.error, ProviderError) as exc:
                        raise ProviderError(
                            "IMAP login failed: check your username and password."
                        ) from exc
                # Servers can advertise additional extensions after authentication.
                capability_rows = self._ok(self.connection.capability())
                self.connection.capabilities = tuple(b" ".join(capability_rows).split())
            extensions = {
                c.decode().upper() if isinstance(c, bytes) else c.upper()
                for c in self.connection.capabilities
            }
            self._gmail_labels = "X-GM-EXT-1" in extensions
            self._discover_folders()
            self._select(config.inbox_folder)
            self._validity = self._uidvalidity()
            _, values = self.connection.response("PERMANENTFLAGS")
            permanent = b" ".join(v for v in (values or []) if isinstance(v, bytes))
            self._arbitrary_keywords = b"\\*" in permanent
            # EXAMINE can omit PERMANENTFLAGS. Use the read-only FLAGS advertisement as a hint;
            # the write-mode provider will verify actual permanent keyword support on SELECT.
            if not write and not permanent:
                _, values = self.connection.response("FLAGS")
                permanent = b" ".join(v for v in (values or []) if isinstance(v, bytes))
                self._arbitrary_keywords = b"\\*" in permanent
            self._can_move = "MOVE" in extensions or "IMAP4REV2" in extensions
            self._uidplus = "UIDPLUS" in extensions or "IMAP4REV2" in extensions
            self.capabilities = ProviderCapabilities(
                labels=self._gmail_labels or self._arbitrary_keywords or not write,
                archive=bool(self.archive_folder and (self._can_move or self._uidplus)),
                trash=bool(self.trash_folder and (self._can_move or self._uidplus)),
                folders=True,
                move=self._gmail_labels or self._can_move or self._uidplus,
            )
        except ProviderError:
            self.close()
            raise
        except TimeoutError as exc:
            self.close()
            raise ProviderError(
                f"IMAP connection or sign-in timed out after {config.timeout:g} seconds; "
                "check the server, port, TLS settings and connection"
            ) from exc
        except (imaplib.IMAP4.error, OSError) as exc:
            self.close()
            raise ProviderError(
                "Cannot connect to IMAP; check host, TLS and authentication"
            ) from exc

    def _uid(self, command, *args):
        try:
            return self.connection.uid(command, *args)
        except TimeoutError as exc:
            raise ProviderError(
                f"IMAP {command} timed out after {self.config.timeout:g} seconds; "
                "check the connection and retry"
            ) from exc
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ProviderError(
                "IMAP connection failed; retry the run with a fresh connection"
            ) from exc

    @staticmethod
    def _ok(response):
        status, data = response
        if status != "OK":
            raise ProviderError("IMAP server rejected the operation")
        return data

    def _select(self, folder):
        try:
            self._ok(self.connection.select(quote(mailbox_wire(folder)), readonly=not self.write))
        except TimeoutError as exc:
            raise ProviderError(
                f"IMAP folder selection timed out after {self.config.timeout:g} seconds; "
                "check the connection and folder settings"
            ) from exc
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ProviderError(
                "Cannot select IMAP folder; check connection and folder settings"
            ) from exc

    def _uidvalidity(self):
        _, values = self.connection.response("UIDVALIDITY")
        value = next((v.decode() for v in (values or []) if isinstance(v, bytes)), "")
        if not value.isdigit():
            raise ProviderError("IMAP server did not return UIDVALIDITY")
        return value

    def _discover_folders(self):
        try:
            rows = self._ok(self.connection.list())
        except (imaplib.IMAP4.error, OSError) as exc:
            raise ProviderError(
                "Cannot list IMAP folders; check the connection and refresh"
            ) from exc
        self._folders = set()
        if self._gmail_labels:
            self._labels.clear()
        for row in rows or []:
            if not isinstance(row, bytes):
                continue
            match = re.match(rb'\(([^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(.+)$', row)
            if not match:
                continue
            flags, wire_name = match.groups()
            wire_name = wire_name.strip()
            if wire_name.startswith(b'"') and wire_name.endswith(b'"'):
                wire_name = wire_name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
            name = mailbox_text(wire_name.decode("ascii"))
            if b"\\noselect" not in flags.lower():
                self._folders.add(name)
            if (
                self._gmail_labels
                and b"\\noselect" not in flags.lower()
                and name.casefold() != "inbox"
                and not name.startswith(("[Gmail]", "[GoogleMail]"))
            ):
                self._labels[name] = name
            if (
                b"\\archive" in flags.lower() or (self._gmail_labels and b"\\all" in flags.lower())
            ) and not self.archive_folder:
                self.archive_folder = name
            if b"\\trash" in flags.lower() and not self.trash_folder:
                self.trash_folder = name
        if (
            self.archive_folder == self.config.inbox_folder
            or self.trash_folder == self.config.inbox_folder
        ):
            raise ProviderError("Archive and Trash folders must differ from the inbox")
        if self.archive_folder and self.archive_folder == self.trash_folder:
            raise ProviderError("Archive and Trash folders must differ")

    def account_identity(self):
        return f"{self.config.username.casefold()}|{self.config.host.casefold()}:{self.config.port}"

    def list_inbox(self, limit=100, cursor=None, query=None):
        self._select(self.config.inbox_folder)
        if self._uidvalidity() != self._validity:
            raise ProviderError("Inbox UIDVALIDITY changed; start a new scan")
        if self._uids is None or cursor is None:
            if query and ("\r" in query or "\n" in query or not query.isascii()):
                raise ProviderError(
                    "IMAP query must be an ASCII SEARCH expression without newlines"
                )
            data = self._ok(self._uid("SEARCH", None, "UNDELETED", query or "ALL"))
            self._uids = sorted(
                b" ".join(v for v in data if isinstance(v, bytes)).split(), key=int, reverse=True
            )
        offset = int(cursor or 0)
        selected = self._uids[offset : offset + limit]
        return InboxPage(
            tuple(
                MessageRef(
                    self.name, self.account_identity(), encode_ref(self._validity, uid.decode())
                )
                for uid in selected
            ),
            str(offset + limit) if offset + limit < len(self._uids) else None,
        )

    def _fetch(self, uid):
        data = self._ok(self._uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])"))
        for entry in data or []:
            if isinstance(entry, tuple) and isinstance(entry[1], bytes):
                metadata, raw = entry
                match = re.search(rb"FLAGS \(([^)]*)\)", metadata)
                flags = tuple(match[1].decode("ascii").split()) if match else ()
                for flag in flags:
                    name = keyword_label(flag)
                    if name and not self._gmail_labels:
                        self._labels[name] = flag
                if self._gmail_labels:
                    labels = self._gmail_message_labels(uid)
                    flags += labels
                    for name in labels:
                        if not name.startswith("\\"):
                            self._labels[name] = name
                return raw, flags
        return None

    def _gmail_message_labels(self, uid):
        # Keep this response separate from BODY literals, which may contain
        # arbitrary text that resembles an IMAP label response.
        return gmail_labels(self._ok(self._uid("FETCH", uid, "(X-GM-LABELS)")))

    def _find_destination(self, target, ref_data):
        if not ref_data.get("rfc_id") or not ref_data.get("fingerprint"):
            return None
        self._select(target)
        rows = self._ok(
            self._uid("SEARCH", None, "HEADER", "Message-ID", quote(ref_data["rfc_id"]))
        )
        matches = []
        for uid in b" ".join(v for v in rows if isinstance(v, bytes)).split():
            found = self._fetch(uid.decode())
            if found and hashlib.sha256(found[0]).hexdigest() == ref_data["fingerprint"]:
                matches.append((uid.decode(), found))
        if len(matches) > 1:
            raise ProviderError("Ambiguous copies in destination; inspect mailbox manually")
        return matches[0] if matches else None

    def _locate(self, ref):
        if ref.provider != self.name or ref.account != self.account_identity():
            raise ProviderError("Message belongs to another account")
        data = decode_ref(ref.message_id)
        self._select(self.config.inbox_folder)
        if self._uidvalidity() != data["validity"]:
            raise ProviderError("Inbox UIDVALIDITY changed; rescan rather than using old UIDs")
        found = self._fetch(data["uid"])
        if found and (
            not data.get("fingerprint")
            or hashlib.sha256(found[0]).hexdigest() == data["fingerprint"]
        ):
            return self.config.inbox_folder, data["uid"], found, data
        for folder in dict.fromkeys(
            (
                self._folder_hints.get(ref.message_id),
                self.trash_folder,
                self.archive_folder,
                *sorted(self._folders),
            )
        ):
            if folder == self.config.inbox_folder:
                continue
            if folder and folder in self._folders:
                found = self._find_destination(folder, data)
                if found:
                    uid, contents = found
                    return folder, uid, contents, data
        return None

    def list_folders(self):
        self._discover_folders()
        if self._gmail_labels:
            return sorted(self._labels, key=str.casefold)
        return sorted(
            (
                name
                for name in self._folders
                if name.casefold() != self.config.inbox_folder.casefold()
            ),
            key=str.casefold,
        )

    def create_folder(self, name):
        self._require_write()
        validate_folder_name(name)
        if name.casefold() == self.config.inbox_folder.casefold():
            raise ProviderError("Choose a destination other than Inbox")
        if name in self.list_folders():
            return name
        try:
            self._ok(self.connection.create(quote(mailbox_wire(name))))
        except (imaplib.IMAP4.error, OSError, ProviderError) as exc:
            # A lost response or concurrent creation may still have succeeded.
            if name not in self.list_folders():
                raise ProviderError(
                    "Could not create this folder. Create it in webmail, then refresh folders."
                ) from exc
        if name not in self.list_folders():
            raise ProviderError(
                "The new folder is not selectable. Create it in webmail and refresh."
            )
        return name

    def is_in_folder(self, ref, folder):
        if ref.provider != self.name or ref.account != self.account_identity():
            raise ProviderError("Message belongs to another account")
        found = self._find_destination(folder, decode_ref(ref.message_id)) is not None
        if found:
            self._folder_hints[ref.message_id] = folder
        return found

    def move_to_folder(self, ref, folder):
        self._require_write()
        validate_folder_name(folder)
        if folder not in self.list_folders():
            raise ProviderError("Destination folder no longer exists; choose an existing folder")
        if folder.casefold() == self.config.inbox_folder.casefold():
            raise ProviderError("Choose a destination other than Inbox")
        data = decode_ref(ref.message_id)
        if not data.get("rfc_id") or not data.get("fingerprint"):
            raise ProviderError("This email has no stable Message-ID for a safe folder move")
        self._folder_hints[ref.message_id] = folder
        if self._gmail_labels:
            found = self._locate(ref)
            if not found:
                raise ProviderError("Message disappeared during move")
            _, uid, _, _ = found
            self._ok(self._uid("STORE", uid, "+X-GM-LABELS", f"({quote(mailbox_wire(folder))})"))
            if not self.is_in_folder(ref, folder):
                raise ProviderError(
                    "Gmail did not persist the destination label; email was not archived"
                )
            # Verification selected another folder. Resolve the source again,
            # checking UIDVALIDITY and fingerprint before using its UID.
            source = self._locate(ref)
            if not source:
                raise ProviderError("Message disappeared before removing its Inbox label")
            _, source_uid, _, _ = source
            self._ok(self._uid("STORE", source_uid, "-X-GM-LABELS", r"(\Inbox)"))
            # Gmail labels are independent of IMAP mailbox membership. Verify
            # the destination label from that mailbox directly; _locate() can
            # still find the message in Inbox if Gmail has not yet reflected
            # the Inbox-label removal in its search results.
            destination = self._find_destination(folder, data)
            if not destination:
                raise ProviderError(
                    "Gmail applied the destination label, but Inboxed could not verify it "
                    "after removing the Inbox label; refresh before retrying"
                )
            labels = destination[1][1]
            if any(label.casefold() == r"\inbox".casefold() for label in labels):
                # Gmail exposes label mailboxes with mailbox-local UIDs. Retry
                # the system-label removal using the UID returned by the
                # destination mailbox, where the newly added label is visible.
                destination_uid = destination[0]
                self._ok(self._uid("STORE", destination_uid, "-X-GM-LABELS", r"(\Inbox)"))
                destination = self._find_destination(folder, data)
                if not destination:
                    raise ProviderError(
                        "Gmail applied the destination label, but Inboxed could not verify it "
                        "after retrying Inbox-label removal; refresh before retrying"
                    )
                labels = destination[1][1]
            if any(label.casefold() == r"\inbox".casefold() for label in labels):
                raise ProviderError(
                    "Gmail applied the destination label, but did not remove the Inbox label; "
                    "the label is still present after retrying from the destination. Refresh "
                    "before retrying the action"
                )
            return
        else:
            self._move(ref, folder)
        current = self.get_message(ref)
        if current is None or current.in_inbox or not self.is_in_folder(ref, folder):
            raise ProviderError(
                "Could not verify the email in the destination folder; refresh before retrying"
            )

    def get_message(self, ref):
        located = self._locate(ref)
        if not located:
            return None
        folder, _uid, (raw, flags), data = located
        message = parse_email(
            raw,
            self.account_identity(),
            data["validity"],
            data["uid"],
            flags,
            folder,
            self.config.inbox_folder,
            self.trash_folder,
        )
        return message

    def list_labels(self):
        if self._gmail_labels:
            self._discover_folders()
            return dict(self._labels)
        # SELECT/EXAMINE advertises the mailbox's defined flags, including
        # keywords. Do not fetch every message just to discover label names:
        # that delays the first scan result by an entire mailbox traversal.
        self._select(self.config.inbox_folder)
        _, rows = self.connection.response("FLAGS")
        for row in rows or []:
            if not isinstance(row, bytes):
                continue
            for flag in row.decode("ascii").strip("()").split():
                name = keyword_label(flag)
                if name:
                    self._labels[name] = flag
        return dict(self._labels)

    def _require_write(self):
        if not self.write:
            raise ProviderError("IMAP provider was opened read-only")

    def create_label(self, name):
        self._require_write()
        if self._gmail_labels:
            if not name or name.startswith(("\\", "[Gmail]", "[GoogleMail]")):
                raise ProviderError("Choose a non-system Gmail label name")
            wire_name = quote(mailbox_wire(name))
            if name not in self._labels:
                status, _ = self.connection.create(wire_name)
                if status != "OK":
                    # Another session may have created this label meanwhile.
                    self._discover_folders()
                    if name not in self._labels:
                        raise ProviderError("Gmail could not create the label")
                self._labels[name] = name
            return name
        if not self._arbitrary_keywords:
            raise CapabilityError("IMAP server does not support arbitrary permanent keywords")
        keyword = label_keyword(name)
        self._labels[name] = keyword
        return keyword

    def apply_label(self, ref, label_id):
        self._require_write()
        if self._gmail_labels:
            if label_id not in self._labels or label_id.startswith("\\"):
                raise CapabilityError("Unknown Gmail label; refresh labels and retry")
            found = self._locate(ref)
            if not found:
                raise ProviderError("Message disappeared during write")
            _, uid, _, _ = found
            self._ok(self._uid("STORE", uid, "+X-GM-LABELS", f"({quote(mailbox_wire(label_id))})"))
            if label_id not in self._gmail_message_labels(uid):
                raise ProviderError("Gmail did not persist the label")
            return
        if not self._arbitrary_keywords or keyword_label(label_id) is None:
            raise CapabilityError("Unsupported IMAP keyword label")
        found = self._locate(ref)
        if not found:
            raise ProviderError("Message disappeared during write")
        _, uid, _, _ = found
        self._ok(self._uid("STORE", uid, "+FLAGS.SILENT", f"({label_id})"))
        verify = self._fetch(uid)
        if not verify or label_id not in verify[1]:
            raise ProviderError("IMAP server did not persist the label keyword")

    def _move(self, ref, target):
        self._require_write()
        if not target or not (self._can_move or self._uidplus):
            raise CapabilityError("Configure a destination and use a server with MOVE or UIDPLUS")
        found = self._locate(ref)
        if not found:
            raise ProviderError("Message disappeared during move")
        folder, uid, _, data = found
        if folder == target:
            return
        if self._can_move:
            self._ok(self._uid("MOVE", uid, quote(mailbox_wire(target))))
        else:
            # Reconcile a previous COPY before retrying; never globally EXPUNGE other emails.
            source_validity = self._uidvalidity()
            existing = self._find_destination(target, data)
            self._select(folder)
            if self._uidvalidity() != source_validity:
                raise ProviderError("Source folder UIDVALIDITY changed; rescan before moving")
            if existing is None:
                self._ok(self._uid("COPY", uid, quote(mailbox_wire(target))))
            self._ok(self._uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)"))
            self._ok(self._uid("EXPUNGE", uid))

    def archive(self, ref):
        self._move(ref, self.archive_folder)

    def trash(self, ref):
        self._move(ref, self.trash_folder)

    def close(self):
        if self.connection is not None:
            try:
                self.connection.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
