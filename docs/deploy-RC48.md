# NightOwl RC48 — plano consolidado de desenvolvimento e validação

**Estado:** planejamento e primeiro incremento local de medição. Este documento não autoriza publicação, deploy, execução recorrente, alteração de flags, migrações operacionais ou atualização de endpoints.

**Base de código consultada originalmente:** `main` em `218526a85b1865ef90c4c05f04353233173b0edc`. A base foi consolidada desde então; ver o estado de integração abaixo.
**Referência histórica:** [plano de implantação RC47](deploy-RC47.md), preservado sem alterações. A RC47 publicada continua imutável; RC48 consolida as implementações da RC47 e trata somente suas pendências.

## 1. Como interpretar os estados

- **Implementado e testado:** existe código integrado e há testes automatizados pertinentes. Isso não implica homologação operacional.
- **Implementado, mas não homologado:** implementação e cobertura técnica existem, mas falta validação real em ambiente representativo.
- **Parcialmente implementado:** parte do requisito existe; há lacuna de código, contrato ou evidência.
- **Pendente:** não há implementação suficiente para considerar o requisito atendido.
- **Dependente de decisão ou medição operacional:** não deve ser resolvido por suposição; requer decisão de produto, medição ou intervenção operacional.

Os estados refletem a documentação e o histórico disponíveis na base indicada. Estados de produção, flags carregadas por processos e resultados de endpoints devem ser reconfirmados pelo responsável operacional antes de qualquer ação.

## 2. Matriz RC47 → RC48

| Requisito | Estado real na base/handoff | Evidência e limite | Tratamento RC48 |
|---|---|---|---|
| RC47-00 — auditoria de coleta, armazenamento e consumidores | Implementado e testado como análise | `docs/deploy-RC47.md` registra o mapeamento. Não representa medição atual de custo de produção. | Manter como referência; medir volume e qualidade antes de propor retenção. |
| RC47-01 — Inventário de Hardware | Implementado, mas não homologado integralmente | PR #36 integrada; compatibilidade legacy mantida. A exatidão de dados depende de hardware real e proveniência. | Evitar reimplementação; registrar apenas defeitos reproduzíveis. |
| RC47-02 — projeções de telemetria e retenção | Parcialmente implementado | PR #28 normalizou projeções e diagnósticos. A métrica plana `telemetry_errors_count` não representa ausência como desconhecida sem alterar seu campo/modelo; resumos JSON preservam qualidade. Identidades físicas de disco/rede requerem validação em endpoint. Retenção não foi reavaliada por custo medido. | RC48-01 mede armazenamento/qualidade e audita consumidores restantes; qualquer mudança semântica/schema fica para proposta separada. |
| RC47-03A — última análise persistida | Implementado e testado | Seleção, qualidade, idempotência, histórico compacto e migration 0047 integrados; proveniência histórica desconhecida permanece explicitamente desconhecida. | Preservar; cobrir regressões que a medição revelar, sem atribuir versão histórica por inferência. |
| RC47-03B — agendamento de análises | Implementado, mas não homologado | Scheduler existente, limite de cinco por rodada, cadência-alvo de 15 dias, dry-run e flags fechadas; primeira captura permanece `unconfigured`. | Completar testes de borda e preparar configuração somente. Não ativar. |
| RC47-03C — saúde/circuit breaker do scheduler | Implementado, mas não homologado | Health persistido, suspensão fail-closed após falhas sistêmicas consecutivas e recuperação auditável; operação real não autorizada. | Verificar a configuração/invocador e preparar observabilidade; não criar scheduler paralelo. |
| RC47-04A/B — Políticas de Execução e atribuição por endpoint | Implementado e testado | Motor e contratos existentes reutilizados; execução manual direcionada ao UUID e sem disparo implícito nos outros alvos. Jobs reais dependem de gates e autorização. | Apenas regressões comprovadas. Não mudar allowlists nem flags. |
| RC47-05 — ajustes de Monitoramento | Implementado, mas não homologado integralmente | Ajustes de UI e hotfix visual integrados; evidência de VM é pontual e não cobre todos os temas/tamanhos. | Regressão desktop/mobile somente se houver defeito demonstrado. |
| RC47-06 — piloto `continuous`, custo e filas legacy | Parcialmente implementado | Houve teste temporário em uma VM, com gráficos atualizados; configuração retornou a `legacy` e uma entrada antiga da fila permaneceu. Não houve medição de frota nem alteração aprovada do instalador. | RC48-03 prepara instrumentação, comparação e rollback. Não migrar endpoint ou mudar default sem aprovação. |
| RC47-07 — terminal assistido/admin | Parcialmente implementado | Assistido já teve validação real pontual, mas a issue #19 de `Get-Date` em ConPTY restrito segue independente; encerramento/revogação ponta a ponta e ampla homologação pendem. `admin_auth` permanece separado e sem homologação real. | Manter bloqueios; corrigir somente regressão reproduzível. Testes operacionais antes de distribuição ampla. |
| RC47-08 — mudança de canal no updater | Implementado e testado, mas não homologado operacionalmente | Modal/backend foram atualizados e cobertos; não houve atualização real de endpoint nesta etapa. | Revalidar fluxo de preview, salvar canal e update sem disparo implícito. Não publicar nem criar job. |
| RC47-09 — WinRM em Ações e Jobs | Implementado, mas não homologado em domínio | Diagnóstico, recuperação/histórico e UI integrados; testes reais de autenticação e recuperação em endpoint elegível ainda são necessários. | Checklist de domínio separado; manter TLS, allowlist e autorização. |
| Gerenciador de Serviços — Fases A/B (escopo incorporado da RC47) | Implementado e testado, mas não homologado com release agente atual | Consulta paginada e operações restritas estão integradas. A consulta requer `collect_services_paged:2`; as operações exigem escopo operador+UUID+serviço+ação e gates fechados. O agente do piloto pode não anunciar a capability; não há atualização Windows autorizada. | Homologar somente após identidade do endpoint resolvida e release assinada compatível aprovada. Nunca inferir suporte pelo código do backend. |
| Identidade duplicada de `CS-CVEL-0281` | Dependente de decisão/medição operacional | Handoff registra dois registros homônimos com machine IDs diferentes; não reconciliar automaticamente por hostname. | Impedir ações piloto até reconciliação comprovada pelo responsável; usar UUID e `machine_id`. |
| Assinatura/publicação da próxima release Windows | Dependente de decisão/medição operacional | O responsável confirmou que a chave original `nightowl-release-2026-02` foi localizada e validada; confirmou também DryRun RC48 aprovado na base `218526a85b1865ef90c4c05f04353233173b0edc`. Isso não valida um candidato posterior nem autoriza publicação. | RC47 permanece imutável. Congelar o código candidato e executar novo DryRun antes de qualquer publicação autorizada. |

