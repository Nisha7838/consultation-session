"""Session lifecycle and per-minute billing.

Billing model
-------------
* A session is billed in arrears for each *completed* minute:
      due_minutes = floor((now - started_at) / 60s)
  Minutes 1..billed_minutes are already paid; a billing run charges only
  minutes billed_minutes+1 .. due_minutes. Re-running billing for the same
  minute therefore charges nothing (plus a unique ledger reference per minute).
* After billing, if the wallet cannot cover the *next* minute, the system ends
  the session (ended_by=system, reason=insufficient_balance). The wallet never
  goes negative.
* A trailing partial minute at the moment the session ends is not charged.
"""

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.exceptions import ActiveSessionExists, DomainError, InsufficientBalance, InvalidTransition, NotAllowed, NotFound
from wallet import services as wallet_services

from . import state_machine as sm
from .models import ConsultationSession, EndedBy, EndReason, SessionStatus


def _lock_session(session_id) -> ConsultationSession:
    try:
        return ConsultationSession.objects.select_for_update().get(pk=session_id)
    except ConsultationSession.DoesNotExist:
        raise NotFound(f"Session {session_id} not found.")


def _ensure_is_session_user(session, actor):
    if session.user_id != actor.pk:
        raise NotAllowed("Only the user who booked this session can do that.")


def _ensure_is_session_provider(session, actor):
    if session.provider_id is None or session.provider_id != actor.pk:
        raise NotAllowed("Only the provider assigned to this session can do that.")


def create_session(user, provider=None) -> ConsultationSession:
    if not user.is_customer:
        raise NotAllowed("Only USER accounts can book sessions.")
    session = ConsultationSession.objects.create(user=user)
    if provider is not None:
        session = assign_session(session.pk, actor=user, provider=provider)
    return session


@transaction.atomic
def assign_session(session_id, actor, provider) -> ConsultationSession:
    session = _lock_session(session_id)
    _ensure_is_session_user(session, actor)
    if not provider.is_provider:
        raise DomainError("The assignee must be a PROVIDER account.", code="invalid_provider")
    if not provider.per_minute_rate or provider.per_minute_rate <= 0:
        raise DomainError("The provider has no valid per-minute rate.", code="invalid_provider")
    sm.apply(session, sm.ASSIGN)
    session.provider = provider
    session.save()
    return session


@transaction.atomic
def reject_session(session_id, actor) -> ConsultationSession:
    """Provider declines an assignment; the session goes back to unassigned."""
    session = _lock_session(session_id)
    _ensure_is_session_provider(session, actor)
    sm.apply(session, sm.REJECT)
    session.provider = None
    session.save()
    return session


@transaction.atomic
def accept_session(session_id, actor) -> ConsultationSession:
    session = _lock_session(session_id)
    _ensure_is_session_provider(session, actor)
    sm.apply(session, sm.ACCEPT)
    session.save()
    return session


@transaction.atomic
def start_session(session_id, actor, now=None) -> ConsultationSession:
    now = now or timezone.now()
    session = _lock_session(session_id)
    _ensure_is_session_user(session, actor)
    sm.ensure_can_apply(session, sm.START)

    # Locking the wallet serialises concurrent "start" calls for the same user,
    # which makes the active-session check below race-free.
    wallet = wallet_services.get_locked_wallet(session.user_id)

    if (
        ConsultationSession.objects.filter(user_id=session.user_id, status=SessionStatus.IN_PROGRESS)
        .exclude(pk=session.pk)
        .exists()
    ):
        raise ActiveSessionExists("You already have a session in progress.")

    rate = session.provider.per_minute_rate
    min_minutes = settings.SESSION_MIN_START_MINUTES
    required = rate * min_minutes
    if wallet.balance < required:
        raise InsufficientBalance(
            f"A balance of at least {required} ({min_minutes} x {rate}/min) is required to start; "
            f"current balance is {wallet.balance}."
        )

    sm.apply(session, sm.START)
    session.per_minute_cost = rate  # fixed for the lifetime of the session
    session.started_at = now
    session.last_billed_at = now
    try:
        with transaction.atomic():
            session.save()
    except IntegrityError:
        # The partial unique index caught a concurrent start that slipped past the check.
        raise ActiveSessionExists("You already have a session in progress.")
    return session


