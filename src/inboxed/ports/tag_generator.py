from typing import Protocol

from inboxed.domain.entities import EmailMessage, TagSuggestion


class TagGenerator(Protocol):
    def suggest(self, message: EmailMessage, existing_tags: list[str]) -> list[TagSuggestion]: ...
