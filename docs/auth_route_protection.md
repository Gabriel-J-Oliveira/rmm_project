# Auth MVP 3 - Protecao de rotas e login NightOwl

## Rotas protegidas

O middleware `config.middleware.LoginRequiredMiddleware` exige usuario autenticado para as areas internas do NightOwl:

- `/`
- `/dashboard/`
- `/tickets/`
- `/tickets/central/`
- `/tickets/<id>/`
- `/tickets/new/`
- `/tickets/settings/`
- `/maintenance/`
- `/maintenance/email-outbox/`
- `/noc/`
- `/alerts/`
- `/events/`
- `/endpoints/`
- `/software/`
- `/software-policies/`
- `/agent-install/`
- `/access-inventory/`
- `/portal/chamados/`
- `/portal/chamados/<id>/`

O portal atual e autenticado. A proxima etapa prevista e criar acesso publico seguro por token em `/portal/chamados/t/<token>/`.

## Rotas publicas

A allowlist publica atual mantem:

- `/accounts/login/`
- `/accounts/logout/`
- `/admin/` e `/admin/login/`
- `/static/`
- `/health/`
- `/healthcheck/`
- `/api/agent/`
- `/api/access-inventory/agent/`
- `/portal/chamados/t/` para uso futuro com token publico

As APIs de agente permanecem liberadas para nao quebrar heartbeat, enrollment e inventario recebido por agente.
`/media/` nao fica publico nesta fase para evitar acesso anonimo direto a anexos ou arquivos enviados.

## Redirect `next`

Quando um usuario anonimo acessa uma rota protegida, o middleware redireciona para:

```text
/accounts/login/?next=<rota_original>
```

O `LoginView` padrao do Django valida o `next` e so redireciona para URL local/segura. Se nao houver `next`, o destino padrao continua `LOGIN_REDIRECT_URL=/tickets/`.

## Login AD

Com `AD_AUTH_ENABLED=True`, `/accounts/login/` usa `config.auth_backends.ActiveDirectoryBackend` antes do `ModelBackend`.

Ao autenticar no AD, o usuario Django local e criado/atualizado com:

- `username`
- `email`
- `first_name`
- `last_name`
- `is_active=True`

Teste:

```powershell
python manage.py test_ad_auth --username usuario
```

Depois valide o login pelo navegador em `/accounts/login/`.

## Login local/admin

O fallback local continua ativo:

```python
AUTHENTICATION_BACKENDS = [
    "config.auth_backends.ActiveDirectoryBackend",
    "django.contrib.auth.backends.ModelBackend",
]
```

O Django Admin continua usando `/admin/login/` e nao e bloqueado pelo middleware global.

## Logout

`/accounts/logout/` encerra a sessao e redireciona para `/accounts/login/`, exibindo mensagem de sessao encerrada.

## Nova tela de login

`templates/registration/login.html` usa layout split-screen:

- branding NightOwl centralizado na coluna esquerda;
- formulario na coluna direita;
- tema escuro;
- roxo como cor principal;
- verde como destaque secundario;
- mensagens de erro curtas e sem detalhes tecnicos.

## Limitacoes

- Ainda nao existe portal publico por token.
- A regra de permissao por grupo AD ainda e inicial.
- O middleware protege rotas por autenticacao, mas nao substitui permissoes finas por perfil.
