# Night Owl - Deploy Linux

Este guia prepara o Night Owl para rodar em um servidor Linux com Django, Gunicorn e Nginx. Ele ainda nao configura systemd, Nginx, HTTPS ou PostgreSQL em producao; esses passos ficam para a etapa de infraestrutura.

## 1. Clonar o repositÃ³rio

```bash
sudo mkdir -p /opt/nightowl
sudo chown "$USER":"$USER" /opt/nightowl
cd /opt/nightowl
git clone <URL_DO_REPOSITORIO> .
```

## 2. Criar ambiente Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configurar variÃ¡veis de ambiente

```bash
cp .env.example .env
nano .env
```

Edite pelo menos:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL` ou as variÃ¡veis `POSTGRES_*`, se optar por PostgreSQL

O arquivo `.env` contem segredos e nao deve ser versionado.

## 4. Validar Django

```bash
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 5. Testar com Gunicorn

```bash
gunicorn --bind 127.0.0.1:8010 config.wsgi:application
```

Depois, em outro terminal do servidor:

```bash
curl http://127.0.0.1:8010/
```

## 6. Static e media

O projeto estÃ¡ configurado com:

- `STATIC_URL=/static/`
- `STATIC_ROOT=BASE_DIR/staticfiles`
- `MEDIA_URL=/media/`
- `MEDIA_ROOT=BASE_DIR/media`
- Whitenoise para servir static files de forma simples quando aplicÃ¡vel

Em produÃ§Ã£o com Nginx, o ideal Ã© mapear:

```nginx
location /static/ {
    alias /opt/nightowl/staticfiles/;
}

location /media/ {
    alias /opt/nightowl/media/;
}
```

## 7. Rotinas de manutenÃ§Ã£o

O comando central de rotinas operacionais pode ser executado manualmente:

```bash
python manage.py run_maintenance_tasks
```

Agendamento real fica para etapa futura. Exemplos futuros:

Linux cron:

```cron
*/5 * * * * /opt/nightowl/.venv/bin/python /opt/nightowl/manage.py run_maintenance_tasks
```

Windows Task Scheduler:

```powershell
python manage.py run_maintenance_tasks
```

## 8. Release e publicacao do agente Windows

Use um unico pipeline para gerar release, ZIP, `version.json`, `checksums.json` e `release-manifest.json`. Nao edite versao ou checksum manualmente.

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.0.8
```

A saida fica em:

```text
artifacts\nightowl-agent\releases\0.1.0.8\
```

Para validar uma release ja gerada:

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.0.8 -ValidateOnly
```

Para publicar localmente no diretorio servido pelo Nginx:

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.0.8 -Publish -PublishPath /opt/nightowl/downloads/agent/windows
```

Para publicar em um host Linux via SSH/SCP:

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.0.8 -Publish -PublishHost root@nightowl.controlsul.com.br -PublishPath /opt/nightowl/downloads/agent/windows
```

A publicacao copia primeiro para um diretorio temporario, valida o ZIP no destino, move para `releases/<versao>` e atualiza `version.json` por ultimo. Se falhar antes do `version.json`, a versao publica anterior continua ativa.

### Politica de versao do agente Windows

O updater aplica uma atualizacao somente quando a versao publicada em `version.json` for maior que a versao instalada em `agent.version.json`/`agent.config.json`, ou quando o manifesto vier com `force=true`.

Use o formato numerico de quatro partes:

```text
0.1.0.x
```

Para qualquer novo pacote, mesmo uma correcao pequena de icone, script ou Tray, incremente a ultima parte. O pipeline bloqueia downgrade e reutilizacao de versao; `-Force` deve ficar restrito a desenvolvimento local.

Depois de publicar no servidor, valide no endpoint:
Depois de publicar no servidor, valide no endpoint:

```powershell
Get-Content "C:\ProgramData\NightOwl\AgentDotNet\agent.version.json"
Invoke-RestMethod "https://nightowl.controlsul.com.br/downloads/nightowl-agent/version.json"
Get-Content "C:\ProgramData\NightOwl\Logs\agent-updater.jsonl" -Tail 80
```

## 9. PrÃ³ximos passos de infraestrutura

Ainda ficam para uma prÃ³xima etapa:

- unit file do `systemd` para Gunicorn
- socket ou service do Gunicorn
- configuraÃ§Ã£o real do Nginx
- HTTPS com certificado
- PostgreSQL real no servidor
- backup do banco e da pasta `media`
- timer de manutenÃ§Ã£o em produÃ§Ã£o

## 9.1 Release candidata para piloto controlado

Versao candidata atual:

```text
0.1.1.0-rc36
```

Esta release deve nascer em `development`, com `rollout_paused=true` e `rollout_percentage=0`. Ela nao deve sobrescrever a release publica `stable/latest` nem criar jobs automaticamente.

### Windows de desenvolvimento

Validacao completa antes de gerar a candidata:

```powershell
dotnet clean
dotnet restore
dotnet build -c Release
dotnet test -c Release
powershell -ExecutionPolicy Bypass -File tests\e2e\Test-NightOwlAgentE2E.ps1 -Mode Simulated -NonInteractive
```

Gerar a release candidata:

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.1.0-rc1 -Channel development
```

Validar a release ja gerada:

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.1.0-rc1 -ValidateOnly
```

Publicar por SSH sem promover para latest:

```powershell
.\scripts\Build-NightOwlAgentRelease.ps1 -Version 0.1.1.0-rc1 -Channel development -Publish -PublishHost root@nightowl.controlsul.com.br -PublishPath /opt/nightowl/downloads/agent/windows
```

Para RC em `development`, o script publica em:

```text
/opt/nightowl/downloads/agent/windows/releases/0.1.1.0-rc1/
```

e preserva:

```text
/opt/nightowl/downloads/agent/windows/version.json
```

### Ubuntu de producao

Antes do deploy do backend:

```bash
cd /opt/nightowl
sudo -u postgres pg_dump nightowl > "/opt/nightowl/backups/nightowl-$(date -u +%Y%m%dT%H%M%SZ).sql"
sudo tar -czf "/opt/nightowl/backups/nightowl-media-static-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" media staticfiles
```

Atualizar codigo e dependencias:

```bash
cd /opt/nightowl
git pull
source .venv/bin/activate
pip install -r requirements.txt
```

Validar migrations antes de aplicar:

```bash
python manage.py check
python manage.py makemigrations --check
python manage.py showmigrations
python manage.py migrate --plan
```

Aplicar deploy:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart nightowl
sudo systemctl status nightowl --no-pager
```

Health checks:

```bash
curl -kI https://nightowl.controlsul.com.br/dashboard/
curl -kI https://nightowl.controlsul.com.br/downloads/nightowl-agent/version.json
curl -kI https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc1/version.json
```

Importar a release candidata no backend:

```bash
python manage.py import_agent_release \
  --version 0.1.1.0-rc1 \
  --channel development \
  --version-json https://nightowl.controlsul.com.br/downloads/nightowl-agent/releases/0.1.1.0-rc1/version.json \
  --release-notes "Release candidata para piloto controlado. Rollout pausado."
```

Confirmar no painel:

- release em `development`;
- status `paused`;
- rollout `0%`;
- nenhum `update_agent` criado automaticamente.

### Endpoint Windows de desenvolvimento

Validar endpoint candidato:

```powershell
Get-Service NightOwlAgentDotNet
Get-ScheduledTask -TaskName "NightOwl Agent Tray"
Get-Content "C:\ProgramData\NightOwl\AgentDotNet\agent.version.json"
Get-Content "C:\ProgramData\NightOwl\Logs\agent-dotnet.jsonl" -Tail 80
Get-Content "C:\ProgramData\NightOwl\Logs\agent-updater.jsonl" -Tail 120
Get-Content "C:\ProgramData\NightOwl\State\update-state.json" -ErrorAction SilentlyContinue
```

Para testar diagnostico local:

```powershell
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Diagnostics.exe" collect -NonInteractive
```

### VM Windows de testes

Executar apenas em VM descartavel:

```powershell
powershell -ExecutionPolicy Bypass -File tests\e2e\Test-NightOwlAgentE2E.ps1 -Mode WindowsVm -AllowDestructive -NonInteractive
```

Cenarios minimos antes de promover:

- instalacao limpa;
- enrollment;
- heartbeat;
- ping;
- collect_disks;
- update normal;
- rollback simulado por falha pos-troca;
- repair;
- uninstall normal;
- reinstall preservando endpoint.

### Promocao development para pilot

Promover somente quando houver evidencia de endpoint real:

1. release importada em `development`;
2. endpoint de desenvolvimento atualizado com sucesso;
3. `agent.config.json`, machine_id e token preservados;
4. Tray presente;
5. heartbeat recuperado;
6. inventario e jobs basicos OK;
7. Diagnostics gerado sem segredos;
8. sem `rollback_failed`;
9. sem fila pendente crescente;
10. decisao administrativa registrada.

No painel, promover para `pilot`, manter rollout pausado, selecionar grupo `Pilot` e aumentar rollout gradualmente.

### Rollback do backend

Se o deploy do backend falhar antes das migrations, reverta o codigo e reinicie:

```bash
git reset --hard <commit_anterior>
sudo systemctl restart nightowl
```

Se migrations foram aplicadas, restaurar backup em janela de manutencao. Nao usar banco de producao para testes.

### Rollback da release

Para bloquear novas entregas:

- pausar rollout; ou
- revogar a release no painel.

Revogacao:

- nao entrega para novos endpoints;
- cancela jobs ainda nao iniciados;
- nao executa downgrade automatico;
- endpoints ja atualizados devem ser avaliados manualmente.

## 10. NightOwl Agent Windows - Icon and Tray Asset

O agente Windows usa um unico icone visual principal:

```text
assets/icons/NightOwl.ico
```

O arquivo aprovado de origem fica arquivado em:

```text
assets/nightowl/icon - novo/
```

Os projetos `NightOwl.Agent.Windows`, `NightOwl.Agent.Tray` e `NightOwl.Agent.Updater` referenciam esse caminho canonico por `..\assets\icons\NightOwl.ico` e publicam o arquivo como:

```text
assets/icons/NightOwl.ico
```

O `.ico` contem as resolucoes aprovadas para tray/taskbar. Nao ha variantes de status, overlays ou icones coloridos por estado. O Tray deve usar sempre `NightOwl.ico`.

Para substituir o icone no futuro, copie o novo arquivo aprovado para:

```powershell
Copy-Item "assets\nightowl\icon - novo\NightOwl.ico" "assets\icons\NightOwl.ico" -Force
```

O ZIP publicado precisa conter:

```text
assets/icons/NightOwl.ico
NightOwl.Agent.Windows.exe
NightOwl.Agent.Tray.exe
NightOwl.Agent.Updater.exe
```

Para gerar o pacote de download no workspace:

```powershell
powershell -ExecutionPolicy Bypass -File NightOwl.Agent.Windows\scripts\Publish-NightOwlAgentDownload.ps1
```

Depois, no servidor Linux:

```bash
sudo /opt/nightowl/scripts/publish-nightowl-agent-downloads.sh
```

Validacoes no servidor:

```bash
curl -kI https://nightowl.controlsul.com.br/downloads/nightowl-agent/NightOwl.Agent.Windows.zip
curl -kI https://nightowl.controlsul.com.br/downloads/nightowl-agent/version.json
curl -kI https://nightowl.controlsul.com.br/downloads/nightowl-agent/checksums.json
unzip -l /opt/nightowl/downloads/agent/windows/NightOwl.Agent.Windows.zip | grep -E "assets[\\/]icons[\\/]NightOwl.ico|NightOwl.Agent.Tray.exe|NightOwl.Agent.Updater.exe"
```

Validacoes no endpoint Windows:

```powershell
Get-Service NightOwlAgentDotNet
Get-ScheduledTask -TaskName "NightOwl Agent Tray"
Get-ChildItem "C:\ProgramData\NightOwl\AgentDotNet\assets\icons"
Test-Path "C:\ProgramData\NightOwl\AgentDotNet\assets\icons\NightOwl.ico"
Get-Process | Where-Object { $_.ProcessName -like "*NightOwl*" }
Get-Content "C:\ProgramData\NightOwl\Logs\agent-dotnet.jsonl" -Tail 80
Get-Content "C:\ProgramData\NightOwl\Logs\agent-tray.jsonl" -Tail 80
```

Checklist visual:

- validar bandeja expandida e recolhida;
- validar barra de tarefas;
- validar Windows Explorer;
- validar Gerenciador de Tarefas;
- validar tema claro e escuro;
- validar escala 100%, 125% e 150%;
- em `16x16`, o icone deve parecer uma cabeca/olhos de coruja forte, nao uma borboleta;
- em `24x24`, a leitura da coruja deve estar clara;
- em `32x32+`, o icone deve manter aparencia profissional.

## 11. NightOwl Agent Windows - Update Flow

O atualizador oficial fica no pacote como:

```text
NightOwl.Agent.Updater.exe
```

Comandos suportados no endpoint Windows:

```powershell
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Updater.exe" status
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Updater.exe" check
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Updater.exe" update
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Updater.exe" rollback
```

O comando `check` baixa:

```text
{ServerUrl}/downloads/nightowl-agent/version.json
```

O comando `update` baixa o ZIP, valida `checksums.json`, extrai em staging, cria backup da instalacao atual, para o servico, fecha o Tray, copia os novos arquivos, preserva `agent.config.json` e `agent-dotnet.state.json`, reinicia o servico e tenta reiniciar o Tray.

Diretorios usados:

```text
C:\ProgramData\NightOwl\Updates\Downloads
C:\ProgramData\NightOwl\Updates\Staging
C:\ProgramData\NightOwl\Backups
C:\ProgramData\NightOwl\Logs\agent-updater.jsonl
```

Arquivos preservados em update/reinstalacao:

```text
agent.config.json
agent-dotnet.state.json
agent_token
machine_id
endpoint_id
logs
packages/jobs locais
```

O Tray expoe uma atualizaÃ§Ã£o local/manual simples, mas as aÃ§Ãµes tecnicas continuam no painel. O menu visual fica restrito a:

- Abrir NightOwl
- Status do agente
- Atualizar agente
- Reiniciar agente
- Sobre

A atualizaÃ§Ã£o remota/manual deve ser enviada pelo painel web do NightOwl, na tela de detalhe do endpoint, pelo botÃ£o **Atualizar agente**. Esse botÃ£o cria um job tecnico `update_agent` com payload controlado:

```json
{
  "target_version": "latest",
  "channel": "stable",
  "force": false,
  "source": "manual_panel"
}
```

Quando o agente recebe `update_agent`, ele executa:

```powershell
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Updater.exe" update --source job --job-id "<job_id>" --channel stable --target-version latest --quiet --json-output
```

Se a atualizacao reiniciar o servico antes do resultado ser enviado, o updater grava o resultado pendente em:

```text
C:\ProgramData\NightOwl\Jobs\pending-update-result.json
```

Ao iniciar, o servico `NightOwlAgentDotNet` tenta enviar esse resultado para `/api/agent/jobs/result/`. Em sucesso, o arquivo e movido para `C:\ProgramData\NightOwl\Jobs\completed\`.

No Tray, o item **Atualizar agente** executa localmente:

```powershell
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Updater.exe" update --source tray --interactive
```

O Windows pode solicitar UAC. O progresso detalhado fica em:

```text
C:\ProgramData\NightOwl\Logs\agent-tray.jsonl
C:\ProgramData\NightOwl\Logs\agent-updater.jsonl
```

O `version.json` publico deve conter `packageUrl`, `checksumUrl`, `installerUrl`, `minimumSupportedVersion`, `requiresRestart`, `force` e notas da versao. O `checksums.json` deve conter `sha256` e `size` para os arquivos publicados.

Checklist de validacao do update pelo painel:

1. Instalar uma versao atual do agente.
2. Publicar uma versao nova em `/downloads/nightowl-agent/`.
3. Abrir o endpoint no painel.
4. Clicar em **Atualizar agente**.
5. Confirmar que o job `update_agent` aparece na fila.
6. Confirmar nos logs `job.update_agent.received`.
7. Confirmar execucao do updater e checksum valido.
8. Confirmar restart do servico.
9. Confirmar que o resultado voltou ao painel como concluido, ja atualizado ou falha amigavel.
10. Confirmar preservacao de `agent.config.json`, `agent-dotnet.state.json`, `machine_id` e token.

## 12. NightOwl Agent Windows - Install, Repair, Reinstall and Uninstall

O update normal continua sendo responsabilidade exclusiva do `NightOwl.Agent.Updater.exe`. O instalador e o repair nao criam `update_id`, nao enviam resultado de job `update_agent` e nao alteram `C:\ProgramData\NightOwl\State\update-state.json`.

Estrutura persistente atual:

```text
C:\ProgramData\NightOwl\Config\agent.config.json
C:\ProgramData\NightOwl\Identity\agent.identity.json
C:\ProgramData\NightOwl\State\agent.state.json
C:\ProgramData\NightOwl\State\update-state.json
C:\ProgramData\NightOwl\State\pending-results\
C:\ProgramData\NightOwl\Logs\
C:\ProgramData\NightOwl\Diagnostics\
C:\ProgramData\NightOwl\Updates\Staging\
C:\ProgramData\NightOwl\Updates\Backup\
C:\ProgramData\NightOwl\Updates\Pending\
```

Comandos principais:

```powershell
# Instalacao limpa ou padrao
.\Install-NightOwlAgentDotNet.ps1 -Install -ServerUrl "https://nightowl.controlsul.com.br" -InstallAsService -RunCheck

