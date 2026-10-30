# RC46 — Encerramento da limpeza do agente

Data: 2026-10-30. Branch `feature/rc46-agent-cleanup-closure`, base recebida
`6fe53cac4f064013fa6993b8c4c56041450d26b9`. Este relatório sucede, sem apagar a
evidência histórica, `rc46-agent-cleanup-final.md`.

## Política definida antes da implementação

Logs técnicos JSONL nativos: até 10 MiB por arquivo ativo e cinco arquivos
rotacionados numerados, até 60 MiB por caminho de log. Registros individuais
limitados a 64 KiB após sanitização; registros maiores recebem um marcador com
o tipo do evento, sem gravar seu conteúdo. Arquivos rotacionados expiram após
30 dias, verificados na próxima escrita. Não há timer ou serviço de limpeza.
Esses são defaults de desenvolvimento para revisão operacional antes do Pilot,
não uma declaração de política corporativa aprovada.

Somente o caminho exato de cada logger e seus sufixos `.1` até `.5` entram nessa
política. Não há varredura ou remoção genérica de temporários. Arquivos abertos
sem permissão de renomear impedem a rotação; nesse caso a escrita falha sem
truncar o arquivo ativo ou continuar seu crescimento. Locks entre processos são
limitados no tempo, sem conceder permissões novas. Leitores devem compartilhar
delete, como o LogTailReader existente. A rotação não é um arquivo de auditoria
durável: evidências necessárias devem ser exportadas pelo fluxo de diagnóstico
antes de expirar. Resultados de jobs e receipts continuam nos seus stores.

Não expurgar resultados sem ACK, inclusive legados não críticos. Quota cheia
impede nova admissão e preserva o backlog. Identidade, configuração, trust,
journal, resultados incertos, backups de rollback e arquivos de instalador não
pertencem à política de logs. Temporários e runners continuam sujeitos à
validação de ownership e estado terminal dos fluxos existentes.

Logs antigos já maiores que o limite não são truncados para satisfazer a quota:
entram inteiros na próxima rotação e deixam de ocupar espaço conforme retenção.
Assim, 60 MiB é o limite para logs produzidos pela nova política, não promessa de
redução imediata de um diretório legado. Não há quota global para caminhos de log
arbitrários escolhidos por configuração. O arquivo `.lock` ocupa zero bytes,
permanece no diretório e herda sua ACL; excluí-lo durante uso poderia separar locks.
Falha de logging mantém os comportamentos dos chamadores: Tray/lifecycle tratam
best effort; o logger do Worker mantém sua propagação de erro e cancelamento.

## Arquitetura e escopo preservados

O serviço SYSTEM `NightOwlAgentDotNet` hospeda os loops existentes. Heartbeat
leve usa APIs nativas sem CIM/PowerShell; inventário tem checkpoints/cadências
independentes; telemetria lê a cada ~10 s, agrega minuto UTC e usa writer/uploader
independentes, canal de 16 e ring de 180 leituras. Jobs têm signal/pull/start,
execução tipada, resultado durável e ACK exato, com um slot pesado compartilhado
com inventário. Calendário e permissões permanecem no Django, com PostgreSQL em
produção; SQLite foi usado exclusivamente nos testes. Migrations 0037/0038/0039
estão presentes. Nenhuma foi modificada.

Tray é por sessão, com timer de 30 s e tolerância monotônica de 120 s. Updater,
Uninstaller e Diagnostics são sob demanda. Shared centraliza caminhos, proteção
de arquivos, trust, estado e lifecycle. Comunicação segue outbound com bearer
individual, TLS e validação de origem; testes HTTP são em loopback. Não foram
alterados autenticação, enrollment, identidade, assinatura, allowlists, rollback,
o anúncio canônico `jobs_protocol_v2` ou seu alias.

## Alterações resolvidas localmente

1. `RotatingJsonlLog` implementa o limite acima, sanitização compartilhada, JSONL
   compacto e lock de arquivo entre processos/sessões. Aplicado a JsonlLogger,
   logs de migração de configuração, NightOwlPaths, Tray, Updater e Uninstaller.
   A rotação não copia/trunca o log ativo quando rename é negado. Caminhos com
   reparse point são rejeitados; não há concessão de ACL para viabilizar logs.
2. Install/uninstall PowerShell usam helper limitado equivalente, com escrita
   UTF-8, lock, redaction, rotação e expiração. A pequena implementação aparece
   nos dois scripts deliberadamente: cada um é distribuído/executado sozinho e
   bootstrap/uninstall não podem depender da presença de DLL ou outro script.
   Não foi ampliada a allowlist de scripts do pacote oficial.
