from typing import Literal

from pydantic import Field, model_validator

from inboxed.config import StrictModel


class IMAPConfig(StrictModel):
    host: str = Field(min_length=1)
    username: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    security: Literal["ssl", "starttls"] = "ssl"
    password: str | None = None
    password_env: str | None = "INBOXED_IMAP_PASSWORD"
    oauth_token_env: str | None = None
    inbox_folder: str = "INBOX"
    archive_folder: str | None = None
    trash_folder: str | None = None
    timeout: float = Field(default=30, gt=0)

    @model_validator(mode="after")
    def default_port(self):
        if self.port is None:
            self.port = 993 if self.security == "ssl" else 143
        return self
