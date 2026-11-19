# NightOwl RC48 — plano consolidado de desenvolvimento e validação

**Estado:** planejamento e primeiro incremento local de medição. Este documento não autoriza publicação, deploy, execução recorrente, alteração de flags, migrações operacionais ou atualização de endpoints.

**Base de código consultada:** `main` em `218526a85b1865ef90c4c05f04353233173b0edc`.
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
| Assinatura/publicação da próxima release Windows | Dependente de decisão/medição operacional | RC47 publicada não será alterada. A configuração/posse segura da chave de release e secrets do ambiente ainda precisam ser confirmadas pelo responsável de publicação. | Fora da frente de desenvolvimento. Nenhuma geração/publicação nesta branch. |

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
- Confirmar chave original, trust, aprovação do environment e processo oficial de assinatura com o responsável. Não recuperar, copiar ou provisionar secrets aqui.
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

Validação local deste incremento: **43 testes Django focados passaram em SQLite**, incluindo o relatório novo e regressões existentes de telemetria contínua/diagnóstico; `manage.py check` e `makemigrations --check --dry-run` passaram; `git diff --check` passou. PostgreSQL não está disponível como serviço/container local nesta estação, portanto o ramo específico de `pg_column_size` ainda precisa do check PostgreSQL isolado do CI antes da integração. Não houve execução de JavaScript ou .NET porque o incremento só acrescenta relatório/command backend e seus testes Python.

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
