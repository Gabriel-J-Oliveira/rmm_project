# Access Inventory PowerShell Export

Scripts para exportar permissoes NTFS de file servers Windows para JSON compativel com o app Django `access_inventory`.

## Scripts

`Export-FileServerAcl.ps1` percorre somente pastas, coleta ACLs com `Get-Acl` e grava um JSON com:

- `file_servers`
- `shares`
- `folders`
- `acl_entries`
- `errors`

O formato principal e achatado porque o management command `import_file_acl` le listas top-level.

O JSON e gravado em UTF-8 sem BOM para ser aceito diretamente pela API Django/DRF.

`Export-AdInventory.ps1` usa o modulo `ActiveDirectory`, quando disponivel, para coletar:

- OUs;
- usuarios;
- grupos;
- memberships diretos de grupos.

O JSON tambem e gravado em UTF-8 sem BOM e e compativel com `/api/access-inventory/agent/ad-inventory/`.

A coleta de memberships usa mapas em memoria de usuarios e grupos por distinguishedName. Computers e outros tipos sao ignorados silenciosamente por padrao; use `-VerboseSkippedMembers` para diagnosticar membros ignorados.

`NightowlAccessInventoryAgent.ps1` le um `config.json`, envia heartbeat para o Night Owl, opcionalmente coleta inventario de AD, chama o exportador de ACLs para cada alvo em `file_acl_targets` e envia os payloads diretamente para a API:

- `POST /api/access-inventory/agent/heartbeat/`
- `POST /api/access-inventory/agent/ad-inventory/`
- `POST /api/access-inventory/agent/file-acl/`

O exportador manual continua disponivel e nao foi removido.

## Configurar o agente

Copie o exemplo:

```powershell
Copy-Item .\config.example.json .\config.json
```

Edite `config.json`:

```json
{
  "server_url": "https://nightowl.control.local",
  "agent_token": "COLE_AQUI_O_TOKEN_GERADO_PELO_DJANGO",
  "hostname": "DC01",
  "collector_name": "DC01 AD Inventory Collector",
  "timeout_sec": 60,
  "log_path": "C:\\Nightowl\\AccessInventory\\logs\\agent.log",
  "temp_directory": "C:\\Nightowl\\AccessInventory\\tmp",
  "ad_inventory": {
    "enabled": true,
    "domain": "control.local",
    "search_base": "",
    "include_disabled_users": true,
    "collect_group_memberships": true,
    "verbose_skipped_members": false,
    "verbose_log": false,
    "export_json_path": "C:\\Nightowl\\AccessInventory\\last_ad_inventory_payload.json"
  },
  "file_acl_targets": []
}
```

Em cada alvo, `path`, `unc_path` e `UncPath` sao aceitos como caminho raiz. Use `path` nos configs novos.
Quando `ad_inventory.enabled=true`, `file_acl_targets` pode ser vazio para execucao AD-only. Se `ad_inventory` estiver ausente ou desabilitado, configure pelo menos um alvo de ACL.

Exemplo de alvo de ACL:

```json
{
  "name": "Financeiro",
  "file_server_name": "SRV-FS01",
  "share_name": "Financeiro",
  "path": "\\\\SRV-FS01\\Financeiro",
  "max_depth": 1,
  "include_inherited": true,
  "verbose_log": false,
  "export_json_path": "C:\\Nightowl\\AccessInventory\\last_file_acl_financeiro.json"
}
```

Crie o agente no Django e guarde o token:

```powershell
venv\Scripts\python.exe manage.py create_inventory_agent --name "SRV-FS01 ACL Agent" --hostname "SRV-FS01"
```

## Rodar o agente manualmente

