# RC47 — Homologacao isolada de PostgreSQL e migrations

## Escopo e estado das evidencias

Este documento prepara uma restauracao e ensaio isolados. Nao autoriza acesso ou mudanca em producao, aplicacao de migrations, criacao de infraestrutura, ativacao de flags, jobs ou servicos.

| Item | Estado | Evidencia |
|---|---|---|
| Main RC47 para este plano | Confirmada no repositorio | `b8b417f520290db7d0b86af71feb86fccd9bf12b` |
| SHA de producao citado | Informado pelo operador, nao consultado no host | `6090228aed49c08ded2ecd8d4ca24658df9eb3b0` |
| PostgreSQL de producao | Informado pelo operador, nao verificado | 17.10 em Debian 13; banco estimado em 13 GB |
| Estado de migration em producao | Informado pelo operador | `agents.0047_persistedtelemetryanalysis_and_more` aplicada; confirmar por coleta direta antes do deploy |
| Backup local | Informado pelo operador; nao acessado neste ambiente | `/opt/nightowl/backups/rc47-consolidated-20261111T133156Z-pr31-33/` |
| Dump | Informado pelo operador | `postgresql-nightowl.dump`, cerca de 4,29 GB; checksums e `pg_restore --list` validados |
| Restauracao integral | Pendente | O operador informa que ainda nao foi exercitada |
| Restore point Veeam | Pendente | Data, consistencia e recuperabilidade ainda nao confirmadas |
| PostgreSQL local | Indisponivel nesta estacao | `psql`, `pg_restore`, `docker` e `podman` nao encontrados; nenhum banco real/sintetico PostgreSQL foi iniciado |

Os dados acima sobre producao, dump e Veeam sao contexto fornecido pelo operador, nao resultado de uma verificacao independente. Nenhum dump, segredo ou dado de producao foi copiado para esta worktree.

## Matriz de migrations

Dependencia linear confirmada no codigo: `0047 -> 0048 -> 0049 -> 0050`. A cadeia de migrations nao prova quais estao aplicadas no banco; verificar o estado restaurado e o host somente por procedimento operacional autorizado.

| Migration | Operacoes | Locks/impacto esperado no PostgreSQL 17 | Reversibilidade e risco de dados | Compatibilidade |
|---|---|---|---|---|
| `agents.0048_separate_admin_terminal_grant` | Adiciona `TerminalAccessGrant.allow_admin_auth` como boolean com default `false`; altera as permissoes de `TerminalSession`, mantendo `open_terminalsession` e adicionando `open_admin_terminalsession`. | `ALTER TABLE` precisa obter lock de catalogo/tabela, incluindo lock exclusivo breve para adicionar coluna. Com default constante, PostgreSQL moderno normalmente evita reescrever todas as linhas; ainda pode aguardar transacoes longas e bloquear brevemente leitores/escritores. O impacto real depende de tabela, workload, transacoes e SQL emitido. | Reversao remove a coluna e portanto qualquer valor nela, alem da mudanca de permissao no estado Django. Sem registros antigos preexistentes para essa coluna. | Codigo antigo pode ignorar a coluna; compatibilidade de INSERT antigo deve ser comprovada. Django pode remover default temporario do banco apos criar coluna; portanto nao presumir que INSERT sem coluna continue aceito. Testar codigo antigo contra schema migrado antes de planejar rollback por codigo. `allow_admin_auth` continua default falso; a migration nao concede grants. |
| `agents.0049_persisted_telemetry_schedule` | Cria `PersistedTelemetryAnalysisSchedule`, OneToOne para `AgentMachine` com `CASCADE`; inclui estado de agenda/retry, historico JSON, revisao e indice em `next_run_at`, mais indice composto `(review_required, next_run_at)`. | Cria tabela e indices vazios. O custo de varrer dados existentes e baixo por nao haver backfill; ainda ha locks de catalogo/relacao e criacao de indices. O volume real futuro depende de quantos endpoints recebem schedule. | Reversao remove tabela e apaga todo o historico/estado nela. Depois que houver dados, reversao nao e sem perda. CASCADE significa que apagar AgentMachine tambem remove o schedule associado. | Codigo anterior normalmente ignora tabela adicional. A compatibilidade operacional depende de nenhum scheduler/flag novo ser habilitado inadvertidamente. |
| `agents.0050_telemetry_scheduler_health` | Adiciona a `AutomationSchedulerHealth` contadores, status/duracao da ultima tentativa, classe/codigo de falha e campos de suspensao/recuperacao. Campos sem `null=True` usam defaults ou `blank`; campos temporais/duracao marcados nullable. | Adicoes de coluna com constantes em PG moderno geralmente usam fast-default, sem reescrita completa, mas exigem lock de tabela/catálogo. Verificar tamanho, transacoes longas e tempo de lock na copia restaurada. | Reversao remove os campos e perde todos os valores escritos neles. Sem dados historicos a preservar antes da aplicacao; apos uso, exportar estado se for requisito de auditoria. | Codigo antigo tende a ignorar campos extras, mas a escrita de linhas deve ser testada com o modelo/codigo anterior, em especial colunas NOT NULL e defaults efetivamente persistidos. |

