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

`NightowlAccessInventoryAgent.ps1` le um `config.json`, envia heartbeat para o Night Owl, chama o exportador de ACLs para cada alvo em `file_acl_targets` e envia o payload diretamente para a API:

- `POST /api/access-inventory/agent/heartbeat/`
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
  "hostname": "SRV-FS01",
  "collector_name": "SRV-FS01 ACL Collector",
  "timeout_sec": 60,
  "log_path": "C:\\Nightowl\\AccessInventory\\logs\\agent.log",
  "temp_directory": "C:\\Nightowl\\AccessInventory\\tmp",
  "file_acl_targets": [
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
  ]
}
```

Em cada alvo, `path`, `unc_path` e `UncPath` sao aceitos como caminho raiz. Use `path` nos configs novos.

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
4. percorre cada item de `file_acl_targets`;
5. coleta ACLs usando `Export-FileServerAcl.ps1`;
6. salva o payload temporario em UTF-8 sem BOM;
7. envia o JSON para `https://nightowl.control.local/api/access-inventory/agent/file-acl/`;
8. grava logs em `log_path`.

Falhas de certificado, DNS, token, endpoint e coleta retornam erro claro no console e no log.

O agente retorna codigo de saida `0` em sucesso e diferente de zero quando uma falha geral ou algum alvo falhar.

## Validar no Django Admin

Depois da execucao, acesse o Admin do Django:

- `Access Inventory > Inventory agents`: confirme `last_seen_at` e `version`.
- `Access Inventory > Inventory agent runs`: verifique runs de `heartbeat` e `file_acl`.
- `Access Inventory > File servers`, `Shares`, `Folders` e `Acl entries`: confirme os dados importados.

Se o token estiver errado ou o agente estiver desativado, a API deve retornar `401`.

## Exportacao manual para JSON

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
