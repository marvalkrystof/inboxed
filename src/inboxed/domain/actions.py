from enum import StrEnum


class ActionKind(StrEnum):
    LABEL = "label"
    MOVE = "move"
    ARCHIVE = "archive"
    TRASH = "trash"


class Approval(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Execution(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
