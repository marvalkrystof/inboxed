from inboxed.domain.actions import ActionKind
from inboxed.domain.entities import (
    ActionState,
    ClassDefinition,
    ClassificationResult,
    ProposedAction,
)


def automatic_class_folder(label: str) -> str:
    return f"Inboxed - {label}"


class PolicyService:
    def propose(
        self,
        result: ClassificationResult | None,
        classes: list[ClassDefinition],
        folder_routes: dict[str, str] | None = None,
    ) -> list[ActionState]:
        if result is None:
            return []
        definition = next(c for c in classes if c.id == result.class_id)
        if definition.cleanup != "none":
            return [ActionState(ProposedAction(ActionKind(definition.cleanup)))]
        folder = (folder_routes or {}).get(
            result.class_id, automatic_class_folder(definition.label)
        )
        if not folder:
            return []
        return [ActionState(ProposedAction(ActionKind.MOVE, folder=folder, create_folder=True))]