No Windows File Server, abra PowerShell como uma conta com permissao de leitura nas ACLs e execute:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\NightowlAccessInventoryAgent.ps1 -ConfigPath .\config.json
```

O agente:

1. valida se `server_url` usa HTTPS;
2. resolve DNS de `nightowl.control.local`;
3. envia heartbeat com `X-Nightowl-Agent-Token`;
4. coleta AD quando `ad_inventory.enabled=true`;
5. envia o JSON de AD para `https://nightowl.control.local/api/access-inventory/agent/ad-inventory/`;
6. percorre cada item de `file_acl_targets`, quando houver;
7. coleta ACLs usando `Export-FileServerAcl.ps1`;
8. salva os payloads temporarios em UTF-8 sem BOM;
9. envia os JSONs de AD e ACL com `Invoke-RestMethod -InFile`, preservando exatamente os arquivos gerados;
10. grava logs em `log_path`.

Falhas de certificado, DNS, token, endpoint e coleta retornam erro claro no console e no log. Quando a API retorna erro HTTP, o agente tenta registrar tambem o corpo da resposta.

O agente retorna codigo de saida `0` em sucesso e diferente de zero quando uma falha geral, a coleta AD ou algum alvo de ACL falhar. Se a coleta AD falhar, o agente ainda tenta executar os `file_acl_targets`.
Quando `file_acl_targets=[]` e AD esta habilitado, o agente registra `No file ACL targets configured; skipping file ACL collection.` e finaliza com sucesso se o envio de AD tambem for bem-sucedido.

## Execucao agendada

Para rodar pelo Agendador de Tarefas do Windows, use uma acao como:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\NightowlAgent\scripts\powershell\access_inventory\NightowlAccessInventoryAgent.ps1" -ConfigPath "C:\NightowlAgent\scripts\powershell\access_inventory\config.json"
```

Recomendacoes:

- configure a tarefa para nao iniciar uma nova instancia se a anterior ainda estiver em execucao;
- use uma conta com permissao de leitura no AD e nas ACLs NTFS necessarias;
- mantenha `log_path` e `temp_directory` em disco local, fora das shares inventariadas;
- monitore `LastTaskResult`: `0` indica sucesso, `1` falha geral e `2` falha em uma ou mais coletas/envios.

O agente tambem cria uma trava simples em:

```text
C:\Nightowl\AccessInventory\tmp\nightowl_access_inventory_agent.lock
```

O caminho exato segue `temp_directory` do `config.json`. Se a trava existir e o PID ainda estiver rodando, o agente sai com mensagem clara para evitar duas coletas simultaneas. Se a trava for antiga e o processo nao existir mais, ela e removida automaticamente.

O log do agente e tolerante a lock de arquivo: se `agent.log` estiver em uso por outro processo, o agente tenta gravar algumas vezes e, se nao conseguir, escreve apenas no console e continua a coleta/envio. Falha de log nao derruba o agente nem invalida o JSON gerado.

## Validar no Django Admin

Depois da execucao, acesse o Admin do Django:

- `Access Inventory > Inventory agents`: confirme `last_seen_at` e `version`.
- `Access Inventory > Inventory agent runs`: verifique runs de `heartbeat`, `ad_inventory` e `file_acl`.
- `Access Inventory > AD organizational units`, `AD users`, `AD groups` e `AD group memberships`: confirme os dados de AD.
- `Access Inventory > File servers`, `Shares`, `Folders` e `Acl entries`: confirme os dados importados.

Se o token estiver errado ou o agente estiver desativado, a API deve retornar `401`.

## Exportacao manual para JSON

### Active Directory

Execute em uma maquina com RSAT Active Directory ou em um domain controller:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Export-AdInventory.ps1 `
  -Domain "control.local" `
  -SearchBase "" `
  -IncludeDisabledUsers `
  -OutputPath ".\ad_inventory.json"
```

O script exige o modulo `ActiveDirectory`. Se o modulo nao existir, ele falha com mensagem clara.

