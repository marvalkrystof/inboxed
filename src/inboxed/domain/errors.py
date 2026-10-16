class InboxedError(Exception):
    """An actionable application error safe to display in the CLI."""


class ProviderError(InboxedError):
    pass


class AuthenticationError(ProviderError):
    """The provider needs renewed authorization rather than an operation retry."""


class ModelError(InboxedError):
    pass


class CapabilityError(InboxedError):
    pass
