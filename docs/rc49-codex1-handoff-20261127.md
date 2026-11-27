# NightOwl — Handoff Codex 1 (27/11/2026)

> Contexto central: [rc49-handoff-contexto-20261127.md](rc49-handoff-contexto-20261127.md). Ler também [Stable 07](rc49-07-stable-desktop-rollout.md) e [deploy-RC49](deploy-RC49.md). Conferir GitHub ao retomar. A referência de código é `main 54bda321be6a91d5369d134d841c24130a644947`, ANTES de qualquer merge posterior.

## 1. Missão e responsabilidades

**Codex 1 cuida do rollout Stable, RC49-07B e, em negociação posterior, Políticas de Execução/Windows Update/limpeza/reboot/AD de computadores.** Não interferir na Central de Software da RC49-03C que o Codex 2 mantém.

- **RC49-07A / PR #75:** campanhas Stable de alvos congelados na publicação, lotes configuráveis, offline pendente, exclusões de servidores e VMs, grupo `stable-desktops`; perfil `NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=False` por padrão.
- **RC49-07B-1 / PR #79:** integrada por merge commit `6cda1db3f2749366a547dee019d286cb08774354`. `AgentRolloutCoverageRound` registra snapshot, hash, contagens e auditoria da cobertura complementar sem reabrir campanha original; migration `agents.0059_stable_coverage_round`, após `0058_softwarepackagerecord`.
- **RC49-07B-2 / PR #83:** IMPLEMENTADA EM BRANCH E ABERTA, **NÃO INTEGRADA**. Branch `codex/rc49-07b2-stable-catchup`, HEAD observado `175acca35b10d11f4294dc66b93cdb688be5274a`. CI 5/5 no HEAD anterior à sincronização ([run 46008929843](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46008929843)). Responsabilidade atual: revisar e integrar este incremento após corrigir conflito de migration e divergências de código.
- O executor `process_agent_rollouts` originalmente só processa campanhas `running`. O B2 acrescenta descoberta de novos desktops/eligibilidade tardia mesmo após conclusão original e executa via campanhas complementares (rodadas ligadas a campanha executora); não reativa campanha abortada. Usa locking PostgreSQL, unicidade/idempotência e revalida assinatura, identidade, versão, política, canal e grupo antes de despacho.

## 2. BLOQUEIO ATUAL — colisão de migrations e arquivos compartilhados

**A main JÁ possui `agents.0062_rc49_software_package_operations` da PR #82, merge `54bda321...`.**

A PR #83 introduziu `agents.0062_stable_coverage_execution_campaign`, dependente de `0061_rc49_software_execution_profiles`. A numeração duplicada forma duas folhas Django independentes. O GitHub pode apresentar mergeable apesar da divergência do grafo Django.

Ao retomar:

1. Fazer fetch da `main` atual, integrar por **merge normal** na branch da #83, preservando o histórico e o trabalho local; não aplicar rebase/squash inadvertido.
2. Verificar se `0062_stable_coverage_execution_campaign` foi aplicada a algum banco persistente. **Se não**, renomear para `0063_stable_coverage_execution_campaign` e depender de `0062_rc49_software_package_operations`. **Se sim**, preservar a migration aplicada e propor reconciliação do grafo sem renomear história usada.
3. Resolver cuidadosamente `agents/views.py` (também alterado pela #82 e pela #84); manter checagens de ACK v2 e bloqueio de resultado para jobs ainda `queued`. Preservar o fluxo de software sem confundi-lo com rollout.
4. Revisar as constraints de unicidade de campanha ativa por release e alvo lógico; rodadas não devem fabricar jobs duplicados; preservar snapshot original e motivos de elegibilidade. Confirmar cancelamento, pausas, release superseded, offline, already_current (satisfeito sem fingir instalação).
5. Executar `manage.py check`, `makemigrations --check --dry-run`, `migrate --plan`, testes de SQLite, concorrência PostgreSQL, contrato Windows, browser isolado e E2E .NET/terminal no SHA atualizado; conferir diff e documentação.
6. Entregar a PR #83 pronta para decisão de merge; **sem merge automático se o responsável não autorizou**, sem deploy, perfil Stable ativado ou jobs na frota.

## 3. Etapas seguintes — negociar depois da #83

- **07B-3 — retries/reconciliação:** registrar `already_current` a partir da versão observada; distinguir atualizado por campanha de já instalado; classificar falhas permanentes/transitórias; limites por alvo, backoff, evitar reenvio após cancelamento/rollback; comparar recibo + versão + heartbeat/saúde e reconciliar alvos sem job.
- **07B-4 — operação e cobertura:** painel agregado original+rodadas, motivos de exclusão, atrasos/offline, pausa/cancelamento, teste de frota sintética e relatório de 100% elegíveis. Cobertura contínua não significa atualização simultânea e não inclui VMs/servidores sem classificação positiva.
- **Políticas de execução (RC49-04, a negociar):** Windows Update de qualidade/segurança, sem reescrever GPO/WSUS; limpeza de disco por categorias permitidas e medição antes/depois; condições locais e agendas reutilizando `AutomationDefinition`/ocorrências/jobs. Campo `Se necessário reiniciar, como será feito?`: janela, notificar/adiar, técnico autoriza ou não reiniciar. Não definir defaults ainda. Escopo AD de grupos de **computadores**, OU e domínio precisa identidade/snapshot auditável e não pode usar grupos de usuários como atalho.
- **Terminal e continuidade:** PR #71 (`check_operational_continuity`) e #72 (grants persistentes do terminal admin) já integradas; não reabilitar/deselecionar gates e flags aprovados em novas RCs. Homologação real Windows/ConPTY independente da #83.

## 4. Regras de execução e comunicação

- Trabalhar por incremento negociado; uma vez aprovado, implementar até PR funcional, CI e documentação, sem parar em uma migration parcial sem bloqueio real.
- Não criar scheduler, motor, transport, app de patrimônio ou novo executor quando já existe contrato reutilizável.
- Não criar backups/checkpoints redundantes para mudanças pequenas; operações de grande impacto de banco/frota são exceção.
- Não declarar implantação por merge: diferenciar código integrado, release assinada e homologação real.
- A PR #84 é do Codex 2: executor MSI Windows, HEAD `8f5fc0d...`, CI 5/5, sem migration e ainda sem merge. O arquivo `agents/views.py` é tocado em ambas as frentes; coordenar a sequência de integração.
- Não mesclar trabalhos com outros projetos ou chats. Este handoff é exclusivo do NightOwl.

## 5. Atualização após integração da PR #83 (27/11/2026)

- Main: `97a5f61518edf9d686f21dc84a23488f7dcb4770`.
- PR #83 integrada por merge commit `97a5f61518edf9d686f21dc84a23488f7dcb4770`; parents: `11b3ba1001486c026586571a9c393ef417820590` e `d7a9b6f52d7dffee83048e5d35e777acab283656`.
- CI run `46042230821`: PostgreSQL, SQLite, Windows deployment contract, Windows isolated browser comparison e Windows .NET/terminal E2E, todos PASS.
- Migration da cobertura Stable: `agents.0063_stable_coverage_execution_campaign`, dependente de `agents.0062_rc49_software_package_operations`. A #83 não foi aplicada em banco persistente antes do merge; CI usou bancos efêmeros.
- A conciliação de `agents/views.py` preservou os contratos v2 e ACK da #82. PR #84 segue separada.
- 07B-3 (retries e reconciliação pós-execução) não iniciada; definir requisitos antes de implementar. Sem deploy, ativação Stable ou jobs de frota decorrentes desta integração.