As estimativas de lock sao baseadas nas operacoes declaradas pelas migrations e no comportamento esperado de PostgreSQL 17; nao sao medicao do banco de producao. Antes da aplicacao operacional, medir a mesma sequencia numa restauracao e obter snapshot de transacoes longas, tamanho das tabelas afetadas e duracao.

### Flags relacionadas

No codigo desta baseline, `config/settings.py` define como padrao:

| Configuracao | Default | Efeito relevante |
|---|---:|---|
| `NIGHTOWL_JOB_AUTOMATIONS_ENABLED` | `false` | Mantem automacoes de jobs desligadas por padrao. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_MANUAL_APPLY_ENABLED` | `false` | Bloqueia aplicacao manual persistida por padrao. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_ENABLED` | `false` | Mantem scheduler de analise persistida desligado por padrao. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_SCHEDULER_DRY_RUN` | `true` | Modo dry-run por padrao, caso scheduler seja habilitado. |
| `NIGHTOWL_PERSISTED_TELEMETRY_ANALYSIS_FIRST_CAPTURE_POLICY` | `unconfigured` | Exige politica definida antes da primeira captura. |

Nao alterar estas configuracoes durante a restauracao ou teste de migrations. O escopo do plano nao inclui habilitar terminal administrativo, scheduler ou automacoes.

## Requisitos do laboratorio

- VM/host Linux descartavel com PostgreSQL 17, de preferencia mesma minor 17.10, locale, encoding, collation e extensoes do ambiente de origem. Confirmar versoes e extensoes com o administrador; nao assumir.
- Espaço livre suficiente para dump comprimido, banco restaurado, WAL, temporarios, indices e uma segunda copia descartavel. Os 4,29 GB do dump e 13 GB informados do banco nao determinam sozinhos o espaco necessario. Medir o tamanho logico, WAL e pico da restauracao antes de provisionar. Como estimativa inicial de planejamento, reservar ao menos 60 GB livres, revisar apos a medicao e nao criar recurso sem autorizacao.
- Recursos de CPU/RAM suficientes para restaurar sem pressao indevida; iniciar planejamento com 4 vCPU e 16 GiB RAM como estimativa, ajustar com base em tempos/carga observados. Nao e requisito medido.
- Armazenamento protegido e acesso restrito. O dump contem dados potencialmente sensiveis: nao deve entrar no Git, ambiente Codex, anexos, telemetria ou logs compartilhados.
- Rede isolada: negar rotas egress para producao, endpoints Windows, DC/DNS corporativo, FS e URLs/canais de agentes. Sem DNS corporativo ou internet durante ensaios. Se dependencias externas forem necessarias, usar mirror previamente aprovado e controlado. Nao iniciar Django web, workers, timers, scheduler, consumers ou tarefas agendadas; manter apenas o Postgres acessivel por loopback/rede privada do laboratorio.
- Nenhum segredo de producao. Criar roles locais descartaveis e credenciais unicas de laboratorio fora do repositorio. Restaurar sem ownership/ACL originais quando o objetivo for schema/dados, documentando que isso nao valida grants/roles da producao.
- Manter uma copia baseline imutavel e uma segunda base descartavel para aplicar/reverter migrations. Nao usar a unica restauracao para testes com MigrationExecutor ou downgrade.

## Procedimento de restauracao e ensaio

Os comandos abaixo sao um roteiro futuro. Executar somente em laboratorio explicitamente aprovado, apos validar os caminhos e nomes. Valores entre `<...>` sao placeholders; nao usar contra producao.

### 1. Preflight somente leitura no servidor de origem

Executar no host de origem do backup, somente em leitura. Estes comandos nao criam arquivos e nao conectam ao PostgreSQL de producao:

```bash
cd /opt/nightowl/backups/rc47-consolidated-20261111T133156Z-pr31-33
sha256sum -c SHA256SUMS
pg_restore --version
pg_restore --list postgresql-nightowl.dump > /dev/null
```

Precondicoes: operador autorizou acesso somente leitura ao backup e confirmou que `SHA256SUMS` cobre o dump. A listagem e descartada em `/dev/null`; nenhum caminho de laboratorio e presumido/criado na origem. Interromper se checksum falhar, versao do cliente for anterior/incompativel com o dump ou a lista apresentar erro. Para evitar qualquer escrita no host de origem, nao redirecionar a listagem para um arquivo nesse host.

Transferir o dump por canal aprovado diretamente para storage protegido do laboratorio; verificar SHA256 novamente no destino e preservar uma copia original somente leitura. Nao transferir via chat, worktree ou repositorio.

### 2. Preparar PostgreSQL no laboratorio isolado

Os comandos desta secao sao exclusivamente no host PostgreSQL do laboratorio, nunca no servidor de origem ou producao. Configurar o cluster de laboratorio para escutar apenas em loopback/rede isolada; neste exemplo, TCP `127.0.0.1:55432`. Confirmar `pg_hba.conf` com regra restrita para esse host/porta e role de laboratorio, coerente com `sslmode` escolhido. Preferir SCRAM e TLS quando houver conexao por rede; nao enfraquecer HBA para fazer o teste passar. Confirmar firewall sem rota para producao e ausencia de processos NightOwl ativos. Criar banco novo com nome exclusivo; todos os comandos abaixo fixam host, porta, role e banco de laboratorio:

```bash
sudo -u postgres createuser --login --pwprompt -p 55432 nightowl_lab_migrator
sudo -u postgres createdb -p 55432 --owner=nightowl_lab_migrator nightowl_restore
```

Antes da restauracao, criar no host de laboratorio um arquivo PGPASSFILE protegido por permissao `0600` (fora de Git), com entradas restritas a `127.0.0.1:55432:nightowl_restore:nightowl_lab_migrator` e, depois, `127.0.0.1:55432:nightowl_migration_test:nightowl_lab_migrator`; ou usar outro mecanismo seguro aprovado. A autenticacao deve corresponder a regra de `pg_hba.conf` que cobre exatamente conexao TCP loopback, role e banco; nunca passar senha em argumentos nem habilitar `trust`. Guardar senha apenas no gestor de segredos do laboratorio e nao a registrar no shell history. Se o dump requerer extensoes/roles adicionais, parar e revisar a lista com o responsavel; nao criar superuser, nao importar roles ou credenciais de producao. Restaurar sem owner/ACL faz os objetos pertencerem ao role local da conexao:

```bash
PGPASSFILE=/secure/lab/pgpass pg_restore \
  --host=127.0.0.1 --port=55432 --username=nightowl_lab_migrator \
  --dbname=nightowl_restore --exit-on-error --verbose --no-owner --no-privileges \
  /secure/lab/postgresql-nightowl.dump \
  2> /secure/lab/pg-restore.log
