# Adendo de revisao: identidade e servico da CS-CVEL-0262

Proposta para aprovacao. Nenhum comando abaixo foi executado na estacao ou em
producao nesta rodada. Sem reparacao de banco, deploy ou recuperacao automatica.
Telemetria central e coleta Microsoft independente permanecem fora do escopo.

## Diagnostico: confirmado versus pendente

- As duas colisoes por serial generico estao cobertas na PR #2, commit
  `66a2f30e06bd0e374a66c3fe005824f1229591c9`: 0262/0059 e 0133/0029.
- A credencial do endpoint misto de 0262 foi rotacionada na associacao com 0059.
  Se 0262 preservou o token anterior, ele nao corresponde ao hash atual. Isso nao
  foi revalidado por autenticacao real nesta rodada e nao deve ser testado
  iniciando cegamente o agente contra o endpoint misto.
- O runner reportou `EXISTING_INSTALLATION_REVIEW / SERVICE_PRESENT_NOT_RUNNING`.
  Isso e evidencia de servico registrado e nao executando naquela tentativa,
  nao de sua causa ou de seu estado atual. Falta timestamp e estado SCM atual.
- Os dois codigos do runner nao foram encontrados no checkout base desta PR.
  Precisamos da versao/hash e do resumo sanitizado desse runner para confirmar
  a condicao exata de classificacao; nao presumir que o codigo signifique crash.
- `Worker.ExecuteAsync` carrega configuracao/estado antes do loop e captura
  excecoes dentro do loop. Erros HTTP dentro dele nao demonstram, sozinhos, que
  o SCM parou o processo. Falhas no startup, ACL/config, binario/runtime,
  dependencias ou parada administrativa continuam hipoteses, nao conclusoes.
- `ConfigService.Load()` cria diretorios, aplica migracoes e persiste estado.
  Portanto NAO instanciar esse servico nem executar o agente em modo check para
  uma auditoria que promete somente leitura.
- Telemetria central habilitada nao inicia um servico Windows parado nem prova
  autenticacao valida. Coleta Microsoft independente tambem nao comprova saude
  do NightOwlAgentDotNet ou ownership do endpoint.

## A. Comandos somente leitura - separados da recuperacao

### A1. Backend, copia forense isolada com o codigo da PR

Usar conexao SELECT-only para banco restaurado isolado. O comando da PR ainda nao
foi deployado: nao pressupor sua existencia no checkout de producao.

```bash
python manage.py audit_agent_identity \
  --endpoint 610fb016-5485-4aa2-a7d0-10892c92bdbd \
  --endpoint ab9352a5-6e6f-4652-90e5-b3dee5a6f4e3
```

Para uma consulta de registros no backend atual, o operador pode executar o bloco
abaixo apos aprovacao. Usa somente SELECT em transacao explicitamente read-only;
nao imprime tokens, hashes, mensagens livres, payloads, comandos ou resultados.

```bash
cd /opt/nightowl
.venv/bin/python manage.py shell <<'PY'
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.db.models import Q, Count, Min, Max
from agents.models import AgentMachine, AgentEnrollmentLog, InventorySnapshot, AgentJob

ids = ['610fb016-5485-4aa2-a7d0-10892c92bdbd', 'ab9352a5-6e6f-4652-90e5-b3dee5a6f4e3']
names = ['CS-CVEL-0262', 'CS-CVEL-0059', 'CS-CVEL-0133', 'CS-CVEL-0029']
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    machines = AgentMachine.objects.filter(Q(pk__in=ids) | Q(hostname__in=names))
    selected = list(machines.values_list('pk', flat=True))
    report = {
        'machines': list(machines.values('id', 'hostname', 'fqdn', 'domain', 'machine_id', 'is_active', 'first_seen_at', 'last_seen_at')),
        'enrollment_events': list(AgentEnrollmentLog.objects.filter(Q(endpoint_id__in=selected) | Q(hostname__in=names)).values('id', 'endpoint_id', 'hostname', 'domain', 'status', 'created_at').order_by('created_at')),
        'snapshots': list(InventorySnapshot.objects.filter(machine_id__in=selected).values('machine_id', 'hostname', 'domain').annotate(count=Count('pk'), first=Min('received_at'), last=Max('received_at'))),
        'jobs': list(AgentJob.objects.filter(endpoint_id__in=selected).values('id', 'endpoint_id', 'job_type', 'status', 'created_at', 'finished_at').order_by('-created_at')[:50]),
    }
    print(json.dumps(report, cls=DjangoJSONEncoder, indent=2))
PY
```