3. `PendingResultQueue` não apaga resultados sem ACK por idade/quota, incluindo
   não críticos. Admissão considera o tamanho serializado do envelope que vai
   entrar; quota cheia produz `RESULT_QUEUE_FULL`, preservando o backlog. Prune
   apenas verifica capacidade. Metadados de retry continuam limitados pelo código
   existente; não prometemos retenção infinita nem execução nova com disco cheio.
4. Retenção do arquivo `sent` começa no ACK. Antes, mover resultado antigo
   preservava mtime antigo e podia eliminar imediatamente seu recibo. A regressão
   exercita um pendente de um ano, quota cheia, ACK, recibo e nova admissão.
5. Repair runner passa argumentos corretamente ao processo PowerShell externo.
   O quote nativo usado também por update/uninstall deixa de duplicar todas as
   barras e escapa apenas as necessárias antes de aspas/fim de argumento.
6. Fixtures repair agora drenam stdout/stderr simultaneamente, têm timeout real
   com encerramento da árvore própria e limpam diretórios de sucesso. A falha de
   persistência mantém ownership externo incerto, sem inventar completion.
7. Novo inventário local somente leitura do legado, com teste sintético; omite
   tokens e argumentos dos processos e distingue consulta negada de ausência.
8. Builder oficial inclui a regressão de retenção dos scripts lifecycle.
   Novo gerador separado cria somente artefato de inspeção de desenvolvimento,
   sem assinatura, trust, instalador executável no ZIP ou capacidade de publicar.

Código removido: expurgo de pendentes por idade/quota antes do ACK, seu TryLoad
sem outro consumidor, append nativo sem limites e quoting incorreto de barras.
Não foram removidos executáveis, DLLs, pacotes NuGet, configurações públicas,
coletores ou extensões futuras de scripts/ConPTY. Backend e frontend não mudaram.

## Repair runner: diagnóstico e limites da conclusão

O literal corrompido do pedido foi relacionado ao nome inequívoco do handoff:
`TestRepairRunnerScriptPersistsCompletedResult`.

A suíte completa original, sem mudança de código, PASSOU neste host. Portanto,
não foi demonstrado que a falha histórica ocorra em todo ambiente nem que sua
causa histórica exata seja conhecida. A nova fixture com espaço no caminho do
instalador/trust reproduziu a mesma asserção `Repair runner pending result should
be completed.`: PowerShell `Start-Process -ArgumentList` junta os elementos e
perde a fronteira do argumento sem quoting nativo. Isso afeta instalações reais
com caminhos assim, não apenas a expectativa do teste.

Após a correção: sucesso, exit 7, fila indisponível, caminho com espaços/apóstrofo
e roundtrip real de argumentos vazios, barras finais e aspas passam. Nenhuma
asserção foi removida, enfraquecida ou transformada em skip. Não declarar reparo
instalado/SCM/assinatura/saúde como homologados: o instalador funcional de teste
é sintético, grava somente versão no diretório temporário e não acessa rede.

## Legado PowerShell e estratégia de aposentadoria

Preservados `NightOwlAgent`/NSSM, `RMM-Agent-Heartbeat`, fontes de configuração,
identidade e estado antigas e os switches de coexistência. `KeepPowerShellAgent`
continua true. `DisablePowerShellAgent` desabilita somente a tarefa e não prova
parada do serviço NSSM; `KeepScheduledTaskFallback` pode permitir coexistência.
O inventário da frota é desconhecido. Ausência de consumidor quente não prova
que `jobsSeconds`, aliases, packagesPath/cachePath ou scripts não sejam usados.

Ferramenta preparada: `scripts/Get-NightOwlLegacyInventory.ps1`. Executá-la apenas
em endpoint/laboratório autorizado; esta rodada executou somente sua fixture.
O relatório compara hashes de machine_id e relata componentes presentes, mas
igualdade desses hashes sozinha não é atestado forte de identidade.

Roteiro futuro por endpoint: obter inventário; revisar continuidade da identidade
com a autenticação já estabelecida; preservar definições de tarefas/serviços e
configuração com ACL restrita no próprio endpoint; confirmar heartbeat,
inventário e job de leitura com ACK; parar somente o legado identificado sob
aprovação operacional; observar duplicações e continuidade; restaurar definições
anteriores se necessário; remover arquivos apenas depois da janela aprovada.
Nunca copiar bearer entre endpoints nem apagar `C:\RMM` por idade. Não foram
alteradas GPOs, tarefas, serviços ou máquinas corporativas.

