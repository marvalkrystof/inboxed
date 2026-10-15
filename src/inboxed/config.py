import json
import re
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
    provider: str = "gmail"
    # Accepted for older configs; OAuth client configuration is app-wide now.
    credentials_file: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


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
            endpoint = urlparse(self.endpoint)
            if (
                endpoint.scheme not in ("http", "https")
                or endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
                or endpoint.username
                or endpoint.password
            ):
                raise ValueError("Classifier Ollama endpoint must be a local loopback URL")
            if self.model.endswith(":cloud"):
                raise ValueError("Choose a local Ollama model, not a cloud model")
        return self


def save_classifier_config(path: Path, classifier: ClassifierConfig) -> None:
    """Update [classifier] while preserving other user-authored TOML sections."""
    path = path.expanduser().resolve()
    try:
        source = path.read_text()
        parsed = tomllib.loads(source)
        candidate = dict(parsed)
        candidate["classifier"] = classifier.model_dump()
        Settings.model_validate(candidate)

        lines = source.splitlines(keepends=True)
        headers = re.compile(r"^\[{1,2}([^\]]+)\]{1,2}\s*(?:#.*)?$")
        retained = []
        index = 0
        while index < len(lines):
            match = headers.match(lines[index].rstrip("\r\n"))
            if match and (
                match.group(1) == "classifier" or match.group(1).startswith("classifier.")
            ):
                index += 1
                while index < len(lines) and not headers.match(lines[index].rstrip("\r\n")):
                    index += 1
                continue
            retained.append(lines[index])
            index += 1

        def toml_value(value):
            return json.dumps(value, ensure_ascii=False)

        block = ["[classifier]\n"]
        for key, value in classifier.model_dump().items():
            if key == "options":
                if value:
                    block.append("\n[classifier.options]\n")
                    block.extend(f"{name} = {toml_value(item)}\n" for name, item in value.items())
            elif isinstance(value, (str, int, float, bool)):
                block.append(f"{key} = {toml_value(value)}\n")
        updated = "".join(block + ["\n"] + retained)
        tomllib.loads(updated)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(updated)
        temp.chmod(path.stat().st_mode & 0o777)
        temp.replace(path)
    except (OSError, ValueError) as exc:
        if isinstance(exc, InboxedError):
            raise
        raise InboxedError(f"Cannot update classifier configuration {path}: {exc}") from exc


def save_tag_config(path: Path, tags: "TagConfig") -> None:
    """Update [tags] while preserving other user-authored TOML sections."""
    path = path.expanduser().resolve()
    try:
        source = path.read_text()
        parsed = tomllib.loads(source)
        candidate = dict(parsed)
        candidate["tags"] = tags.model_dump()
        Settings.model_validate(candidate)

        lines = source.splitlines(keepends=True)
        headers = re.compile(r"^\[{1,2}([^\]]+)\]{1,2}\s*(?:#.*)?$")
        retained = []
        index = 0
        while index < len(lines):
            match = headers.match(lines[index].rstrip("\r\n"))
            if match and (
                match.group(1) == "tags" or match.group(1).startswith("tags.")
            ):
                index += 1
                while index < len(lines) and not headers.match(lines[index].rstrip("\r\n")):
                    index += 1
                continue
            retained.append(lines[index])
            index += 1

        def toml_value(value):
            return json.dumps(value, ensure_ascii=False)

        block = ["[tags]\n"]
        for key, value in tags.model_dump().items():
            if key == "options":
                if value:
                    block.append("\n[tags.options]\n")
                    block.extend(f"{name} = {toml_value(item)}\n" for name, item in value.items())
            elif isinstance(value, (str, int, float, bool)):
                block.append(f"{key} = {toml_value(value)}\n")
        updated = "".join(retained + ["\n"] + block)
        tomllib.loads(updated)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(updated)
        temp.chmod(path.stat().st_mode & 0o777)
        temp.replace(path)
    except (OSError, ValueError) as exc:
        if isinstance(exc, InboxedError):
            raise
        raise InboxedError(f"Cannot update tags configuration {path}: {exc}") from exc


class TagConfig(StrictModel):
    backend: str = "ollama"
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    timeout: float = Field(default=120, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def local_endpoint(self) -> "TagConfig":
        if self.backend == "ollama":
            endpoint = urlparse(self.endpoint)
            if (
                endpoint.scheme not in ("http", "https")
                or endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
                or endpoint.username
                or endpoint.password
            ):
                raise ValueError("Ollama endpoint must be a local loopback URL")
            if self.model.endswith(":cloud"):
                raise ValueError("Choose a local Ollama model, not a cloud model")
        return self


class Settings(StrictModel):
    accounts: dict[str, AccountConfig]
    classes: list[ClassConfig] = Field(min_length=1)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    tags: TagConfig = Field(default_factory=TagConfig)

    @model_validator(mode="after")
    def unique_names(self) -> "Settings":
        if len({c.id for c in self.classes}) != len(self.classes):
            raise ValueError("Class IDs must be unique")
        if len({c.label.casefold() for c in self.classes}) != len(self.classes):
            raise ValueError("Class labels must be unique")
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
        if account is not None and account.get("provider") not in ("gmail", "imap"):
            raise InboxedError("Provider must be gmail or imap")
        if account is not None:
            candidate = dict(parsed)
            candidate_accounts = dict(accounts)
            candidate_accounts[alias] = account
            candidate["accounts"] = candidate_accounts
            Settings.model_validate(candidate)
            if account["provider"] == "imap":
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
            retained.append(f"provider = {json.dumps(account['provider'])}\n")
            if account["provider"] != "gmail":
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
        settings = Settings.model_validate(tomllib.loads(path.read_text()))
        return settings
    except FileNotFoundError as exc:
        if path == Path("config.toml"):
            hint = "Run `inboxed init` to create a starter config, then edit it for your account."
        else:
            hint = f"Create the file or run `inboxed --config {path} init`."
        raise InboxedError(f"Cannot load configuration {path}: file not found. {hint}") from exc
    except (OSError, ValueError) as exc:
        raise InboxedError(f"Cannot load configuration {path}: {exc}") from exc
