# RC46 - Correcao do contrato de capacidades Jobs V2

Data: 2026-10-30. Branch `feature/rc46-agent-core`, HEAD
`0d5d8ceb28928b6a580cdff15a5144b373093296`. Alteracoes locais, sem commit,
push, merge, deploy, publicacao ou contato com producao.

## Causa e impacto

O produtor Worker.SendHeartbeatAsync anunciava `job_protocol_v2`, mas o consumidor
agents.automation.known_v2 exigia `jobs_protocol_v2`. A forma plural e canonica:
ja era usada pelo contrato/documentacao DEC-04B e por seus fixtures. A forma
singular e preservada como alias historico da implementacao DEC-04A, sem presumir
que essa RC tenha sido publicada.

Era uma **falha funcional**, nao apenas risco futuro: um heartbeat real com a
forma singular gerava jobs_v2_capability_unconfirmed na automacao. Isso bloqueava
materializacao de ocorrencias agendadas/recorrentes e tambem a modalidade imediata
criada por AutomationDefinition. A mesma verificacao protege entrega e inicio.

O caminho manual imediato preexistente nao exigia essa capability e continuava
funcionando. O cabecalho de transporte X-NightOwl-Job-Protocol: 2 e o campo protocol
da sinalizacao sao contratos diferentes, nao afetados por essa grafia.

## Produtores e consumidores revisados

| Componente | Papel |
| --- | --- |
| WindowsInventoryCollector.BuildHeartbeat | Payload leve, sem compor a lista de capabilities |
| Worker.SendHeartbeatAsync | Produtor efetivo: adiciona capabilities ao payload antes do HTTP |
| AgentApiClient.PostHeartbeatAsync / AgentHeartbeatPayload.Agent | Serializam/transmitem o anuncio; nao reinterpretam nomes |
| AgentHeartbeatView / record_heartbeat | Ingestao e persistencia do heartbeat em InventorySnapshot |
| agents.automation.known_v2 | Consumidor de protocolo para materializacao/entrega/inicio de automacoes |
| agents.internal_jobs.internal_capability | Consumidor separado, exige capacidade exata de cada operacao; nao recebe alias generico |
| Fixtures DEC-04B | Antes usavam somente plural e, por isso, nao detectaram a divergencia do produtor real |
| Documentos DEC-04B e Lote1 | Evidencia historica; a pendencia do Lote1 e resolvida por esta entrega |

Busca pelos dois nomes no checkout identificou apenas esse produtor de runtime
e esse consumidor de protocolo, alem de testes/documentacao.

## Correcao minima

1. Worker anuncia `jobs_protocol_v2` e mantem `job_protocol_v2` na mesma lista,
   uma vez cada. A lista de capacidades de operacoes continua filtrada como antes.
2. known_v2 aceita explicitamente qualquer um dos dois nomes em uma lista valida.
   Nao aceita prefixos, nomes parecidos, strings isoladas, dicionarios ou capacidade
   desconhecida. Mantem vinculo ao machine_id e a agent_version do heartbeat.
3. Nenhum alias e traduzido para collect_services, install_software ou outra
   operacao privilegiada. Flags, allowlists, catalogo, identidade, permissao,
   janela, exclusividade e recibos continuam independentes e obrigatorios.

Matriz de compatibilidade:

| Anuncio | Consumidor corrigido |
| --- | --- |
| Somente plural | Reconhecido |
| Somente singular historico | Reconhecido |
| Ambos | Reconhecido, sem duplicar ocorrencia |
| Sem V2 | Continua compativel com job manual legado; automacao permanece bloqueada |
| Desconhecido/parecido/tipo invalido | Nao reconhecido |
| Nome correto com identidade/versao contraditoria | Nao reconhecido |

Backend anterior que ja procura plural tambem reconhece o novo anuncio duplo.
Backend corrigido reconhece o anuncio singular anterior sem exigir sua regravacao
no historico. Nao houve migration, mudanca de payload de job ou de configuracao.

## Testes com o anuncio real

O novo modo de teste .NET --heartbeat-contract inicia o Worker real com seu
construtor de teste, caminhos temporarios e transporte HTTP interceptado localmente.
Captura o corpo realmente enviado pelo loop de heartbeat, nao uma lista reescrita
por fixture. Inventario pesado e telemetria ficam desabilitados/nao devidos nesse
harness; nenhuma instalacao ou servico de producao e alterado.