```

`127.0.0.1:55432/nightowl_restore` e explicitamente o cluster/banco descartavel do laboratorio; nao substituir esses valores por host, porta, role ou database de producao. O log pode conter nomes de objetos e erros; manter restrito, revisar/redigir antes de compartilhar. Interromper no primeiro erro. Nao repetir sobre base parcialmente restaurada: preservar log e criar novo banco descartavel apos diagnostico/aprovacao.

### 3. Validar restauracao

No laboratorio, conectar explicitamente ao banco restaurado, com PGPASSFILE/HBA ja validados; nunca executar estas consultas no banco de producao como parte deste roteiro:

```bash
PGPASSFILE=/secure/lab/pgpass psql \
  --host=127.0.0.1 --port=55432 --username=nightowl_lab_migrator \
  --dbname=nightowl_restore --set=ON_ERROR_STOP=1
```

No prompt `psql` dessa conexao de laboratorio, registrar sem conteudo de linhas:

```sql
SELECT current_database(), current_user, inet_server_addr(), inet_server_port();
SELECT version();
SELECT current_database(), pg_size_pretty(pg_database_size(current_database()));
SELECT extname, extversion FROM pg_extension ORDER BY extname;
SELECT app, name, applied FROM django_migrations
 WHERE app = 'agents' AND name IN (
   '0047_persistedtelemetryanalysis_and_more',
   '0048_separate_admin_terminal_grant',
   '0049_persisted_telemetry_schedule',
   '0050_telemetry_scheduler_health'
 ) ORDER BY name;