## 3. Pendências por natureza

### Exigem código ou validação técnica nesta consolidação

1. **RC48-01:** gerar medição limitada e reproduzível de volumes, qualidade e projeções; auditar os consumidores restantes de CPU, RAM, disco e rede. Só propor alteração de retenção quando houver números e impacto em alertas/histórico.
2. **RC48-02:** completar regressões de primeira captura, offline/backoff, idempotência, qualidade, concorrência e recuperação do scheduler. Confirmar que a execução de jobs continua independente do scheduler de telemetria.
3. **RC48-03:** produzir checklist de piloto e indicadores comparáveis para agente, backend, PostgreSQL, rede, filas e jobs; cobrir compatibilidade das filas legacy e pré-condições de rollback. Revisar o instalador apenas após decisão de migração.
4. **RC48-04:** rodar regressões de terminal, políticas/jobs, updater, WinRM e responsividade na base consolidada; abrir correção pequena apenas para falha reproduzível.

### Exclusivamente operacionais (não serão executadas por esta frente)

- Confirmar SHA implantado, configuração efetiva dos processos HTTP/ASGI e flags carregadas em runtime.
- Confirmar se o invocador recorrente RC47-03B está provisionado, frequência, health e alertas; não criar timer/cron paralelo.
- Backup PostgreSQL, estado da migration 0047–0050, capacidade de restauração e plano de reversão do schema.
- Obter série de CPU/RAM, tamanho e crescimento PostgreSQL, tráfego, I/O, profundidade/idade de filas e falhas de jobs sob carga representativa.
- Conciliar a identidade do piloto pelo UUID e machine ID; sem exclusão ou fusão baseada somente em hostname.
- Para a publicação futura, confirmar trust, aprovação do environment e processo oficial com o responsável; a chave original `nightowl-release-2026-02` foi localizada/validada e o DryRun RC48 passou na base `218526a85b1865ef90c4c05f04353233173b0edc`. Mudanças posteriores no código exigem congelamento de commit e novo DryRun. Não expor ou copiar material privado.
- Homologar em VMs: terminal e consentimento, `Get-Date`, update/repair/rollback, WinRM em domínio, capabilities de serviços e fluxo de jobs.