O teste Django captura esse subprocesso em memoria e ingere o corpo pelo endpoint
real de heartbeat com bearer sintetico em banco isolado. A identidade nativa do
processo e representada somente nesse banco de teste. Nao se conecta ao computador
corporativo cujo nome possa aparecer no payload nem ao backend de producao.
Nenhum bearer/cabecalho e incluido no corpo exportado; ha uma assercao contra isso.

Depois da ingestao, os testes confirmam:

- raw_payload persistiu ambos os nomes do Worker;
- a automacao imediata criou um job e o agente .NET devolveu seu recibo real;
- a ocorrencia unica esperou seu horario e executou pelo motor DEC-04A;
- a primeira ocorrencia recorrente executou e avancou o cursor15 minutos;
- a segunda ocorrencia e sua idempotencia foram exercitadas com relogio simulado,
  sem alegar soak real de15 minutos;
- agentes sem V2 mantem o fluxo manual e nao ganham acesso a automacoes;
- mesmo com flags internas habilitadas em teste, capacidade de protocolo e nomes
  desconhecidos nao autorizam coleta de servicos/instalacao de software;
- o Worker nao anuncia install_software desabilitado nem tipos desconhecidos
  presentes na lista de configuracao do harness.

Os testes .NET existentes de isolamento de heartbeat tambem verificam os dois
nomes no corpo enviado pelo Worker. Isso evita depender somente da integracao
Django para detectar nova divergencia de grafia.

## Resultados

- Build Release Windows: PASS, zero avisos/erros.
- Heartbeat .NET focado: PASS, incluindo10 grupos anteriores de identidade/estado/hardware.
- Jobs runtime .NET focado: PASS.
- Regressao Lote1 --cleanup-only: PASS, aliases e formas de hardware preservados,
  uma normalizacao de discos e zero PowerShell no heartbeat.
- Django focado: PASS,62 testes em20,756s, dois skips (browser opt-in e PostgreSQL).
- Integracao Worker -> ingestao -> automacao -> motor .NET -> recibo: PASS em
  ambiente isolado; segunda janela recorrente usa relogio simulado.
- git diff --check: PASS.

Comandos reproduziveis, apenas no checkout de desenvolvimento:

```powershell
dotnet build NightOwl.Agent.Windows.Tests -c Release --no-restore
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --lightweight-heartbeat-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --jobs-only
dotnet run --project NightOwl.Agent.Windows.Tests -c Release --no-build -- --cleanup-only
$env:DJANGO_SECRET_KEY='isolated-rc46-test-key'
$env:DJANGO_DEBUG='True'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AD_AUTH_ENABLED='False'
& 'C:\Users\gabriel.oliveira\Downloads\rmm_project\venv\Scripts\python.exe' manage.py test agents.test_automation agents.test_automation_protocol_e2e agents.test_job_control agents.test_lightweight_heartbeat --settings=config.settings_test_browser --noinput
git diff --check
```

Nao executar build enquanto os testes integrados estiverem usando as DLLs.
Nao imprimir o corpo capturado do heartbeat nem saida bruta de subprocessos para
diagnosticar credenciais: os testes reportam apenas marcadores sanitizados.

## Arquivos desta correcao

- NightOwl.Agent.Windows/Worker.cs: anuncio canonico com alias.
- agents/automation.py: reconhecimento explicito do alias.
- NightOwl.Agent.Windows.Tests/Program.cs: entrada do harness.
- NightOwl.Agent.Windows.Tests/LightweightHeartbeatTests.cs: captura do Worker real
  e assercoes nos testes existentes.
- agents/test_automation.py: compatibilidade, tipos invalidos e gates privilegiados.
- agents/test_automation_protocol_e2e.py: substituir heartbeat artificial por
  anuncio real e cobrir imediato/agendado/recorrente.
- docs/rc46-jobs-v2-capabilities-fix.md: esta reconciliacao.

Nenhuma refatoracao, limpeza adicional ou alteracao em frontend, scheduler,
calendario, dados persistidos, migration0039, telemetry, inventario, Updater,
Uninstaller ou scripts foi feita nesta tarefa. DEC-01/02/03/04A/04B e Lote1
permanecem preservados.

**Situacao RC46:** a falha funcional deste contrato esta corrigida localmente.
A publicacao continua NO-GO pelos demais gates: PostgreSQL real, falha conhecida
do repair runner e homologacoes anteriores de carga/lifecycle/MSI/telemetria.
Esses gates nao foram dispensados nem reclassificados por esta correcao focada.
