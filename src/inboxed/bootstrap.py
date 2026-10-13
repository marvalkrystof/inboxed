from pathlib import Path
from contextlib import contextmanager
from collections.abc import Callable

from inboxed.config import Settings
from inboxed.domain.entities import Run
from inboxed.domain.errors import InboxedError
from inboxed.ports.classifier import Classifier
from inboxed.ports.mail_provider import MailProvider
from inboxed.ports.tag_generator import TagGenerator
from inboxed.providers.gmail.auth import load_credentials, token_path
from inboxed.providers.gmail.provider import GmailProvider
from inboxed.providers.imap.config import IMAPConfig
from inboxed.providers.imap.provider import IMAPProvider
from inboxed.storage.sqlite import SQLiteRunRepository


# Register adapters here; orchestration services never import vendor SDKs.
def imap_factory(account, data_dir, write):
    return IMAPProvider(IMAPConfig.model_validate(account.options), write=write)


PROVIDERS: dict[str, Callable[..., MailProvider]] = {"gmail": GmailProvider, "imap": imap_factory}
CLASSIFIERS: dict[str, Callable[..., Classifier]] = {}
TAG_GENERATORS: dict[str, Callable[..., TagGenerator]] = {}


def build_classifier(settings: Settings) -> Classifier:
    factory = CLASSIFIERS.get(settings.classifier.backend)
    if factory:
        return factory(settings.classifier)
    if settings.classifier.backend == "kev":
        from inboxed.models.ollama.kev_classifier import KevClassifier

        config = settings.classifier
        return KevClassifier(config.endpoint, config.model, config.timeout)
    if settings.classifier.backend in ("laya", "ollama"):
        raise InboxedError(
            f"Classifier backend '{settings.classifier.backend}' has been removed. "
            "Please use 'kev' for classification in your configuration."
        )
    raise InboxedError(f"Unknown classifier backend: {settings.classifier.backend}")


def build_tag_generator(settings: Settings) -> TagGenerator:
    from inboxed.models.ollama.tag_generator import OllamaTagGenerator

    factory = TAG_GENERATORS.get(settings.tags.backend)
    if factory:
        return factory(settings.tags)
    if settings.tags.backend != "ollama":
        raise InboxedError(f"Unknown tag backend: {settings.tags.backend}")
    return OllamaTagGenerator(settings.tags.endpoint, settings.tags.model, settings.tags.timeout)


def build_provider(settings: Settings, alias: str, data_dir: Path, write=False) -> MailProvider:
    account = settings.accounts.get(alias)
    if account is None:
        raise InboxedError(f"Unknown account alias: {alias}")
    factory = PROVIDERS.get(account.provider)
    if factory is None:
        raise InboxedError(f"Unknown provider: {account.provider}")
    if account.provider != "gmail":
        return factory(account, data_dir, write)
    return factory(credentials=load_credentials(token_path(data_dir, alias), write=write))


def run_settings(run: Run) -> Settings:
    return Settings.model_validate(run.config)


def alias_for_run(run: Run) -> str:
    alias = run.account_alias
    if not alias:
        raise InboxedError("Run is missing its account alias")
    return alias


def repository(data_dir: Path) -> SQLiteRunRepository:
    return SQLiteRunRepository(data_dir / "runs.sqlite3")


@contextmanager
def provider_session(settings: Settings, alias: str, data_dir: Path, write=False):
    provider = build_provider(settings, alias, data_dir, write)
    try:
        yield provider
    finally:
        close = getattr(provider, "close", None)
        if close:
            close()