Para testar primeiro sem memberships:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Export-AdInventory.ps1 `
  -Domain "control.local" `
  -SearchBase "" `
  -IncludeDisabledUsers `
  -SkipGroupMemberships `
  -OutputPath ".\ad_inventory_sem_memberships.json"
```

Parametros uteis:

- `-SkipGroupMemberships`: gera JSON com `memberships` vazio.
- `-VerboseSkippedMembers`: mostra DNs ignorados por nao serem usuarios/grupos carregados, como computers ou objetos fora do escopo.

### File ACL

Execute em um PowerShell com permissao de leitura no caminho UNC:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Export-FileServerAcl.ps1 `
  -FileServerName "SRV-FS01" `
  -ShareName "Financeiro" `
  -UncPath "\\SRV-FS01\Financeiro" `
  -OutputPath ".\file_acl_financeiro.json" `
  -MaxDepth 3 `
  -IncludeInherited
```

Para um teste pequeno, use `-MaxDepth 1` em uma share controlada:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Export-FileServerAcl.ps1 `
  -FileServerName "SRV-FS01" `
  -ShareName "TI-Teste" `
  -UncPath "\\SRV-FS01\TI-Teste" `
  -OutputPath ".\file_acl_ti_teste.json" `
  -MaxDepth 1 `
  -VerboseLog
```

## Parametros

- `-FileServerName`: nome do servidor de arquivos que sera gravado no JSON.
- `-ShareName`: nome logico da share. Tambem e usado como prefixo do caminho das pastas no inventario.
- `-UncPath`: caminho UNC raiz que sera percorrido.
- `-OutputPath`: arquivo JSON de saida.
- `-MaxDepth`: profundidade maxima a partir da raiz. Use `0` para somente a pasta raiz, `1` para filhos diretos, ou omita para sem limite.
- `-IncludeInherited`: inclui entradas herdadas. Sem esse parametro, o script exporta apenas ACLs explicitas.
- `-VerboseLog`: mostra logs por pasta processada.

## Permissoes necessarias

A conta que executa o script precisa:

- acessar a share via UNC;
- listar diretorios;
- ler ACLs NTFS das pastas;
- resolver nomes de dominio para SID, quando possivel.
- para inventario AD, carregar o modulo `ActiveDirectory` e ler OUs, usuarios, grupos e membros.

Pastas sem permissao nao interrompem a execucao. O erro fica em `errors` dentro do JSON.

## Importar no Django

Copie o JSON gerado para a maquina do projeto Django e execute:

```powershell
venv\Scripts\python.exe manage.py import_file_acl .\file_acl_financeiro.json
```

Se o inventario AD ja tiver sido importado com `import_ad_inventory`, o importador tentara vincular `identity_sid` com `ADUser.sid` ou `ADGroup.sid`.

## Limitacoes conhecidas

- Exporta somente pastas, nao arquivos.
- `identity_type` sai como `unknown`; o Django resolve para usuario ou grupo quando o SID existir no inventario AD.
- Caminhos inacessiveis sao registrados em `errors`, mas suas subpastas podem nao ser descobertas.
- Em arvores grandes, `Get-Acl` pode demorar bastante e gerar JSONs grandes.
- O script nao calcula permissoes efetivas nem resolve grupos aninhados.
- O script nao consulta LDAP diretamente.

## Cuidados antes de rodar em producao

- Teste primeiro com `-MaxDepth 1` ou `-MaxDepth 2`.
- Rode fora de horarios criticos em shares grandes.
- Grave a saida em disco local, nao dentro da propria arvore exportada.
- Monitore tamanho do JSON e tempo de execucao.
- Valide o arquivo em ambiente de homologacao antes de importar no banco principal.

## Proximos passos

- Adicionar exclusoes por caminho.
- Permitir exportar varias shares em lote.
- Criar resumo CSV opcional.
- Classificar `identity_type` no PowerShell quando houver fonte confiavel.
- Integrar com um exportador AD/LDAP para usuarios, grupos e OUs.
