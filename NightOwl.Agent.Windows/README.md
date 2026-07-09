# NightOwl.Agent.Windows

MVP do agente Windows oficial do NightOwl RMM em .NET Worker Service. Ele nasce em paralelo ao agente PowerShell atual e usa as mesmas APIs Django.

## Escopo do MVP

- Roda como servico Windows separado: `NightOwlAgentDotNet`.
- Envia heartbeat para `POST /api/agent/heartbeat/`.
- Envia coleta agregada para `POST /api/agent/collect/`.
- Faz pull em `GET /api/agent/jobs/pull/`.
- Envia resultado para `POST /api/agent/jobs/result/`.
- Executa apenas jobs seguros: `ping`, `collect_logs`, `collect_disks`, `collect_software`, `collect_security`, `force_inventory`.
- Escreve logs JSONL em `C:\ProgramData\NightOwl\Logs\agent-dotnet.jsonl`.

## Nao faz ainda

- Deploy real de software.
- Execucao arbitraria livre de scripts.
- Reboot remoto.
- Auto update do agente.
- Agente Linux.

## Estrutura local

- `C:\ProgramData\NightOwl\Agent\` para config/state compartilhados.
- `C:\ProgramData\NightOwl\AgentDotNet\` para binarios publicados do agente .NET.
- `C:\ProgramData\NightOwl\Logs\` para logs.
- `C:\ProgramData\NightOwl\Jobs\` para estado futuro de jobs.
- `C:\ProgramData\NightOwl\Packages\` para pacotes futuros.
- `C:\ProgramData\NightOwl\Cache\` para cache futuro.

## Build/publish

Instale o .NET SDK 8+ e rode:

```powershell
dotnet publish .\NightOwl.Agent.Windows\NightOwl.Agent.Windows.csproj -c Release -r win-x64 --self-contained true -o .\publish\NightOwl.Agent.Windows
```

Edite `agent.config.json` com o token do endpoint e a URL base do servidor NightOwl.

## Instalacao do servico

Execute como Administrador:

```powershell
.\NightOwl.Agent.Windows\scripts\Install-NightOwlAgentDotNet.ps1 -PublishPath .\publish\NightOwl.Agent.Windows
```

Para remover:

```powershell
.\NightOwl.Agent.Windows\scripts\Uninstall-NightOwlAgentDotNet.ps1
```

## Contrato esperado do backend

O pull de jobs deve retornar sempre:

```json
{ "jobs": [] }
```

ou:

```json
{
  "jobs": [
    {
      "id": "uuid",
      "type": "ping",
      "payload": {},
      "created_at": "2026-07-09T12:00:00Z",
      "timeout_seconds": 300
    }
  ]
}
```

O agente envia coletas agregadas com `system`, `hardware`, `network`, `disks`, `software`, `security` e `patches`.
