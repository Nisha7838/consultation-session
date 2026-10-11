"""Wallet operations. All balance changes go through credit()/debit().

Locking order used across the project (to avoid deadlocks):
    session row  ->  wallet row
    recharge row ->  wallet row
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.exceptions import DomainError, InsufficientBalance, NotFound

from .models import RechargeTransaction, Wallet, WalletTransaction


def get_locked_wallet(user_id) -> Wallet:
    """Return the user's wallet with a row lock. Must be called inside transaction.atomic()."""
    wallet, _ = Wallet.objects.get_or_create(user_id=user_id)
    return Wallet.objects.select_for_update().get(pk=wallet.pk)


def credit(wallet: Wallet, amount: Decimal, reference: str, description: str = "") -> WalletTransaction:
    """Add money to a wallet that the caller has already locked."""
    if amount <= 0:
        raise DomainError("Credit amount must be positive.", code="invalid_amount")
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        type=WalletTransaction.Type.CREDIT,
        amount=amount,
        balance_after=wallet.balance,
        reference=reference,
        description=description,
    )


def debit(
    wallet: Wallet,
    amount: Decimal,
    reference: str,
    description: str = "",
    session=None,
    minute_index=None,
) -> WalletTransaction:
    """Take money from a wallet that the caller has already locked. Never goes negative."""
    if amount <= 0:
        raise DomainError("Debit amount must be positive.", code="invalid_amount")
    if wallet.balance < amount:
        raise InsufficientBalance(f"Wallet balance {wallet.balance} cannot cover {amount}.")
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated_at"])
    return WalletTransaction.objects.create(
        wallet=wallet,
        type=WalletTransaction.Type.DEBIT,
        amount=amount,
        balance_after=wallet.balance,
        reference=reference,
        description=description,
        session=session,
        minute_index=minute_index,
    )


def initiate_recharge(user, amount: Decimal) -> RechargeTransaction:
    return RechargeTransaction.objects.create(user=user, amount=amount)


@transaction.atomic
def process_recharge_callback(reference: str, new_status: str):
    """Apply a (mock) payment-gateway callback. Idempotent.

    Returns (recharge, applied). `applied` is False when the callback was a duplicate
    of the state the recharge is already in, so nothing was changed.
    """
    try:
        recharge = RechargeTransaction.objects.select_for_update().get(reference=reference)
    except RechargeTransaction.DoesNotExist:
        raise NotFound(f"No recharge with reference '{reference}'.")

    if recharge.status == new_status:
        return recharge, False  # duplicate delivery: already processed, do nothing

    if recharge.status != RechargeTransaction.Status.PENDING:
        raise DomainError(
            f"Recharge is already '{recharge.status}' and cannot become '{new_status}'.",
            code="recharge_already_finalized",
        )

    recharge.status = new_status
    recharge.completed_at = timezone.now()
    recharge.save(update_fields=["status", "completed_at"])

    if new_status == RechargeTransaction.Status.SUCCESS:
        wallet = get_locked_wallet(recharge.user_id)
        # The unique ledger reference is a second guarantee against double credit.
        credit(wallet, recharge.amount, reference=f"recharge:{recharge.reference}", description="Wallet recharge")

    return recharge, True
