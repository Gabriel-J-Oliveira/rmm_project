# NightOwl — Handoff canônico de contexto RC49 (27/11/2026)

> **Leia este arquivo primeiro em novos chats e sessões Codex.** É a fotografia de desenvolvimento conferida no GitHub em 27/11/2026 (13h BRT), **não um relatório de deploy ou homologação real**.
> **Main de código:** `97a5f61518edf9d686f21dc84a23488f7dcb4770`. Confirmar GitHub a cada retomada; PR documental não representa deploy.
> Fonte funcional: [deploy-RC49.md](deploy-RC49.md); visão completa: [nightowl-visao-produto-roadmap-rc49.md](nightowl-visao-produto-roadmap-rc49.md). Handoffs específicos: [Codex 1](rc49-codex1-handoff-20261127.md) e [Codex 2](rc49-codex2-handoff-20261127.md).

## 1. Objetivo e decisões de produto aprovadas

O NightOwl é um RMM para **monitorar, proteger, inventariar, administrar, automatizar e manter o histórico** do parque de TI. O alvo principal do canal Stable são **desktops físicos Windows elegíveis**; servidores e VMs de laboratório não participam automaticamente. O canal Development atende pilotos e testes.

- Central de Endpoints com hardware, software, telemetria e identidade; patrimônio **estável e distinto de agente/instalação**; código patrimonial legível, único, provisório e pesquisável, sem prefixos definitivos até decidir a nomenclatura das **duas empresas**.
- **Aba Registros dentro do Endpoint**, sem nova tela geral de Ativos: observações, incidentes, formatações, manutenção, troca de responsável e histórico. Futuro vínculo com chamados fica para próxima RC; usuário observado pelo agente **não é** responsável patrimonial.
- **NOC** com alertas reais, políticas de alerta, cards de atenção, modo TV, central de notificações, ações reais e informação recente. Não considerar CI como homologação operacional.
- **Central de Software** com duas abas, “Softwares coletados” (inventário verdadeiro, sem fallback mock) e “Uploads”. Uploads em **duas colunas no desktop** e uma no mobile; **nome amigável abre drawer**. Armazenar MSI/EXE, editar/substituir binário, hashes, aprovação/revogação auditada; sem versionamento perpétuo de binários, mas preservar histórico de hashes/execuções e **não remover binário em uso**. Instalar/atualizar/desinstalar devem confirmar estado instalado, não apenas saída/ACK.
- **Impressoras/FollowMe**: desejadas dentro da administração de software/distribuição (frente 03E/08), respeitando o sistema de filas existente, não pressupor instalação tradicional por IP. **Bitdefender** deve ser referência de proteção/controle de aplicações e eventual análise antimalware futura; aprovação manual de arquivo não é veredito antimalware.
- **Políticas de Execução** além do bloqueio de apps: Windows Update controlado, limpeza segura de disco, agendamentos, condições locais (CPU, RAM, energia, armazenamento, uso ativo, rede), exceções e escopo. Preferir operação tipada por job e **não shell genérico**. Para reinícios, mostrar `Se necessário reiniciar, como será feito?` com opções por política (janela, notificar/adiar, autorização técnica, não reiniciar automaticamente); os limites/defaults ainda precisam ser definidos. Respeitar GPO/WSUS. Drivers e feature upgrades fora do primeiro recorte Windows Update.
- Escopo futuro de políticas por **todos, domínio, OU, grupos de segurança de computadores AD, endpoints e exclusões**. Grupos de **usuários** não equivalem a grupos de computadores; associação AD deve usar identidade confiável e snapshot auditável.
- Biblioteca de scripts pré-aprovados, execução remota, terminal assistido/admin, serviços Windows e ciclo de vida físico/operacional permanecem na visão.
- **Continuidade operacional:** novos deploys/upgrade/rollback devem preservar configurações, flags, grants e capabilities já homologados, salvo revogação explícita ou incompatibilidade real. Não repetir todo o processo de homologação de funções antigas a cada RC. **Backups/checkpoints extras só em operações de grande impacto no banco ou na frota**; não exigir backup para documentação, migrations meramente geradas, ajustes pequenos ou testes sintéticos. Ambiente é de homologação controlada, com aceitação de risco deliberada pelo responsável.

