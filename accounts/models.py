from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    class Role(models.TextChoices):
        USER = "USER", "User"
        PROVIDER = "PROVIDER", "Provider"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.USER)
    # The provider's current rate. It is copied onto a session when the session
    # starts, so later rate changes never affect a running session.
    per_minute_rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~Q(role="PROVIDER") | Q(per_minute_rate__gt=0),
                name="provider_has_positive_rate",
            ),
        ]

    @property
    def is_customer(self):
        return self.role == self.Role.USER

    @property
    def is_provider(self):
        return self.role == self.Role.PROVIDER
