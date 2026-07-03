# Auth MVP 2 - Validacao AD e identidade na Desk

## Variaveis AD necessarias

Configure no `.env` quando for validar contra o dominio:

```env
AD_AUTH_ENABLED=True
AD_SERVER_URI=ldap://192.168.104.2
AD_DOMAIN=control.local
AD_REALM=CONTROL.LOCAL
AD_BIND_DN=CN=usuario-bind,OU=USUARIOS,DC=control,DC=local
AD_BIND_PASSWORD=change-me
AD_USER_SEARCH_BASE=OU=USUARIOS,DC=control,DC=local
AD_GROUP_SEARCH_BASE=OU=GRUPOS,DC=control,DC=local
AD_USER_ATTR=sAMAccountName
AD_EMAIL_ATTR=mail
AD_FIRST_NAME_ATTR=givenName
AD_LAST_NAME_ATTR=sn
AD_REQUIRE_TLS=False
AD_ADMIN_GROUP=
AD_TECH_GROUP=
```

Nunca versionar senha real de bind. O login local/admin continua ativo pelo `django.contrib.auth.backends.ModelBackend`.

## Diagnostico

Use:

```powershell
python manage.py test_ad_auth --username usuario
```

O comando valida:

- `AD_AUTH_ENABLED`;
- conexao LDAP;
- bind do usuario de servico;
- busca do usuario;
- atributos `username`, `mail`, `givenName`, `sn` e `displayName`;
- grupos encontrados e pertinencia aos grupos configurados em `AD_ADMIN_GROUP` e `AD_TECH_GROUP`.

O comando nao pede nem exibe senha do usuario final e nao imprime o `AD_BIND_PASSWORD`.

## Login

Com `AD_AUTH_ENABLED=True`, `/accounts/login/` tenta autenticar no AD pelo backend `config.auth_backends.ActiveDirectoryBackend`.

Ao autenticar, o usuario local Django e criado ou atualizado com:

- `username`;
- `email`;
- `first_name`;
- `last_name`;
- `is_active=True`.

Se `AD_ADMIN_GROUP` ou `AD_TECH_GROUP` estiverem configurados e o usuario pertencer a eles, `is_staff=True` e aplicado. `is_superuser` nao e promovido automaticamente.

Com `AD_AUTH_ENABLED=False`, o backend AD ignora a tentativa e o login local/admin segue pelo `ModelBackend`.

## Desk usando request.user

As acoes reais da Desk passam a usar o usuario autenticado quando disponivel:

- criar chamado pelo drawer rapido;
- criar e assumir chamado;
- assumir chamado;
- adicionar comentario publico ou interno;
- alterar status;
- alterar prioridade;
- alterar fila;
- alterar SLA;
- resolver;
- escalar;
- auditoria;
- templates e EmailOutbox que usam `{{tecnico}}`.

Quando nao ha usuario autenticado, o fallback seguro e `Técnico` para acoes manuais ou `Equipe NightOwl` para notificacoes automaticas.

## Atribuidos a mim

O filtro `Atribuidos a mim` usa o usuario logado. Para compatibilidade com chamados antigos, a busca considera:

- `request.user.username`;
- nome completo;
- e-mail;
- primeiro nome.

## Portal autenticado

No Portal de Chamados, quando o usuario esta autenticado:

- se `request.user.email` existir, lista apenas chamados com `requester_email` igual ao e-mail do usuario;
- se o usuario nao tiver e-mail cadastrado, mostra estado vazio amigavel;
- nao mostra chamados de outros solicitantes.

Sem autenticacao, a V1 ainda permite filtro controlado por `?email=` para simulacao ate existir token publico/autenticacao do solicitante.

## Limitacoes restantes

- Ainda nao ha sincronizacao periodica de usuarios/grupos AD.
- Ainda nao ha tela de mapeamento de perfis por grupo.
- Ainda nao ha token publico do portal.
- Ainda nao ha validacao de pertencimento a grupos em todas as permissoes da Desk.
