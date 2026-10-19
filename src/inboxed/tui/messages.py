from typing import Any

from textual.message import Message


class RunChosen(Message):
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id


class EmailHighlighted(Message):
    def __init__(self, message_id: str):
        super().__init__()
        self.message_id = message_id


class EmailOpened(EmailHighlighted):
    pass


class EmailSelectionToggled(Message):
    def __init__(self, message_id: str):
        super().__init__()
        self.message_id = message_id


class JobStarted(Message):
    def __init__(self, job_id: int, kind: str, label: str, run_id: str | None = None):
        super().__init__()
        self.job_id, self.kind, self.label, self.run_id = job_id, kind, label, run_id


class JobProgress(Message):
    def __init__(self, job_id: int, payload: dict):
        super().__init__()
        self.job_id, self.payload = job_id, payload


class JobFinished(Message):
    def __init__(
        self,
        job_id: int,
        kind: str,
        result: Any = None,
        error: str | None = None,
        context: dict | None = None,
    ):
        super().__init__()
        self.job_id, self.kind, self.result, self.error = job_id, kind, result, error
        self.context = context or {}
