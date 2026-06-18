# Handoff Codex - Nightowl Access Inventory / Reestruturacao de Acessos

## 1. Visao Geral Do Projeto

Nightowl e um projeto Django usado para RMM, chamados e inventario de acessos.

O app principal deste trabalho e `access_inventory`.

Objetivo do modulo:
- inventariar acessos de file server;
- coletar ACLs NTFS;
- coletar inventario do Active Directory;
- relacionar ACLs com usuarios e grupos AD;
- apoiar uma reestruturacao de permissoes com linguagem executiva para diretoria.

A area principal desta etapa e **Reestruturacao de Acessos**, baseada nos models:
- `AccessReviewPlan`
- `AccessReviewFolder`
- `AccessReviewPrincipal`
- `AccessReviewRule`

## 2. Ambiente Conhecido

Servidor Linux:
- projeto: `/opt/nightowl`
- venv: `/opt/nightowl/.venv`
- service systemd: `nightowl`
- URL interna: `https://nightowl.control.local`
- Gunicorn: porta `8010`

File server / coleta:
- file server usado para ACL: `FS` / `SRV-FS01`
- caminho correto da coleta atual: `E:\controlsul`

Plano correto atual:
- `plan_id=3`
- nome: `Reestruturação de Acessos — Estrutura real E:\controlsul`
- share correta atual: `share_id=5`

Planos antigos:
- `plan_id=1`: piloto antigo
- `plan_id=2`: estrutura antiga/parcial

Os planos 1 e 2 sao historicos/legado e nao devem aparecer para diretoria. Nao apagar esses dados.

## 3. Agentes E Coleta

Agentes:
- `DC01` coleta inventario AD.
- `FS` / `SRV-FS01` coleta ACL.

Estado conhecido:
- AD inventory ja funciona.
- File ACL com depth 3 foi exportado com sucesso a partir de `E:\controlsul`.
- O endpoint HTTP de `file-acl` falhou com payload grande.
- A importacao grande foi feita manualmente via command:

```bash
python manage.py import_file_acl /tmp/file_acl_controlsul_depth3.json
```

Resultado da importacao manual:
- folders criados: `2554`
- folders atualizados: `16`
- acl_entries criados: `9162`
- acl_entries atualizados: `67`

Depois disso a share correta ficou como:
- `share_id=5`
- `2570` folders

## 4. Estrutura Real Relevante

O plano 3 foi populado com:

```bash
python manage.py seed_access_review_folders --plan-id 3 --share-id 5
```

Hierarquia real relevante:

```text
controlsul
  Administrativo
    FINANCEIRO
      DIVIDENDOS
      FATUR
      EMPRESTIMO
      MOVIMENTAÇÃO BANCÁRIA
      REEMB CUSTAS
      ...
  Juridico
    ADM
    CIVEL
    OPER
    PRAZOS
    ...
```

A tela principal da reestruturacao deve mostrar apenas o escopo executivo inicial:
- `controlsul\Administrativo`
- `controlsul\Juridico`

As outras pastas existem no plano 3, mas ficam ocultas na tela inicial para nao poluir a apresentacao. Nao apagar nem remover essas pastas.

## 5. UI Atual Da Reestruturacao

Rotas:
- `/access-inventory/reviews/`
- `/access-inventory/reviews/<plan_id>/`
- `/access-inventory/reviews/<plan_id>/folders/<folder_id>/`

Comportamento atual:
- `/access-inventory/reviews/` mostra apenas o plano mais recente/ativo.
- `/access-inventory/reviews/3/` mostra os cards `Administrativo` e `Juridico`.
- A navegacao por pasta usa `parent_id` e `plan_id`.
- `controlsul` e raiz tecnica e nao deve ser protagonista da UX.
- Cards da Reestruturacao nao devem ter hover.
- Somente botoes clicaveis devem manter hover.

Tela individual da pasta:
- mostra subpastas diretas;
- mostra permissoes atuais encontradas;
- mostra permissoes propostas quando importadas.

## 6. Permissoes Atuais

A tela de pasta ja mostra usuarios efetivos com base nas ACLs atuais.

A UI expande grupos AD para usuarios:

```text
Pasta -> Usuario -> Permissao atual -> Origem
```

Exemplo:

```text
Ana Souza | Leitura e escrita | via grupo GG_FINANCEIRO_RW
```

O grupo aparece apenas como detalhe de origem:

```text
via grupo NOME_DO_GRUPO
```

Rotulo de permissao atual:
- `Somente leitura`
- `Leitura e escrita`
- `Controle total`
- `Personalizada`
- `Negado`

Permissoes especiais devem explicar direitos tecnicos como:
- `WriteAttributes`
- `DeleteSubdirectoriesAndFiles`
- `ReadPermissions`
- `ChangePermissions`
- `TakeOwnership`

A tabela de permissoes atuais nao deve ter hover.

Helper relevante:
- `access_inventory/services/access_review.py`
- `get_current_effective_user_access(review_folder)`
- `describe_acl_rights(acl)`

## 7. Importador De Permissoes Propostas

Command implementado:

```bash
python manage.py import_access_review_rules --plan-id 3 --file planilha.csv --dry-run
python manage.py import_access_review_rules --plan-id 3 --file planilha.csv
```

