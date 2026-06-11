# Access Inventory PowerShell Export

Scripts para exportar permissoes NTFS de file servers Windows para JSON compativel com o app Django `access_inventory`.

## Script

`Export-FileServerAcl.ps1` percorre somente pastas, coleta ACLs com `Get-Acl` e grava um JSON com:

- `file_servers`
- `shares`
- `folders`
- `acl_entries`
- `errors`

O formato principal e achatado porque o management command `import_file_acl` le listas top-level.

## Exemplo de uso

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
