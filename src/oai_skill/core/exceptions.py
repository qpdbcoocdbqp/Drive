"""Application-level exceptions mapped by the HTTP layer."""


class OaiSkillError(RuntimeError):
    """Base exception for expected service failures."""


class ServiceNotReadyError(OaiSkillError):
    """The Codex runtime has not started or is shutting down."""


class ConversationBusyError(OaiSkillError):
    """A conversation already has a turn in progress."""


class ConversationNotFoundError(OaiSkillError):
    """The requested Codex conversation does not exist."""


class TurnTimeoutError(OaiSkillError, TimeoutError):
    """A Codex turn exceeded the configured deadline."""