```

Comparar a lista exata com `showmigrations`. Validar schemas, tabelas e contagens agregadas contra metadados/relatorio obtidos pelo operador na origem, sem extrair nomes de pessoas, tokens ou payloads. Verificar `pg_amcheck` se instalado e permitido, e revisar se constraints/indices estao validos. Registrar duracao, tamanho final, warnings e checksums do artefato; nao enviar dados brutos.

GO para continuar somente se o restore terminou sem erro, as extensoes necessarias existem, checks de integridade passam e a migration 0047 esta aplicada. Se 0047 estiver ausente, houver migration divergente, contagens inesperadas ou erro de integridade, interromper e reconciliar a origem do dump antes de prosseguir.

### 4. Clonar e aplicar migrations

Manter `nightowl_restore` como baseline imutavel. Criar uma copia independente para o ensaio, por exemplo `nightowl_migration_test`, no cluster loopback `127.0.0.1:55432`, enquanto nao ha conexoes na origem do template. Somente o administrador local do cluster isolado deve criar a copia; definir o owner como `nightowl_lab_migrator`. Se nao for possivel clonar com consistencia, restaurar novamente o mesmo dump em um novo banco descartavel. Nao aplicar downgrade na baseline.

```bash
sudo -u postgres createdb -p 55432 --template=nightowl_restore \
  --owner=nightowl_lab_migrator nightowl_migration_test
```

No checkout limpo da main RC47 `b8b417f520290db7d0b86af71feb86fccd9bf12b`, em ambiente Python isolado. Em cada shell destinado aos comandos Django, fixar explicitamente `DATABASE_URL` e `PGPASSFILE` para o banco descartavel do laboratorio (sem senha na URL). Exemplo:

```bash
export DATABASE_URL='postgres://nightowl_lab_migrator@127.0.0.1:55432/nightowl_migration_test?sslmode=prefer'
export PGPASSFILE=/secure/lab/pgpass
```

Confirmar a URL efetiva, host, porta e nome do banco antes de qualquer comando de escrita; se nao corresponderem exatamente ao alvo isolado, interromper. Nunca copiar uma URL de producao para este roteiro. Entao executar:

```bash
git rev-parse HEAD
python manage.py showmigrations agents
python manage.py migrate --plan
```

Precondicao: `HEAD` exatamente no SHA aprovado; URL/host do banco confirmado como isolado; nenhuma credencial de producao; flags desligadas. O plano deve conter somente a cadeia pendente esperada 0048, 0049 e 0050 (ou explicar qualquer outra migration antes). Interromper se houver migrations inesperadas, conexao fora da rede isolada ou 0047 nao aplicada.

Aplicar apenas no banco descartavel. Limites sao exemplo operacional conservador, devem ser aprovados pelo DBA:

```bash
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=120s' \
  DATABASE_URL='postgres://nightowl_lab_migrator@127.0.0.1:55432/nightowl_migration_test?sslmode=prefer' \
  PGPASSFILE=/secure/lab/pgpass \
  python manage.py migrate agents 0050_telemetry_scheduler_health --noinput
