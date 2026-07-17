from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from .authz import is_access_inventory_user, is_nightowl_technical_user


class LoginRequiredMiddleware:
    """Require authentication for internal NightOwl routes with a small public allowlist."""

    PUBLIC_EXACT_PATHS = {
        '/accounts/login/',
        '/accounts/logout/',
        '/admin/login/',
        '/favicon.ico',
        '/health/',
        '/healthcheck/',
    }
    PUBLIC_PREFIXES = (
        '/static/',
        '/admin/',
        '/api/agent/',
        '/api/access-inventory/agent/',
        '/portal/chamados/t/',
    )
    REQUESTER_PREFIXES = (
        '/meus-chamados/',
        '/portal/chamados/',
        '/tickets/attachments/',
    )
    REQUESTER_EXACT_PATHS = {
        '/meus-chamados',
        '/portal/chamados',
        '/accounts/logout/',
    }
    ACCESS_INVENTORY_EXACT_PATHS = {
        '/access-inventory',
        '/api/access-inventory',
    }
    ACCESS_INVENTORY_PREFIXES = (
        '/access-inventory/',
        '/api/access-inventory/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        path = request.path_info
        if self._is_public(path):
            return self.get_response(request)
        if user and user.is_authenticated:
            if self._is_access_inventory_allowed(path, user, request):
                return self.get_response(request)
            if self._is_requester_allowed(path) or is_nightowl_technical_user(user):
                return self.get_response(request)
            return redirect('requester-ticket-list')
        return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)

    def _is_public(self, path):
        if path in self.PUBLIC_EXACT_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES)

    def _is_requester_allowed(self, path):
        if path in self.REQUESTER_EXACT_PATHS:
            return True
        return any(path.startswith(prefix) for prefix in self.REQUESTER_PREFIXES)

    def _is_access_inventory_allowed(self, path, user, request):
        in_access_inventory = path in self.ACCESS_INVENTORY_EXACT_PATHS or any(
            path.startswith(prefix) for prefix in self.ACCESS_INVENTORY_PREFIXES
        )
        return in_access_inventory and is_access_inventory_user(user, request)
