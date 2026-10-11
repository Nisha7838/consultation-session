import hmac

from django.conf import settings
from rest_framework import generics, permissions, status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import IsUserRole

from . import services
from .models import RechargeTransaction, Wallet
from .serializers import (
    RechargeCallbackSerializer,
    RechargeCreateSerializer,
    RechargeSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)


class WalletView(generics.RetrieveAPIView):
    serializer_class = WalletSerializer

    def get_object(self):
        wallet, _ = Wallet.objects.get_or_create(user=self.request.user)
        return wallet


class WalletTransactionListView(generics.ListAPIView):
    serializer_class = WalletTransactionSerializer

    def get_queryset(self):
        return self.request.user.wallet.transactions.all()


class RechargeListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsUserRole]

    def get(self, request):
        recharges = RechargeTransaction.objects.filter(user=request.user).order_by("-created_at")
        return Response(RechargeSerializer(recharges, many=True).data)

    def post(self, request):
        serializer = RechargeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recharge = services.initiate_recharge(request.user, serializer.validated_data["amount"])
        return Response(RechargeSerializer(recharge).data, status=status.HTTP_201_CREATED)


class RechargeCallbackView(APIView):
    """Mock payment-gateway webhook. Authenticated by a shared secret, not a user token."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        secret = request.headers.get("X-Webhook-Secret", "")
        if not hmac.compare_digest(secret, settings.RECHARGE_WEBHOOK_SECRET):
            raise AuthenticationFailed("Invalid webhook secret.")

        serializer = RechargeCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        recharge, applied = services.process_recharge_callback(
            serializer.validated_data["reference"], serializer.validated_data["status"]
        )
        return Response(
            {"recharge": RechargeSerializer(recharge).data, "applied": applied, "duplicate": not applied},
            status=status.HTTP_200_OK,
        )