### Decisões pendentes

- Primeira análise: manter `unconfigured`. `staggered_24h` é preferência de produto registrada, não decisão aprovada. Comparar sua distribuição/carga antes de uma ativação futura.
- Critérios de qualidade para substituir última análise e interpretação de dado ausente.
- Política de retenção futura após medição. Manter 7 dias para detalhe v2 de 60 s, 30 dias para 5 min e 90 dias para 1 h até decisão autorizada; não apagar dados.
- Alterar ou não o instalador para novas instalações em `continuous`; exige decisão e, se houver alteração do agente, novo pacote Windows assinado e homologado.
- Critérios de encerramento da issue #19 e habilitação do terminal administrativo, independentes do simples passe dos testes automatizados.

## 4. Incremento inicial RC48-01A — medição read-only

Primeira implementação iniciada na branch `codex/rc48-roadmap-telemetry`, derivada da `main` no SHA base declarado. O incremento adiciona o comando `report_telemetry_storage`, com escopo explícito `--endpoint UUID` ou `--all-endpoints`, janela limitada a 1–365 dias e amostra por grupo limitada a 1–1000 linhas.

O relatório distingue contagens e limites temporais exatos dentro da janela consultada das estatísticas amostradas; informa esquema/resolução, presença de famílias de métricas, qualidade de janela, faixas/mínimos/máximos, tamanho lógico JSON e, em PostgreSQL, tamanho físico amostrado das colunas e tamanho total global da tabela (incluindo índices/TOAST). Não publica IDs de endpoints. Tamanho lógico não é apresentado como tráfego de rede. Dados legacy são identificados separadamente e não recebem a retenção v2 por inferência.

Arquivos da primeira PR proposta:

- `agents/telemetry_storage_report.py`
- `agents/management/commands/report_telemetry_storage.py`
- `agents/test_telemetry_storage_report.py`
- este documento

Não há migration, dependência nova, escrita, coleta paralela, mudança de retenção, alteração de flags ou consulta operacional executada. O comando não será executado em produção por esta tarefa. Ele mede o estado que lhe for fornecido quando chamado; não certifica custos de tráfego, CPU ou crescimento futuro.

Validação deste incremento: **43 testes Django focados passaram em SQLite**, incluindo o relatório novo e regressões existentes de telemetria contínua/diagnóstico; `manage.py check`, `makemigrations --check --dry-run` e `git diff --check` passaram localmente. O run [45172812114](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/45172812114), no commit `9c8871c`, passou nos cinco jobs, incluindo PostgreSQL (`pg_column_size`) e E2Es Windows. No run [45174445584](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/45174445584), no HEAD atual `9c2d901` (o único delta em relação ao run verde é documental), SQLite, PostgreSQL, navegador isolado e contrato Windows passaram; o E2E Windows/terminal falhou na espera da página do endpoint. O primeiro run encontrou uma conversão incorreta de tupla para inteiro no caminho PostgreSQL, corrigida antes dos dois runs listados. Não houve execução de JavaScript ou .NET local porque este incremento só acrescenta relatório/command backend e seus testes Python.

### Investigação comparativa do `browser_condition_timeout`

O teste `test_real_browser_real_pty_simulated_local_consent_click` falhou no run `45174445584` na fase `endpoint_page`, aguardando até 30 segundos a condição `document.readyState==='complete' && [data-terminal-start]:not(:disabled)`. No run verde `45172812114`, o mesmo teste passou.

Evidências comparadas:

- Ambos os runs usaram Chrome for Testing `156.0.8078.80`; a inicialização CDP concluiu em `638 ms` no run verde e `534 ms` no run falho. `Browser.getVersion` foi executado pelo launcher antes do smoke.
- No run falho, o processo do agente simulado ainda estava ativo e a consulta do estado sintético não encontrou `TerminalSession`; a falha ocorreu antes do clique/request que cria a sessão.
- O launcher reportou processo Chrome encerrado, nenhum processo associado ao perfil restante e remoção do perfil temporário.
- O gate isolado do navegador passou, mas isso não reproduz o caminho do endpoint Django/terminal.

