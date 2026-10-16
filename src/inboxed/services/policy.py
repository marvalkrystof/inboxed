from inboxed.domain.actions import ActionKind
from inboxed.domain.entities import (
    ActionState,
    ClassDefinition,
    ClassificationResult,
    ProposedAction,
    TagSuggestion,
)

CLASS_PREFIX = "Inboxed/Class/"
TAG_PREFIX = "Inboxed/Tag/"


def automatic_class_folder(label: str) -> str:
    return f"Inboxed - {label}"


class PolicyService:
    def propose(
        self,
        result: ClassificationResult | None,
        classes: list[ClassDefinition],
        tags: list[TagSuggestion],
        folder_routes: dict[str, str] | None = None,
    ) -> list[ActionState]:
        actions = []
        if folder_routes is not None:
            if result:
                definition = next(c for c in classes if c.id == result.class_id)
                if definition.cleanup != "none":
                    actions.append(ActionState(ProposedAction(ActionKind(definition.cleanup))))
                else:
                    folder = folder_routes.get(
                        result.class_id, automatic_class_folder(definition.label)
                    )
                    if folder:
                        actions.append(
                            ActionState(
                                ProposedAction(
                                    ActionKind.MOVE,
                                    folder=folder,
                                    create_folder=True,
                                )
                            )
                        )
            # Topic tags remain in saved history; an email has one destination.
            return actions
        if result:
            definition = next(c for c in classes if c.id == result.class_id)
            actions.append(
                ActionState(ProposedAction(ActionKind.LABEL, CLASS_PREFIX + definition.label))
            )
            if definition.cleanup != "none":
                actions.append(ActionState(ProposedAction(ActionKind(definition.cleanup))))
        for tag in tags:
            actions.append(
                ActionState(ProposedAction(ActionKind.LABEL, TAG_PREFIX + tag.name, generated=True))
            )
        # Label actions precede cleanup actions, even when tags are generated.
        return sorted(actions, key=lambda a: a.proposal.kind != ActionKind.LABEL)