Arquivos criados:
- `access_inventory/management/commands/import_access_review_rules.py`
- `access_inventory/services/import_access_review_rules.py`

CSV esperado, separado por virgula:

```csv
area,pasta_base,subpasta,escopo,principal_tipo,principal_nome,permissao,acao,observacao
```

Valores:
- `escopo`: `exata`, `demais_subpastas`
- `principal_tipo`: `usuario`, `grupo`
- `permissao`: `RO`, `RW`, `FULL`, `NONE`, `CUSTOM`
- `acao`: `manter`, `adicionar`, `remover`, `alterar`

Regras:
- `escopo=exata` aplica somente a pasta alvo.
- `escopo=demais_subpastas`:
  - localiza `controlsul\<area>\<pasta_base>`;
  - pega filhos diretos;
  - exclui subpastas ja citadas com `escopo=exata`;
  - aplica as demais subpastas diretas.

O importador:
- nao altera ACL real;
- usa `AccessReviewPrincipal` e `AccessReviewRule`;
- deve ser idempotente.

## 8. Resolucao De Usuarios Na Importacao Das Propostas

O importador `import_access_review_rules` ja tenta resolver `principal_tipo=usuario` contra `ADUser`.

Comportamento implementado:
- ignora acentos, maiusculas/minusculas e espacos repetidos;
- tenta match exato por `sam_account_name`, `username` se existir, `user_principal_name`, `email` e `display_name`;
- depois tenta `display_name` comeca com o texto informado;
- por fim tenta `display_name` contendo o texto informado, somente se nao houver ambiguidade;
- se houver match unico, vincula `AccessReviewPrincipal.ad_user` e usa o `display_name` real do AD;
- se nao houver match, cria o principal textual e marca `user_resolution=not_found` em `notes`;
- se houver multiplos matches, nao escolhe automaticamente, cria o principal textual e marca `user_resolution=ambiguous` com candidatos em `notes`;
- `--dry-run` mostra mensagens de resolucao, por exemplo `original`, `resolvido`, `status` e `candidatos`.

Exemplos esperados:
- `Roseli` -> `Roseli Branco`, se unico no AD;
- `Ana Claudia` / `Ana Claudia` com acento -> resolve ignorando acento;
- `Bruna` -> ambiguo se existirem `Bruna Brito` e `Bruna Oliveira`;
- usuario inexistente nao quebra a importacao.

Melhoria futura:
- criar uma tela simples de revisao para principals ambiguos/nao encontrados antes da apresentacao final.

## 9. Fluxo Padrao De Deploy No Servidor

Depois de `git pull`:

```bash
cd /opt/nightowl
source .venv/bin/activate
python manage.py check
python manage.py test access_inventory
python manage.py collectstatic --noinput
systemctl restart nightowl
```

## 10. Comandos Uteis

Ver shares e contagem:

```bash
python manage.py shell -c "from access_inventory.models import Share, Folder; from django.db.models import Count; print(list(Share.objects.order_by('id').values('id','name','file_server_id'))); print(list(Folder.objects.values('share_id').annotate(c=Count('id')).order_by('share_id')))"
```

Validar plano 3:

```bash
python manage.py shell -c "from access_inventory.models import AccessReviewFolder; qs=AccessReviewFolder.objects.filter(plan_id=3); print('total=', qs.count()); print(list(qs.filter(parent__isnull=True).values('id','name','proposed_path')[:20]))"
```

Buscar pasta:

```bash
python manage.py shell -c "from access_inventory.models import AccessReviewFolder; print(list(AccessReviewFolder.objects.filter(plan_id=3, proposed_path__icontains='FINANCEIRO').values('id','name','proposed_path')[:50]))"
```

Importar proposta:

```bash
python manage.py import_access_review_rules --plan-id 3 --file /tmp/proposta_financeiro.csv --dry-run
python manage.py import_access_review_rules --plan-id 3 --file /tmp/proposta_financeiro.csv
```

## 11. Convencoes Importantes

- Nao hardcodar `Administrativo` / `Juridico` fora do helper de escopo executivo temporario.
- Nao apagar dados reais do plano.
- Nao mexer em ACL real do file server.
- Nao alterar agentes PowerShell sem necessidade.
- Nao criar migrations sem necessidade.
- Sempre preferir dados reais do banco.
- A UI para diretoria deve ser simples, executiva e centrada em:

```text
Pasta -> Usuario -> Permissao
```

## 12. Arquivos E Helpers Relevantes

Services:
- `access_inventory/services/access_review.py`
- `access_inventory/services/import_access_review_rules.py`
- `access_inventory/services/seed_access_review_folders.py`

Commands:
- `access_inventory/management/commands/seed_access_review_folders.py`
- `access_inventory/management/commands/import_access_review_rules.py`
- `access_inventory/management/commands/import_file_acl.py`
- `access_inventory/management/commands/import_ad_inventory.py`
- `access_inventory/management/commands/resolve_acl_identities.py`

Templates:
- `templates/access_inventory/review_plan_list.html`
- `templates/access_inventory/review_plan_detail.html`
- `templates/access_inventory/review_folder_detail.html`

CSS:
- `static/css/nightowl.css`

Tests:
- `access_inventory/tests.py`
