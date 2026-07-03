from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect

from .authz import is_nightowl_technical_user


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
        '/media/',
        '/admin/',
        '/api/agent/',
        '/api/access-inventory/agent/',
        '/portal/chamados/t/',
    )
    REQUESTER_PREFIXES = (
        '/meus-chamados/',
        '/portal/chamados/',
    )
    REQUESTER_EXACT_PATHS = {
        '/meus-chamados',
        '/portal/chamados',
        '/accounts/logout/',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        path = request.path_info
        if self._is_public(path):
            return self.get_response(request)
        if user and user.is_authenticated:
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
