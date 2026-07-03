from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse

from .authz import is_nightowl_technical_user


class NightOwlLoginView(LoginView):
    template_name = 'registration/login.html'

    def get_success_url(self):
        redirect_url = self.get_redirect_url()
        if redirect_url:
            if is_nightowl_technical_user(self.request.user):
                return redirect_url
            if redirect_url == '/meus-chamados' or redirect_url.startswith(('/meus-chamados/', '/portal/chamados/')):
                return redirect_url
        if is_nightowl_technical_user(self.request.user):
            return reverse('tickets:central')
        return reverse('requester-ticket-list')


def nightowl_logout(request):
    logout(request)
    messages.success(request, 'Sessao encerrada com sucesso.')
    return redirect('login')
