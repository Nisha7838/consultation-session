"""The six scenarios the assignment requires, exercised end-to-end through the API."""

from decimal import Decimal

from consultations.models import ConsultationSession
from wallet.models import WalletTransaction

from .helpers import BillingAPITestCase, at, minutes


class RequiredScenarioTests(BillingAPITestCase):
    # 1. Insufficient balance -------------------------------------------------
    def test_1_user_with_less_than_five_minutes_of_balance_cannot_start(self):
        self.recharge(249)  # 5 x 50 = 250 required
        session_id = self.accepted_session()

        resp = self.action(session_id, "start")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["error"], "insufficient_balance")
        session = ConsultationSession.objects.get(pk=session_id)
        self.assertEqual(session.status, "accepted_by_provider")
        self.assertIsNone(session.started_at)
        self.assertEqual(self.balance(), Decimal("249.00"))

    # 2. Happy path -----------------------------------------------------------
    def test_2_happy_path_recharge_create_accept_start_bill_two_minutes_end(self):
        self.recharge(1000)
        self.assertEqual(self.balance(), Decimal("1000.00"))

        session_id = self.create_session()
        self.assertEqual(self.action(session_id, "accept", self.provider_client).status_code, 200)

        with at(minutes(0)):
            self.assertEqual(self.action(session_id, "start").status_code, 200)
        with at(minutes(1)):
            self.assertEqual(self.action(session_id, "bill").data["billed_minutes"], 1)
        with at(minutes(2)):
            self.assertEqual(self.action(session_id, "bill").data["billed_minutes"], 2)
        with at(minutes(2, seconds=10)):
            resp = self.action(session_id, "end")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "completed")
        self.assertEqual(resp.data["ended_by"], "user")
        self.assertEqual(resp.data["end_reason"], "user_ended")
        self.assertEqual(resp.data["billed_minutes"], 2)
        self.assertEqual(Decimal(resp.data["billed_amount"]), Decimal("100.00"))
        self.assertEqual(resp.data["talk_time_seconds"], 130)
        self.assertEqual(self.balance(), Decimal("900.00"))  # 1000 - 2 x 50
        self.assertEqual(WalletTransaction.objects.filter(session_id=session_id).count(), 2)

    # 3. Balance runs out -----------------------------------------------------
    def test_3_session_auto_ends_when_wallet_cannot_cover_next_minute(self):
        self.recharge(290)  # enough to start (>= 250), runs out after 5 minutes
        session_id = self.started_session(start_at=minutes(0))

        for m in range(1, 5):
            with at(minutes(m)):
                self.assertEqual(self.action(session_id, "bill").data["status"], "in_progress")
        with at(minutes(5)):
            resp = self.action(session_id, "bill")

        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "completed")
        self.assertEqual(resp.data["ended_by"], "system")
        self.assertEqual(resp.data["end_reason"], "insufficient_balance")
        self.assertEqual(resp.data["billed_minutes"], 5)
        self.assertEqual(resp.data["billable_seconds"], 300)
        self.assertEqual(resp.data["talk_time_seconds"], 300)
        self.assertEqual(self.balance(), Decimal("40.00"))  # 290 - 5 x 50, never negative

        # Nothing more can be billed once the system has ended it.
        with at(minutes(6)):
            self.assertEqual(self.action(session_id, "bill").status_code, 400)
        self.assertEqual(self.balance(), Decimal("40.00"))

    # 4. Invalid state transition --------------------------------------------
    def test_4_ending_a_session_before_it_started_returns_400(self):
        self.recharge(1000)
        session_id = self.accepted_session()

        resp = self.action(session_id, "end")

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["error"], "invalid_transition")
        self.assertIn("accepted_by_provider", resp.data["detail"])
        self.assertEqual(ConsultationSession.objects.get(pk=session_id).status, "accepted_by_provider")

    # 5. Duplicate recharge callback -----------------------------------------
    def test_5_duplicate_success_callback_credits_wallet_once(self):
        resp = self.user_client.post("/api/wallet/recharges/", {"amount": "500"}, format="json")
        reference = resp.data["reference"]

        first = self.callback(reference, "success")
        second = self.callback(reference, "success")

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.data["applied"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(self.balance(), Decimal("500.00"))
        self.assertEqual(WalletTransaction.objects.filter(type="credit").count(), 1)

    # 6. Duplicate billing ----------------------------------------------------
    def test_6_billing_the_same_minute_twice_charges_once(self):
        self.recharge(1000)
        session_id = self.started_session(start_at=minutes(0))

        with at(minutes(1)):
            self.action(session_id, "bill")
            self.action(session_id, "bill")
        with at(minutes(1, seconds=45)):  # still inside minute 2, nothing new is due
            resp = self.action(session_id, "bill")

        self.assertEqual(resp.data["billed_minutes"], 1)
        self.assertEqual(self.balance(), Decimal("950.00"))
        self.assertEqual(WalletTransaction.objects.filter(session_id=session_id).count(), 1)