# Repair idempotente
.\Install-NightOwlAgentDotNet.ps1 -Repair -ServerUrl "https://nightowl.controlsul.com.br" -InstallAsService -RunCheck

# Reinstalacao administrativa preservando identidade
.\Install-NightOwlAgentDotNet.ps1 -Reinstall -ServerUrl "https://nightowl.controlsul.com.br" -InstallAsService -RunCheck

# Recuperacao manual apos rollback_failed
.\Install-NightOwlAgentDotNet.ps1 -Repair -ForceRecovery -Force -ServerUrl "https://nightowl.controlsul.com.br" -InstallAsService -RunCheck

# Uninstall normal preservando identidade e estado
.\Uninstall-NightOwlAgentDotNet.ps1

# Purge explicito: remove identidade, estado, logs e exige novo enrollment
.\Uninstall-NightOwlAgentDotNet.ps1 -Purge
```

Regras de seguranca:

- `Repair` e `Reinstall` adquirem o lock global do updater e bloqueiam se houver update/rollback ativo.
- `rollback_failed` bloqueia `Repair`/`Reinstall` sem `-ForceRecovery`.
- Config, Identity, State, Logs, Diagnostics, Updates, backups e pending-results sao preservados por `Repair`, `Reinstall` e uninstall normal.
- `Purge` nunca e implicito e exige confirmacao, ou `-Purge -NonInteractive -Force`.
- JSON invalido ou conflito de identidade e preservado como `.preserved-<timestamp>` antes de qualquer correcao.
- Relatorios sanitizados sao gravados em `C:\ProgramData\NightOwl\Diagnostics\*-report-<timestamp>.json`.

## 13. NightOwl Agent Windows - Local Diagnostics

O pacote oficial inclui o utilitario local read-only:

```text
C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Diagnostics.exe
```

Uso padrao:

```powershell
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Diagnostics.exe" collect
```

Saida:

```text
C:\ProgramData\NightOwl\Diagnostics\NightOwl-Diagnostics-<hostname>-<timestamp UTC>.zip
```

Opcoes:

```powershell
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Diagnostics.exe" collect -IncludeWindowsEvents
& "C:\ProgramData\NightOwl\AgentDotNet\NightOwl.Agent.Diagnostics.exe" collect -OutputPath C:\Temp -NoNetworkTests
```

O diagnostico nao inicia/paralisa servico, nao executa repair, nao dispara update, nao envia pending-results e nao faz enrollment. Ele coleta resumos sanitizados de sistema, versoes, servico, filesystem, ACLs, config/identity, updater, jobs, pending-results, logs recentes, eventos opcionais e conectividade publica sem Authorization.

O ZIP contem, entre outros:

```text
summary.txt
summary.json
versions.json
service.json
system.json
filesystem.json
permissions.json
config-summary.json
identity-summary.json
update-state.sanitized.json
updates-summary.json
jobs-summary.json
pending-results-summary.json
connectivity.json
warnings.json
logs/
events/
manifest.json
```

Regras:

- tokens, Authorization, cookies, senhas, enrollment e queries sensiveis sao redigidos como `[REDACTED]`;
- logs sao limitados e sanitizados linha a linha;
- falha em uma fonte vira warning e nao cancela o pacote;
- exit code `0` significa pacote sem erro critico; `1` significa pacote criado com warnings.

## 14. NightOwl Agent - roadmap e contexto atual

Este bloco registra o estado canonico apos o fechamento dos canarios da Fase 4.

Roadmap atual:

| Fase | Escopo | Status |
| --- | --- | --- |
| 0A | CI / publishing independente da maquina do desenvolvedor | CLOSED |
| 0B | Automatic secure trusted-key bundle sync | CLOSED |
| 1 | ACLs por SID | CLOSED |
| 2 | Git hygiene / build reproducibility | CLOSED |
| 3 | Internal release pipeline | CLOSED |
| 4 | Updater / rollback / observability local / lifecycle resilience | CLOSED |
| 5 | Secrets / configuration hardening | CLOSED |
| 6 | Fleet rollout / policies | IN PROGRESS |
| 7 | Central observability | PENDING |
| 8 | Wider RMM / Desk | PENDING |

`PHASE_4_LIFECYCLE_HARDENING = CLOSED`

Baseline atual do agente:

```text
Agent baseline: 0.1.1.0-rc36
Agent baseline commit: 7bf20ba3cefda76731abd97204fa918e4e37bdbc
```

RC36 permanece a release de referencia dos canarios finais da Fase 4.
A Fase 5 introduziu hardening local do agente Windows em commit posterior a RC36 e,
por isso, exigiu uma nova release para chegar aos endpoints.

Release de agente publicada para canario da Fase 5:

```text
Agent release: 0.1.1.0-rc38
Agent release id: 50ddcb17-e579-498d-8ada-8b89e693cc6d
Agent release commit: 1e8f24571a6a180804670db4a7fe603cfd956d1d
Channel: development
Status: paused
Rollout percentage: 0
Rollout paused: true
ZIP SHA256: 9d74cdf77b10a4a6764863dbd44d54daf076f74ecb04d865192a8284f80e90c2
ZIP size: 71932696
Signature: RSA-PSS-SHA256 valid
Legacy unsigned: false
```

RC38 foi publicada pelo publisher oficial usando artefato local previamente validado.
O upload versionado, URLs publicas, import Django e `verify_agent_release` passaram.
`stable/latest` nao foi alterado, nenhum job foi criado e nenhum rollout foi iniciado.

Release final de agente validada para fechamento da Fase 5:

```text
Agent release: 0.1.1.0-rc39
Agent release id: 6a699546-ebd0-4788-af77-5f846b66a618
Agent release commit: b865f313d7a48e69cd18d52c6090c450da24c563
Build id: a06c9f7aad714308aeebab0facbee323
Channel: development
Status: paused
Rollout percentage: 0
Rollout paused: true
ZIP SHA256: 8f355d178cbf7d5a64aadbb2a4b4771985a8b92a1c1e8d740f1b6c61dda37d2b
Signature: RSA-PSS-SHA256 valid
Legacy unsigned: false
```

RC39 permanece em `development`, pausada e com rollout `0`. Ela nao promoveu
`stable/latest`, nao criou jobs automaticos e nao foi promovida para pilot/stable.

Phase 5 status:

```text
PHASE_5_STATUS = CLOSED
READY_FOR_PHASE6 = true
```

Blocos de Phase 5 ja implementados:

- `fd2173817caddca0bb895ff80ff9696291db8af8`: `security_preflight` inicial para configuracao de seguranca.
- `1dd474d`: ajuste de preflight estrito.
- `937d6a`: diagnostico seguro de matches de segredo sem expor valores.
- `712ec42a9d511b846435c9c017c41cf4dd9dc9a8`: remocao de segredos versionados e bloqueio de export plaintext de agent tokens.
- `507c89031dd74a1d73b6e1616e214e5f9e2114a7`: hardening configuravel de cookies HTTPS e redirect SSL no Django.
- `f151f2218028fc2547b1338e96e980fa434d57ae`: hardening local do agente Windows, incluindo ACLs sensiveis, runner/autorizacao AdminOnly e redaction.
- `57e46edd2b8d4ed38d760beb71630c5af56f8a89`: redaction de resultados de AgentJob antes de persistir job, receipt e audit metadata.
- `1e8f24571a6a180804670db4a7fe603cfd956d1d`: isolamento de `SECURE_SSL_REDIRECT` nos settings de teste para reproduzibilidade no servidor.
- `b865f313d7a48e69cd18d52c6090c450da24c563`: resiliencia da persistencia de estado do agente e retentativas do `NightOwlFileStore`, empacotado em RC39.

O commit `f151f2218028fc2547b1338e96e980fa434d57ae` e posterior a RC36 e esta contido na RC38.
O commit `b865f313d7a48e69cd18d52c6090c450da24c563` e posterior a RC38 e esta contido na RC39, release usada para fechar o canario da Fase 5.

Baseline Linux validado para o servico em producao:

```text
User=nightowl
Group=nightowl
UMask=0027
/opt/nightowl/.env=0640 root:nightowl
/opt/nightowl/logs=0750 nightowl:nightowl
/opt/nightowl/media=0750 nightowl:nightowl
Gunicorn nao executa como root
```

Validacoes reais registradas:

```text
ENV_READABLE_BY_NIGHTOWL=PASS
ENV_BLOCKED_FOR_WWW_DATA=PASS
LOG_WRITE_AS_NIGHTOWL=PASS
MEDIA_WRITE_AS_NIGHTOWL=PASS
DJANGO_AS_NIGHTOWL=PASS
SERVICE_LEAST_PRIVILEGE=PASS
GUNICORN_NOT_RUNNING_AS_ROOT=PASS
LOCAL_HTTP_STATUS=301
PUBLIC_HTTPS_STATUS=200
POST_MIGRATION_ERRORS=NONE
```

AD TLS canary de infraestrutura:

```text
domain_controller=dc01.control.local
LDAPS_636=PASS hostname_validated=true chain_trusted=true
```

Implementacao, push e deploy do hardening de transporte AD foram concluidos. A producao utiliza `AD_SERVER_URI=ldaps://dc01.control.local` com `AD_REQUIRE_TLS=True`; o certificado LDAPS foi validado por hostname e trust store. O backend continua suportando `ldap://` com StartTLS e `ldaps://` com TLS implicito, ambos com validacao de certificado pelo trust store do sistema.

