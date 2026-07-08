# NightOwl RMM - contratos futuros de API

Este documento descreve os contratos esperados pelo frontend do NightOwl RMM com base na camada mockada atual `MockNightowlApi` (`static/js/mock_nightowl_api.js`).

Escopo:

- Documentar formato esperado de dados para backend/agente futuros.
- Preservar os nomes usados atualmente no frontend sempre que possivel.
- Servir como ponte para substituir `MockNightowlApi` por endpoints reais.

Fora de escopo:

- Implementar backend real.
- Implementar autenticacao de agente.
- Definir politica final de seguranca, RBAC ou multi-tenant.

## Tipos base

### Status e enums

```text
Severity:
success | info | warning | critical | security | muted

EndpointStatus:
online | offline | unknown | critical

AlertStatus:
open | acknowledged | muted | resolved

JobStatus:
queued | sent | running | completed | failed | expired | cancelled

JobType:
force_inventory | defender_check | disk_check | collect_logs | ping |
cleanup_temp | run_script | install_software | windows_update_scan

EventCategory:
agent | system | alerts | jobs | security | inventory | maintenance
```

## 1. Endpoints

### GET `/api/endpoints/`

Objetivo: listar endpoints para Dashboard, NOC, Endpoints e filtros operacionais.

Query params planejados:

- `q`: busca por hostname, IP, usuario, setor, dominio, sistema ou atencao.
- `status`: `online`, `offline`, `unknown`, `critical`.
- `type`: `server`, `workstation`, `notebook`.
- `sector`: setor/tag.
- `agent`: `current`, `outdated`, `unknown`.

Resposta:

```json
{
  "results": [
    {
      "id": "00000000-0000-4000-8000-000000000101",
      "hostname": "FIN-012",
      "status": "online",
      "ip": "192.168.104.42",
      "user": "Mariana Souza",
      "sector": "Financeiro",
      "domain": "CONTROL",
      "os": "Windows 11 Pro 23H2",
      "type": "workstation",
      "healthScore": 46,
      "attention": "Defender critico",
      "agent": {
        "version": "1.4.2",
        "recommendedVersion": "1.4.2",
        "state": "current",
        "lastRun": "ha 3 min",
        "nextHeartbeat": "~5 min"
      }
    }
  ],
  "count": 1
}
```

Campos principais:

- `id`: identificador unico do endpoint.
- `hostname`: nome principal exibido em tabelas/cards.
- `status`: estado operacional.
- `healthScore`: 0 a 100.
- `attention`: principal motivo de atencao.
- `agent`: resumo do agente NightOwl.

### GET `/api/endpoints/:id/`

Objetivo: ficha operacional completa do endpoint individual.

Resposta:

```json
{
  "id": "00000000-0000-4000-8000-000000000105",
  "hostname": "SRV-ERP-01",
  "status": "online",
  "ip": "192.168.104.10",
  "user": "svc-erp",
  "sector": "Infraestrutura",
  "domain": "CONTROL",
  "os": "Windows Server 2022",
  "type": "server",
  "healthScore": 58,
  "attention": "Disco 93%",
  "agent": {
    "version": "1.4.2",
    "recommendedVersion": "1.4.2",
    "state": "current",
    "mode": "PowerShell agendado",
    "path": "C:\\RMM",
    "source": "\\\\192.168.104.120\\controlsul\\Comum\\_Agents",
    "runtime": "PowerShell 5.1",
    "lastRun": "ha 2 min",
    "nextHeartbeat": "~5 min",
    "lastError": ""
  },
  "security": {
    "antivirus": "Bitdefender",
    "status": "ok",
    "signature": "2026-07-08 10:11",
    "firewall": "Ativo",
    "bitlocker": "Nao aplicavel",
    "remoteTools": []
  },
  "disks": [
    {
      "name": "C:",
      "usedPercent": 93,
      "totalGb": 512,
      "freeGb": 36,
      "severity": "critical"
    }
  ],
  "software": [],
  "alerts": [],
  "events": [],
  "jobs": []
}
```

### GET `/api/endpoints/:id/events/`

Objetivo: listar timeline tecnica relacionada ao endpoint.

Resposta:

