# RC46 — Roteiro de homologação Windows/Pilot

Estado: preparado em código, **NÃO HOMOLOGADO / NO-GO para publicação**.
Data: 2026-10-30. Este roteiro não autoriza execução em endpoints, assinatura,
publicação ou mudanças em infraestrutura. DEC-04C/04D não são iniciadas aqui.

## Pré-requisitos e isolamento

VM Windows descartável com snapshot, sessão interativa e conta SYSTEM/usuário
comum conforme o cenário. Backend isolado com TLS e PostgreSQL descartável,
identidades sintéticas distintas, acesso ao material público de trust autorizado
e artefato Pilot gerado/assinado no fluxo oficial. Nenhuma credencial corporativa
deve ser copiada para o laboratório. Preservar evidências antes de restaurar VM.

Builder local foi executado com testes, sem SkipTests/Publish, e parou em
`RELEASE_TRUST_ROOTS_MISSING`; não contornado. ZIP unsigned de desenvolvimento é
somente inspeção: **não instalar esse ZIP no lugar de Pilot**.

Uma instalação Stopped de `NightOwlAgentDotNet` foi vista na máquina de
desenvolvimento em consulta somente leitura; não foi usada para testes de SCM.
Não foi iniciada, reparada, removida nem atualizada.

## Matriz de execução futura

| Cenário | Evidência exigida | Estado local / pendência |
| --- | --- | --- |
| Instalação limpa assinada | Hash/assinatura/trust, paths/ACL, identidade única, serviço e tarefa de logon válidos | Helpers/config/trust/ACL testados; operação instalada pendente |
| Atualização de RC45/legado | Config/identidade/estado/filas preservados, mesma condição funcional após migrações | Fixtures e loader .NET passaram; VM e versão antiga reais pendentes |
| Configuração explícita | TelemetryMode/quotas/cadências/URLs/allowlist/pin/flags preservados | PowerShell -> loader .NET testado, sem ativar software |
| Perfil antigo sem flags | Telemetria desabilitada/legacy; nenhuma habilitação implícita | PASS local |
| Perfil novo do installer | Enabled true, legacy explícito, 300/900, defaults antigos preservados | PASS local; seleção continuous exige configuração explícita |
| Identidade e re-enrollment | Mesmo bearer/endpoint quando legítimo, conflito falha fechado, sem reassociação por serial | Contratos Django passaram; rede/VM pendentes |
| Logon comum | Tray na sessão correta sem criar Config/Identity/State nem precisar escrever ProgramData | Startup duplicado em processo real PASS; logon completo pendente |
| Usuário sem acesso a Config/State | Status público sem token, URL correta e último heartbeat, logs locais por usuário | Projeção, locks, limite/leitor e resolução de paths PASS; DACL com conta real pendente |
| Múltiplas sessões | Uma instância por sessão; segunda instância da mesma sessão encerra | Mutex same-session real PASS, sessão 2; duas sessões reais pendentes |
| Reinício do Explorer | Ícone e menu recuperados sem novo Worker/identidade, sem duplicata | Pendente; Explorer do desenvolvedor não foi reiniciado |
| Serviço pending/restart | Grace monotônico <=120 s, Running recupera, mudança entre estados não estende prazo | Política determinística PASS; SCM real pendente |
| Serviço ausente/query negada | Tray encerra sem bootstrap protegido; ausência distinta de Running | Unit policy PASS; teste de ausência real não executado porque existe serviço local Stopped |
| Update com Tray aberto | Quiesce, processos filhos encerrados, arquivos substituídos sem tocar outro endpoint/runner | Updater fixtures PASS; VM pendente |
| Handshake e rollback | Só completar com confirmação; backup verificado; restaurar binários/identidade após falha | Testes de estados/arquivos/ACK PASS; update assinado real pendente |
| Repair | Installer pinned, completion/failure persistidos, sem enrollment indevido | Runner funcional PASS; serviço reparado real pendente |
| Repair em caminhos com espaços | Caminhos/trust preservados, exit correto, argumentos vazios/aspas/barras corretos | PASS funcional no PowerShell 5.1 |
| Fila indisponível/ACL/disco cheio | Preservar resultado incerto/ownership; não repetir mutação cegamente | Injeções/locks/readonly PASS; volume cheio físico e power cut pendentes |
| Logs/rotação/restart | Limites, leitores, fragmentos preservados, próximo JSONL válido e redaction | PASS local, threads e processos; crash físico pendente |
| Offline/ACK perdido | Fila/journal persistem, replay idempotente, nenhuma reexecução de efeito conhecido | Contratos e loopback PASS; rede/proxy reais pendentes |
| Corrupção e quota | Quarentena ocupa quota, não elimina evidência e faz backpressure | PASS local; reconciliação operacional necessária |
| Uninstall autorizado | Consumir autorização, encerrar Tray/serviço apenas do install root, remover tarefa e gerar receipt/ACK | Fixtures PASS; operação instalada pendente |
| Purge | Aprovação explícita, preservações/erros auditáveis e sem remover outro endpoint | Contrato local revisado; não executado |
| Diagnostics | Coleta limitada/redacted; logs do usuário atual, sem varrer perfis alheios | Fixture PASS; execução SYSTEM e usuário em VM pendente |
| PostgreSQL | Migrations, unique/advisory/row locks, delivery/start/result/cancel/scheduler concorrentes | PASS local PostgreSQL 16.15: 717 testes sem skips, incluindo 29 gates de banco; não equivale a carga/produção |
| Carga e TLS | 100 agentes, reconexão, proxy, timeouts, sockets e soak | Pendente; harness de 30 s não aprova esse gate |

