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


class PolicyService:
    def propose(
        self,
        result: ClassificationResult | None,
        classes: list[ClassDefinition],
        tags: list[TagSuggestion],
    ) -> list[ActionState]:
        actions = []
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