## 2. Estado atual de GitHub e PRs

| Entrega | Situação em 27/11 | Referência |
| --- | --- | --- |
| RC49-01 patrimônio/identidade/remover agente | Implementação integrada; operação real ainda precisa homologação | #64, #65; [01B](rc49-01b-identidade-ciclo-de-vida.md) |
| RC49-02 NOC, alertas, modo TV e notificações | Incrementos 02A–02E integrados; homologação operacional pendente | #69, #70, #73, #74, #76; [02](rc49-02-noc-operacional.md) |
| Continuidade runtime e terminal admin | Código integrado; autorização/grants e Windows reais exigem validação operacional | #71, #72; [continuidade](rc49-10-runtime-continuity.md), [grants](rc49-admin-terminal-grant-management.md) |
| Stable snapshot inicial (07A) | Integrado; campanha de conjunto fixo, perfil desligado por padrão | #75; [Stable](rc49-07-stable-desktop-rollout.md) |
| Stable coverage foundation (07B-1) | Integrado | #79, merge `6cda1db3f2749366a547dee019d286cb08774354`, migration `agents.0059` |
| Central de Software 03A | Integrada | #78, merge `384858d88f0c929f206a8c11df5abd6db564ba34`; `agents.0058` |
| 03B upload MSI/EXE privado | Integrado | #80, merge `677a96a3c61ab024c94896a6339cdfe544235551`; `agents.0060` |
| 03C-A perfis e aprovação | Integrado | #81, merge `c7c59e1646bb0e001254d8a0474a9beec0d17eb8`; `agents.0061` |
| 03C-B1 operações/backend/autorização/ACK | **Integrado** | #82, merge `54bda321be6a91d5369d134d841c24130a644947`; `agents.0062_rc49_software_package_operations`; CI [46012576544](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46012576544) 5/5 |
| 07B-2 catch-up Stable | **Integrada #83** | merge `97a5f61518edf9d686f21dc84a23488f7dcb4770`; CI 46042230821 5/5; migration 0063 |
| 03C-B2 executor MSI Windows | **PR #84 aberta, sem merge** | HEAD `75777e6be7033a3a79718fe7a082f695cc00fb94`; CI 46045236911 5/5; sincronizar com #83; sem teste real |
| Documentação histórica RC47 e planejamento Zabbix/Wazuh | Integradas, não mudam escopo funcional RC49 | #51 e #50 |

