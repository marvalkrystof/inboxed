class InboxedError(Exception):
    """An actionable application error safe to display in the CLI."""


class ProviderError(InboxedError):
    pass


class ModelError(InboxedError):
    pass


class CapabilityError(InboxedError):
    pass
