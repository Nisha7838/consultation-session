from django.db.models import Q
from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from core.permissions import IsProviderRole, IsUserRole

from . import services
from .models import ConsultationSession
from .serializers import AssignSerializer, SessionCreateSerializer, SessionSerializer


class SessionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    POST /api/sessions/                 create (USER)        body: {"provider_id": optional}
    POST /api/sessions/{id}/assign/     USER                 body: {"provider_id": ...}
    POST /api/sessions/{id}/accept/     assigned PROVIDER
    POST /api/sessions/{id}/reject/     assigned PROVIDER
    POST /api/sessions/{id}/start/      USER
    POST /api/sessions/{id}/end/        USER or PROVIDER
    POST /api/sessions/{id}/cancel/     USER or PROVIDER (before start)
    POST /api/sessions/{id}/bill/       USER or PROVIDER (periodic billing tick)
    """

    serializer_class = SessionSerializer

    def get_queryset(self):
        user = self.request.user
        return ConsultationSession.objects.filter(Q(user=user) | Q(provider=user))

    def get_permissions(self):
        perms = [permissions.IsAuthenticated()]
        if self.action in ("create", "assign", "start"):
            perms.append(IsUserRole())
        elif self.action in ("accept", "reject"):
            perms.append(IsProviderRole())
        return perms

    def _respond(self, session, code=status.HTTP_200_OK):
        return Response(SessionSerializer(session).data, status=code)

    def create(self, request, *args, **kwargs):
        serializer = SessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = services.create_session(request.user, provider=serializer.validated_data.get("provider_id"))
        return self._respond(session, status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        session = self.get_object()
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._respond(
            services.assign_session(session.pk, request.user, serializer.validated_data["provider_id"])
        )

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        return self._respond(services.accept_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._respond(services.reject_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        return self._respond(services.start_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        return self._respond(services.end_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._respond(services.cancel_session(self.get_object().pk, request.user))

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        return self._respond(services.bill_session(self.get_object().pk))
