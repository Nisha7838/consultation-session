import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Last line of defence: the database itself refuses a negative balance.
            models.CheckConstraint(condition=Q(balance__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"Wallet({self.user_id}) = {self.balance}"


class RechargeTransaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recharges")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=64, unique=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="recharge_amount_positive"),
        ]

    def __str__(self):
        return f"Recharge {self.reference} {self.amount} [{self.status}]"


class WalletTransaction(models.Model):
    """Append-only ledger. Every balance change writes exactly one row.

    `reference` is unique, which makes every credit/debit idempotent at the DB
    level: "recharge:<ref>" for top-ups, "session:<id>:minute:<n>" for billing.
    """

    class Type(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="transactions")
    type = models.CharField(max_length=8, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=128, unique=True)
    description = models.CharField(max_length=255, blank=True)
    session = models.ForeignKey(
        "consultations.ConsultationSession",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="charges",
    )
    minute_index = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="wallet_txn_amount_positive"),
            models.UniqueConstraint(
                fields=["session", "minute_index"],
                condition=Q(session__isnull=False),
                name="one_charge_per_session_minute",
            ),
        ]

    def __str__(self):
        return f"{self.type} {self.amount} ({self.reference})"
