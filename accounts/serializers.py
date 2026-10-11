from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    per_minute_rate = serializers.DecimalField(
        max_digits=10, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True
    )

    class Meta:
        model = User
        fields = ["id", "username", "password", "role", "per_minute_rate"]

    def validate(self, attrs):
        role = attrs.get("role", User.Role.USER)
        if role == User.Role.PROVIDER and not attrs.get("per_minute_rate"):
            raise serializers.ValidationError({"per_minute_rate": "Providers must set a per-minute rate."})
        if role == User.Role.USER:
            attrs["per_minute_rate"] = None
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "role", "per_minute_rate"]