Validacoes AD reais registradas:

```text
security_preflight --strict=PASS
service_bind=PASS
user_search=PASS
interactive_login_valid_credentials=PASS
interactive_login_invalid_password_rejected=PASS
```

HSTS/HTTPS canary de infraestrutura:

```text
PUBLIC_HTTPS_STATUS=PASS
SECURE_HSTS_SECONDS=300
SECURE_HSTS_INCLUDE_SUBDOMAINS=false
SECURE_HSTS_PRELOAD=false
PUBLIC_CERT_RENEWAL_SIMULATION=PASS
```

O canario atual valida HSTS curto em producao. HSTS longo, preload e autenticacao TLS do origin permanecem fora deste fechamento e nao devem ser considerados concluidos por este registro.

Canario de agente da Fase 5:

```text
Target endpoint: CS-SRV-CST
Endpoint id: 476f5039-5e7e-4b0f-b24c-849ee6551434
Machine id: c4e59106-035a-455f-bdeb-3e8287718dd6
Central agent version observed before canary: 0.1.1.0-rc37
Local service binary observed later: 0.1.1.0-rc38
Target release: 0.1.1.0-rc38
Update job: defb294b-8188-45a8-b425-42090bf72dc0
Update result: completed
Decision: RC39 candidate required before final Phase 5 canary closure
```

A inspecao central pre-canario confirmou que o endpoint esta em lifecycle
`installed`, sem jobs ativos de lifecycle, sem uninstall pendente, sem
rollback_failed e com identidade central/local coerente. A release RC38 esta
integra e selecionavel manualmente. O update RC37 -> RC38 foi executado por
`update_agent` normal, via painel/manual, e o backend registrou `completed`,
`updated=true`, `installed_version=0.1.1.0-rc38`, health check confirmado e
rollback falso. Apos a coleta local de diagnostico, o endpoint voltou a aparecer
online no backend, com `last_seen` em `2026-09-15T20:42:36Z`, inventarios
recebidos e consultas de jobs recorrentes.

A evidencia original do Windows mostrou `System.UnauthorizedAccessException`
seguida de `HostOptions.BackgroundServiceException` durante gravacao de estado.
A causa consolidada para a RC39 e que uma falha transitoria em
`StateService.SaveAsync` podia escapar da fronteira do loop e encerrar o
`BackgroundService`. A correcao candidata deve manter o servico vivo, registrar
`state.save.failed` sanitizado, aplicar backoff entre tentativas e adicionar
retentativa limitada no `NightOwlFileStore` para falhas transitorias de
gravacao/substituicao atomica. A ocorrencia suspeita de `Bearer` em logs foi
reclassificada como falso positivo, sem segredo plaintext confirmado.

Canario final da Fase 5 com RC39:

```text
Target endpoint: CS-SRV-CST
Endpoint id: 476f5039-5e7e-4b0f-b24c-849ee6551434
Machine id: c4e59106-035a-455f-bdeb-3e8287718dd6
Update path: 0.1.1.0-rc38 -> 0.1.1.0-rc39
Job id: abf51b3c-bb3d-4a6d-b49c-a544c22f0c2c
Receipt id: 6a9cf56f-6b4d-4dca-bf4b-5e0fa2dd9b07
Result id: 5b125e55-00e6-412f-aeb5-ddaad1df514e
Result: completed
Exit code: 0
Health check: confirmed
Rollback performed: false
Conflict count: 0
Machine id preserved: true
```

Observacao sustentada apos o update:

```text
Observation duration: 24.56h
Endpoint remained online: true
Inventory snapshots: 315
Activity points: 15040
Maximum observed activity gap: 72.33s
Offline changes: 0
Offline alerts: 0
state.save.failed: 0
UnauthorizedAccessException: 0
Persistence-related IOException: 0
BackgroundServiceException: 0
service.loop.failed: 0
rollback_failed: 0
Unexpected jobs: 0
```

Diagnostico final local do Windows:

```text
Diagnostic package: NightOwl-Diagnostics-CS-SRV-CST-20260917T132143Z.zip
Diagnostic SHA256: b013c3a7cdfc721a725082be904c13027a61fb812c351da9d8e8c185b4a4f996
Warnings: 0
Service: Running / Automatic
Agent process start: 2026-09-16T12:42:45Z
Agent process restart during canary: false
Tray start: 2026-09-16T12:43:24Z
RC39 version/build/commit: confirmed
Identity/config match: confirmed
Sensitive ACLs: PASS
State temporary files: 0
Pending results: 0
HTTPS/TLS connectivity: PASS
Secrets in process arguments: 0
```

Residuo historico aceito:

```text
Legacy service-install.log lines with enrollment_token field: 15
Date range: 2026-08-25 to 2026-09-04
AFTER_RC39: 0
Classification: test-environment residue, not RC39 regression
Risk acceptance: database and credentials will be reset before production
```

Nenhum valor de token, credencial ou chave deve ser registrado neste documento.

Capacidades de lifecycle validadas em canario real:

- clean deployment
- update
- repair
- reinstall
- lifecycle state persistence
- Tray uninstall
- panel uninstall
- offline uninstall queue
- pre-dispatch cancellation
- cancelled job never dispatched
- offline -> online delivery
- uninstall execution after endpoint returns
- uninstall expiry
- expired job never dispatched
- panel-only purge
- purge local persistent-data removal
- central history preservation after purge

Modelo de lifecycle consolidado:

```text
Install -> Installed
Installed -> Update -> Installed
Installed -> Repair -> Installed
Installed -> Reinstall -> Installed
Installed -> Uninstall -> Uninstalled
Installed -> Purge -> Purged
```

Principios consolidados:

- Operational status e lifecycle sao conceitos independentes; por exemplo, `status=offline` com `lifecycle=purged` e valido.
- Heartbeat/status operacional tardio nao pode reativar endpoint `uninstalled` ou `purged`; somente deployment/reinstall saudavel normaliza para `installed`.
- Uninstall normal preserva identidade/configuracao persistente suficiente para reinstall controlado.
- Purge e destrutivo localmente, mas nao apaga endpoint nem historico central.
- Credenciais administrativas nao sao persistidas em jobs.
- Purge exige autorizacao explicita distinta e confirmacao do hostname.
- Jobs cancelados ou expirados antes do dispatch nunca podem ser entregues posteriormente.

### Canario: cancelamento offline

Endpoint: `CS-SRV-CST`

```text
endpoint_id=476f5039-5e7e-4b0f-b24c-849ee6551434
```

Fluxo validado:

```text
offline
-> uninstall solicitado
-> waiting_for_agent / queued
-> cancelado no painel
-> endpoint voltou online
-> uninstall nao foi entregue
```

Resultado:

```text
request_status=cancelled
job_status=cancelled
dispatched_at=None
started_at=None
```

O agente permaneceu instalado e operacional.

Flags:

```text
OFFLINE_UNINSTALL_WAITING=PASS
PRE_DISPATCH_CANCEL=PASS
CANCELLED_JOB_NOT_DISPATCHED=PASS
ENDPOINT_SURVIVES_CANCEL=PASS
```

### Canario: offline -> online -> uninstall

Endpoint offline recebeu uma solicitacao de uninstall. Ao retornar online:

```text
queued -> dispatched -> running -> completed
```

Resultado local:

```text
ServiceExists=False
AgentProcess=False
TrayTask=False
AgentDotNet=False
```

Dados de uninstall normal preservados:

```text
Config=True
Identity=True
State=True
Trust=True
Logs=True
```

`agent.state.json`:

```text
install_status=uninstalled
uninstalled_at=<timestamp preenchido>
```

Uninstaller:

```text
uninstall.start
-> uninstall.binary_remove.started
-> uninstall.binary_remove.completed
-> uninstall.completed
```

Flags:

```text
OFFLINE_UNINSTALL_DISPATCH=PASS
OFFLINE_TO_ONLINE_DELIVERY=PASS
UNINSTALL_EXECUTED_ON_RETURN=PASS
PERSISTENT_DATA_PRESERVED=PASS
```

### Canario: expiry

```text
request_id=0e57bf4f-dfbe-43bb-890e-fac52be847e1
job_id=954ca925-c2d4-4fe7-884e-de64589353f7
```

Foi alterado `expires_at` somente no canario para simular TTL expirado.

Resultado:

```text
endpoint_status=online
request_status=expired
request_error_code=UNINSTALL_REQUEST_EXPIRED
job_status=expired
dispatched_at=None
started_at=None
finished_at=<preenchido>
job_error=Job expirou antes do pull do agente.
```

O agente permaneceu instalado e operacional.

Flags:

```text
UNINSTALL_EXPIRY=PASS
EXPIRED_JOB_NOT_DISPATCHED=PASS
ENDPOINT_SURVIVES_EXPIRY=PASS
```

### Canario: purge

```text
job_id=df58aa85-abe7-4a42-b244-44a52e734eef
request_id=461c050a-ebd7-4024-9f75-0e1939ed56b9
```

Resultado do job:

```text
status=completed
stage=completed
progress=100
```

Payload:

```json
{
  "mode": "purge",
  "source": "panel",
  "timeout_seconds": 900,
  "purge_authorized": true
}
```

Result:

```text
mode=purge
type=uninstall_agent
machine_id=c4e59106-035a-455f-bdeb-3e8287718dd6
binary_removed=true
uninstall_status=completed
persistent_data_preserved=false
```

Backend final:

```text
status=offline
agent_lifecycle_status=purged
request_status=completed
job_status=completed
```

Historico central apos purge:

```text
jobs_count=31
inventory_count=1844
audit_count=47550
uninstall_requests=10
```

Conclusao: purge remove o agente/estado local previsto, mas nao apaga o endpoint nem seu historico central.

Flags:

```text
PANEL_PURGE=PASS
PURGE_COMPLETED=PASS
PURGE_BINARY_REMOVED=PASS
PURGE_PERSISTENT_DATA_REMOVED=PASS
PURGE_HISTORY_PRESERVED=PASS
```

### Fixes importantes do fechamento

1. Backend lifecycle permanecia `uninstalled` apos reinstall antigo.

   Fix: deployment saudavel normaliza lifecycle para `installed` e limpa metadata antiga de uninstall. Inclui reconciliacao idempotente.

   Commit: `ddb1cfe019e349992d58d5acfcf62ab7acb5b8f4`

2. Cancelamento de uninstall offline existia no backend, mas estava pouco descobrivel na UX.

   Fix: cancelamento contextual diretamente no card do job pendente.

   Commit: `f45bf153c9564b7f0e7ca19ddf2efc3291c8c704`

3. Jobs `cancelled`/`expired` eram representados semanticamente com `stage=failed`.

   Fix: stage dedicado para `cancelled` e `timed_out`.

   Commit: `6c7200f594294a10846827b5d8d0ffb052aa0616`

4. Edge cases finais de offline/cancel/expiry/purge foram consolidados no backend.

   Commit relevante: `ccfefb8808946c0aec7580ed2234ae070fba8ca9`

5. Heartbeat/status operacional tardio podia deixar `machine.status=online` com `agent_lifecycle_status=purged`.

   Fix: lifecycle terminal (`uninstalled`/`purged`) nao pode ser promovido por heartbeat/status normal; somente deployment saudavel pode reativar.

   Commit: `1c095558e62a22b457ca9bf0a6190a2a917982bb`

### Phase 5 - closure gates

