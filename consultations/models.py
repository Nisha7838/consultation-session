from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class SessionStatus(models.TextChoices):
    UNASSIGNED = "unassigned", "Unassigned"
    ASSIGNED = "assigned", "Assigned"
    ACCEPTED_BY_PROVIDER = "accepted_by_provider", "Accepted by provider"
    IN_PROGRESS = "in_progress", "In progress"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class EndedBy(models.TextChoices):
    USER = "user", "User"
    PROVIDER = "provider", "Provider"
    SYSTEM = "system", "System"


class EndReason(models.TextChoices):
    USER_ENDED = "user_ended", "Ended by user"
    PROVIDER_ENDED = "provider_ended", "Ended by provider"
    INSUFFICIENT_BALANCE = "insufficient_balance", "Insufficient Balance"
    CANCELLED_BY_USER = "cancelled_by_user", "Cancelled by user"
    CANCELLED_BY_PROVIDER = "cancelled_by_provider", "Cancelled by provider"


class ConsultationSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sessions"
    )
    provider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="provider_sessions",
    )
    status = models.CharField(
        max_length=32, choices=SessionStatus.choices, default=SessionStatus.UNASSIGNED
    )

    # Snapshot of the provider's rate, taken when the session starts.
    per_minute_cost = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Billing progress: minutes 1..billed_minutes have been charged.
    billed_minutes = models.PositiveIntegerField(default=0)
    billed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00")
    )
    last_billed_at = models.DateTimeField(null=True, blank=True)

    # Actual wall-clock talk time, persisted when the session ends.
    talk_time_seconds = models.PositiveIntegerField(null=True, blank=True)

    ended_by = models.CharField(max_length=16, choices=EndedBy.choices, blank=True)
    end_reason = models.CharField(max_length=32, choices=EndReason.choices, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # A user can have at most one in-progress session, enforced by the database.
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status=SessionStatus.IN_PROGRESS),
                name="one_active_session_per_user",
            ),
            models.CheckConstraint(
                condition=Q(billed_amount__gte=0),
                name="session_billed_amount_non_negative",
            ),
        ]

    def __str__(self):
        return f"Session {self.pk} [{self.status}]"

    @property
    def billable_seconds(self):
        return self.billed_minutes * 60
