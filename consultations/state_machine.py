from core.exceptions import InvalidTransition

from .models import SessionStatus as S

ASSIGN = "assign"
REJECT = "reject"
ACCEPT = "accept"
START = "start"
END = "end"
CANCEL = "cancel"

TRANSITIONS = {
    ASSIGN: ({S.UNASSIGNED}, S.ASSIGNED),
    REJECT: ({S.ASSIGNED}, S.UNASSIGNED),
    ACCEPT: ({S.ASSIGNED}, S.ACCEPTED_BY_PROVIDER),
    START: ({S.ACCEPTED_BY_PROVIDER}, S.IN_PROGRESS),
    END: ({S.IN_PROGRESS}, S.COMPLETED),
    CANCEL: ({S.UNASSIGNED, S.ASSIGNED, S.ACCEPTED_BY_PROVIDER}, S.CANCELLED),
}


def can_apply(status: str, action: str) -> bool:
    allowed_from, _ = TRANSITIONS[action]
    return status in allowed_from


def ensure_can_apply(session, action: str) -> None:
    if not can_apply(session.status, action):
        allowed_from, _ = TRANSITIONS[action]
        allowed = ", ".join(sorted(allowed_from))
        raise InvalidTransition(
            f"Cannot '{action}' a session in state '{session.status}'. Allowed only from: {allowed}."
        )


def apply(session, action: str) -> None:
    """Validate and move the session to the next state (does not save)."""
    ensure_can_apply(session, action)
    session.status = TRANSITIONS[action][1]


def allowed_actions(status: str) -> list[str]:
    return [
        action
        for action, (allowed_from, _) in TRANSITIONS.items()
        if status in allowed_from
    ]