```text
CURRENT PHASE: PHASE 6 - FLEET ROLLOUT / POLICIES
PHASE_5_STATUS: CLOSED
READY_FOR_PHASE6: true
```

Gates fechados antes de liberar a Fase 6:

- `security_preflight --strict` PASS em producao. Concluido.
- zero segredos literais versionados. Concluido.
- arquivos runtime de segredo protegidos por permissoes seguras no servidor. Concluido.
- HTTPS/HSTS canario de producao validado, com `max-age=300`, `includeSubDomains=false` e `preload=false`. Concluido.
- AD com transporte seguro via LDAPS e validacao de certificado. Concluido.
- regressoes de redaction de logs/stdout/exceptions PASS. Concluido no backend em `57e46edd2b8d4ed38d760beb71630c5af56f8a89`.
- publicacao de nova RC do agente contendo `f151f2218028fc2547b1338e96e980fa434d57ae` ou commit posterior. Concluido com RC38 e encerrado com RC39.
- canario real validando ACLs locais, arquivos sensiveis e ausencia de segredo em args/logs. Concluido com RC39 e diagnostico final do Windows.
- upgrade sem regressao de heartbeat, jobs, update, repair e uninstall. Concluido: RC38 -> RC39 completed, health check confirmado, 24.56h online e sem jobs inesperados.
- pipeline de publicacao sem vazamento de segredo e com artefatos assinados completos. Concluido para RC38 e RC39.
- `stable/latest` preservado em `0.1.0.7`, sem promocao de RC39 para pilot/stable e sem rollout automatico.

No fechamento da Fase 5, a Fase 6 permaneceu `PENDING`, liberada para planejamento
separado. O registro posterior abaixo formaliza seu inicio documental em 2026-09-17.

## Fase 6 - Fleet Rollout / Policies

Inicio documental: `2026-09-17`. O levantamento e a especificacao comecaram;
`IN_PROGRESS` nao significa campanha, orquestrador ou rollout automatico ativos.
As Fases 0A-5 continuam CLOSED; Fases 7 e 8 continuam PENDING.

```text
PHASE_5_STATUS = CLOSED
READY_FOR_PHASE6 = true
PHASE_6_STATUS = IN_PROGRESS
PHASE_6_ACTIVE_SUBPHASE = 6D
PHASE_6_ORCHESTRATOR_ENABLED = false
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false
AUTO_PAUSE_IMPLEMENTED = false
```

### Objetivo e baseline funcional

Transformar os controles individuais de update em rollout de frota controlado,
rastreavel, reproduzivel e auditavel, dividido em ondas e protegido por gates.
A governanca planejada conecta release publicada, politica, selecao da coorte,
aprovacao, campanha, onda, job, health check, reconciliacao e decisao de avancar
ou pausar novas entregas. Nao ha promocao ou distribuicao automatica implicita.

Antes da Fase 6, ja existem:

- canais `development`, `pilot` e `stable`;
- politicas `manual`, `notify_only`, `automatic` e `maintenance_window`;
- `AgentReleaseGroup`, associacao de endpoints e grupos permitidos por release;
- `update_paused`, `pinned_agent_version` e `is_pilot_endpoint`;
- `rollout_percentage`, `rollout_paused` e selecao deterministica por endpoint/machine_id;
- validacao de assinatura, revogacao e minimum updater version;
- criacao basica/idempotente sequencial de jobs de update;
- promocao, pausa, revogacao e auditoria de releases.

Esses controles nao constituem uma campanha persistente acompanhada de ponta a
ponta. A idempotencia concorrente ainda precisa ser garantida. Os indicadores
de automacao desabilitada acima descrevem a nova arquitetura da Fase 6; nao
significam que o GET legado deixou de criar jobs quando a politica o permite.

### Lacunas antes da implementacao

Todos os itens iniciam com status `UNDER_REVIEW_IN_6A`. O levantamento tecnico
de 2026-09-17 confirmou os comportamentos abaixo; nenhuma correcao foi aplicada.

| ID | Lacuna observada | Contrato futuro a validar |
| --- | --- | --- |
| F6-GAP-01 | GET `/api/agent/update-policy/` pode criar job; `evaluate_agent_update_policy` grava timestamp com `record_evaluation=True`. | Separar avaliacao pura, telemetria e despacho; somente orquestrador cria jobs automaticos. |
| F6-GAP-02 | Aumento de 10% para 25% libera novos buckets na proxima avaliacao, sem onda ou observacao persistente. | Ondas, aprovacao intermediaria, observacao minima e snapshot. |
| F6-GAP-03 | Nao existe snapshot persistente de campanha. | Registrar selecionados/excluidos, razoes, onda, job, politica efetiva, versao inicial e bucket. |
| F6-GAP-04 | Metricas recalculam elegibilidade e usam frota atual/jobs historicos. | Metricas de uma coorte automatica congelada, sem mudar denominador silenciosamente. |
| F6-GAP-05 | Nao existe auto-pause de campanha. | Suspender novas entregas diante de falha, rollback, rollback_failed, health ausente, offline, job travado ou limite excedido. |
| F6-GAP-06 | Maintenance window usa timezone corrente do Django, sem timezone proprio. | Definir timezone, dias, limites e janelas que atravessam meia-noite. |
| F6-GAP-07 | Flag `is_pilot_endpoint` e grupo Pilot sao independentes. | Escolher fonte canonica e definir migracao/compatibilidade. |
| F6-GAP-08 | Lista vazia de rollout_groups nao limpa associacoes no fluxo atual. | Permitir remocao de todos os grupos com semantica explicita. |

### Registro inicial do inventario 6A

Consulta em `2026-09-17T14:28:47Z`, via Django em transacao PostgreSQL READ ONLY.
Nenhuma API de update-policy/pull ou avaliacao com persistencia foi executada.

- Frota: 6 AgentMachine ativos cadastralmente, 0 inativos; 3 online e 3 offline.
- Lifecycle: 1 installed e 5 sem valor; 5 machine_id em formato UUID e 1 baseado em hostname.
- Agent: 0.1.0 (3), 0.1.0.6 (1), RC17 (1), RC39 (1).
- Canais: stable (4), development (2), pilot (0).
- Todos manual, auto_update_enabled=false, update_paused=false, sem pin e sem janela.
- Grupos Critical, Internal IT, Manual, Pilot, Remote, Servers e Workstations: todos vazios; nenhuma flag Pilot ativa.
- Updates historicos: completed (23), failed (5), invalid_parameters (1), rolled_back (1), sent (2); queued/running (0).
- Dois updates sent antigos no TAXCEL, para RC13/RC14, bloqueiam novos updates pelo fluxo atual. Reconciliacao exige decisao separada; nenhum job foi alterado.
- Releases cadastradas: RC1-RC39, exceto RC7/RC8; todas development/paused/rollout=0, nao revogadas e nao mandatory.
- RC38 e RC39 mantem assinatura valida registrada; stable/latest publico permanece 0.1.0.7, sem registro stable correspondente no banco.
- Updater/Tray sao reportados no inventario. A elegibilidade atual recorre a agent_version, sem utilizar essas versoes reais; valores antigos 1.0.0 nao provam compatibilidade funcional.

Referencia 6A: CS-SRV-CST, endpoint `476f5039-5e7e-4b0f-b24c-849ee6551434`,
machine_id `c4e59106-035a-455f-bdeb-3e8287718dd6`, online/installed, Agent/Updater/Tray
RC39, development/manual, sem grupo/pin/janela, auto-update e pause do endpoint
false. Ultimo update RC38 -> RC39 completed/exit=0; nenhum job ativo na consulta.
Esse snapshot nao substitui um novo preview antes de qualquer futura campanha.

### Arquitetura planejada, ainda nao implementada

| Componente | Responsabilidade e conceitos planejados | Estados candidatos |
| --- | --- | --- |
| AgentRolloutCampaign | Release/channel, estado, grupos-alvo, percentual/escopo, concorrencia, observacao minima, criterios de pausa, responsavel/aprovador, motivos e timestamps. | draft, ready, running, paused, completed, aborted |
| AgentRolloutWave | Sequencia, percentual/quantidade-alvo, estado, inicio/termino, observacao, sucessos/falhas/rollbacks, decisao administrativa e snapshot dos gates. | A definir em 6B/6C |
| AgentRolloutTarget | Campanha, endpoint, wave, bucket, versao inicial, politica efetiva, estado/exclusao, job e timestamps de selecao/inicio/conclusao. | excluded, eligible, queued, running, succeeded, failed, rolled_back, cancelled |

Nenhum desses models existe por causa deste registro. Contrato planejado:

- evaluate: somente leitura, sem timestamp/auditoria ou criacao de job;
- preview: calcula coorte e razoes, sem executar;
- dispatch/orchestrator: unico componente autorizado a criar jobs automaticos;
- reconcile: acompanha job, receipt, versao efetiva e health;
- administrative actions: pause/resume/advance/abort/revoke com autorizacao e auditoria.

### Preview e integridade da coorte

Toda campanha devera ter preview explicito: release, politica, grupos,
candidatos, elegiveis, excluidos, razoes, buckets e impacto total.
Planeja-se `cohort_hash` para comparar PREVIEW APROVADO com COORTE QUE SERA
EXECUTADA antes dos primeiros jobs. Mudanca relevante que invalide o fingerprint
exigira novo preview/aprovacao. Algoritmo definitivo sera definido em 6B.

### Regras operacionais planejadas

1. Nenhuma release inicia rollout automaticamente.
2. Nenhuma campanha comeca sem preview.
3. Critical/Servers ficam fora da participacao automatica por default.
4. Endpoint offline, desinstalado ou com job incompativel ativo nao recebe novo job.
5. Mandatory nunca ignora assinatura, revogacao, pausa, incompatibilidade ou gates criticos.
6. Downgrade exige autorizacao explicita.
7. Pause de campanha impede novos jobs.
8. Jobs nao despachados poderao ser cancelados conforme semantica futura.
9. Pause administrativa nao interrompe simplesmente jobs em execucao.
10. Revogacao impede imediatamente novas entregas.
11. Auto-pause nao gera rollback em massa automaticamente.
12. Recovery/rollback coletivo depende de decisao administrativa separada.

### Auto-pause: criterios iniciais planejados

Ainda nao ativos; amostra minima e detalhes de reconcile serao definidos antes
da implementacao. `AUTO_PAUSE_IMPLEMENTED = false`.

| Sinal | Resposta planejada |
| --- | --- |
| Qualquer rollback_failed | Pausa imediata. |
| Falha de assinatura/checksum | Pausa imediata e investigacao/revogacao. |
| Qualquer rollback durante Pilot | Pausa imediata. |
| 2 endpoints com falha na mesma onda | Pausa. |
| Failure rate > 10%, apos amostra minima | Pausa. |
| Offline > 15 minutos no periodo critico pos-update | Contabilizar conforme politica futura de health/reconcile. |
| Update job acima do timeout | Investigar/pausar conforme gate. |
| Health check nao confirmado | Nunca considerar sucesso. |

### Ondas e politica inicial por canal

Modelo inicial sujeito a revisao e aprovacao no fechamento da 6A:

| Onda | Alvo inicial | Observacao minima |
| --- | --- | --- |
| Development | 1 endpoint manual | 24h |
| Pilot A | 1-3 endpoints nomeados | 24h |
| Pilot B | Restante do grupo Pilot | 24h |
| Stable A | 10% dos elegiveis | 24h |
| Stable B | 25% | 24h |
| Stable C | 50% | 24h |
| Stable D | 100% | 48h |

Frotas pequenas poderao usar quantidades absolutas. O inventario inicial de seis
endpoints recomenda 1 referencia, depois 1 elegivel adicional, depois restante
aprovado. Valores definitivos dependem de compatibilidade, grupos e contrato 6A.

| Canal / grupo operacional | Politica planejada | Publico |
| --- | --- | --- |
| Development | Manual explicita | CS-SRV-CST / laboratorio |
| Pilot | Maintenance window | TI interna / endpoints descartaveis |
| Stable | Manual ou notify_only inicialmente | Estacoes comuns apos aprovacao |
| Critical / Servers | Manual | Servidores e endpoints criticos |

Critical/Servers sao grupos/politica de protecao, nao novos release channels.

### Subfases e gates

| Subfase | Escopo | Status |
| --- | --- | --- |
| 6A | Inventario e contrato operacional | CLOSED |
| 6B | Preview e politicas em massa | CLOSED |
| 6C | Campanha e orquestrador | CLOSED |
| 6D | Metricas, reconciliacao e auto-pause | IN PROGRESS |
| 6E | Canario real e encerramento | PENDING |

#### 6A - Inventario e contrato operacional

