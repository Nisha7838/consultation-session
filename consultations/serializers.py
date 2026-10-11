from django.contrib.auth import get_user_model
from rest_framework import serializers

from . import state_machine as sm
from .models import ConsultationSession

User = get_user_model()


class SessionSerializer(serializers.ModelSerializer):
    allowed_actions = serializers.SerializerMethodField()
    billable_seconds = serializers.IntegerField(read_only=True)

    class Meta:
        model = ConsultationSession
        fields = [
            "id",
            "user",
            "provider",
            "status",
            "allowed_actions",
            "per_minute_cost",
            "started_at",
            "ended_at",
            "billed_minutes",
            "billed_amount",
            "billable_seconds",
            "talk_time_seconds",
            "last_billed_at",
            "ended_by",
            "end_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_allowed_actions(self, obj):
        return sm.allowed_actions(obj.status)


class ProviderField(serializers.PrimaryKeyRelatedField):
    def get_queryset(self):
        return User.objects.filter(role=User.Role.PROVIDER)


class SessionCreateSerializer(serializers.Serializer):
    provider_id = ProviderField(required=False, allow_null=True)


class AssignSerializer(serializers.Serializer):
    provider_id = ProviderField()
