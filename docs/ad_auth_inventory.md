# Auth MVP 1 - Inventario AD/LDAP

## Arquivos encontrados

- `access_inventory/models.py`
  - `ADOrganizationalUnit`, `ADUser`, `ADGroup`, `ADGroupMembership`.
  - Estruturas uteis para inventario e correlacao de usuarios/grupos do AD.
- `access_inventory/services/import_ad_inventory.py`
  - Importa usuarios, grupos, OUs e membros por JSON.
  - Usa campos como `sAMAccountName`, `distinguishedName`, `mail`, SID e membership.
- `access_inventory/services/resolve_acl_identities.py`
  - Resolve ACLs por SID contra `ADUser`/`ADGroup`.
- `access_inventory/services/sync_access_review_rules_from_ad_groups.py`
  - Reaproveita grupos ja importados para regras de revisao de acesso.
- `access_inventory/README.md`
  - Declara explicitamente que a primeira versao nao implementa LDAP nem PowerShell.
- `access_inventory/agent_auth.py`
  - Token de agente do inventario, sem relacao com login de usuarios.

## Dependencias encontradas

- Nao havia `ldap3`, `python-ldap` ou `django-auth-ldap` no `requirements.txt`.
- Foi adicionada `ldap3>=2.9,<3.0` por ser pure Python e funcionar bem em Windows/Linux.

## Configuracoes existentes

- Antes desta fase nao havia configuracao `AD_*` em `settings.py`.
- Foram adicionadas variaveis em `.env.example` e leitura em `config/settings.py`.

## O que foi reaproveitado

- Os models `ADUser`, `ADGroup` e `ADGroupMembership` continuam como fonte de inventario AD.
- Os nomes de atributos usados no import (`sAMAccountName`, `mail`, `distinguishedName`) guiaram a configuracao do backend LDAP.
- A autenticacao nova fica separada em `config/ad_ldap.py` e `config/auth_backends.py`, sem misturar com importacao de inventario.

## Lacunas encontradas

- `access_inventory` nao tinha cliente LDAP online.
- Nao havia login via AD.
- Nao havia diagnostico de bind/busca LDAP.
- Nao havia mapeamento de grupos AD para `is_staff`.
- Nao havia tela customizada de login.

## Plano aplicado

- Criado backend `config.auth_backends.ActiveDirectoryBackend`.
- Mantido fallback local `django.contrib.auth.backends.ModelBackend`.
- Criada camada LDAP reutilizavel em `config/ad_ldap.py`.
- Criado comando seguro `python manage.py test_ad_auth --username usuario`.
- Adicionada rota `/accounts/login/` via auth URLs do Django e template visual simples.
- `AD_AUTH_ENABLED=False` desativa totalmente o backend AD sem quebrar login local.

## Variaveis AD

- `AD_AUTH_ENABLED`
- `AD_SERVER_URI`
- `AD_DOMAIN`
- `AD_REALM`
- `AD_BIND_DN`
- `AD_BIND_PASSWORD`
- `AD_USER_SEARCH_BASE`
- `AD_GROUP_SEARCH_BASE`
- `AD_USER_ATTR`
- `AD_EMAIL_ATTR`
- `AD_FIRST_NAME_ATTR`
- `AD_LAST_NAME_ATTR`
- `AD_REQUIRE_TLS`
- `AD_ADMIN_GROUP`
- `AD_TECH_GROUP`
- `AD_TIMEOUT`

## Seguranca

- Senha de bind fica apenas em `.env`.
- Senha do usuario nunca e logada.
- O comando diagnostico nao pede senha do usuario; ele usa apenas bind de servico e busca atributos/grupos.
- `AD_ADMIN_GROUP` e `AD_TECH_GROUP` so promovem `is_staff` quando configurados e quando o usuario pertence aos grupos.
- `is_superuser` nao e concedido automaticamente nesta fase.