`CLOSED` em 2026-09-17 apos inventario e aprovacao do contrato 6B.1. Escopo: frota, versoes, canais, politicas, grupos, updater,
jobs e releases; validar F6-GAP; estabelecer baseline CS-SRV-CST; definir
estados, gates, sucesso/falha, matriz de testes e estrategia Pilot.
Restricoes do levantamento: somente leitura, sem rollout automatico, sem novo
comportamento operacional e sem AgentJob provocado pelas consultas.
Gate: inventario documentado, lacunas confirmadas/corrigidas no contrato,
contrato aprovado, ambiguidades resolvidas, arquitetura 6B/6C definida e zero
alteracao operacional involuntaria. O levantamento nao fecha esses gates sozinho.

#### 6B - Preview e politicas em massa

`CLOSED` em 2026-09-17 apos validacao isolada PostgreSQL descrita abaixo:
evaluate separado de dispatch, preview somente leitura e deterministico,
razoes, cohort_hash, grupos consistentes/limpeza completa, operacoes em massa,
timezone de maintenance window e auditoria. Gate:
`preview must be deterministic and reproducible`; preview nunca cria job automatico.

#### 6C - Campanha e orquestrador

`IN PROGRESS`: persistencia Campaign/Wave/Target entregue em 6C.1 abaixo.
Ainda pendentes: comando planejado `process_agent_rollouts`,
execucao periodica, idempotencia transacional, concorrencia limitada, vinculo
campanha/target/job, restart safety e feature flag global inicialmente OFF.
Gate: pelo menos 250 endpoints sinteticos, execucao concorrente/repetida, zero
jobs duplicados e restart sem perda do estado da campanha.

#### 6D - Metricas, reconciliacao e auto-pause

`PENDING`: metricas por campanha/onda, job/health, offline pos-update,
rollback/rollback_failed, timeout/stalled, auto-pause, pause/resume, preview
antes de advance e auditoria. Gate: simular sucesso, falha, rollback,
rollback_failed, offline, health ausente, job travado, pausa e retomada.

#### 6E - Canario real

`PENDING`: referencia atual CS-SRV-CST; preferir ao menos dois Windows
descartaveis antes da validacao final. Fluxo planejado:

1. Baselines conhecidos e release candidata Pilot inicialmente pausada.
2. Primeira onda com um endpoint; confirmar demais intocados.
3. Cumprir observacao e avancar explicitamente para o proximo endpoint.
4. Validar ausencia de duplicacao/vazamento entre grupos, health, jobs, audit e identidade.

RC39 continua prerelease: nao planejar sua promocao para stable.
Uma versao final futura, por exemplo `0.1.1.0`, sera tratada separadamente apos
aprovacao da Fase 6. Nenhum canario novo foi executado neste registro.

### Organizacao prevista de desenvolvimento

Plano sujeito a ajustes apos 6A:

1. `phase6/rollout-spec-and-preview`
2. `phase6/campaign-models`
3. `phase6/rollout-orchestrator`
4. `phase6/fleet-policy-ui`
5. `phase6/autopause-and-canary`

Incrementos do orquestrador chegam com automacao desabilitada; ativacao exige
preview atualizado da frota e aprovacao explicita.

### Criterios de encerramento da Fase 6

- [ ] Preview e coorte executada correspondem.
- [ ] Fingerprint/cohort validation aprovado, se adotado.
- [ ] Nenhum job duplicado sob concorrencia.
- [ ] Pausa bloqueia novas entregas.
- [ ] Endpoints excluidos permanecem intocados.
- [ ] Critical/Servers protegidos.
- [ ] Maintenance window respeita timezone.
- [ ] Auto-pause validado.
- [ ] Revogacao validada.
- [ ] Campanha sobrevive a restart.
- [ ] Ator, motivo e impacto auditados.
- [ ] Canario real com mais de um endpoint.
- [ ] Ausencia de vazamento entre grupos.
- [ ] Nenhuma alteracao involuntaria em stable/latest.
- [ ] security_preflight --strict continua PASS.
- [ ] Documentacao final atualizada.
- [ ] PHASE_6_STATUS pode ser alterado para CLOSED.

Planejamento nao comprova nenhum item desse checklist.

### Convencao de acompanhamento

DEPLOY.md continua a fonte canonica de roadmap tecnico, fases, gates, releases
e validacoes operacionais. Ao finalizar cada subfase:

1. Atualizar status `PENDING -> IN_PROGRESS -> CLOSED` e `PHASE_6_ACTIVE_SUBPHASE`.
2. Registrar commits, migrations relevantes, testes/resultados, incidentes,
   desvios, decisoes arquiteturais e validacoes de producao/canario aplicaveis.
3. Preservar o historico de cada subfase concluida.
4. Adicionar mudancas de decisao como registro posterior, explicando a substituicao.
5. Alterar `PHASE_6_STATUS = CLOSED` somente com todos os gates finais comprovados.

### 6B.1 - Eligibility core e preview somente leitura (2026-09-17)

Entrega de dominio implementada; 6B continua IN PROGRESS. O contrato aprovado
fecha a 6A. Orquestrador, rollout automatico e auto-pause permanecem desabilitados;
6C-6E permanecem PENDING. Nenhum deploy ou migration em producao nesta entrega.

Decisoes aprovadas e implementadas:

- Pilot: grupo com slug `pilot` e a unica autoridade da avaliacao. A flag antiga
  permanece fisicamente para compatibilidade, mas nao concede elegibilidade.
  A migration associa flags antigas ao grupo sem remover membros existentes;
  futuras mudancas de pertencimento devem usar o grupo.
- Updater/Tray: campos persistidos no AgentMachine, alimentados por heartbeat,
  inventario e status quando reportados. Payload parcial preserva ultima versao
  valida. Backfill usa somente valores reportados nos inventarios historicos;
  nunca copia agent_version para preencher updater_version.
- Avaliacao pura: `evaluate_agent_update_eligibility` retorna uma decisao sem
  salvar endpoint, release, grupos, job ou auditoria. Recebe instante aware
  explicito; validade temporal da chave tambem usa esse instante.
- Telemetria: `record_agent_update_policy_evaluation` e separada. O adapter
  `evaluate_agent_update_policy` preserva o parametro record_evaluation legado.
- GET legado: AgentUpdatePolicyView separa evaluate, record e
  `_dispatch_legacy_policy_update`; esse caminho ainda pode criar job como antes.
  O preview nao usa esse adapter nem o dispatch.
- Preview automatico: exclui endpoint inativo, lifecycle vazio/nao installed,
  lifecycle terminal, offline, last_seen ausente/stale/futuro, identidade nao UUID
  ou duplicada, grupos Critical/Servers e update queued/sent/running.
  Freshness default: 900 segundos, configuravel na chamada; nao altera identidade
  ou lifecycle. Update com expires_at vencido ou created_at anterior a 900 segundos
  e classificado stale, sem reconciliar status ou resultado.
- Mandatory: preview respeita percentual, consentimento automatico e todos os
  gates; nao equivale a 100%. No caminho legado permanece o comportamento anterior
  de percentual/auto_update_enabled, explicitamente coberto por teste.
- Janela: diaria, timezone IANA por endpoint; vazio usa settings.TIME_ZONE.
  Limites inclusivos e travessia de meia-noite; start=end, metade ausente ou
  timezone invalido bloqueiam. API de politica valida antes de salvar.
- Grupos: campo omitido preserva associacoes; chave rollout_groups explicitamente
  vazia (valor vazio no formulario) remove todas. Nao cria job.

Servico implementado: `build_agent_rollout_preview(release, endpoints=...,
target_group_ids=..., now=..., freshness_seconds=900, mode='automatic')`.
Sem API publica/UI de campanha neste incremento. Retorna release_id, version,
channel, generated_at, mode, total_candidates, eligible_count, excluded_count,
targets ordenados por endpoint UUID, cohort_schema e cohort_hash.
Target: endpoint_id, machine_id, hostname, current_version, updater_version,
bucket, eligible, reason_code, groups (IDs), policy e channel. Nao inclui secrets
ou hashes de autenticacao. Candidatos excluidos nao sao substituidos.

Fingerprint schema 1: SHA-256 de JSON ASCII com chaves ordenadas e separadores
compactos, incluindo contrato, grupos/filtros, configuracao e identidade dos
artefatos da release, politica/identidade/versoes/bucket/decisao dos targets,
pause/pin/lifecycle/status e janela efetiva. Targets/grupos sao ordenados.
Nao inclui hostname, labels, generated_at nem last_seen exato; freshness altera
o hash quando muda a decisao. O hash nao constitui aprovacao persistida.

Novos reason codes: `agent_version_unknown`, `endpoint_inactive`,
`endpoint_lifecycle_terminal`, `endpoint_lifecycle_unknown`, `endpoint_offline`,
`endpoint_stale`, `machine_identity_invalid`, `machine_identity_ambiguous`,
`protected_endpoint_group`, `update_job_active`, `update_job_stale`,
`pinned_release_mismatch`, `updater_version_unknown`, `update_policy_invalid`,
`maintenance_window_invalid`, `maintenance_timezone_invalid`.
Permanecem os motivos de assinatura/chave, revogacao, pausa, bootstrap/minimum
updater, canal/grupos, percentual, politica, janela e downgrade.

Migration local: `agents/0030_rollout_eligibility_contract` adiciona updater_version,
tray_version e maintenance_window_timezone, associa Pilot legado e faz backfill
de componentes reportados. Reverse de dados e no-op para nao apagar associacoes
ou historico; a remocao dos novos campos segue o reverse normal de schema.

Arquivos: agents/models.py, services.py, views.py, tests.py,
test_rollout_preview.py, migrations/0030_rollout_eligibility_contract.py,
dashboard/views.py e DEPLOY.md. Nenhum componente .NET alterado.

Validacao: testes locais SQLite com config.settings_test; avaliacao/preview
instrumentados para permitir somente SELECT, zero jobs/audits novos, hash
repetivel/independente da ordem e labels, exclusoes operacionais, updater real,
Pilot/backfill, mandatory, assinatura, downgrade, grupos e janelas/timezone.
Suite completa agents: 202 testes PASS antes das duas regressoes adicionais de
filtro duplicado/timezone herdado; rodada focada: 86 testes PASS, incluindo essas
duas (21 testes de preview e 65 de Policy/Governance).
py_compile e manage.py check PASS; makemigrations --check --dry-run sem mudancas
pendentes alem da migration 0030 criada. git diff --check PASS.

Restantes 6B.2: exposicao administrativa/autorizada do preview e operacoes em
massa, UI, contrato de aprovacao/fingerprint sob mudanca concorrente, otimizacao
de consultas e validacao PostgreSQL das migrations. Dados legados com versoes
1.0.0 reportadas nao provam capacidade de updater: nao inventar compatibilidade.
Os sent antigos do TAXCEL continuam preservados. Dispatch concorrente legado
nao ganhou garantia transacional neste incremento; campanha/orquestrador e
reconciliacao ficam para 6C/6D. RC39 e stable/latest permanecem inalterados.

### 6B.2 - Preview administrativo e politicas em massa (2026-09-17)

Implementacao sobre `3a94f936c8c0625513e0cc5eb6685aaa106e0487`, sem deploy.
6B permanece IN PROGRESS; PHASE_6_ACTIVE_SUBPHASE = 6B. 6C permanece PENDING.
PHASE_6_ORCHESTRATOR_ENABLED = false; PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false;
AUTO_PAUSE_IMPLEMENTED = false. Nenhum Campaign/Wave/Target, scheduler,
dispatch, reconciliacao ou aprovacao persistente foi criado.

APIs administrativas POST/JSON, com CSRF, usuario ativo/autenticado, acesso
tecnico e permissao explicita:

- `/api/agent/releases/<release_id>/rollout-preview/`:
  `agents.view_agent_release_rollout`; entrada `target_group_ids` (IDs explicitos,
  vazio significa sem filtro), `freshness_seconds` inteiro de 60 a 3600,
  default 900. Instante unico por request. Retorna release/version/channel,
  metadata administrativa, generated_at, mode automatic, cohort_schema=1,
  cohort_hash, total_candidates/eligible_count/excluded_count, targets,
  reason_counts e eligible_percentage. Somente SELECT; nao chama dispatch legado.
- `/api/agent/releases/<release_id>/rollout-preview/validate/`:
  mesma permissao/filtros, `expected_cohort_hash` e `cohort_schema=1` obrigatorios.
  Recalcula sem persistir; 200 matches=true, ou 409 preview_changed com hashes
  esperado/atual e preview atualizado. Hash nao e aprovacao ou autorizacao.