## Lifecycle, temporários e decisões explícitas de retenção

| Recurso revisado | Decisão |
| --- | --- |
| Config/Identity/Trust/checkpoints | Essenciais; sem limpeza por idade, sem mudança de precedência ou ACL |
| Inbox/outcome journal V2 e filas | Manter recovery, idempotência e ACK; não aplicar política de log |
| sent | 1.000 registros/14 dias desde ACK, conforme contrato existente |
| Telemetria contínua | Preservar 128 MiB/192 h default e contadores explícitos de perda |
| Quarentena | Limites V2 existentes preservados; não varrer diretórios legados desconhecidos |
| Update staging/cache/MSI | Limpeza permanece ligada ao fluxo e ownership; estado incerto exige reconciliação |
| Backups de rollback | Preservados até verificação de terminal/recibos e decisão operacional; sem expurgo etário |
| Runners updater | Limpeza existente valida nome, estado terminal e ausência de uso; não remove o runner atual |
| Repair stdout/stderr/runner log | Evidência por operação, preservada com o runner até reconciliação; não tratada como lixo por idade |
| Diagnostics | ZIP sob demanda, limites existentes de coleta/staging e finally preservados; exportação entregue não é descartável |
| Temporários de escrita atomic/durable | Finally/retry existentes mantidos, sem varredura de `.tmp` possivelmente em uso |
| Logs técnicos residentes/lifecycle | Novas quotas e retenção específicas; sem daemon de limpeza |

Preservar runner incerto e backup é decisão de recuperação, não redundância sem
decisão. Sem inventário operacional não existe quota global segura para todos
esses arquivos de evidência. A política operacional de exportação/reconciliação
continua necessária antes de rollout. Nenhuma limpeza de ProgramData ou do Temp
geral foi executada nesta máquina.

## Footprint

Não há novo processo residente, timer, thread persistente ou dependência NuGet.
O lock é aberto/fechado por escrita, espera no máximo cerca de 400 ms entre
tentativas e não depende de thread de manutenção. Leitura do Tray continua
limitada a 1 MiB + fronteira/400 linhas; não foi refeita. Logs passam por redaction
e serialização antes do lock, com custo adicional por evento que não representa
uma alegação de redução de CPU do serviço.

Serviço e Tray continuam os dois residentes esperados; processos PowerShell dos
coletores e runners são temporários. Não foi iniciado Worker real nem instalado
serviço para medir CPU/RSS/IO sustentado, startup ou tamanho instalado. Os números
de harness são apenas locais/sintéticos, sem comparação de frota antes/depois.
Self-contained compartilha um diretório de runtime, não cinco cópias independentes.
Trimming/AOT/reflection/runtime e suporte futuro a scripts/ConPTY foram mantidos.

## Resultado e próximos gates

**Resolvido localmente:** rotação dos logs técnicos, proteção dos resultados sem
ACK, retenção dos recibos a partir do ACK, quoting do repair/lifecycle e regressões
locais. Os resultados completos, comandos, versões e limitações estão em
`rc46-new-machine-validation.md`.

**Preservado intencionalmente:** compatibilidade PowerShell/configuração/identidade,
filas/ACK, backup/rollback, evidência de runner incerto, trust, dependências e
extensões futuras. Não há remoção pendente baseada apenas em preferência de estilo.

**Pendente de ambiente:** PostgreSQL, VM descartável com lifecycle/Tray instalado,
MSI/assinatura, carga/TLS/proxy de 100 agentes, falha física de armazenamento,
inventário da frota e aceitação operacional da política de retenção. Windows está
disponível aqui; o que falta é laboratório autorizado e configuração instalada,
não o sistema operacional.

**Bloqueio técnico:** nenhum teste de código local permaneceu com falha após as
correções. O builder oficial continua bloqueado por trust roots autorizadas
ausentes e não entrega uma release validada. Isso não foi reclassificado como PASS.

Fechamento local do escopo de código, com preservações explícitas; **NO-GO para
publicação/instalação ou homologação de produção**. Não houve commit, push,
merge, publicação, instalação, migrations em produção ou início de DEC-04C/04D.
Próximas ações: revisar o diff e a política; provisionar gates de ambiente;
homologar Pilot assinado no fluxo oficial; só retomar DEC-04C mediante nova
aprovação explícita depois desses gates, sem início automático por este relatório.