### A2. Na 0262, por operador/canal administrativo previamente aprovado

Nao despachar estes comandos como AgentJob para o UUID misto. Nao provisionar
canal remoto novo ou usar credenciais de outro endpoint para diagnosticar.
Preferir SYSTEM para leitura dos arquivos protegidos. Se houver Access Denied,
registrar isso e parar a coleta correspondente, sem alterar ACLs.

```powershell
$ErrorActionPreference = 'Stop'
$cs = Get-CimInstance Win32_ComputerSystem
if ($env:COMPUTERNAME -ine 'CS-CVEL-0262' -or
    -not $cs.PartOfDomain -or $cs.Domain -ine 'control.local') {
    throw 'TARGET_IDENTITY_REVIEW'
}
[pscustomobject]@{
    ObservedUtc = [DateTime]::UtcNow.ToString('o')
    Hostname = $cs.Name
    Domain = $cs.Domain
    ContextSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
}
Get-CimInstance Win32_ComputerSystemProduct |
    Select-Object UUID, Vendor, Name
$svc = Get-CimInstance Win32_Service -Filter "Name='NightOwlAgentDotNet'"
if ($null -eq $svc) { 'SERVICE=ABSENT' } else {
    $svc | Select-Object Name, State, Status, StartMode, StartName,
        ExitCode, ServiceSpecificExitCode, ProcessId
}

$root = Join-Path $env:ProgramData 'NightOwl'
$exe = Join-Path $root 'AgentDotNet\NightOwl.Agent.Windows.exe'
try {
    $file = Get-Item -LiteralPath $exe -ErrorAction Stop
    $file | Select-Object Name, Length, LastWriteTimeUtc
    Get-FileHash -LiteralPath $exe -Algorithm SHA256
    Get-AuthenticodeSignature -LiteralPath $exe | Select-Object Status
    Get-Acl -LiteralPath $root | Select-Object Owner, AreAccessRulesProtected
} catch { 'INSTALL_FILES_READ=FAILED_OR_UNAVAILABLE' }

# Leitura por allowlist: nunca imprimir JSON completo ou agentToken.
$paths = @('Config\agent.config.json', 'AgentDotNet\agent.config.json',
           'Identity\agent.identity.json', 'State\agent.state.json')
foreach ($relative in $paths) {
    try {
        $json = Get-Content -LiteralPath (Join-Path $root $relative) -Raw |
            ConvertFrom-Json -ErrorAction Stop
        $mid = $json.machineId
        if (-not $mid) { $mid = $json.machine_id }
        [pscustomobject]@{
            File = $relative
            ReadStatus = 'PASS'
            MachineId = $mid
            AgentVersion = $json.agentVersion
            CredentialPresent = -not [string]::IsNullOrWhiteSpace([string]$json.agentToken)
        }
    } catch {
        [pscustomobject]@{ File = $relative; ReadStatus = 'MISSING_UNREADABLE_OR_INVALID' }
    }
}
Remove-Variable json -ErrorAction SilentlyContinue

$since = (Get-Date).AddDays(-3)
try {
    Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$since;
        ProviderName='Service Control Manager'; Id=7000,7001,7009,7011,7023,7024,7031,7034,7035,7036} |
        Where-Object { ($_.Properties.Value -join ' ') -match 'NightOwl' } |
        Select-Object TimeCreated, Id, RecordId, ProviderName
} catch { 'SCM_EVENTS=UNAVAILABLE_OR_NO_MATCH' }
try {
    Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$since; Id=1000,1001,1026} |
        Where-Object { ($_.Properties.Value -join ' ') -match 'NightOwl' } |
        Select-Object TimeCreated, Id, RecordId, ProviderName
} catch { 'APPLICATION_EVENTS=UNAVAILABLE_OR_NO_MATCH' }
```

Identity guard above is a read-only diagnostic scope check, not sufficient alone
to authorize recovery: correlate the AD object SID/DN, local UUID and historical
machine_id before approving an action. The exact corporate AD SID must be read
from trusted records, not invented or replaced with the laboratory SID.

