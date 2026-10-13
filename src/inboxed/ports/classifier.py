from typing import Any, Protocol

from inboxed.domain.entities import ClassDefinition, ClassificationResult, EmailMessage


class Classifier(Protocol):
    def classify(
        self,
        message: EmailMessage,
        classes: list[ClassDefinition],
        exemplars: dict[str, list[dict[str, Any]]] | None = None,
    ) -> ClassificationResult: ...
