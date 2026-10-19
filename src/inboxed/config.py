import json
import os
import re
import tempfile
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from inboxed.domain.entities import ClassDefinition
from inboxed.domain.errors import InboxedError


def write_default_config(path: Path, *, force: bool = False) -> Path:
    """Create a starter TOML config without replacing user settings by accident."""
    path = path.expanduser()
    if path.exists() and not force:
        raise InboxedError(f"Configuration already exists: {path} (use --force to replace it)")
    resource = files("inboxed").joinpath("config.example.toml")
    # Wheels include the root template as package data; editable installs use its source file.
    template = (
        resource.read_text()
        if resource.is_file()
        else (Path(__file__).resolve().parents[2] / "config.example.toml").read_text()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template)
    return path.resolve()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountConfig(StrictModel):
    provider: Literal["imap"] = "imap"
    organization: Literal["folders"] = "folders"
    folder_routes: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def use_folder_organization(cls, data):
        if isinstance(data, dict) and data.get("organization") == "labels":
            data = {**data, "organization": "folders"}
        return data

    @model_validator(mode="after")
    def valid_folders(self):
        for folder in self.folder_routes.values():
            if folder:
                validate_folder_name(folder)
            if folder.casefold() == "inbox":
                raise ValueError("Choose a destination other than Inbox")
        return self


def validate_folder_name(name: str) -> str:
    if not name.strip() or name != name.strip() or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError(
            "Folder names must not be blank or contain control characters or surrounding spaces"
        )
    return name


class ClassConfig(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=80)
    cleanup: Literal["none", "archive", "trash"] = "none"
    allow_automation: bool = False
    confidence_threshold: float = Field(default=0.9, ge=0, le=1)
    require_approval: bool = True

    def definition(self) -> ClassDefinition:
        return ClassDefinition(**self.model_dump())


def validate_local_ollama_endpoint(value: str) -> str:
    endpoint = urlparse(value)
    if (
        endpoint.scheme not in ("http", "https")
        or endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("Ollama endpoint must be a local loopback URL")
    return value


def validate_local_ollama_model(value: str) -> str:
    if not value.strip():
        raise ValueError("Choose an Ollama model or enter its tag")
    if value.casefold().endswith((":cloud", "-cloud")):
        raise ValueError("Choose a local Ollama model, not a cloud model")
    return value


class ClassifierConfig(StrictModel):
    backend: Literal["kev", "laya", "ollama"] = "kev"
    device: Literal["auto", "cpu", "mps", "cuda"] = "auto"
    max_loaded: int = Field(default=2, ge=1, le=3)
    endpoint: str = "http://localhost:11434"
    model: str = "qwen3.5:9b"
    timeout: float = Field(default=180, gt=0)
    context_length: int = Field(default=32768, ge=2048, le=262144)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def local_ollama(self) -> "ClassifierConfig":
        if self.backend in ("ollama", "kev"):
            validate_local_ollama_endpoint(self.endpoint)
            validate_local_ollama_model(self.model)
        return self


def _toml_value(value):
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                f"{json.dumps(str(key))} = {_toml_value(item)}" for key, item in value.items()
            )
            + " }"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, (str, int, float, bool)):
        return json.dumps(value, ensure_ascii=False)
    raise ValueError(f"Unsupported model option type: {type(value).__name__}")


def _save_model_sections(path: Path, updates: dict[str, dict]) -> None:
    """Validate all changed model sections, then replace the config once."""
    path = path.expanduser().resolve()
    temporary = None
    try:
        source = path.read_text()
        parsed = tomllib.loads(source)
        Settings.model_validate({**parsed, **updates})
        headers = re.compile(r"^\[{1,2}([^\]]+)\]{1,2}\s*(?:#.*)?$")
        retained = []
        skip = False
        for line in source.splitlines(keepends=True):
            match = headers.match(line.rstrip("\r\n"))
            if match:
                section = match.group(1)
                skip = (
                    section == "tags"
                    or section.startswith("tags.")
                    or any(section == name or section.startswith(name + ".") for name in updates)
                )
            if not skip:
                retained.append(line)
        blocks = []
        for section, config in updates.items():
            blocks.append(f"[{section}]\n")
            for key, value in config.items():
                if key != "options":
                    blocks.append(f"{key} = {_toml_value(value)}\n")
            if config.get("options"):
                blocks.append(f"\n[{section}.options]\n")
                blocks.extend(
                    f"{json.dumps(str(key))} = {_toml_value(value)}\n"
                    for key, value in config["options"].items()
                )
            blocks.append("\n")
        updated = "".join(retained).rstrip() + "\n\n" + "".join(blocks)
        Settings.model_validate(tomllib.loads(updated))
        fd, filename = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(filename)
        with os.fdopen(fd, "w") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(path.stat().st_mode & 0o777)
        temporary.replace(path)
    except (OSError, ValueError) as exc:
        raise InboxedError(f"Cannot update model settings: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_classifier_config(path: Path, classifier: ClassifierConfig) -> None:
    _save_model_sections(path, {"classifier": classifier.model_dump()})


class Settings(StrictModel):
    accounts: dict[str, AccountConfig]
    classes: list[ClassConfig] = Field(min_length=1)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)

    @model_validator(mode="before")
    @classmethod
    def discard_retired_settings(cls, data):
        if isinstance(data, dict):
            data = {key: value for key, value in data.items() if key != "tags"}
        return data

    @model_validator(mode="after")
    def unique_names(self) -> "Settings":
        if len({c.id for c in self.classes}) != len(self.classes):
            raise ValueError("Class IDs must be unique")
        if len({c.label.casefold() for c in self.classes}) != len(self.classes):
            raise ValueError("Class labels must be unique")
        class_ids = {c.id for c in self.classes}
        for alias, account in self.accounts.items():
            if set(account.folder_routes) - class_ids:
                raise ValueError(f"Unknown class in folder routes for account {alias}")
        return self

    def definitions(self) -> list[ClassDefinition]:
        return [c.definition() for c in self.classes]


