"""Additional behaviour beyond the six required scenarios."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase

from consultations import state_machine as sm
from consultations.models import ConsultationSession
from core.exceptions import InsufficientBalance, InvalidTransition
from wallet import services as wallet_services
from wallet.models import Wallet, WalletTransaction

from .helpers import BillingAPITestCase, User, at, minutes


class StateMachineTableTests(SimpleTestCase):
    def test_happy_path_sequence_is_allowed(self):
        class Fake:
            status = "unassigned"

        s = Fake()
        for action, expected in [
            (sm.ASSIGN, "assigned"),
            (sm.ACCEPT, "accepted_by_provider"),
            (sm.START, "in_progress"),
            (sm.END, "completed"),
        ]:
            sm.apply(s, action)
            self.assertEqual(s.status, expected)

    def test_terminal_states_allow_nothing(self):
        self.assertEqual(sm.allowed_actions("completed"), [])
        self.assertEqual(sm.allowed_actions("cancelled"), [])

    def test_skipping_a_step_is_rejected(self):
        class Fake:
            status = "assigned"

        with self.assertRaises(InvalidTransition):
            sm.apply(Fake(), sm.START)


class SessionRuleTests(BillingAPITestCase):
    def test_exactly_five_minutes_of_balance_is_enough_to_start(self):
        self.recharge(250)
        self.started_session()
        self.assertEqual(self.balance(), Decimal("250.00"))  # nothing charged at start

    def test_user_cannot_have_two_in_progress_sessions(self):
        self.recharge(1000)
        self.started_session()
        second = self.accepted_session()

        resp = self.action(second, "start")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["error"], "active_session_exists")

    def test_db_enforces_single_active_session(self):
        session = ConsultationSession.objects.create(user=self.user, provider=self.provider, status="in_progress")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ConsultationSession.objects.create(user=self.user, provider=self.provider, status="in_progress")
        self.assertTrue(session.pk)

    def test_per_minute_cost_is_fixed_once_session_starts(self):
        self.recharge(1000)
        session_id = self.started_session(start_at=minutes(0))
        self.provider.per_minute_rate = Decimal("80.00")
        self.provider.save()

        with at(minutes(2)):
            resp = self.action(session_id, "bill")

        self.assertEqual(Decimal(resp.data["per_minute_cost"]), Decimal("50.00"))
        self.assertEqual(self.balance(), Decimal("900.00"))

    def test_provider_can_end_session(self):
        self.recharge(1000)
        session_id = self.started_session(start_at=minutes(0))
        with at(minutes(3)):
            resp = self.action(session_id, "end", self.provider_client)
        self.assertEqual(resp.data["ended_by"], "provider")
        self.assertEqual(resp.data["end_reason"], "provider_ended")
        self.assertEqual(self.balance(), Decimal("850.00"))

    def test_end_bills_completed_minutes_not_billed_yet_and_skips_partial_minute(self):
        self.recharge(1000)
        session_id = self.started_session(start_at=minutes(0))
        with at(minutes(3, seconds=40)):  # no billing ticks happened in between
            resp = self.action(session_id, "end")
        self.assertEqual(resp.data["billed_minutes"], 3)
        self.assertEqual(self.balance(), Decimal("850.00"))

    def test_late_billing_charges_only_what_wallet_can_cover(self):
        self.recharge(290)
        session_id = self.started_session(start_at=minutes(0))
        with at(minutes(8)):  # 8 minutes due, wallet covers 5
            resp = self.action(session_id, "bill")
        self.assertEqual(resp.data["status"], "completed")
        self.assertEqual(resp.data["ended_by"], "system")
        self.assertEqual(resp.data["billed_minutes"], 5)
        self.assertEqual(resp.data["talk_time_seconds"], 480)
        self.assertEqual(self.balance(), Decimal("40.00"))

    def test_user_end_after_money_ran_out_is_recorded_as_system_end(self):
        self.recharge(290)
        session_id = self.started_session(start_at=minutes(0))
        with at(minutes(7)):
            resp = self.action(session_id, "end")
        self.assertEqual(resp.data["ended_by"], "system")
        self.assertEqual(resp.data["end_reason"], "insufficient_balance")
        self.assertEqual(self.balance(), Decimal("40.00"))

    def test_start_twice_and_accept_twice_are_invalid(self):
        self.recharge(1000)
        session_id = self.started_session()
        self.assertEqual(self.action(session_id, "start").status_code, 400)
        self.assertEqual(self.action(session_id, "accept", self.provider_client).status_code, 400)

    def test_cancel_before_start_and_not_after(self):
        session_id = self.accepted_session()
        resp = self.action(session_id, "cancel")
        self.assertEqual(resp.data["status"], "cancelled")
        self.assertEqual(resp.data["end_reason"], "cancelled_by_user")
        self.assertEqual(self.action(session_id, "start").status_code, 400)

    def test_provider_reject_returns_session_to_unassigned_and_it_can_be_reassigned(self):
        session_id = self.create_session()
        resp = self.action(session_id, "reject", self.provider_client)
        self.assertEqual(resp.data["status"], "unassigned")
        self.assertIsNone(resp.data["provider"])
        resp = self.action(session_id, "assign", data={"provider_id": self.provider.pk})
        self.assertEqual(resp.data["status"], "assigned")

    def test_session_created_without_provider_is_unassigned(self):
        resp = self.user_client.post("/api/sessions/", {}, format="json")
        self.assertEqual(resp.data["status"], "unassigned")
        self.assertEqual(self.action(resp.data["id"], "accept", self.provider_client).status_code, 404)


class RolePermissionTests(BillingAPITestCase):
    def test_user_cannot_accept_and_provider_cannot_start_or_create(self):
        self.recharge(1000)
        session_id = self.create_session()
        self.assertEqual(self.action(session_id, "accept").status_code, 403)
        self.action(session_id, "accept", self.provider_client)
        self.assertEqual(self.action(session_id, "start", self.provider_client).status_code, 403)
        resp = self.provider_client.post("/api/sessions/", {}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_other_provider_cannot_accept(self):
        other = User.objects.create_user("dr_eve", password="pass12345", role="PROVIDER", per_minute_rate=10)
        session_id = self.create_session()
        self.assertEqual(self.action(session_id, "accept", self.client_for(other)).status_code, 404)

    def test_unauthenticated_requests_are_rejected(self):
        self.assertEqual(self.webhook_client.get("/api/wallet/").status_code, 401)

    def test_register_and_login(self):
        resp = self.webhook_client.post(
            "/api/auth/register/",
            {"username": "dr_new", "password": "pass12345", "role": "PROVIDER"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)  # provider without a rate
        resp = self.webhook_client.post(
            "/api/auth/register/",
            {"username": "carol", "password": "pass12345", "role": "USER"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data["token"])
        resp = self.webhook_client.post("/api/auth/token/", {"username": "carol", "password": "pass12345"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Wallet.objects.filter(user__username="carol").exists())


class WalletTests(BillingAPITestCase):
    def test_wallet_balance_endpoint(self):
        self.recharge(300)
        resp = self.user_client.get("/api/wallet/")
        self.assertEqual(Decimal(resp.data["balance"]), Decimal("300.00"))

    def test_failed_callback_does_not_credit_and_cannot_later_succeed(self):
        resp = self.user_client.post("/api/wallet/recharges/", {"amount": "500"}, format="json")
        reference = resp.data["reference"]
        self.assertEqual(self.callback(reference, "failed").status_code, 200)
        resp = self.callback(reference, "success")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["error"], "recharge_already_finalized")
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_callback_requires_webhook_secret(self):
        resp = self.user_client.post("/api/wallet/recharges/", {"amount": "500"}, format="json")
        self.assertIn(self.callback(resp.data["reference"], secret="wrong").status_code, (401, 403))
        self.assertEqual(self.balance(), Decimal("0.00"))

    def test_callback_for_unknown_reference_is_404(self):
        self.assertEqual(self.callback("does-not-exist").status_code, 404)

    def test_invalid_recharge_amount_rejected(self):
        resp = self.user_client.post("/api/wallet/recharges/", {"amount": "-5"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_debit_never_makes_balance_negative(self):
        self.recharge(40)
        with transaction.atomic():
            wallet = wallet_services.get_locked_wallet(self.user.pk)
            with self.assertRaises(InsufficientBalance):
                wallet_services.debit(wallet, Decimal("50.00"), reference="test")
        self.assertEqual(self.balance(), Decimal("40.00"))

    def test_database_rejects_negative_balance(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Wallet.objects.filter(user=self.user).update(balance=Decimal("-1.00"))

    def test_ledger_matches_balance(self):
        self.recharge(1000)
        session_id = self.started_session(start_at=minutes(0))
        with at(minutes(3)):
            self.action(session_id, "end")
        txns = WalletTransaction.objects.filter(wallet__user=self.user)
        credits = sum(t.amount for t in txns if t.type == "credit")
        debits = sum(t.amount for t in txns if t.type == "debit")
        self.assertEqual(credits - debits, self.balance())
        self.assertEqual(len(self.user_client.get("/api/wallet/transactions/").data), 4)


class ManagementCommandTests(BillingAPITestCase):
    def test_bill_active_sessions_command(self):
        self.recharge(290)
        session_id = self.started_session(start_at=minutes(0))
        out = StringIO()
        with at(minutes(5)):
            call_command("bill_active_sessions", stdout=out)
        self.assertIn("1 ended", out.getvalue())
        self.assertEqual(ConsultationSession.objects.get(pk=session_id).end_reason, "insufficient_balance")
