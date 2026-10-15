"""Versioned password-derived encryption for saved run payloads."""

import base64
import json
import os

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from inboxed.domain.errors import InboxedError

CHECK = b"inboxed-run-storage-v1"
MIN_PASSWORD_LENGTH = 12


def derive_cipher(password: str, salt: bytes) -> Fernet:
    # 64 MiB, three passes; parameters are fixed by the format version so an
    # edited database cannot force an unbounded allocation during unlock.
    try:
        kdf = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536)
        return Fernet(base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8"))))
    except UnsupportedAlgorithm as exc:
        raise InboxedError(
            "This cryptography installation does not support Argon2id. "
            "Install a cryptography wheel with a supported OpenSSL version"
        ) from exc


def create_encryption(password: str) -> tuple[Fernet, str]:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise InboxedError("Use a run history password of at least 12 characters")
    salt = os.urandom(16)
    cipher = derive_cipher(password, salt)
    metadata = json.dumps(
        {
            "format": "argon2id-fernet-v1",
            "salt": base64.b64encode(salt).decode("ascii"),
            "check": cipher.encrypt(CHECK).decode("ascii"),
        }
    )
    return cipher, metadata


def unlock_encryption(password: str, metadata: str) -> Fernet:
    try:
        data = json.loads(metadata)
        if data["format"] != "argon2id-fernet-v1":
            raise ValueError("Unknown encryption format")
        salt = base64.b64decode(data["salt"], validate=True)
        if len(salt) != 16:
            raise ValueError("Invalid salt")
        cipher = derive_cipher(password, salt)
        if cipher.decrypt(data["check"].encode("ascii")) != CHECK:
            raise InvalidToken
    except InvalidToken as exc:
        raise InboxedError("Incorrect password or damaged run encryption settings") from exc
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError) as exc:
        raise InboxedError("Run encryption settings are damaged or unsupported") from exc
    return cipher