- `/api/endpoints/bulk-policy/`: `agents.change_agentmachine`;
  `endpoint_ids` explicitos, 1-500 IDs, `reason` obrigatorio, `changes` nao vazio.
  Campos omitidos preservados: update_channel, update_policy, auto_update_enabled,
  update_paused, pinned_agent_version, maintenance_window_start/end/timezone.
  Horarios locais, timezone IANA; pin SemVer ou string vazia para remover.
  Grupos: `changes.groups = {action: add|remove|replace|clear, ids: [...]}`;
  clear exige ids vazio. add/remove exigem IDs; replace vazio remove todos.
  Grupo omitido preserva. Nenhuma lista vazia seleciona implicitamente a frota.
  `apply=false`/omitido produz dry-run com before/after, warnings/rejections e
  bulk_change_hash separado do cohort_hash, sem writes/auditoria/jobs.
  Aplicar exige `apply=true`, `confirmed_count` igual ao escopo e
  `expected_bulk_change_hash` do dry-run. Mudanca de estado retorna 409
  bulk_preview_changed; qualquer rejeicao bloqueia a operacao inteira.

Concorrencia: transaction.atomic, select_for_update nos endpoints em ordem de
PK, releitura/revalidacao do plano e fingerprint dentro da transacao. Writer
individual de politica participa do mesmo lock. Saves somente dos campos
alterados; M2M explicito. Auditoria agregada obrigatoria na mesma transacao;
falha de auditoria desfaz alteracoes. Registra ator, motivo sanitizado, timestamp,
quantidades, campos, IDs e diff dos grupos. Sem tokens ou hashes de autenticacao.
Fingerprint bulk protege before/after e rejeicoes, nao e fingerprint de rollout.

Critical/Servers: bloqueia habilitar auto_update_enabled=true/politica automatic
e remover protecao. Nao ha override nesta entrega. Offline, lifecycle desconhecido,
identidade invalida e updates ativos/stale aparecem como warnings no dry-run;
editar politica nunca cria job. Configurar politica automatic continua podendo
habilitar o comportamento do GET legado numa avaliacao futura, nao uma campanha
6C; administradores devem considerar esse impacto ao confirmar o plano.

UI: Releases possui Rollout Preview, filtros, metadata, contagens, hash/data,
breakdown e tabela dos targets; refresh/validacao somente, sem iniciar campanha.
Listagem de endpoints usa selecao existente para editar politicas, grupos e
janela/pin; dry-run, motivo, confirmacao de quantidade e aplicar. Sem double
submit; 409 exige novo preview. Reason codes desconhecidos possuem fallback
textual seguro; respostas sao inseridas como texto, nao HTML. Mock nao permite bulk.

Performance: contexto de leitura pre-carrega grupos/jobs ativos, chaves e allowed
groups, agrega identidades duplicadas uma vez. 250 endpoints sinteticos:
1.501 SELECTs no caminho individual, 6 SELECTs em lote; mesma coorte
sob repeticao. Sem jobs no teste de performance. Eligibility individual continua
disponivel sem contexto; preview usa exatamente as mesmas regras.

Validacao local: suite agents/config (243 testes) PASS; check/py_compile/
makemigrations --check --dry-run PASS, nenhuma nova migration. Testes adicionais
cobrem autorizacao/CSRF, zero writes, 409 sob mudancas materiais, grupos,
omissoes, pin/timezone, rollback, auditoria obrigatoria e protecao Critical/Servers.
Regressoes de GET legado/update manual permanecem cobertas. Migration 0030 tem
backfill repetivel testado em SQLite, snapshots malformados e M2M preservada.
Rodada final focada: 104 testes PASS; UI Playwright/Edge com fixtures sinteticas:
preview, hash/409, fallback seguro, dry-run/apply, unica submissao e desktop/mobile
PASS. Capturas locais ignoradas em artifacts/fleet-ui. Nenhum POST real foi usado
no teste de browser; APIs reais foram testadas somente no banco de teste SQLite.
Suite completa final agents/config com browser habilitado: 247 testes PASS.

Gate restante material: PostgreSQL local/teste nao disponivel (sem psql, Docker
ou servico PostgreSQL local). Nao foi usada producao para experimentacao.
Antes de fechar 6B, validar forward/reaplicacao da migration 0030 em banco
PostgreSQL com dados, Pilot/backfill/campos vazios/snapshots malformados/M2M,
e concorrencia real de row locks/bulk edits. SQLite nao comprova esses gates.
RC39, stable/latest, CS-SRV-CST, TAXCEL e jobs reais permaneceram inalterados.

### Encerramento 6B - PostgreSQL e concorrencia real (2026-09-17)

6B = CLOSED; PHASE_6_ACTIVE_SUBPHASE = 6C; 6C = PENDING.
PHASE_6_STATUS = IN_PROGRESS. Nenhuma implementacao de 6C foi iniciada.
PHASE_6_ORCHESTRATOR_ENABLED = false;
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false; AUTO_PAUSE_IMPLEMENTED = false.
Este registro encerra o gate PostgreSQL pendente nos registros historicos 6B.1/6B.2;
os estados IN PROGRESS desses registros descrevem o momento de suas entregas.

Codigo validado exatamente: `8205715a6993d2ece5df53e0becd9dcf8aae40ae`.
Engine: PostgreSQL 17.10, Debian 17.10-0+deb13u1, UTF8, timezone UTC.
Validacao via SSH, sem deploy, restart ou migration no database de producao.
Preflight: checkout de producao detached em
`a0f0f8d659a70b828af408406ed4f2e0826be770`, limpo, origin/main no mesmo SHA.
Database da aplicacao: nightowl; role nightowl_user, sem SUPERUSER/CREATEDB/CREATEROLE.
Migration agents.0030 ja estava aplicada antes desta tarefa; lista aplicada
0001-0030 permaneceu identica. Servico active/running antes e depois, mesmo PID
1560194 e mesmo inicio 2026-09-17T16:13:53Z. Disco: 2,9 GB livres no preflight.

Isolamento: dois bancos vazios descartaveis, sem clone de dados reais:
`nightowl_phase6b_validation_20260917171226` e
`nightowl_phase6b_validation_20260917171329`.
Cada rodada utilizou role temporaria propria, LOGIN sem privilegios administrativos,
senha aleatoria apenas em memoria e worktree detached temporario no commit validado.
Settings de teste foram configurados em memoria para o database temporario;
current_database foi verificado antes dos testes e em cada conexao concorrente.
O servico continuou usando seu checkout/ambiente originais. Nao foi lido .env
de producao para copiar credenciais ao ambiente de teste.

MigrationExecutor: schema completo isolado, reverse ate agents.0029, criacao de
dados pelos models historicos 0029 e forward 0030. Matriz sintetica:

| Endpoint | Entrada | Resultado 0030 |
| --- | --- | --- |
| A | Flag Pilot true, sem associacao, updater rc38+build e Tray rc37 reportados | Pilot associado; updater rc38 normalizado e Tray rc37 |
| B | Flag Pilot false, membro Pilot existente | Associacao Pilot preservada; versoes desconhecidas vazias |
| C | Agent rc39, snapshot sem componentes | updater/tray vazios, sem copiar agent_version |
| D | Payload vazio/lista, collections incompleta, versoes invalidas | Sem excecao; updater/tray vazios |
| E | Snapshots validos antigos/novos e snapshot mais recente invalido | Ultima versao valida por componente: updater rc38, Tray rc37 |
| F | Membros Workstations e Remote | Ambos os relacionamentos M2M preservados |

maintenance_window_timezone inicialmente vazio em todos os seis casos.
Forward/check PASS. Ciclo 0030 -> 0029 -> 0030 PASS: reverse remove colunas
updater_version/tray_version/maintenance_window_timezone; RunPython reverse e
no-op, nao remove associacoes Pilot ou M2M preexistente. Reaplicacao repopula
componentes reportados, sem duplicar associacoes e sem inventar valores.

Concorrencia comprovada com conexoes PostgreSQL independentes:

- Row lock: T1 adquiriu SELECT FOR UPDATE, T2 aguardou; pg_stat_activity confirmou
  wait_event_type=Lock. T2 so adquiriu depois de T1 liberar, espera de 0,760 s.
  Ordem monotonic: T1 lock 672571,631968; T2 tentativa 672571,632330;
  T1 liberacao 672572,391038; T2 aquisicao 672572,392699.
- Bulk simultaneo: dois dry-runs do mesmo estado inicial. Apply A manteve seu
  lock aberto; apply B ficou em Lock. A confirmou pause=true; B retornou 409
  bulk_preview_changed, sem sobrescrever estado. Novo dry-run B/aplicacao confirmou
  notify_only preservando pause=true. Auditoria: uma alteracao A e uma B valida,
  nenhum evento de alteracao para o plano rejeitado.
- Writer independente: mudou politica em outra transacao entre preview e apply;
  plano antigo retornou 409, sem aplicar seu pause obsoleto.
- Auditoria indisponivel simulada depois do save/M2M: rollback preservou politica,
  grupos e quantidade anterior de auditorias. Nenhuma alteracao parcial.
- M2M add/remove/replace/clear PASS, no mesmo contrato transacional; protecoes
  Critical/Servers e escopo explicito cobertos pela suite PostgreSQL.

Suite PostgreSQL em ambas as rodadas: 104 testes PASS, zero falhas:
agents.test_rollout_preview, agents.test_fleet_policy,
agents.tests.AgentReleasePolicyTests e agents.tests.AgentReleaseGovernanceTests.
Inclui preview/API/hash, autorizacao, auditoria/rollback, protecoes, GET legado
e update manual regressivo. Segunda rodada: 5,536 s. Preview de 250 sinteticos
permaneceu deterministico: 1.501 consultas individuais versus 6 em lote.
Warnings de staticfiles temporario ausente nao afetaram checks ou testes;
nenhum collectstatic/deploy foi feito no servidor de producao.

Zero AgentJob criado pelos testes dedicados de migration/bulk/concorrencia.
Fixtures regressivas criaram jobs exclusivamente sinteticos no database descartavel;
nenhum job real criado. Producao manteve 6 endpoints e 76 jobs. Fingerprint dos
identificadores/politicas permaneceu igual antes/depois da rodada confirmatoria.
RC39 permaneceu development/paused/rollout=0, assinatura valida; stable/latest
permaneceu 0.1.0.7, ZIP SHA256
`88d73cf5146a7120da6d313645441f3e4a941b54ff18aded087216e9e1043c25`.
Hashes de version.json e checksums.json publicos permaneceram identicos.
CS-SRV-CST, TAXCEL, releases e politicas nao receberam writes desta validacao.

Cleanup PASS: DROP apenas dos bancos/roles temporarios, remocao dos worktrees
temporarios e script remoto. Consultas finais confirmaram zero databases/roles/
conexoes de validacao restantes e nenhum processo de teste ativo. Checkout de
producao permaneceu no mesmo HEAD, limpo, servico sem restart.
Todos os gates materiais 6B PASS; campanha/orquestrador/reconcile/auto-pause
continuam ausentes. Nenhuma nova migration, funcionalidade ou release criada.

### 6C.1 - Persistencia de campanhas e state machines (2026-09-17)

6C = IN PROGRESS; PHASE_6_ACTIVE_SUBPHASE = 6C. 6A/6B = CLOSED;
6D/6E = PENDING. PHASE_6_ORCHESTRATOR_ENABLED = false;
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false; AUTO_PAUSE_IMPLEMENTED = false.
Este incremento implementa somente decisao persistente, nao dispatch.
Os registros anteriores de ausencia dos models descrevem suas respectivas entregas.

- AgentRolloutCampaign: release FK protegida, snapshot dos artefatos/seguranca,
  channel, schema/hash 6B, instante/freshness, grupos, plano, contagens,
  concorrencia/observacao, criador/aprovador, motivo sanitizado e timestamps.
  URLs dos artefatos sao representadas por hashes separados, sem query sensivel.
- AgentRolloutWave: sequencia, quantidade absoluta, observacao, estado,
  estado de retomada e timestamps. AgentRolloutTarget: endpoint FK protegido,
  identidade/hostname/versoes/canal/politica/grupos/bucket/decisao congelados,
  razao de exclusao, wave e timestamps. Persistimos elegiveis E excluidos.
- Migration `agents.0031_agentrolloutcampaign_agentrolloutwave_and_more`:
  tabelas novas, uniques/partial uniques/checks e 14 guards em SQLite/PostgreSQL.
  Uma campanha nao terminal por release; sequence unica; endpoint unico por
  campanha; uma wave running/observing/paused por campanha; contagens nao negativas,
  concorrencia >= 1, observacao >= 0 e bucket 0-99. Guards garantem wave/target e
  current_wave da mesma campanha, snapshot imutavel apos READY inclusive em
  save/QuerySet.update/bulk_update e impedem reabrir/thaw de estado terminal/READY.
  PostgreSQL serializa writes dos filhos com o lock da campanha, inclusive approval.
- API POST `/api/agent/releases/<release_id>/rollout-campaigns/`: CSRF, usuario
  tecnico ativo e permissao `agents.add_agentrolloutcampaign`. Recebe schema/hash,
  grupos, freshness 60-3600, wave_plan, concurrency_limit 1-500, observacao minima,
  reason e ready opcional (default true). Nao existe endpoint de start/dispatch.
