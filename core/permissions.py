from rest_framework.permissions import BasePermission


class IsUserRole(BasePermission):
    message = "Only accounts with role USER can perform this action."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_customer)


class IsProviderRole(BasePermission):
    message = "Only accounts with role PROVIDER can perform this action."

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_provider)