Esses fatos afastam falha de inicialização CDP naquele run, mas não distinguem redirect/autenticação, resposta HTTP incompleta/erro, script estático ausente ou erro de execução do JS do terminal. O harness atual não registra status final/redirect da navegação, presença de marcadores DOM sanitizados ou erros de console/rede por categoria. A causa de sincronização/readiness permanece **não confirmada**; não foi alterado código de terminal e não se considera o run verde anterior como explicação.

No HEAD atual `489f551`, duas novas validações limpas passaram nos cinco jobs: run pull request [45177479027](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/45177479027) e dispatch independente [45177527008](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/45177527008). Os logs dos E2Es confirmam Chrome CDP pronto e limpeza de perfil/processos. A falha não se reproduziu nessas execuções, mas a causa do timeout anterior continua desconhecida; não classificar como resolvida nem atribuir a causa a runner, terminal ou rede sem instrumentação adicional. Se for exigida causa raiz antes de distribuição ampla, abrir investigação/harness separado que registre status/redirect, erros de carregamento e readiness DOM sanitizados, sem alterar o contrato funcional do terminal.

### Revisão de custo e limite das consultas RC48-01A

O relatório não executa escrita, mas sua amostragem limitada não limita o custo das agregações exatas:

- `--sample-limit` (1–1000) limita linhas carregadas na aplicação para qualidade/faixas/tamanho JSON por combinação schema/resolução. As contagens, datas mínima/máxima e contagem além da retenção continuam sendo agregações/contagens sobre todas as linhas que correspondem à janela solicitada.
- `--all-endpoints` filtra por `collected_at`; o índice existente `endpoint_perf_time_idx` começa por `endpoint_id`, por isso não garante busca eficiente por período global. A agregação global pode ler grande parte da tabela. A amostra global ordenada por `collected_at` também pode precisar de ordenação; `--sample-limit` não reduz essas leituras. O cálculo do tamanho físico global consulta `pg_total_relation_size` e é incluído apenas em PostgreSQL.
- A janela máxima de 365 dias limita o intervalo temporal, não o número de linhas nem o tempo. Uma janela de 365 dias em frota grande não pode ser chamada de consulta de custo limitado sem plano e volume representativos.
- `--endpoint UUID` restringe por endpoint e pode aproveitar o índice composto endpoint+data; `--all-endpoints` é explícito e não é o modo padrão. Nenhuma consulta foi feita contra produção.

Proteções recomendadas antes de uma medição global em ambiente grande: executar primeiro em réplica/banco isolado ou janela aprovada; medir `EXPLAIN (ANALYZE, BUFFERS)` com volume representativo e timeout operacional; definir limite de duração explícito que falhe claramente; considerar índice global começando por `collected_at` somente se plano e carga demonstrarem necessidade. Não adicionar índice ou restringir a janela agora sem medição. Testes de desempenho devem verificar que a amostra materializada nunca excede o limite, que número de consultas depende do número de grupos schema/resolução e não do número de linhas, e que o plano de consulta do caminho global é revisado com fixture PostgreSQL representativa. Os testes atuais comprovam comportamento/correção e o caminho `pg_column_size`, não custo temporal na escala de produção.

Execução futura, somente por operador autorizado e com banco/ambiente explicitamente selecionado:

```powershell
python manage.py report_telemetry_storage --endpoint <UUID> --window-days 90 --sample-limit 200
python manage.py report_telemetry_storage --all-endpoints --window-days 90 --sample-limit 200
```

Usar um único modo de escopo por chamada. Revisar a saída agregada antes de anexá-la a qualquer ticket; a medição global não atribui o tamanho físico PostgreSQL a um endpoint específico.

## 5. Ordem recomendada e dependências

1. **RC48-01A — medição de telemetria/armazenamento.** Sem mutação; valida o contrato do relatório e fornece evidência para decisões de retenção. Esta é a primeira PR proposta.
2. **RC48-01B — auditoria de projeções e consumidores.** Com resultados representativos de 01A, mapear campos/alertas/API e abrir correção mínima apenas onde houver perda ou interpretação incorreta reproduzível. Qualquer migration para representar desconhecido no campo plano requer decisão separada.
3. **RC48-02 — confiabilidade da análise automática.** Validar testes SQLite/PostgreSQL e execução isolada, confirmar recuperação e limites; a ativação e o invocador real ficam com Operações. Primeira captura segue `unconfigured` até decisão.
4. **RC48-03 — preparação do piloto continuous.** Definir baseline e janela de observação, captura de filas legacy e rollback; requer identidade de piloto resolvida, release assinada autorizada e decisão sobre instalação/configuração.
5. **RC48-04 — regressões transversais.** Executar os gates das capacidades já integradas, sem alteração funcional especulativa. Corrigir separadamente qualquer falha reproduzível.
6. **Homologação/publicação:** somente após integração das PRs, aprovação operacional, backup, assinatura/trust verificadas e plano do responsável de produção. RC47 assinada permanece imutável.

