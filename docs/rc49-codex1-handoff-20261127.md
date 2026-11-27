# NightOwl — Handoff Codex 1 (27/11/2026)

> Contexto central: [rc49-handoff-contexto-20261127.md](rc49-handoff-contexto-20261127.md). Ler também [Stable 07](rc49-07-stable-desktop-rollout.md) e [deploy-RC49](deploy-RC49.md). Conferir GitHub ao retomar. A referência de código é main `97a5f61518edf9d686f21dc84a23488f7dcb4770` após #83.

## 1. Missão e responsabilidades

**Codex 1 cuida do rollout Stable, RC49-07B e, em negociação posterior, Políticas de Execução/Windows Update/limpeza/reboot/AD de computadores.** Não interferir na Central de Software da RC49-03C que o Codex 2 mantém.

- **RC49-07A / PR #75:** campanhas Stable de alvos congelados na publicação, lotes configuráveis, offline pendente, exclusões de servidores e VMs, grupo `stable-desktops`; perfil `NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=False` por padrão.
- **RC49-07B-1 / PR #79:** integrada por merge commit `6cda1db3f2749366a547dee019d286cb08774354`. `AgentRolloutCoverageRound` registra snapshot, hash, contagens e auditoria da cobertura complementar sem reabrir campanha original; migration `agents.0059_stable_coverage_round`, após `0058_softwarepackagerecord`.
- **RC49-07B-2 / PR #83 INTEGRADA**, merge `97a5f61518edf9d686f21dc84a23488f7dcb4770`, CI 5/5 no HEAD d7a9b6f; sem deploy.
- O executor `process_agent_rollouts` originalmente só processa campanhas `running`. O B2 acrescenta descoberta de novos desktops/eligibilidade tardia mesmo após conclusão original e executa via campanhas complementares (rodadas ligadas a campanha executora); não reativa campanha abortada. Usa locking PostgreSQL, unicidade/idempotência e revalida assinatura, identidade, versão, política, canal e grupo antes de despacho.

## 2. Colisão de migrations resolvida

0063 Stable depende de 0062 operações; manter grafo linear e coordenar com Codex 2. Nova migration deve usar folha vigente. Não repetir sincronização da #83 integrada.

## 3. Etapas seguintes — negociar depois da #83

- Codex 1: 07B-3 retries/reconciliação no runner existente. Execução inicial + até 3 retries adicionais, backoff configurável 15 min, 1 h, 4 h após falha recuperável; offline/condição temporária aguardam sem consumir tentativa. Comunicação/download só permitem retry se não execução comprovada ou estado reconciliado; não reenviar cegamente resultado ambíguo. Reconciliar identidade, recibo, versão, heartbeat e saúde; distinguir already_current de atualização comprovada. Assinatura/identidade inválida, rollback ou falha permanente bloqueiam e alertam; esgotamento requer intervenção. Revalidar release vigente, aprovação, pausa/aborto, exclusões e lotes; não reabrir campanhas concluídas nem duplicar jobs. Verificar release sem elegíveis iniciais e ingresso posterior sem contornar aprovação. Depois 07B-4 UI agregada de cobertura, rodadas e tentativas.
- **07B-4 — operação e cobertura:** painel agregado original+rodadas, motivos de exclusão, atrasos/offline, pausa/cancelamento, teste de frota sintética e relatório de 100% elegíveis. Cobertura contínua não significa atualização simultânea e não inclui VMs/servidores sem classificação positiva.
- Roadmap de políticas aprovado em 27/11: após Stable, 04A catálogo de ações nativas versionadas e dropdown/formulário dinâmico por schema; 04B motor de políticas, condições e agendamentos; 04C Windows Update qualidade/segurança respeitando GPO/WSUS, sem drivers/feature upgrades no primeiro recorte; 04D limpeza segura de disco por categorias permitidas com espaço antes/depois; 04E reboot (janela, aviso/adiamento, autorização técnica ou não automático); 04F escopos dinâmicos por AD de computadores, domínio, OU, grupos, endpoints e exclusões; 05 biblioteca de scripts personalizados aprovados/versionados, parâmetros tipados e auditoria. Capacidades nativas no código, políticas e parâmetros na UI; scripts aprovados alimentam a mesma página. Reutilizar AutomationDefinition/Occurrence, AgentJob, v2, RBAC e scheduler existente. Defaults de manutenção ainda a definir por incremento. Não é autorização de deploy ou implementação simultânea de todo roadmap.
- **Terminal e continuidade:** PR #71 (`check_operational_continuity`) e #72 (grants persistentes do terminal admin) já integradas; não reabilitar/deselecionar gates e flags aprovados em novas RCs. Homologação real Windows/ConPTY independente da #83.

## 4. Regras de execução e comunicação

- Trabalhar por incremento negociado; uma vez aprovado, implementar até PR funcional, CI e documentação, sem parar em uma migration parcial sem bloqueio real.
- Não criar scheduler, motor, transport, app de patrimônio ou novo executor quando já existe contrato reutilizável.
- Não criar backups/checkpoints redundantes para mudanças pequenas; operações de grande impacto de banco/frota são exceção.
- Não declarar implantação por merge: diferenciar código integrado, release assinada e homologação real.
- #84 do Codex 2: HEAD `75777e6be7033a3a79718fe7a082f695cc00fb94`, CI 5/5, sem migration, aberta. Sincronizar com #83 e repetir CI antes de integrar; coordenar agents/views.py.
- Não mesclar trabalhos com outros projetos ou chats. Este handoff é exclusivo do NightOwl.

## 5. Atualização após integração da PR #83 (27/11/2026)

- Main: `97a5f61518edf9d686f21dc84a23488f7dcb4770`.
- PR #83 integrada por merge commit `97a5f61518edf9d686f21dc84a23488f7dcb4770`; parents: `11b3ba1001486c026586571a9c393ef417820590` e `d7a9b6f52d7dffee83048e5d35e777acab283656`.
- CI run `46042230821`: PostgreSQL, SQLite, Windows deployment contract, Windows isolated browser comparison e Windows .NET/terminal E2E, todos PASS.
- Migration da cobertura Stable: `agents.0063_stable_coverage_execution_campaign`, dependente de `agents.0062_rc49_software_package_operations`. A #83 não foi aplicada em banco persistente antes do merge; CI usou bancos efêmeros.
- A conciliação de `agents/views.py` preservou os contratos v2 e ACK da #82. PR #84 segue separada.
- 07B-3 não iniciada; requisitos registrados acima. Sem deploy, ativação Stable ou jobs de frota decorrentes desta integração.