- Criacao atomica: lock da release, preview 6B recalculado sem novo algoritmo,
  hash/schema exatos; divergencia retorna 409 preview_changed sem persistencia.
  Auditoria obrigatoria agregada: campaign.created/ready/state_changed/aborted,
  wave.created/state_changed; nenhuma auditoria por Target. Falha desfaz tudo.
- Plano: counts positivos, observation_seconds >= minimo, somente uma remaining
  na ultima posicao, sem ondas vazias/excesso. READY exige todos os elegiveis
  atribuidos; draft admite plano parcial ou vazio. Zero elegiveis somente draft.
  Distribuicao persistida por (bucket, endpoint UUID); excluidos sem wave.
- Campaign: draft -> ready -> running; running <-> paused; running -> completed
  somente com todas as waves completed; nao terminais -> aborted. Abortar cancela
  waves nao terminais. Wave: pending -> ready -> running -> observing -> completed;
  running/observing -> paused -> estado anterior; nao terminal -> cancelled.
  Campanha running e predecessoras completed sao exigidas para iniciar uma wave;
  completar exige inicio/observacao e tempo minimo. Running/completed sao SOMENTE
  estados de dominio: nao provam dispatch, health ou resultados nesta entrega.
- Release alterada/pausada/revogada depois nao reescreve o snapshot historico.
  Snapshot NAO e autorizacao eterna: dispatch futuro devera revalidar seguranca.

Validacao final: SQLite agents/config, 279 testes, 276 PASS e 3 skips
(browser opt-in e dois testes exclusivos PostgreSQL), zero falhas, 43,449 s.
PostgreSQL isolado: 136 testes PASS, zero skips/falhas, 7,167 s.
check/py_compile/makemigrations --check/diff --check PASS; scan sensivel do diff:
zero matches. Testes sinteticos cobrem 250 targets, waves deterministicas,
exclusoes, stale preview por policy/grupo/updater/release/job/online/freshness,
imutabilidade, constraints, autorizacao/CSRF, state machines e rollback de auditoria.
PostgreSQL 17.10: forward vazio, reverse/reaplicacao com dados preexistentes
sinteticos sem alterar AgentMachine/AgentRelease, indexes/guards e duas conexoes
concorrentes (uma cria, outra 409), restart com 250 targets em nova conexao.
Banco e role descartaveis, sem clone de producao; cleanup confirmado.
Zero AgentJob criado pelo servico/estados de campanha; AgentJob de fixtures
regressivas existe somente no banco sintetico. Targets aceitam somente
eligible/excluded e agent_job NULL nesta entrega, com CHECK real no banco.
Nao houve deploy/migration em producao, restart, rollout, canario ou nova release.
RC39/stable/latest/CS-SRV-CST/TAXCEL permanecem fora do escopo operacional.

### 6C.2 - Orchestration planning e dispatch revalidation (2026-09-17)

6C = IN PROGRESS; 6A/6B = CLOSED; 6D/6E = PENDING.
PHASE_6_ORCHESTRATOR_ENABLED = false;
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false; AUTO_PAUSE_IMPLEMENTED = false.
Agora existem flags runtime reais, ambos default false:
`NIGHTOWL_ROLLOUT_ORCHESTRATOR_ENABLED` e
`NIGHTOWL_AUTOMATIC_ROLLOUT_ENABLED`. O helper central
`rollout_orchestration_enabled()` exige os dois para futura execucao automatica.
Planner funciona com flags OFF; mesmo ON nao existe executor/dispatch nesta entrega.

- 6B preview decide quem seria selecionado; 6C.1 congela quem foi aprovado;
  6C.2 revalida somente os Targets aprovados da Wave operacional existente.
  Nunca recalcula percentual/bucket, substitui bloqueados ou toma Targets da Wave 2.
  Campaign precisa estar running; Wave ready/running, predecessoras completed e
  current_wave coerente. Nenhuma transicao de estado e automatizada pelo planner.
- `agents/rollout_planning.py`: contexto em lote, avaliador individual com now aware,
  comparador central do contrato da release e plano deterministico por bucket/UUID.
  Seguranca atual verifica pausa/revogacao/status, assinatura, chave ativa/validade,
  algoritmo RSA-PSS-SHA256, URLs HTTPS no dominio permitido e hashes/tamanho completos.
  Usa validacao persistida da assinatura; nao baixa artefatos nem revalida RSA offline.
- Drift material: version, channel, sha256, size, manifest_sha256, signature_sha256,
  signature_key_id, minimum_updater_version, legacy_unsigned, mandatory,
  hashes das quatro URLs e allowed_groups. URLs completas nao entram no resultado.
  Percentual/status administrativo nao reescreve snapshot; pausa/revogacao/assinatura
  sao gates atuais independentes. Alteracao apenas de percentual/hostname nao reseleciona.
- Endpoints: identidade UUID igual ao snapshot e nao duplicada, ativo/installed/online,
  last_seen nao futuro dentro do freshness aprovado, sem pause/Critical/Servers,
  canal/politica/grupos/pin/consentimento e janela compatveis com aprovacao.
  Updater real persistido, sem fallback de agent_version; bootstrap/minimo incompativel,
  versao desconhecida/igual/maior bloqueiam. target_already_current nao prova sucesso.
- Novas aprovacoes enriquecem o JSON existente exclusion_metadata com
  dispatch_policy_snapshot (pin, auto_update_enabled e janela/timezone), sem migration.
  Campanhas antigas sem esse snapshot bloqueiam com target_policy_snapshot_incomplete:
  nao inferimos valores historicos nem alteramos Targets congelados. Exigem abort/recreate
  administrativo com novo preview aprovado antes de futura execucao.
- Jobs update_agent/repair_agent/uninstall_agent queued/sent/running bloqueiam;
  expirados ou criados ha mais de 900 segundos distinguem stale, sem reconciliacao.
  Capacidade = max(0, concurrency_limit - endpoints aprovados ocupados por esses jobs),
  contando cada endpoint uma vez em toda a Campaign, inclusive ocupacao stale.
  selected_for_dispatch contem no maximo essa capacidade; todos os resultados e razoes
  continuam no plano. Nao reserva, cria lease ou garante exclusao concorrente:
  duas chamadas podem selecionar os mesmos IDs. Idempotencia executora pertence a 6C.3.
- Command `python manage.py process_agent_rollouts --plan-only [--campaign <uuid>]`
  imprime JSON seguro, sem auditoria/writes/jobs. Sem --plan-only sempre recusa com
  ROLLOUT_DISPATCH_NOT_IMPLEMENTED, qualquer que seja a combinacao dos flags.
  Nenhuma API/botao Start adicional foi criado.

Reason codes explicitos: campaign_not_running, wave_not_dispatchable,
target_not_approved, release_revoked, release_paused, release_not_available,
release_contract_changed, signature_invalid, key_unknown, key_revoked,
key_not_yet_valid, key_expired, release_domain_invalid, release_metadata_incomplete,
endpoint_identity_changed, machine_identity_ambiguous, endpoint_inactive,
endpoint_lifecycle_terminal, endpoint_lifecycle_unknown, endpoint_offline,
endpoint_stale, endpoint_paused, protected_endpoint_group, endpoint_channel_changed,
endpoint_policy_changed, endpoint_groups_changed, pinned_release_mismatch,
target_policy_snapshot_incomplete, maintenance_window_invalid,
maintenance_timezone_invalid, outside_maintenance_window, update_job_active,
update_job_stale, updater_version_unknown, updater_bootstrap_required,
minimum_updater_incompatible, agent_version_unknown, target_already_current,
downgrade_requires_force, eligible_for_dispatch. Nao usamos texto de UI como decisao.

Validacao: agents/config SQLite 299 testes, 295 PASS e 4 skips (browser opt-in e
tres testes PostgreSQL), zero falhas. PostgreSQL isolado 156 PASS, zero skips/falhas.
Planner com 1 e 250 Targets: 8 SELECTs nos dois casos, ordem/now deterministas,
input invertido identico, capacity coberta e todas as sete superficies persistidas
(Campaign/Wave/Target/endpoint/release/job/audit) identicas antes/depois.
Drift individual, flags OFF/ON, command seguro, janela, snapshot legado fail-closed,
URL com query sintetica nao exposta e restart em nova conexao cobertos.
PostgreSQL 17.10: forward vazio, reverse/forward com dados sinteticos preservados,
14 guards e indexes intactos; banco/role temporarios removidos apos testes.

Migration 0031 e seus 14 guards NAO foram alterados. Target continua snapshot-only:
eligible/excluded, agent_job/started_at/completed_at NULL por CHECK.
6C.3 devera abrir execucao/AgentJob com nova migration e revisao deliberada dos
CHECKs/guards, nunca editando migration publicada. Nenhum scheduler/reconcile/auto-pause.
Zero AgentJob criado pelo planner/command; fixtures regressivas somente em bancos
descartaveis. Sem Campaign/job real, migration/deploy em producao ou restart.
RC39, stable/latest, CS-SRV-CST, TAXCEL e politicas reais nao foram alterados.

### 6C.3 - Dispatch idempotente e vinculo Target/AgentJob (2026-09-17)

6C = IN PROGRESS; 6D/6E = PENDING. PHASE_6_ORCHESTRATOR_ENABLED = false;
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false; AUTO_PAUSE_IMPLEMENTED = false.
Executor agora existe, mas continua desabilitado por default pelos dois flags runtime.
Nao existe scheduler/timer/cron, controle administrativo novo ou reconcile nesta entrega.

- `dispatch_rollout_campaign(campaign, now=aware)` exige ambos os flags ON,
  Campaign running e Wave operacional explicitamente running/current_wave correta.
  Wave ready permanece somente planejavel. Nao inicia/avanca/completa Campaign/Wave.
- Uma transacao adquire locks na ordem Campaign, Wave, Targets da Wave por PK,
  AgentMachines aprovados por PK, release e signing key. O lock inclui TODOS os
  endpoints aprovados da Campaign para estabilizar tambem a ocupacao da capacidade,
  nao somente os candidatos. Recarrega o contexto e revalida safety dentro dos locks;
  plano externo nao autoriza dispatch. Nenhuma substituicao ou uso da Wave seguinte.
- Protocolo comum `agents/lifecycle_jobs.py`: atomic + AgentMachine FOR UPDATE
  ordenado por PK + verificar lifecycle update/repair/uninstall queued/sent/running.
  Writers inventariados/adaptados: endpoint_job_create (update/repair/manual),
  endpoint_uninstall_request (painel), AgentSelfUninstallAuthorizeView (Tray),
  AgentUpdatePolicyView._dispatch_legacy_policy_update (GET legado), executor.
  GET continua legado, nao vira Campaign. Update existente pode ser reutilizado
  pelo GET; repair/uninstall existente bloqueia novo update. Historico duplicado
  ativo/stale apenas bloqueia: nao e reconciliado/cancelado/deduplicado.
- Payload reutiliza build_update_agent_job_payload/AgentUpdateDecision sem chamar
  evaluate_agent_update_policy nem selecao 6B. source=rollout_campaign, force=false,
  timeout=900, attempt=1, expires_at=now+30min, created_by=rollout_orchestrator,
  correlation_id=Target UUID. AgentJob.payload.rollout_metadata guarda Campaign/Wave/
  sequence/Target/cohort_hash/schema. Pull retira SOMENTE essa metadata administrativa
  de payload/parameters enviados ao agente; evita ampliar o contrato estrito RC39.
  URLs/hashes/assinatura/minimum updater mantem exatamente o builder comum existente.
- Job bulk_create, Target bulk_update eligible->queued com agent_job/started_at,
  e AuditEvent agregado rollout.dispatch_created pertencem ao mesmo lote atomico.
  Auditoria guarda IDs/contagem/release/hash, nao payload/URL/token. Qualquer falha
  desfaz jobs, links e auditoria. Repeticao/restart nunca troca job de Target runtime,
  mesmo se Job completed; Target permanece queued ate reconcile futuro da 6D.
- Capacidade preserva 6C.2: limite menos endpoints aprovados ocupados por lifecycle
  ativo/stale, contando endpoint uma vez. Jobs da propria Campaign ocupam o limite.
  Planner retorna target_already_dispatched para queued/running com job e
  target_runtime_terminal para succeeded/failed/rolled_back/cancelled;
  selected_for_dispatch continua contendo somente eligible + safety PASS.
- Command: --plan-only continua read-only com flags OFF; --execute exige --campaign
  UUID explicita e flags ON. Modos conflitantes/sem Campaign sao recusados. Nao executa
  todas as Campaigns implicitamente. Nenhum comando execute foi usado contra dados reais.