## 6. Critérios de aceite da RC48

- Cada mudança de código tem PR pequena, testes pertinentes, diff revisado e autorização de integração separada.
- Flags e políticas permanecem fail-closed; scheduler e gravação automática não são ativados por código ou deploy.
- Não há nova coleta paralela nem mistura de dados `legacy` e `continuous`.
- Nenhuma alteração de retenção ocorre antes de medição e aprovação explícitas.
- Identidade sempre é vinculada a UUID/machine ID; hostname duplicado bloqueia operações até revisão.
- Release RC47 permanece imutável; qualquer mudança de agente demanda novo artefato assinado, compatível e homologado.
- Checks sintéticos são diferenciados de homologação operacional em Windows/Linux.
- Nenhum deploy, publicação, migration operacional, atualização de agente ou rollout é realizado por esta frente.

## 7. Referências

- [Plano/handoff original RC47](deploy-RC47.md)
- [Handoff final RC46→RC47](rc46-final-closure-rc47-handoff.md)
- Documentação de RC47-03A/B/C, telemetry continuous, política de modo e publicação do agente listada no plano original.
- PRs RC47 citadas na matriz/histórico do repositório; os estados de CI e integração devem ser reconfirmados em cada HEAD futuro.

## 8. Estado da consolidação RC48 (23/11/2026)

| Incremento | Estado e referência | Evidência/limite |
|---|---|---|
| RC48-01A — relatório read-only de telemetria/armazenamento | Integrada na #54; merge `e939636e3c0cf22b39b4518661a19fc1cda1cee3` | Sem migration ou escrita. A agregação `--all-endpoints` pode ler grande parte da tabela; não foi executada em produção. |
| RC48-01B — projeções e consumidores | Integrada na #55; merge `a4ce0c5e184a9a976197855c96ed180efb43d1f8` | Testes e gates aprovados. Fallback legacy reutiliza agregação por grupo sem misturar schema v1/v2. Medição sintética local não é benchmark de produção. |
| RC48-02 — confiabilidade do scheduler de análise | PR #56 aberta, HEAD `02467a27116692a0d7319f36363ba0e48b9cb500` | Run `45211422554`, tentativa 2: cinco checks aprovados no mesmo SHA, incluindo 11 E2Es Windows e probe isolado Chrome/CDP. A tentativa 1 falhou no probe após o smoke funcional. Causa não confirmada. |
| RC48-03 — preparação do piloto continuous | PR #57 aberta, branch `codex/rc48-03-continuous-pilot` | Run `45219241033`: cinco checks aprovados em `fd848710e540dae2f8837a6fe239263d86aa78a8`, antes da revisão documental. As métricas de fila são aditivas no v2 e exigem nova release Windows assinada. |
| RC48-04 — fixtures WinRM | PR #58 aberta, HEAD `66a8fe5865c7f0d6f5acec91433456b7019a1f0f` | Run `45224500294`: cinco checks aprovados. Diff limitado a dois arquivos de testes WinRM. |

As intermitências Browser E2E/Chrome-CDP e Repair Runner permanecem ocorrências históricas sem causa confirmada; um run verde posterior não as encerra. A issue #19 de PowerShell `Get-Date` em ConPTY restrito também é independente e continua como pendência operacional/técnica. Nenhum deploy, migration operacional, ativação de flag, publicação de agente ou ação em endpoint ocorreu nesta consolidação.

## 9. Auditoria RC48-04 — regressões de integração

