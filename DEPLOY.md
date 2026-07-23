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
0.1.1.0-rc1
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