Nao retornar mensagens completas dos eventos ou logs JSONL. Com os RecordIds,
um operador autorizado pode revisar localmente os eventos/logs do intervalo do
runner e devolver apenas categoria, codigo e horario sanitizados. Preservar
os originais sob acesso restrito; ausencia de eventos nao prova ausencia de falha.
Verificar tambem se NIGHTOWL_HOME ou configuracao de servico usa caminho distinto
antes de concluir que arquivo ausente significa instalacao incompleta.

## B. Plano de recuperacao - NAO executar ainda

1. Aprovar a PR apos testes, incluindo concorrencia em PostgreSQL isolado. Manter
   GPO de novas instalacoes pausada; nao misturar deploy da correcao com reparacao.
2. Correlacionar 0262 por SID AD/FQDN e identidade local. Preservar registros dos
   dois pares, binarios/config/estado/fila/buffer e logs, com backup protegido.
3. Determinar a causa SCM: parado por acao administrativa, falha de startup,
   ACL/config, binario/runtime ou causa ainda desconhecida. Servico presente nao
   autoriza reinstalacao, takeover de ACL ou execucao repetida do installer.
4. Nao iniciar simplesmente o servico com a identidade/credencial antiga: isso
   nao desfaz a rotacao e pode enviar dados ou consumir jobs do registro misto.
   Os hashes em backup nao recuperam o token bruto, e nao serao restaurados no
   banco. Service Running e autenticacao valida sao gates diferentes.
5. Apos aprovacao separada, recuperar somente 0262 por acao SYSTEM restrita ao
   computador comprovado: preservar instalacao, criar identidade local nova e
   enrollment individual curto/de uso unico, hostname/dominio corretos, com
   entrega segura em memoria. Sem senha/token em SYSVOL, argumentos ou logs.
   O mecanismo dessa recuperacao ainda precisa ser revisado; nao improvisar
   exclusao de arquivos de identidade ou reutilizar token consumido.
6. Corrigir somente a causa comprovada do servico, se necessario. Inicia-lo uma
   vez, em janela aprovada e com controle para nao executar jobs remotos antigos
   ou incertos; o primeiro inicio nao e um diagnostico read-only. Observar SCM,
   autenticacao, novo endpoint independente, heartbeat e inventario coerente.
7. Somente depois confirmar telemetria real com timestamp posterior. Telemetria
   configurada e coleta Microsoft independente nao substituem essa confirmacao.
8. Se falhar: parar novas tentativas, preservar estado, reconciliar o resultado.
   Nao reverter o banco, copiar token de 0059 ou restaurar a identidade colidida
   para tentar iniciar novamente. Arquivos originais ficam como evidencia;
   retorno operacional exige nova decisao, nao rollback cego de credenciais.

0059, 0133 e 0029 nao serao recuperadas automaticamente. Cada ownership e eventual
reconciliacao do registro misto precisam de aprovacao individual. A PR nao afirma
que a correcao preventiva, por si so, recupere nenhum dos quatro computadores.

GO para revisao da PR e coleta diagnostica aprovada. NO-GO para recuperacao da
0262 enquanto faltarem causa SCM, identidade local, mecanismo seguro de entrega
da autorizacao e gate de teste PostgreSQL. Nenhuma mudanca de producao autorizada
por este documento.

## Validacao desta rodada

- PR #2 confirmada OPEN, head `66a2f30e06bd0e374a66c3fe005824f1229591c9`.
- `agents.test_enrollment_identity`: 28 testes, 27 PASS, 1 SKIP (PostgreSQL).
  Inclui os dois pares reais, serial aparentemente unico, impossibilidade de
  rotacao sem bearer, preservacao do token e ausencia de mutacoes apos rejeicao.
- Parser PowerShell local do bloco diagnostico: PASS. Isso NAO e execucao no
  Windows da 0262 nem confirma SCM, autenticacao, ACLs ou arquivos dessa estacao.
- `git diff --check`: PASS. Nenhuma mudanca de codigo neste adendo.
- PostgreSQL concorrente continua pendente; nao foi substituido por simulacao.
- Sem commit, push, deploy, consulta nova a producao ou comando na estacao nesta
  rodada. Este plano fica sujeito a aprovacao separada antes da coleta/recuperacao.