## Ordem operacional proposta, somente após autorização

1. Gerar Pilot imutável, assinado, com chaves/roots protegidas no ambiente de
   release; usar versão nova, nunca sobrescrever RC45/stable. Rodar todos os
   gates oficiais, verificar manifesto/assinatura/hashes e guardar evidências.
2. Em snapshot limpo, instalar Pilot com identidade sintética. Conferir SCM,
   tarefa de logon, assinatura, ACL, URLs e perfis efetivos. Confirmar heartbeat,
   inventário e resultado de job de leitura com ACK no backend isolado.
3. Restaurar snapshot com release anterior e repetir atualização. Comparar a
   projeção funcional normalizada: máquina/servidor, caminhos, capacidades e
   políticas, respeitando opt-outs e configurações explícitas antigas.
4. Exercitar interrupções em cada etapa, preservando journal, backups, filas e
   ownership. Unknown não autoriza reinstalar nem apagar runner/backup.
5. Executar cenários Tray/Explorer/múltiplas sessões e filesystem, depois repair
   e uninstall. Guardar recibos e ACK antes de restaurar a VM.
6. Validar a matriz de deploy/versão/permissões PostgreSQL do ambiente autorizado,
   TLS e carga, e só então avaliar publicação pausada/rollout
   zero/canário, sob aprovação específica separada. Nenhuma dessas ações ocorreu.

## Legado: aposentadoria por endpoint

Executar `Get-NightOwlLegacyInventory.ps1` somente em contexto autorizado;
revisar fontes de identidade, serviço NSSM `NightOwlAgent`, tarefa
`RMM-Agent-Heartbeat`, argumentos/configuração protegidos no endpoint e GPO de
origem. Hashes de machine_id iguais são referência de continuidade, não atestado
forte ou autorização de remover.

Antes de desativar: .NET autenticado, heartbeat, inventário e job com ACK;
configuração/estado/pendentes reconciliados; definição de tarefa/serviço e dados
necessários ao rollback guardados com ACL. Parar somente componentes identificados
e impedir que GPO os recrie. Observar a janela aprovada e só depois remover os
arquivos previstos. Falha .NET restaura a definição anterior no mesmo endpoint;
não copiar bearer entre computadores. Não apagar C:\RMM por idade/nome.

`DisablePowerShellAgent` continua afetando apenas a tarefa; não representa
aposentadoria do NSSM. Defaults de coexistência e parâmetros de compatibilidade
permanecem deliberadamente. Inventário da frota e autorização operacional ainda
são necessários. Scripts de inventário AD/access_inventory são outro componente,
não código morto substituído pelo agente endpoint.

## Critério de conclusão

O gate funcional local de PostgreSQL foi fechado com instância descartável
autenticada em loopback; não há mais 29 testes de banco não executados. A rodada
final também habilitou os dois browsers: 717 testes, 143,662 s, zero skips. A
instância criada foi encerrada e suas bases de teste foram removidas. Evidência:
`artifacts/rc46-final-review/django-postgres-final.log` e
`postgres-lifecycle-verification.json`. Não houve alteração de schema corporativo.

Registrar, para cada linha, build/SHA, VM/snapshot, resultado e evidência, sem
transformar pendência em PASS. Falha de assinatura, identidade, ACK, ownership,
recuperação ou autorização é bloqueante. Bugs revelados no laboratório exigem
correção pontual e regressão; os testes locais não afirmam ausência absoluta de
bugs. A revisão geral de simplificação não precisa ser repetida para começar a
DEC-04C numa tarefa específica; homologação e gates de release continuam abertos.