def _charge_due_minutes(session, wallet, now) -> bool:
    """Charge every completed, not-yet-billed minute. Returns False if the wallet ran dry."""
    elapsed_seconds = max(0, (now - session.started_at).total_seconds())
    due_minutes = int(elapsed_seconds // 60)
    cost = session.per_minute_cost

    while session.billed_minutes < due_minutes:
        if wallet.balance < cost:
            return False
        minute = session.billed_minutes + 1
        wallet_services.debit(
            wallet,
            cost,
            reference=f"session:{session.pk}:minute:{minute}",
            description=f"Session {session.pk} minute {minute}",
            session=session,
            minute_index=minute,
        )
        session.billed_minutes = minute
        session.billed_amount += cost

    session.last_billed_at = now
    return True


def _complete(session, now, ended_by, reason):
    sm.apply(session, sm.END)
    session.ended_at = now
    session.ended_by = ended_by
    session.end_reason = reason
    session.talk_time_seconds = int(max(0, (now - session.started_at).total_seconds()))


@transaction.atomic
def bill_session(session_id, now=None) -> ConsultationSession:
    """Periodic billing tick for an in-progress session. Safe to call repeatedly."""
    now = now or timezone.now()
    session = _lock_session(session_id)
    if session.status != SessionStatus.IN_PROGRESS:
        raise InvalidTransition(f"Only in-progress sessions can be billed; this session is '{session.status}'.")

    wallet = wallet_services.get_locked_wallet(session.user_id)
    paid_all_due = _charge_due_minutes(session, wallet, now)

    if not paid_all_due or wallet.balance < session.per_minute_cost:
        _complete(session, now, EndedBy.SYSTEM, EndReason.INSUFFICIENT_BALANCE)

    session.save()
    return session


@transaction.atomic
def end_session(session_id, actor, now=None) -> ConsultationSession:
    """User or provider ends an in-progress session; completed minutes are billed first."""
    now = now or timezone.now()
    session = _lock_session(session_id)

    if session.user_id == actor.pk:
        ended_by, reason = EndedBy.USER, EndReason.USER_ENDED
    elif session.provider_id == actor.pk:
        ended_by, reason = EndedBy.PROVIDER, EndReason.PROVIDER_ENDED
    else:
        raise NotAllowed("Only the session's user or provider can end it.")

    sm.ensure_can_apply(session, sm.END)

    wallet = wallet_services.get_locked_wallet(session.user_id)
    if not _charge_due_minutes(session, wallet, now):
        # Money ran out before the participant ended the call.
        ended_by, reason = EndedBy.SYSTEM, EndReason.INSUFFICIENT_BALANCE

    _complete(session, now, ended_by, reason)
    session.save()
    return session


@transaction.atomic
def cancel_session(session_id, actor) -> ConsultationSession:
    session = _lock_session(session_id)
    if session.user_id == actor.pk:
        ended_by, reason = EndedBy.USER, EndReason.CANCELLED_BY_USER
    elif session.provider_id == actor.pk:
        ended_by, reason = EndedBy.PROVIDER, EndReason.CANCELLED_BY_PROVIDER
    else:
        raise NotAllowed("Only the session's user or provider can cancel it.")
    sm.apply(session, sm.CANCEL)
    session.ended_by = ended_by
    session.end_reason = reason
    session.ended_at = timezone.now()
    session.save()
    return session


def bill_all_active_sessions(now=None) -> list[ConsultationSession]:
    """Bill every in-progress session; intended to be run every minute (cron / scheduler)."""
    now = now or timezone.now()
    ids = ConsultationSession.objects.filter(status=SessionStatus.IN_PROGRESS).values_list("pk", flat=True)
    billed = []
    for session_id in list(ids):
        try:
            billed.append(bill_session(session_id, now=now))
        except InvalidTransition:
            continue  # ended between the query and the lock
    return billed