def save_account_config(
    path: Path,
    alias: str,
    account: dict[str, Any] | None,
    *,
    replace_existing: bool = True,
) -> None:
    """Add/update/remove one account table while preserving the rest of the TOML file."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", alias):
        raise InboxedError("Account alias must use letters, numbers, underscores or hyphens")
    path = path.expanduser().resolve()
    try:
        source = path.read_text()
        parsed = tomllib.loads(source)
        accounts = parsed.get("accounts", {})
        if account is None and alias not in accounts:
            raise InboxedError(f"Unknown account: {alias}")
        if account is not None and alias in accounts and not replace_existing:
            raise InboxedError(f"Account already exists: {alias}")
        if account is not None and account.get("provider", "imap") != "imap":
            raise InboxedError("Provider must be imap")
        if account is not None:
            candidate = dict(parsed)
            candidate_accounts = dict(accounts)
            candidate_accounts[alias] = account
            candidate["accounts"] = candidate_accounts
            Settings.model_validate(candidate)
            from inboxed.providers.imap.config import IMAPConfig

            IMAPConfig.model_validate(account.get("options", {}))

        lines = source.splitlines(keepends=True)
        headers = re.compile(r"^\[{1,2}([^\]]+)\]{1,2}\s*(?:#.*)?$")
        retained = []
        index = 0
        while index < len(lines):
            match = headers.match(lines[index].rstrip("\r\n"))
            section = match.group(1) if match else ""
            if match and (
                section == f"accounts.{alias}" or section.startswith(f"accounts.{alias}.")
            ):
                index += 1
                while index < len(lines) and not headers.match(lines[index].rstrip("\r\n")):
                    index += 1
                continue
            retained.append(lines[index])
            index += 1
        if account is not None:
            retained.append("\n" if retained and not retained[-1].endswith("\n\n") else "")
            retained.append(f"[accounts.{alias}]\n")
            retained.append(f"provider = {json.dumps(account.get('provider', 'imap'))}\n")
            retained.append(f"organization = {json.dumps('folders')}\n")
            retained.append(f"folder_routes = {_toml_value(account.get('folder_routes', {}))}\n")
            options = account.get("options", {})
            retained.append(f"\n[accounts.{alias}.options]\n")
            for key, value in options.items():
                if value is None:
                    continue
                if isinstance(value, bool):
                    serialized = str(value).lower()
                elif isinstance(value, (int, float)):
                    serialized = str(value)
                elif isinstance(value, str):
                    serialized = json.dumps(value, ensure_ascii=False)
                else:
                    raise InboxedError(f"Unsupported account option: {key}")
                retained.append(f"{key} = {serialized}\n")
        updated = "".join(retained)
        parsed_updated = tomllib.loads(updated)
        if account is not None and alias not in parsed_updated.get("accounts", {}):
            raise InboxedError("Could not write account configuration")
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(updated)
        temp.chmod(path.stat().st_mode & 0o777)
        temp.replace(path)
    except InboxedError:
        raise
    except (OSError, ValueError) as exc:
        raise InboxedError(f"Cannot update account configuration {path}: {exc}") from exc


def load_settings(path: Path) -> Settings:
    try:
        raw = tomllib.loads(path.read_text())
        for alias, account in raw.get("accounts", {}).items():
            if isinstance(account, dict) and account.get("provider", "imap") != "imap":
                raise InboxedError(
                    f"Account {alias}: only IMAP is supported. Set provider = 'imap', remove "
                    "obsolete account fields, and configure host, username and credentials "
                    "in its options table. See config.example.toml."
                )
        settings = Settings.model_validate(raw)
        return settings
    except FileNotFoundError as exc:
        if path == Path("config.toml"):
            hint = "Run `inboxed init` to create a starter config, then edit it for your account."
        else:
            hint = f"Create the file or run `inboxed --config {path} init`."
        raise InboxedError(f"Cannot load configuration {path}: file not found. {hint}") from exc
    except (OSError, ValueError) as exc:
        raise InboxedError(f"Cannot load configuration {path}: {exc}") from exc