```

Um lock timeout deve abortar a transacao e ser investigado; nao aumentar limites indiscriminadamente. Registrar tempo e SQL/log sanitizado. Depois:

```bash
python manage.py showmigrations agents
python manage.py migrate --plan
python manage.py check
python manage.py makemigrations --check --dry-run
```

Verificar a tabela `agents_persistedtelemetryanalysisschedule`, indices, colunas de `TerminalAccessGrant` e `AutomationSchedulerHealth`, nullability/defaults e consistencia das migrations. Executar testes Django pertinentes com base de teste separada derivada do snapshot restaurado, sempre isolada; jamais apontar a suite para a baseline preservada. Nao iniciar scheduler. Usar apenas registros sinteticos identificaveis, sem acionar rede/agentes.

### 5. Compatibilidade e rollback de ensaio

Em clones descartaveis separados, validar pelo menos:

1. Codigo RC47 com schema 0050.
2. Codigo antigo da baseline implantada informado pelo operador contra schema 0050: executar leitura e INSERT/UPDATE com dados sinteticos nos modelos compartilhados afetados, especialmente `TerminalAccessGrant` e `AutomationSchedulerHealth`, sem enviar os novos campos (`allow_admin_auth` e os campos de health). Confirmar que o ORM/codigo antigo funciona com as colunas NOT NULL e defaults realmente existentes. Tambem testar operacoes antigas em `AgentMachine` com a tabela OneToOne adicional presente. `PersistedTelemetryAnalysisSchedule` nao existe no codigo antigo; confirmar que o codigo antigo nao precisa acessa-la e que a tabela pode permanecer sem uso. Isso determina se rollback apenas de codigo e seguro.
3. Upgrade desde 0047, downgrade `0050 -> 0047`, e verificacao explicita de dados perdidos no clone. A reversao de 0049 descarta todos os schedules, de 0050 descarta valores dos campos novos e de 0048 descarta `allow_admin_auth`.

Comando de downgrade exclusivamente no clone descartavel, apos snapshot adicional e autorizacao do responsavel pelo laboratorio:

```bash
DATABASE_URL='postgres://nightowl_lab_migrator@127.0.0.1:55432/nightowl_migration_test?sslmode=prefer' \
PGPASSFILE=/secure/lab/pgpass \
  python manage.py migrate agents 0047_persistedtelemetryanalysis_and_more --noinput
