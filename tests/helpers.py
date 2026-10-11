from contextlib import contextmanager
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from wallet.models import Wallet

User = get_user_model()

T0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=dt_timezone.utc)


def minutes(n, seconds=0):
    return T0 + timedelta(minutes=n, seconds=seconds)


@contextmanager
def at(moment):
    """Freeze django.utils.timezone.now() at `moment`."""
    with mock.patch("django.utils.timezone.now", return_value=moment):
        yield


class BillingAPITestCase(APITestCase):
    RATE = Decimal("50.00")

    def setUp(self):
        self.user = User.objects.create_user("alice", password="pass12345", role=User.Role.USER)
        self.provider = User.objects.create_user(
            "dr_bob", password="pass12345", role=User.Role.PROVIDER, per_minute_rate=self.RATE
        )
        self.user_client = self.client_for(self.user)
        self.provider_client = self.client_for(self.provider)
        self.webhook_client = APIClient()

    # --- helpers --------------------------------------------------------------
    def client_for(self, user):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Token {Token.objects.create(user=user).key}")
        return client

    def balance(self, user=None):
        return Wallet.objects.get(user=user or self.user).balance

    def callback(self, reference, status="success", secret=None):
        return self.webhook_client.post(
            "/api/wallet/recharges/callback/",
            {"reference": reference, "status": status},
            format="json",
            HTTP_X_WEBHOOK_SECRET=secret or settings.RECHARGE_WEBHOOK_SECRET,
        )

    def recharge(self, amount, client=None):
        resp = (client or self.user_client).post("/api/wallet/recharges/", {"amount": str(amount)}, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        cb = self.callback(resp.data["reference"])
        self.assertEqual(cb.status_code, 200, cb.data)
        return resp.data["reference"]

    def create_session(self, client=None, provider=None):
        resp = (client or self.user_client).post(
            "/api/sessions/", {"provider_id": (provider or self.provider).pk}, format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        return resp.data["id"]

    def action(self, session_id, name, client=None, data=None):
        return (client or self.user_client).post(f"/api/sessions/{session_id}/{name}/", data or {}, format="json")

    def accepted_session(self):
        session_id = self.create_session()
        resp = self.action(session_id, "accept", self.provider_client)
        self.assertEqual(resp.status_code, 200, resp.data)
        return session_id

    def started_session(self, start_at=T0):
        session_id = self.accepted_session()
        with at(start_at):
            resp = self.action(session_id, "start")
        self.assertEqual(resp.status_code, 200, resp.data)
        return session_id