Main de código `97a5f61518edf9d686f21dc84a23488f7dcb4770`. [#83](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/83) integrada por merge normal, HEAD `d7a9b6f52d7dffee83048e5d35e777acab283656`, CI [46042230821](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46042230821) 5/5. Migration agents.0063_stable_coverage_execution_campaign depende de 0062_rc49_software_package_operations; colisão resolvida. [#84](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/84) aberta, HEAD `75777e6be7033a3a79718fe7a082f695cc00fb94`, CI [46045236911](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46045236911) 5/5 no HEAD, base incorporada `11b3ba1001486c026586571a9c393ef417820590`, sem migration. Mergeable na consulta, mas falta incorporar #83, preservar ambos os contratos em agents/views.py, repetir CI e obter autorização de merge. #86 é documental. **Sem evidência de deploy RC49, migrations operacionais, release Windows assinada RC49, ativação Stable, despacho de software ou instalação real.** Último ambiente relatado RC48/48.1; confirmar produção separadamente.

### Atualização após PR #83 — 27/11/2026

Main: `97a5f61518edf9d686f21dc84a23488f7dcb4770`. PR #83 foi integrada por merge commit normal; CI run `46042230821` passou 5/5. A migration Stable está como `agents.0063_stable_coverage_execution_campaign`, dependente de `agents.0062_rc49_software_package_operations`; cadeia consolidada: `0057_admin_terminal_access_grant` → `0058_softwarepackagerecord` → `0059_stable_coverage_round` → `0060_rc49_software_package_uploads` → `0061_rc49_software_execution_profiles` → `0062_rc49_software_package_operations` → `0063_stable_coverage_execution_campaign`. A PR #84 não foi incorporada. 07B-3 não iniciada; requisitos registrados abaixo. Esta integração não implica deploy, aplicação operacional de migrations, ativação do Stable ou jobs reais.

## 3. Dependências e migrations — resolvido

Cadeia agents linear 0057–0063; 0063 Stable depende de 0062 operações. Antiga 0062 Stable sem registro de aplicação persistente, segundo handoff. #84 não tem migration. Coordenar numeração futura e preservar ambas as alterações em agents/views.py. CI da #83 passou Django check e makemigrations --check; migrate --plan não confirmado separadamente.

## 4. Responsabilidades simultâneas

### Codex 1 — Stable / RC49-07B

- #83 integrada: catch-up com campanhas complementares e snapshots imutáveis, sem retries automáticos.
- Codex 1: 07B-3 retries/reconciliação no runner existente. Execução inicial + até 3 retries adicionais, backoff configurável 15 min, 1 h, 4 h após falha recuperável; offline/condição temporária aguardam sem consumir tentativa. Comunicação/download só permitem retry se não execução comprovada ou estado reconciliado; não reenviar cegamente resultado ambíguo. Reconciliar identidade, recibo, versão, heartbeat e saúde; distinguir already_current de atualização comprovada. Assinatura/identidade inválida, rollback ou falha permanente bloqueiam e alertam; esgotamento requer intervenção. Revalidar release vigente, aprovação, pausa/aborto, exclusões e lotes; não reabrir campanhas concluídas nem duplicar jobs. Verificar release sem elegíveis iniciais e ingresso posterior sem contornar aprovação. Depois 07B-4 UI agregada de cobertura, rodadas e tentativas.
- Roadmap de políticas aprovado em 27/11: após Stable, 04A catálogo de ações nativas versionadas e dropdown/formulário dinâmico por schema; 04B motor de políticas, condições e agendamentos; 04C Windows Update qualidade/segurança respeitando GPO/WSUS, sem drivers/feature upgrades no primeiro recorte; 04D limpeza segura de disco por categorias permitidas com espaço antes/depois; 04E reboot (janela, aviso/adiamento, autorização técnica ou não automático); 04F escopos dinâmicos por AD de computadores, domínio, OU, grupos, endpoints e exclusões; 05 biblioteca de scripts personalizados aprovados/versionados, parâmetros tipados e auditoria. Capacidades nativas no código, políticas e parâmetros na UI; scripts aprovados alimentam a mesma página. Reutilizar AutomationDefinition/Occurrence, AgentJob, v2, RBAC e scheduler existente. Defaults de manutenção ainda a definir por incremento. Não é autorização de deploy ou implementação simultânea de todo roadmap.
- Ações nativas e políticas ainda não implementadas.

### Codex 2 — Central de Software / RC49-03C

- **Recém-integrada:** PR [#82](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/82), backend B1. Corrigidos `import re` no validador e rejeição/auditoria de ACK novo de operação v2 ainda `queued`; aceitar apenas novos resultados `sent`/`running`; replays registrados idempotentes.
- #84 MSI v2 implementado e revisado. Revisão B2: corrigido ACK com agent_version (validação estrita), falhas v2 e propagação de error_code; novos ACKs queued rejeitados, v1 e replay preservados. Detecção MSI MsiGetProductInfoExW em contexto de máquina, consulta inicial de tamanho ERROR_SUCCESS; timeout HTTP de perfil/artefato subordinado ao token do job, até 1800 s. Testes sintéticos/doubles, sem instalação real. Repair Runner: timeout histórico ~20,9 s, rerun ~6,9 s, causa não comprovada. Major upgrades com mudança de ProductCode, EXE, reconciliação geral e escala pendentes. Confiança publisher por pin local opt-in; gerenciamento centralizado é proposta futura.
- Codex 2: fechar sincronização/CI da #84 e obter autorização de merge normal; depois negociar 03C-C executor EXE (argumentos estruturados por fabricante, detecção explícita antes/depois, sem flags silenciosas universais, sem executar UninstallString coletado, preservar aprovação/hash/revisão, transporte v2, verificação e compatibilidade MSI/v1). Gerenciamento centralizado de confiança proposto a encaixar; depois 03C-D escala, UI/progresso, atualização/desinstalação e reconciliação; 03E/08 FollowMe, Bitdefender futuro. Testes reais de software somente no fechamento do desenvolvimento RC49.
- **Depois da B2:** RC49-03C-C para **EXE** com argumentos estruturados, detecção explícita e compatibilidade por fornecedor; RC49-03C-D para atualização/desinstalação/reconciliação em escala, offline, distribuição gradual e interface de gestão; distinguir MSI já implementado na B2 de cobertura generalizada de software. Futuramente RC49-03E / 08 para **impressoras FollowMe**. Scan antimalware/Bitdefender é incremento posterior a negociar.

## 5. Implementações por frente e pendências ainda abertas

| Frente | Código entregue | O que falta para considerar funcionalmente encerrada |
| --- | --- | --- |
| 01 Registros/patrimônio | Aba Registros e identidade patrimonial, remoção verificada (PRs históricas) | Homologação de troca de hardware, remoção/reinstalação e códigos oficiais das duas empresas; tickets na próxima RC |
| 02 NOC | 02A–02E integradas | Validar NOC/alertas/notificações com dados, operadores e configuração real |
| 03A/03B | Inventário verdadeiro, UI, cadastro/armazenamento privado MSI/EXE, aprovação/auditoria | Deploy operacional: conferir storage privado e limite multipart/Nginx antes de habilitar upload |
| 03C-A/B1 | Perfil versionado, aprovação, contrato imutável, API de operação idempotente, download privado, ACK verificado | B2 agente assinada e homologada; depois EXE e operações em escala |
| 03C-B2 | **PR #84 aberta, código ainda não na main** | Revisão, merge, pacote Windows assinado do SHA final, testes reais só após fechamento de desenvolvimento conforme decisão do responsável |
| 07A/07B1/07B2 | Campanha fixa, rodadas e catch-up integrados | 07B-3/4; flag opt-in off |
| 04 Políticas | Estrutura reaproveitável e roadmap 04A–04F aprovado | Catálogo, motor, Windows Update, limpeza, reboot e AD de computadores |
| 05 Scripts | Base para catálogo/execução limitada | Biblioteca segura, aprovação, parâmetros, escopos, auditoria, sem shell arbitrário |
| 06 Serviços/terminal | RC48 com serviços paginados e terminal; grants persistentes em #72 | Homologação Windows real, permissões e UI de serviços; controle operacional por RBAC/grant |
| 08 Impressoras | Ideia negociada | Contrato FollowMe e políticas de distribuição |
| 09 Segurança | Telemetria e integração futura planejada | Conexão Bitdefender/postura e análise antimalware, sem duplicar bloqueio |
| 10 Confiabilidade | #71, continuidade runtime, instrumentos de baseline | Confirmar comportamento de deploy/relay/flags; acompanhar intermitências Chrome/CDP/Repair Runner |

## 6. Contratos e limites técnicos importantes

- Motor único de jobs: `AgentJob`, pull/ACK, idempotência, `AuditEvent`, envelopes versionados; não criar transporte/scheduler paralelo.
- No B1, `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=False` por default; registrar job **não** o entrega com flag desabilitada. Revogação/política/identidade/capability são revalidadas ao entregar. ACK de job `queued` v2 **não** pode fingir execução.
- Download agente autorizado por bearer existente **em cabeçalho**, por job e endpoint vinculados, sem URL pública/token query. Binário substituído não deve ser removido com operação ativa. Package/profile approval é ligada a hash + revisão; substituir perfil/artefato invalida aprovação.
- Para MSI v2: sem `UninstallString` de inventário, sem shell arbitrário, sem reboot implícito. Só confirmar instalado por ProductCode/versão compatíveis; código `3010` significa `reboot_pending`.
- Servidor Linux para uploads exige `NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT` privado, `NIGHTOWL_SOFTWARE_PACKAGE_MAX_UPLOAD_BYTES` (teto 1 GiB na 03B), espaço e `client_max_body_size` compatível (documentação sugere `1025m` para multipart de 1 GiB). Não inferir que isso está provisionado em produção.
- Stable: `NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED` desligada por default, grupo `stable-desktops`, apenas desktop físico Windows 10/11 com classificação positiva, release stable assinada a 100%, lotes, exclusões de VMs/servidores/grupos protegidos. Offline não ocupa vaga; sucesso depende de recibo + versão + saúde, não de job criado.
- Testes mínimos usuais: SQLite, PostgreSQL com concorrência, Windows deployment contract, browser isolado, Windows .NET/terminal E2E. CI verde não encerra riscos intermitentes do Chrome/CDP, Repair Runner ou questão `Get-Date` no ConPTY restrito.
- Operação controlada: sem merge/deploy/migrations operacionais/produção/flag/frota/release Windows fora de autorização explícita. **Não exigir staging VM ou backups repetidos por pequenos ajustes**. Checkpoint só proporcional a impacto real (grande mutação de banco, frota, operação irreversível).

## 7. Piloto Windows e distinção entre ambientes

- Piloto histórico de RC48: Hyper-V VM `windows-client`, hostname `CS-CVEL-0281`, agente associado a UUID `c97045f2-35ff-48f1-afa5-095173fbb3dd` e machine ID `1eca4af2-4950-4a2d-9b9f-fa7010a62464`. Existe Dell físico com hostname semelhante; **não usar hostname como identidade**.
- Testes reais prévios RC48: telemetria continuous e consulta paginada de serviços; RC48.1 reduziu coleta a 295 serviços em 3 páginas (~10,3 s em teste informado). **Não extrapolar** que novos executores RC49 foram instalados ou testados nessa VM.
- Último backend publicado relatado nas conversas pertence à RC48/RC48.1; **verificar versão de produção na hora da próxima implantação**, pois integrações de PR no GitHub não fazem deploy.

## 8. Protocolo de retomada para novo chat ou Codex

1. Leia este arquivo, [visão](nightowl-visao-produto-roadmap-rc49.md), [deploy](deploy-RC49.md) e os handoffs [Codex 1](rc49-codex1-handoff-20261127.md) / [Codex 2](rc49-codex2-handoff-20261127.md). Leia os documentos específicos da frente antes de propor trabalho.
2. **Confirme online** `main`, PRs abertas, seus HEAD/base/CI e o grafo de migrations. Não confiar apenas em nomes, resumos ou datas anteriores.
3. Distinga: **planejado / aprovado / código em PR / integrado / publicado / homologado real**.
4. Faça recomendações e prompts executáveis com aceite claro; o responsável revisa as decisões de produto por incremento. Não interromper implementações autorizadas depois de cada etapa trivial; chegar a PR/testes completos.
5. Evite perguntas redundantes, backups exagerados e controles que reintroduzam indisponibilidade de recursos já homologados. Preserve segurança do contrato, auditabilidade e compatibilidade.
6. Não misture este projeto com outros repositórios do usuário. Este contexto é **exclusivo do NightOwl**.

## 9. Documentos que devem permanecer coerentes

- [deploy-RC49.md](deploy-RC49.md) — decisões de produto, políticas e roadmap.
- [nightowl-visao-produto-roadmap-rc49.md](nightowl-visao-produto-roadmap-rc49.md) — objetivos, backlog amplo e patrimônio.
- [rc49-03a-central-software.md](rc49-03a-central-software.md), [rc49-03b-upload-gerenciamento-instaladores.md](rc49-03b-upload-gerenciamento-instaladores.md), [rc49-03c-a-perfis-execucao.md](rc49-03c-a-perfis-execucao.md), [rc49-03c-b1-software-operations.md](rc49-03c-b1-software-operations.md) — fases da Central de Software.
- [rc49-07-stable-desktop-rollout.md](rc49-07-stable-desktop-rollout.md) — contrato Stable e nota de 07B.
- [rc49-01a-registros-endpoint.md](rc49-01a-registros-endpoint.md), [rc49-01b-identidade-ciclo-de-vida.md](rc49-01b-identidade-ciclo-de-vida.md), [rc49-02-noc-operacional.md](rc49-02-noc-operacional.md) — incrementos anteriores e pendências reais.
- [rc49-admin-terminal-grant-management.md](rc49-admin-terminal-grant-management.md), [rc49-10-runtime-continuity.md](rc49-10-runtime-continuity.md) — terminal/admin e continuidade.
- [nightowl-api-contracts.md](nightowl-api-contracts.md) — **documento histórico de contrato mock**; não é fonte vigente para API de software v2.