```

Nao usar este comando em producao. O rollback operacional preferencial pode ser restaurar backup consistente em ambiente aprovado; downgrade de schema nao substitui recuperacao de dados. Se ja houve escrita sob schema novo, voltar codigo sem reverter schema so e aceitavel depois que o teste de compatibilidade confirmar que o codigo antigo tolera as colunas/tabelas novas. Se nao, e necessario plano de manutencao e restauracao, considerando que restaurar o backup anterior perde todas as escritas posteriores ao ponto de backup.

## Plano futuro de recuperacao e rollback operacional

Antes de qualquer deploy real, exigir: backup PostgreSQL recente, checksum verificado, restore integral ensaiado, RPO/RTO aceitos, janela de manutencao, responsavel com permissao, duracao medida de migrations e snapshot do estado de migrations. Confirmar no host a versao/sha, conexoes longas, espaco, WAL, replicacao (se houver), disponibilidade e compatibilidade de codigo.

Nao presumir que dump custom e backup Veeam sao intercambiaveis. Restaurar dump recupera o banco ate o instante/logico representado pelo dump. Restore de VM pode reverter sistema e dados juntos e perder escritas posteriores. Qualquer PITR depende de arquivamento WAL e configuracao comprovados.

Em caso de falha de migration em producao: nao executar repetidamente nem fazer downgrade improvisado. Parar no checkpoint, preservar logs, estado de `django_migrations`, dump/WAL e diagnostico; decidir com DBA entre correcao revisada e restauracao. Uma migration pode ter operacoes transacionais e outras nao; verificar o SQL real e estado parcial no PG17 antes de definir recuperacao.

## Veeam e Proxmox — dados ainda necessarios

Nao ha evidencia nesta worktree de que o restore point Veeam possa ser restaurado diretamente para Proxmox. Confirmar com a equipe de infraestrutura:

- Produto/versao Veeam, tipo do job (VM inteira, agente, imagem/disco ou backup de arquivos), modo de consistencia e timestamp do ultimo restore point.
- Resultado e data do ultimo teste de restore; protecao contra ransomware/imutabilidade, criptografia e quem detem a chave de restore.
- RPO/RTO aprovado, tamanho do repositorio e capacidade livre para restore paralelo.
- Opcoes habilitadas: SureBackup/lab isolado, instant recovery, exportacao de disco/OVF/VMDK/VHDX ou restauracao file-level; formato e destino suportados.
- Versao Proxmox, formato de disco aceito, drivers/UEFI/TPM, procedimento validado de importacao e isolamento de NIC antes do primeiro boot.
- Se a restauracao deve ocorrer em sandbox Veeam ou Proxmox e quem aprova/acompanha. Nao iniciar VM restaurada conectada a rede corporativa.

GO para confiar no Veeam somente apos um teste documentado de recuperacao isolada e verificacao de aplicacao/banco. Suporte comercial a um formato nao e prova de procedimento testado neste ambiente.

## Criterios GO / NO-GO

### GO para ensaio isolado

- Hash do dump validado na origem e destino; `pg_restore --list` legivel por cliente compativel.
- Rede isolada testada por regra/rotas, sem acesso a agentes, producao ou FS.
- Restore integral conclui; integridade e extensoes verificadas.
- Snapshot baseline preservado; teste ocorre em clone descartavel.
- Main/SHA e plano de migrations revisados; migration plan contem exatamente o esperado.
- Flags de scheduler/automacoes mantidas desligadas e nenhum worker/timer iniciado.

### GO para futura migration em producao (nao concedido por este documento)

- Restore completo comprovado em ambiente isolado e rollback/RPO/RTO aprovado.
- Duracao/locks observados em copia representativa e janela aprovada pelo DBA.
- Compatibilidade de deploy em ordem definida (codigo antigo/novo e schema) demonstrada.
- Backup imediatamente anterior e capacidade de restauracao verificados.
- `migrate --plan` diretamente no host aprovado e revisado antes de qualquer execucao.
- Autorizacao operacional especifica. Este documento nao e essa autorizacao.

### NO-GO imediato

Checksum/restore/integridade falha; fonte ou destino nao comprovadamente isolado; migration inesperada; baseline difere; locks/tempo excedem janela; capacidade insuficiente; flags alteradas; falta de backup restauravel; ou incerteza sobre Veeam/Proxmox material ao plano de recuperacao.

## Checklist do operador

- [ ] Confirmar o SHA de producao diretamente no host e registrar hora/fonte; nao presumir `6090228...`.
- [ ] Confirmar migrations aplicadas e `migrate --plan` no checkout em execucao.
- [ ] Confirmar SHA RC47 exato `b8b417f520290db7d0b86af71feb86fccd9bf12b`.
- [ ] Confirmar backup local, metadados, hashes e que dump nao foi alterado.
- [ ] Obter do Veeam o restore point e evidencia do restore teste; esclarecer formato para Proxmox.
- [ ] Obter recursos/espaco medidos e aprovacao para laboratorio isolado; nao criar recursos implicitamente.
- [ ] Configurar bloqueio de rede e provar que clientes/servidor/FS nao sao alcancaveis.
- [ ] Restaurar dump completo em banco isolado e registrar evidencias sem dados pessoais.
- [ ] Preservar snapshot 0047 e executar migrations somente em clone.
- [ ] Executar verificacoes Django, integridade, compatibilidade de codigo antigo e downgrade destrutivo em clones independentes.
- [ ] Registrar resultado, logs sanitizados, duracao, lock waits e decisao GO/NO-GO.
- [ ] Manter scheduler, automacoes, workers e flags desabilitados durante o ensaio.

## Evidencias desta entrega

**Realizadas no codigo/worktree:** revisao das operacoes e dependencias de 0048, 0049 e 0050; leitura dos defaults relacionados em `config/settings.py`; confirmacao de branch baseada na main indicada; confirmacao de ausencia de `psql`, `pg_restore`, Docker e Podman locais.

**Nao realizadas:** leitura do host de producao, leitura/validacao independente do backup, restauracao integral, criacao de VM/rede/banco, execucao PostgreSQL das migrations, testes de lock concorrente, teste com codigo antigo, downgrade, verificacao do Veeam, execucao de `manage.py` apontada a producao ou qualquer mudanca operacional.

**Bloqueio para declarar homologacao PostgreSQL:** falta runtime/cliente PG17 nesta estacao e falta ambiente restaurado isolado. SQLite nao substitui validacao de locks, DDL ou concorrencia do PostgreSQL.
