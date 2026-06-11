# Access Inventory

App Django para inventariar e consultar relacoes de acesso entre Active Directory, file servers, shares, pastas e ACLs NTFS.

Esta primeira versao nao implementa LDAP nem PowerShell. Os dados entram por arquivos JSON gerados externamente.

## Estrutura dos dados

- `ADOrganizationalUnit`: OUs por distinguished name.
- `ADUser`: usuarios AD por SID, com OU opcional.
- `ADGroup`: grupos AD por SID, com OU opcional.
- `ADGroupMembership`: vinculo de grupo pai com usuario ou grupo membro.
- `FileServer`: servidor de arquivos, com FK opcional para `agents.AgentMachine` quando houver correspondencia segura.
- `Share`: compartilhamento UNC.
- `Folder`: pasta dentro de uma share.
- `AclEntry`: entrada de ACL NTFS, vinculada automaticamente a usuario ou grupo quando o SID existir no inventario AD.

## Migrations

```powershell
venv\Scripts\python.exe manage.py makemigrations access_inventory
venv\Scripts\python.exe manage.py migrate
```

## Importar JSONs de exemplo

```powershell
venv\Scripts\python.exe manage.py import_ad_inventory sample_data\access_inventory\ad_inventory_sample.json
venv\Scripts\python.exe manage.py import_file_acl sample_data\access_inventory\file_acl_sample.json
```

Os comandos sao idempotentes e fazem upsert. Ao final, mostram quantos registros foram criados, atualizados e ignorados.

## Telas

- Dashboard: `/access-inventory/`
- Usuarios AD: `/access-inventory/users/`
- Grupos AD: `/access-inventory/groups/`
- File servers: `/access-inventory/file-servers/`
- Detalhe de pasta: acessado pelo detalhe do file server.

## API

O projeto ja usa Django REST Framework, entao este app fornece endpoints read-only em:

- `/api/access-inventory/ous/`
- `/api/access-inventory/users/`
- `/api/access-inventory/groups/`
- `/api/access-inventory/memberships/`
- `/api/access-inventory/file-servers/`
- `/api/access-inventory/shares/`
- `/api/access-inventory/folders/`
- `/api/access-inventory/acl-entries/`

## Proximos passos

- Criar script PowerShell para exportar ACLs NTFS.
- Criar script PowerShell/LDAP para exportar usuarios, grupos e OUs.
- Resolver grupos aninhados.
- Calcular permissoes efetivas.
- Integrar com chamados para solicitacoes de acesso, aprovacao e auditoria.
- Integrar com RMM de forma mais completa.
- Criar visualizacao em grafo.
- Criar visao 3D opcional futuramente.