```json
{
  "results": [
    {
      "id": "E-003",
      "title": "Disco em atencao",
      "eventType": "alert.created",
      "severity": "warning",
      "category": "alerts",
      "source": "System",
      "endpointId": "00000000-0000-4000-8000-000000000105",
      "endpoint": "SRV-ERP-01",
      "actor": "NightOwl",
      "description": "Disco C: acima de 90% no servidor ERP.",
      "timestamp": "2026-07-08T10:42:00-03:00"
    }
  ]
}
```

### GET `/api/endpoints/:id/alerts/`

Objetivo: listar alertas relacionados ao endpoint.

Resposta: mesmo formato de `AlertItem`, documentado em Alertas.

### GET `/api/endpoints/:id/jobs/`

Objetivo: listar jobs/tarefas relacionados ao endpoint.

Resposta: mesmo formato de `JobItem`, documentado em Jobs.

## 2. Alertas

### GET `/api/alerts/`

Objetivo: alimentar Central de Alertas, NOC e detalhes de endpoint.

Query params planejados:

- `q`: busca por titulo, endpoint, descricao, tipo ou responsavel.
- `status`: `open`, `acknowledged`, `muted`, `resolved`.
- `severity`: `info`, `warning`, `critical`, `security`.
- `endpointId`: endpoint relacionado.

Resposta:

```json
{
  "results": [
    {
      "id": "A-1048",
      "endpointId": "00000000-0000-4000-8000-000000000101",
      "endpoint": "FIN-012",
      "title": "Bitdefender ausente em FIN-012",
      "description": "O agente detectou ausencia do Bitdefender na maquina financeira FIN-012.",
      "severity": "critical",
      "status": "open",
      "type": "security_antivirus",
      "owner": "Nao atribuido",
      "age": "ha 6 min",
      "ticket": ""
    }
  ],
  "count": 1
}
```

Status possiveis:

- `open`: novo/aberto.
- `acknowledged`: reconhecido por tecnico.
- `muted`: silenciado temporariamente.
- `resolved`: resolvido.

### POST `/api/alerts/:id/acknowledge/`

Objetivo: reconhecer alerta e registrar evento operacional.

Payload:

```json
{
  "actor": "gabriel.oliveira",
  "note": "Em analise pelo NOC."
}
```

Resposta:

```json
{
  "id": "A-1048",
  "status": "acknowledged",
  "owner": "Gabriel Oliveira",
  "updatedAt": "2026-07-08T11:00:00-03:00",
  "event": {
    "eventType": "alert.acknowledged",
    "category": "alerts"
  }
}
```

### POST `/api/alerts/:id/resolve/`

Objetivo: resolver alerta manualmente.

Payload:

```json
{
  "actor": "gabriel.oliveira",
  "resolution": "Bitdefender reinstalado e confirmado no endpoint."
}
```

Resposta:

```json
{
  "id": "A-1048",
  "status": "resolved",
  "resolvedAt": "2026-07-08T11:04:00-03:00"
}
```

### POST `/api/alerts/:id/silence/`

Objetivo: silenciar alerta por periodo controlado.

Payload:

```json
{
  "duration": "1h",
  "reason": "Janela de manutencao",
  "actor": "gabriel.oliveira"
}
```

Resposta:

```json
{
  "id": "A-1048",
  "status": "muted",
  "mutedUntil": "2026-07-08T12:04:00-03:00"
}
```

### POST `/api/alerts/:id/comment/`

Objetivo: adicionar observacao operacional ao alerta.

Payload:

```json
{
  "message": "Cliente confirmou impacto no financeiro.",
  "visibility": "internal",
  "actor": "gabriel.oliveira"
}
```

Resposta:

```json
{
  "id": "C-9001",
  "alertId": "A-1048",
  "message": "Cliente confirmou impacto no financeiro.",
  "createdAt": "2026-07-08T11:05:00-03:00"
}
```

## 3. Eventos

### GET `/api/events/`

Objetivo: alimentar a linha do tempo tecnica/auditoria operacional.

Query params planejados:

- `q`: texto livre.
- `category`: `agent`, `system`, `alerts`, `jobs`, `security`, `inventory`, `maintenance`.
- `severity`: severidade.
- `endpointId`: endpoint relacionado.
- `period`: `24h`, `7d`, `30d`, `all`.

Resposta:

```json
{
  "results": [
    {
      "id": "E-002",
      "title": "Alerta critico criado",
      "eventType": "alert.created",
      "severity": "critical",
      "category": "alerts",
      "source": "System",
      "endpointId": "00000000-0000-4000-8000-000000000101",
      "endpoint": "FIN-012",
      "actor": "NightOwl",
      "description": "Bitdefender ausente detectado no FIN-012.",
      "timestamp": "2026-07-08T10:55:00-03:00"
    }
  ],
  "count": 1
}
```

### GET `/api/events/:id/`

Objetivo: abrir drawer/detalhe de evento.

Resposta:

```json
{
  "id": "E-002",
  "title": "Alerta critico criado",
  "eventType": "alert.created",
  "severity": "critical",
  "category": "alerts",
  "source": "System",
  "endpointId": "00000000-0000-4000-8000-000000000101",
  "endpoint": "FIN-012",
  "actor": "NightOwl",
  "description": "Bitdefender ausente detectado no FIN-012.",
  "timestamp": "2026-07-08T10:55:00-03:00",
  "payload": {
    "alertId": "A-1048",
    "rule": "security_antivirus",
    "detectedValue": "missing"
  }
}
```

## 4. Jobs

### GET `/api/jobs/`

Objetivo: listar execucoes remotas e rotinas operacionais.

Query params planejados:

- `q`: endpoint, tipo, comando, resultado ou criado por.
- `status`: `queued`, `sent`, `running`, `completed`, `failed`, `expired`, `cancelled`.
- `type`: tipo de tarefa.
- `endpointId` ou `endpoint`.
- `period`: `24h`, `7d`, `all`.

Resposta:

```json
{
  "results": [
    {
      "id": "J-002",
      "endpointId": "00000000-0000-4000-8000-000000000105",
      "endpoint": "SRV-ERP-01",
      "type": "disk_check",
      "name": "Verificacao de disco",
      "status": "queued",
      "command": "nightowl.disk.check",
      "createdBy": "Renan Santos",
      "createdAt": "2026-07-08T10:56:00-03:00",
      "startedAt": "",
      "finishedAt": "",
      "durationMs": 0,
      "result": "Aguardando agente",
      "stdout": "",
      "stderr": "",
      "exitCode": null,
      "payload": {
        "endpoint": "SRV-ERP-01",
        "volumes": ["C:", "D:"]
      },
      "timeline": ["queued"]
    }
  ],
  "count": 1
}
```

Status possiveis:

- `queued`: criado, aguardando envio/pull.
- `sent`: entregue ao agente.
- `running`: em execucao.
- `completed`: finalizado com sucesso.
- `failed`: finalizado com erro.
- `expired`: expirou antes de executar.
- `cancelled`: cancelado pelo operador/sistema.

### POST `/api/jobs/`

Objetivo: criar tarefa remota para um endpoint.

Payload:

```json
{
  "endpointId": "00000000-0000-4000-8000-000000000105",
  "type": "disk_check",
  "command": "nightowl.disk.check",
  "createdBy": "gabriel.oliveira",
  "payload": {
    "volumes": ["C:", "D:"]
  }
}
```

Resposta:

```json
{
  "id": "J-local-1783528200000",
  "endpointId": "00000000-0000-4000-8000-000000000105",
  "endpoint": "SRV-ERP-01",
  "type": "disk_check",
  "status": "queued",
  "command": "nightowl.disk.check",
  "createdAt": "2026-07-08T11:10:00-03:00"
}
```

### GET `/api/jobs/:id/`

Objetivo: retornar detalhe completo para drawer de jobs.

Resposta: mesmo formato de `JobItem`, com `payload`, `stdout`, `stderr`, `exitCode`, `timeline` e eventos relacionados.

### POST `/api/jobs/:id/cancel/`

Objetivo: cancelar job pendente/em execucao, se permitido.

Payload:

```json
{
  "actor": "gabriel.oliveira",
  "reason": "Acao disparada por engano"
}
```

Resposta:

```json
{
  "id": "J-002",
  "status": "cancelled",
  "finishedAt": "2026-07-08T11:12:00-03:00",
  "result": "Cancelado pelo operador"
}
```

### POST `/api/jobs/:id/retry/`

Objetivo: reexecutar job, criando novo registro vinculado ao anterior.

Payload:

```json
{
  "actor": "gabriel.oliveira"
}
```

Resposta:

```json
{
  "id": "J-local-1783528300000",
  "rerunOf": "J-002",
  "status": "queued",
  "type": "disk_check"
}
```

## 5. Agente

### POST `/api/agent/heartbeat/`

Objetivo: agente informa que esta vivo e envia estado basico.

Payload:

```json
{
  "agentId": "FIN-012",
  "hostname": "FIN-012",
  "ip": "192.168.104.42",
  "agentVersion": "1.4.2",
  "status": "online",
  "loggedUser": "Mariana Souza",
  "timestamp": "2026-07-08T11:15:00-03:00"
}
```

Resposta:

```json
{
  "accepted": true,
  "nextHeartbeatSeconds": 300
}
```

### POST `/api/agent/inventory/`

Objetivo: agente envia snapshot de inventario, seguranca, discos, softwares e sistema.

Payload:

```json
{
  "agentId": "FIN-012",
  "hostname": "FIN-012",
  "collectedAt": "2026-07-08T11:15:00-03:00",
  "os": "Windows 11 Pro 23H2",
  "disks": [
    {
      "name": "C:",
      "usedPercent": 56,
      "totalGb": 512,
      "freeGb": 225
    }
  ],
  "security": {
    "antivirus": "Defender",
    "status": "ok",
    "signature": "2026-07-08 10:11",
    "firewall": "Ativo",
    "bitlocker": "Ativo",
    "remoteTools": []
  },
  "software": [
    {
      "name": "Microsoft 365 Apps",
      "category": "microsoft",
      "risk": "low",
      "version": "2407",
      "publisher": "Microsoft Corporation",
      "installedAt": "2026-07-08T10:15:00-03:00"
    }
  ]
}
```

Resposta:

```json
{
  "accepted": true,
  "eventsCreated": 3,
  "alertsCreated": 1
}
```

### GET `/api/agent/jobs/pull/`

Objetivo: agente consulta tarefas pendentes para executar.

Query params planejados:

- `agentId`: identificador do agente.
- `limit`: quantidade maxima de jobs.

Resposta:

```json
{
  "jobs": [
    {
      "id": "J-002",
      "type": "disk_check",
      "command": "nightowl.disk.check",
      "payload": {
        "volumes": ["C:", "D:"]
      },
      "timeoutSeconds": 300
    }
  ]
}
```

Efeito esperado no backend:

- Job muda de `queued` para `sent` quando entregue.
- Evento `job.sent` e auditoria tecnica sao registrados.

### POST `/api/agent/jobs/result/`

Objetivo: agente envia resultado de execucao.

Payload:

```json
{
  "jobId": "J-002",
  "agentId": "SRV-ERP-01",
  "status": "completed",
  "startedAt": "2026-07-08T11:16:00-03:00",
  "finishedAt": "2026-07-08T11:16:42-03:00",
  "durationMs": 42000,
  "exitCode": 0,
  "stdout": "C: used=93%; D: used=61%",
  "stderr": "",
  "result": {
    "disks": [
      {
        "name": "C:",
        "usedPercent": 93
      }
    ]
  }
}
```

Resposta:

```json
{
  "accepted": true,
  "jobStatus": "completed",
  "eventsCreated": 1,
  "alertsUpdated": 1
}
```

## Observacoes de compatibilidade frontend

- O frontend atual espera camelCase nos campos (`healthScore`, `createdAt`, `endpointId`).
- Datas devem ser ISO 8601.
- IDs podem ser UUIDs ou strings estaveis, desde que nao mudem entre requests.
- A troca de `MockNightowlApi` por API real deve preservar os contratos acima ou adaptar em uma camada unica de client.
- Falhas de operacao devem retornar JSON amigavel, sem stacktrace:

```json
{
  "error": "invalid_transition",
  "message": "Esta tarefa nao pode ser cancelada neste status."
}
```

Status HTTP sugeridos:

- `200 OK`: leitura/acao concluida.
- `201 Created`: job criado.
- `400 Bad Request`: payload invalido.
- `401 Unauthorized`: nao autenticado.
- `403 Forbidden`: sem permissao.
- `404 Not Found`: recurso inexistente.
- `409 Conflict`: transicao invalida ou estado concorrente.
- `422 Unprocessable Entity`: validacao de regra operacional.
- `500 Internal Server Error`: erro inesperado controlado.
