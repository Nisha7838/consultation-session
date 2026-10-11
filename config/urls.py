from django.urls import include, path

urlpatterns = [
    path("api/auth/", include("accounts.urls")),
    path("api/wallet/", include("wallet.urls")),
    path("api/", include("consultations.urls")),
]
