# Manifesto da release candidata 0.1.1.0-rc1

## Identificacao

- Produto: NightOwl Agent Windows
- Versao candidata: `0.1.1.0-rc1`
- Canal inicial: `development`
- Rollout inicial: `0%`
- Rollout pausado: `true`
- Publicacao automatica em stable/latest: `false`
- Minimum updater: `0.1.0.7`
- Commit base no momento da preparacao: `a33902e38a8731af3ab4355ff9454f7cd02d7dc2`
- Preparado em UTC: `2026-07-23T15:31:50Z`

## Componentes esperados

- `NightOwl.Agent.Windows.exe`
- `NightOwl.Agent.Tray.exe`
- `NightOwl.Agent.Updater.exe`
- `NightOwl.Agent.Diagnostics.exe`
- `NightOwl.Agent.Shared.dll`
- `assets/icons/NightOwl.ico`
- `agent.version.json`
- `Install-NightOwlAgentDotNet.ps1`
- `Uninstall-NightOwlAgentDotNet.ps1`
- `version.json`
- `checksums.json`
- `release-manifest.json`

## Migrations incluidas

- `agents.0020_agentjob_attempt_agentjob_correlation_id_and_more`
- `agents.0021_agentreleasegroup_agentmachine_auto_update_enabled_and_more`

## Guardrails da candidata

- A release deve ser importada pausada.
- A release deve ter `rollout_percentage=0`.
- A release deve ficar em `development` ate validacao real.
- Nenhum job automatico deve ser criado no momento da importacao.
- `version.json` publico stable nao deve ser sobrescrito pela RC.

## Testes planejados

- `dotnet clean`
- `dotnet restore`
- `dotnet build -c Release`
- `dotnet test -c Release`
- `tests\e2e\Test-NightOwlAgentE2E.ps1 -Mode Simulated -NonInteractive`
- `python manage.py check`
- `python manage.py makemigrations --check`
- `python manage.py collectstatic --noinput`
- `python manage.py showmigrations`
- `python manage.py migrate --plan`

## Testes ainda fora desta preparacao

- Integration contra backend de teste.
- WindowsVm com instalacao limpa.
- WindowsVm com update normal.
- WindowsVm com falha pos-troca e rollback.
- Reboot durante `waiting_health_check`.
- Repair/Reinstall em endpoint Windows real.

## Hashes finais

- `NightOwl.Agent.Windows.zip`: `d79301675902848d2eb2d2f0561f65cbc3e5b4f62d06bd2f5e62c8a9686ec533`
- `version.json`: `a25474c04d0057235f1d1007c31e1e52672d8ba21934a46bad1c0ac1ebbf23b0`
- `checksums.json`: `90b6453f6c4edc11c44bec84705fe13d62c0fc3f02badd489669b8d471ba1e67`
- `release-manifest.json`: `58105a3e364ec4ab6ad0ff2068435b65dc4d8093d3eed87449730037abad062d`
- `Install-NightOwlAgentDotNet.ps1`: `29a5ca38130cd5a95f6a53aa9bb1350c7eb227f17ff452b10ed7ed8851f4320b`
- `Uninstall-NightOwlAgentDotNet.ps1`: `e0c9c6fbb8719e84831cfc9d239ae644801cb60b1ba48bbb8adde2aeb9f53bb5`
- `NightOwl.ico`: `af0b28fd42b1bf58b10f6d629ceb3a9030f0ec9d348a1054db5d63ace0870ada`