Migration nova `0032_rollout_target_execution`, sem editar 0031:
- Target states excluded/eligible/queued/running/succeeded/failed/rolled_back/cancelled.
  CHECK exige excluded sem wave/job/timestamps; eligible com wave e sem execucao;
  queued/running com wave/job/started_at e sem completed_at; terminais com esses
  campos mais completed_at. Todos runtime sao eligibility_at_selection=true.
- Unique condicional agent_job nao NULL; sem unique global por endpoint/lifecycle.
  16 guards: preserva os anteriores, troca target_freeze para congelar todos os
  snapshots exceto os cinco campos de execucao, acrescenta transicao e binding.
  eligible->queued; queued->running/terminal; running->terminal. Sem reabertura,
  troca/remocao de job associado ou alteracao dos timestamps finais persistidos.
- Forward recusa explicitamente drafts antigos com eligible sem Wave:
  ROLLOUT_WAVE_ASSIGNMENT_REQUIRED. Nao inventa distribuicao nem altera snapshots.
  Novas Campaigns exigem plano cobrindo todos os elegiveis inclusive em draft;
  zero elegiveis continua permitido somente em draft. Preparacao administrativa
  dos drafts preexistentes e pre-condicao antes de futura migration em producao.
- Reverse verifica PRIMEIRO ausencia de runtime/agent_job. Havendo qualquer um,
  ROLLBACK_UNSAFE e nenhuma remocao de vinculo/historico. Sem runtime, retorna ao
  CHECK/guards snapshot-only 0031; forward novamente preserva endpoints/releases.

Validacao sintetica: PostgreSQL 17.10 com database/role descartaveis, sem clone
nem uso do database de producao. Forward vazio/dados existentes, reverse antes
do dispatch e reverse recusado depois, 16 guards e unique/indexes PASS.
Concorrencia repetida (duas conexoes) com 250 Targets e limite 3: somente 3 jobs,
zero duplicacao; nova conexao/restart nao redespacha. Writer manual REAL do painel
versus Campaign testado nas duas ordens, incluindo espera comprovada pelo lock.
Rollback forcado em criacao de job, save do Target e audit preserva estado anterior.
Cenario 250 Targets, primeira Wave com 5 e duas ocupacoes preexistentes: 22 queries,
somente 1 novo job, capacidade esgotada, Wave seguinte intacta; sem N+1 material.
Regressoes de update/repair/uninstall/GET legado/pull/result incluidas na suite.

Nenhum deploy/migration de producao, .env/flag real alterado, restart, Campaign/job
real ou update real. RC39/stable/latest/CS-SRV-CST/TAXCEL permanecem intocados.

Resultado final: agents/config SQLite 312 testes, 305 PASS e 7 skips (browser
opt-in e seis testes PostgreSQL), zero falhas. PostgreSQL isolado 169 PASS,
zero skips/falhas; rodada confirmatoria 15,318 s. Django check, py_compile,
makemigrations --check --dry-run e git diff --check PASS; nenhuma migration
adicional alem de 0032 esperada. Scan do diff sem segredo literal novo.
Cleanup dos bancos/roles/arquivos temporarios confirmado antes do commit.
Rodada PostgreSQL ampliada: 217 testes PASS, zero skips/falhas, 17,033 s,
incluindo uninstall administrativo, diagnostics e job progress/pull/result.
Uninstall administrativo SQLite repetido apos revisao final: 13 PASS.

### 6C.4 - Control plane e execucao periodica segura (2026-09-21)

6C = CLOSED; PHASE_6_ACTIVE_SUBPHASE = 6D; 6D/6E = PENDING.
PHASE_6_STATUS = IN_PROGRESS. PHASE_6_ORCHESTRATOR_ENABLED = false;
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false; AUTO_PAUSE_IMPLEMENTED = false.
O schema/orchestrator desta fase ainda nao esta em producao. Nenhuma flag real foi
habilitada e nao houve deploy, migration, restart, Campaign ou AgentJob real.

Control plane administrativo:
- APIs POST/JSON de Campaign e Wave exigem usuario tecnico ativo, permissao
  `agents.change_agentrolloutcampaign`, CSRF, motivo e expected state. Leitura exige
  `agents.view_agentrolloutcampaign`. Locks e comparacao de estado/revisao retornam
  conflito 409 em decisoes obsoletas; audit e transicao pertencem a mesma transacao.
- Campaign expoe somente approve/start/pause/resume/abort. Wave expoe somente
  prepare/start/pause/resume/prepare_next_wave. Nao existem Complete, Force Advance,
  Retry Target, reconcile ou interpretacao de receipt/result nesta entrega.
- Start/resume para estado executavel exigem os dois kill switches ON. Approve,
  prepare, pause e abort continuam disponiveis com flags OFF quando o estado permite.
  Pause/abort preservam AgentJobs e estados runtime dos Targets; abort cancela apenas
  estados de dominio das Waves e o historico permanece intacto.
- A pagina `/agent-rollouts/` apresenta Campaign, Waves, 250 Targets, blockers,
  contagens runtime e flags; nao mostra payloads, tokens ou URLs de artefato. Acoes
  invalidas ficam ausentes e Start/Resume ficam bloqueados e explicados com flags OFF.

Runner:
- `process_agent_rollouts --run-once` e modo explicito, mutuamente exclusivo de
  `--plan-only` e `--execute`. Nao aceita Campaign; considera apenas Campaign running
  cuja current Wave esteja running e somente chama o executor idempotente 6C.3.
  Nunca transiciona Campaign/Wave, avanca Wave ou reconcilia resultado.
- Um advisory lock PostgreSQL global nao bloqueante permite uma rodada por vez;
  concorrente retorna `already_running`. Lock e liberado em sucesso/erro. SQLite nao
  simula essa garantia. Erros de contrato conhecidos bloqueiam apenas a Campaign;
  erro inesperado/database aborta a rodada. Rodada vazia nao cria AuditEvent.
- Preflight read-only oficial antes da 0032:
  `python manage.py check_rollout_migration_readiness`. O resultado obrigatorio e
  `eligible_without_wave=0`; valor maior bloqueia o deploy, sem autoatribuir Wave.

Mecanismo periodico planejado, NAO instalado/ativado nesta entrega:
```ini
# /etc/systemd/system/nightowl-rollout-orchestrator.service
[Unit]
Description=NightOwl rollout orchestrator (one safe round)
After=network-online.target postgresql.service nightowl.service

[Service]
Type=oneshot
User=nightowl
Group=nightowl
WorkingDirectory=/opt/nightowl
EnvironmentFile=/opt/nightowl/.env
ExecStart=/opt/nightowl/.venv/bin/python manage.py process_agent_rollouts --run-once
NoNewPrivileges=true
PrivateTmp=true

# /etc/systemd/system/nightowl-rollout-orchestrator.timer
[Unit]
Description=Run NightOwl rollout orchestrator once per minute
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
Persistent=false
[Install]
WantedBy=timers.target
```
O EnvironmentFile deve permanecer protegido para o usuario do servico. Nao executar
daemon-reload/enable/start ate decisao operacional explicita e flags duplas ON.

6D.1 implementa metrics/reconcile, validacao de receipt/exit/version/health e
progresso objetivo. Auto-pause permanece explicitamente fora deste incremento.
Nenhum Target foi marcado success/failure pela 6C.

Gate final 6C.4: SQLite agents/config 327 testes, 316 PASS e 11 skips
(PostgreSQL/browser opt-in), zero falhas. PostgreSQL 17.10 isolado: regressao
ampliada 231 PASS e rodada final focada 15 PASS; concorrencia administrativa,
dois runners, lock/restart/error cleanup, tres Waves, 250 Targets e schema 0031
readiness PASS. Detail UI com 250 Targets: 14 queries; run-once: 25 queries;
dispatch 6C.3 permaneceu em 22 queries. Lifecycle scripts, Django check, py_compile,
node --check, makemigrations --check --dry-run e git diff --check PASS. Cleanup
confirmou zero databases/roles temporarios. Nenhuma migration nova foi criada.

### 6D.1 - Reconciliacao e metricas observaveis (2026-09-21)

6C = CLOSED; PHASE_6_ACTIVE_SUBPHASE = 6D; 6D = IN PROGRESS; 6E = PENDING.
PHASE_6_ORCHESTRATOR_ENABLED = false;
PHASE_6_AUTOMATIC_ROLLOUT_ENABLED = false; AUTO_PAUSE_IMPLEMENTED = false.
Esta entrega nao instala timer, nao habilita flags, nao avanca/completa Campaign ou
Wave e nao cria, repete ou modifica AgentJob. O dispatch 6C e a reconciliacao 6D.1
continuam processos separados.

Contrato de evidencia:
- `evaluate_rollout_target` e puro sobre Target/Job/receipt/status ja carregados.
  `summarize_rollout_campaign` somente le e produz metricas de Campaign e de cada
  Wave com denominadores seguros, sem AuditEvent ou mudanca de estado.
- `reconcile_rollout_campaign` bloqueia Campaign e Targets em ordem deterministica e
  altera somente `AgentRolloutTarget.state`, `completed_at` e `updated_at`, conforme
  os guards da 0032. Rodadas repetidas/restart sao idempotentes.
- Binding Campaign/Target/Job exige tipo update_agent, endpoint, release,
  correlation_id e rollout_metadata de Campaign/Wave/Target/coorte exatos. Divergencia
  falha fechada como `binding_invalid`; FK e payload nunca sao reparados.
- Sucesso requer Job completed, exit_code zero, resultado final recebido, result_id,
  receipt do mesmo Job/endpoint sem conflito, versao instalada e versao atual do
  endpoint iguais ao alvo e health confirmado. Resultado final e a fonte primaria;
  `AgentOperationalStatus` e evidencia complementar vinculada ao mesmo Job.
- Completed incompleto permanece running com classificacao waiting_result_receipt,
  waiting_result_evidence, waiting_version ou waiting_health. Receipt conflitante
  bloqueia sucesso. queued/sent permanecem queued; running permanece running;
  falhas/timeout/expired/interrupted/duplicate terminam failed; cancelled e
  rolled_back preservam estados proprios; rollback_failed termina failed com metrica
  distinta. Target terminal nunca reabre por evidencia tardia conflitante.
- Stale/timeout usam `job_stale_info` e sao sinais observaveis, sem alterar AgentJob ou
  fabricar falha. `offline_post_update` contabiliza Target despachado nao terminal cujo
  endpoint esta offline/stale.

Metricas read-only: total, excluded, eligible_initial, dispatched, queued, running,
succeeded, failed, rolled_back, cancelled, terminal, in_flight, waiting_health,
stalled, offline_post_update, rollback_failed, receipt_conflict, binding_invalid e
taxas de sucesso/falha/conclusao. Wave inclui apenas seus Targets; excluded pertence
somente ao agregado da Campaign. A pagina `/agent-rollouts/` mostra apenas essas
metricas e classificacao/razao/versao sanitizadas, sem payload, URL, stdout/stderr ou
segredo e sem novos controles mutaveis.

Execucao explicita:
- `python manage.py reconcile_agent_rollouts --campaign <UUID>` reconcilia uma
  Campaign; `--run-once` percorre Campaigns relevantes. Funciona com os dois flags OFF
  e continua observando Jobs ja despachados em Campaign paused/aborted.
- O comando nao substitui `process_agent_rollouts`; nenhum scheduler/timer foi criado.
- Audit `rollout.reconciled` existe somente quando ha transicao. Inconsistencia de
  binding/evidencia terminal gera audit agregado deduplicado por fingerprint; no-op
  normal nao gera ruido.

Matriz 6D.1: sucesso completo; completed sem exit/result/receipt/health/versao;
receipt conflitante; exit nao zero (incluindo 10); failed/timed_out/expired/
interrupted/cancelled/rolled_back/rollback_failed/duplicate; queued/sent/running;
stale/offline; binding invalido; Target terminal; 250 Targets com consultas limitadas;
duas reconciliacoes concorrentes; corrida com escrita de resultado e convergencia na
rodada seguinte. Auto-pause, auto-advance, completion automatica, retry e rollout real
permanecem fora do escopo e pendentes para incrementos posteriores da 6D.

Gate 6D.1: SQLite agents/config 338 testes, 325 PASS e 13 skips opt-in;
regressao 6B/6C focada 112 testes, 100 PASS e 12 skips. PostgreSQL 17.10 isolado
na migration 0032: 11 testes 6D.1 PASS e regressao ampliada 207 PASS, incluindo
concorrencia, restart, pull/result, waiting health, target-not-installed, repair e
uninstall. Resumo read-only de 250 Targets ficou abaixo de 15 queries; detail UI
ficou em 18 queries. Django check, py_compile, node --check, makemigrations
--check --dry-run, lifecycle scripts e git diff --check PASS. Banco/role PostgreSQL
temporarios foram removidos; banco de producao nao foi usado. Nenhuma migration foi
criada e nenhum deploy, Campaign, AgentJob ou rollout real foi executado.
