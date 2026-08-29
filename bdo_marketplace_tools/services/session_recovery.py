"""Neutral session-recovery policy used by production orchestration."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionRecoveryOptions:
    """Optional adjustments to one session check or recovery attempt."""

    force_expired: bool = False
    clear_market_session: bool = False


class NoSessionFaults:
    """Production session policy: never inject a synthetic session fault."""

    def options_for(self, auth_context=None) -> SessionRecoveryOptions:
        return SessionRecoveryOptions()

    def session_validated(self, auth_context=None) -> None:
        return None

    def context_changed(self, auth_context=None) -> None:
        return None