| Área | Estado automatizado na base atual | Limite que permanece |
|---|---|---|
| Terminal assistido/ConPTY | O smoke funcional passou nas duas tentativas do run `45211422554`. O probe Chrome/CDP falhou na primeira e passou na segunda | A causa da falha do launcher permanece desconhecida. A issue #19 (cmdlet `Get-Date` sob token restrito) segue aberta. Distribuição ampla depende de homologação em Windows |
| Terminal administrativo | Fluxo independente de autorização e telas/testes estão integrados; checks Windows recentes construíram e exercitaram os contratos | Sem homologação com conta administrativa autorizada real; manter grant/admin auth desabilitados até decisão e teste operacional. Sem elevação implícita |
| Gerenciador de Serviços A/B | Consulta paginada e ações restritas estão integradas com autorização/identidade e testes | Exige agente com capability e release compatível; allowlist/flags não foram habilitadas. Nenhuma consulta/ação real foi executada nesta consolidação |
| Jobs e Políticas | Gestão central, associação por identidade e execução manual direcionada ao UUID têm cobertura de contrato/regressão | Nenhum job foi enviado a endpoint; identidade ambígua impede piloto por hostname |
| Updater/canal/rollback | Seleção explícita de canal/release, assinatura e contratos de rollback têm testes | Nenhuma atualização real foi feita; RC48 candidata precisa de novo ValidateOnly/DryRun e autorização de publicação |
| WinRM | Diagnóstico, recuperação e histórico possuem integração e testes automatizados | Autenticação/recuperação real em endpoint elegível do domínio permanece pendente; não confundir TCP/WS-Man com autenticação |
| Frontend e assets | Gates visuais desktop/mobile das áreas Monitoramento e WinRM passaram durante RC48-01B; contrato de versionamento está integrado | Homologação visual nos temas e navegadores do ambiente operacional não é substituída por screenshots do CI |
| Legacy/v2/análise persistida | Testes focados RC48-01B passaram. Nos testes locais RC48-02 sob SQLite, os cenários específicos de PostgreSQL foram reservados ao CI. A tentativa 2 do run `45211422554` aprovou SQLite e PostgreSQL | O scheduler e sua primeira captura dependem de confirmação operacional do invocador, validação da qualidade dos dados e decisão de ativação |

Na primeira tentativa do run `45211422554`, o smoke funcional de terminal, ConPTY e Repair Runner passou. O probe isolado falhou com `launch_failed`, encerrou o processo e removeu o perfil. Na segunda tentativa, o probe ficou pronto em 330 ms, com limpeza confirmada e sem alterações no SHA, no harness ou nos timeouts. Os avisos `access_denied`, `sandbox` e `other_error` também aparecem nas execuções verdes e não demonstram a causa da falha.

## 10. Sequência para integração e congelamento

1. Após autorização específica, integrar #56 por merge commit e confirmar o novo SHA da main e seus gates.
2. Incorporar essa main à #57 por merge normal, conferir o diff e revalidar os cinco checks no novo HEAD. Após revisão e autorização própria, integrar a #57 e conferir os gates da nova base.
3. Repetir o procedimento na #58 sobre a main consolidada e confirmar que o diff continua só nos fixtures WinRM.

Pausar a sequência se houver falha de CI, mudanças fora do escopo, conflitos ou revisão pendente. As três PRs não introduzem migrations nem mudanças funcionais de frontend. O schema de produção deve ser conferido separadamente contra as migrations já existentes na base. O backend da #56 preserva o scheduler desabilitado, dry-run ligado e primeira captura `unconfigured` por padrão. A #57 acrescenta métricas no agente; o backend v2 existente as aceita.

O congelamento exige o SHA consolidado com gates aprovados, histórico das intermitências de Chrome/CDP e Repair Runner e decisão explícita sobre o risco residual. Para o piloto, confirmar UUID e machine_id, o contrato v2 e ACK, a configuração realmente carregada e o registro das capabilities no heartbeat. Os anúncios de terminal ConPTY, terminal administrativo e gerenciamento de serviços dependem dos gates existentes do agente e das autorizações do backend. Homologar em VM os recursos habilitados no escopo aprovado.

Após o congelamento e com autorização própria, confirmar versão livre no catálogo e revalidar a chave e o trust originais no ambiente oficial. Executar ValidateOnly com `validate_only=true`, `dry_run=false` e `skip_tests=false`. Após aprovação, executar DryRun com `validate_only=false`, `dry_run=true` e `skip_tests=false`, no mesmo SHA e no canal `development`. Guardar o SHA de origem, a versão, os testes, o manifesto, o SHA-256 do ZIP e do manifesto e a verificação da assinatura RSA-PSS. Seguir [o procedimento oficial de CI](nightowl_agent_release_ci.md). A publicação e a instalação exigem autorizações próprias.
