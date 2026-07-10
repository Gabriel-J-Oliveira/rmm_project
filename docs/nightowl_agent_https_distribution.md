# Distribuicao HTTPS do agente NightOwl

## Objetivo

Substituir a distribuicao principal por SMB pelo download direto via HTTPS 443 no proprio servidor NightOwl.

Dominios recomendados:

- `https://rmm.controlsul.com`
- `https://nightowl.controlsul.com`

Use certificado publico confiavel para atender maquinas fora do dominio. Nao dependa de CA do AD para este fluxo.

## Estrutura no servidor

Publicar os arquivos em:

```text
/opt/nightowl/downloads/agent/windows/
```

Arquivos esperados:

```text
NightOwl.Agent.Windows.zip
Install-NightOwlAgentDotNet.ps1
Uninstall-NightOwlAgentDotNet.ps1
checksums.json
version.json
```

## Gerar pacote local

No workspace do projeto:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\NightOwl.Agent.Windows\scripts\Publish-NightOwlAgentDownload.ps1
```

Saida local:

```text
NightOwl.Agent.Windows\publish\downloads\agent\windows\
```

Copie o conteudo dessa pasta para:

```text
/opt/nightowl/downloads/agent/windows/
```

## Nginx

Adicionar no server HTTPS do NightOwl:

```nginx
location /downloads/nightowl-agent/ {
    alias /opt/nightowl/downloads/agent/windows/;
    autoindex off;
    add_header Cache-Control "no-store";
}
```

Depois:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Validar:

```bash
curl -I https://rmm.controlsul.com/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1
curl -I https://rmm.controlsul.com/downloads/nightowl-agent/NightOwl.Agent.Windows.zip
curl https://rmm.controlsul.com/downloads/nightowl-agent/version.json
```

## Instalar via HTTPS

Bootstrap recomendado no endpoint Windows, em PowerShell como Administrador:

```powershell
$dir = "$env:TEMP\NightOwlAgent"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Invoke-WebRequest "https://rmm.controlsul.com/downloads/nightowl-agent/Install-NightOwlAgentDotNet.ps1" -OutFile "$dir\Install-NightOwlAgentDotNet.ps1" -UseBasicParsing
powershell.exe -ExecutionPolicy Bypass -File "$dir\Install-NightOwlAgentDotNet.ps1" -ServerUrl "https://rmm.controlsul.com" -EnrollmentToken "TOKEN" -InstallAsService -RunCheck
```

Se `-PackageUrl` nao for informado, o instalador deriva automaticamente:

```text
<ServerUrl>/downloads/nightowl-agent/NightOwl.Agent.Windows.zip
```

Tambem e possivel informar explicitamente:

```powershell
powershell.exe -ExecutionPolicy Bypass -File "$dir\Install-NightOwlAgentDotNet.ps1" -ServerUrl "https://rmm.controlsul.com" -EnrollmentToken "TOKEN" -PackageUrl "https://rmm.controlsul.com/downloads/nightowl-agent/NightOwl.Agent.Windows.zip" -InstallAsService -RunCheck
```

## Segurança

- O download do instalador e ZIP pode ser publico inicialmente.
- A ativacao exige `EnrollmentToken`.
- O enrollment troca `EnrollmentToken` por `agent_token` individual do endpoint.
- O `EnrollmentToken` nao fica salvo como token operacional.
- Use tokens com expiracao e limite de uso.
- Tentativas invalidas de enrollment sao registradas.
- `-AllowInsecureTls` existe apenas para laboratorio e nao deve ser usado em producao.

## Contrato de enrollment

Endpoint:

```text
POST /api/agent/enroll/
```

Payload:

```json
{
  "enrollment_token": "enroll_xxx",
  "machine_id": "stable-machine-id",
  "hostname": "CS-CVEL-0254",
  "fqdn": "CS-CVEL-0254.control.local",
  "domain": "control.local",
  "os_name": "Microsoft Windows 11 Pro",
  "agent_version": "0.1.0",
  "agent_mode": "dotnet-service",
  "serial_number": "ABC123"
}
```

Resposta:

```json
{
  "status": "ok",
  "agent_token": "rmm_live_xxx",
  "endpoint_id": "uuid",
  "machine_id": "stable-machine-id",
  "server_time": "2026-07-09T19:00:00Z",
  "config": {
    "heartbeat_seconds": 300,
    "jobs_seconds": 10,
    "collect_seconds": 3600
  }
}
```

## Validação operacional

Depois de instalar:

- Servico `NightOwlAgentDotNet` instalado e `Running`.
- Log em `C:\ProgramData\NightOwl\Logs\agent-dotnet.jsonl`.
- Evento `heartbeat.sent`.
- Endpoint aparece no NightOwl.
- Jobs `ping`, `collect_disks`, `collect_software` e `force_inventory` executam.

## Fora desta fase

- Execucao remota arbitraria de script.
- Deploy real de software.
- Reboot remoto.
- Instalacao de patches.
