# NightOwl — Handoff Codex 2 (27/11/2026)

> Fonte central: [rc49-handoff-contexto-20261127.md](rc49-handoff-contexto-20261127.md). Consulte [deploy-RC49](deploy-RC49.md), [visão do produto](nightowl-visao-produto-roadmap-rc49.md) e os documentos [03A](rc49-03a-central-software.md), [03B](rc49-03b-upload-gerenciamento-instaladores.md), [03C-A](rc49-03c-a-perfis-execucao.md) e [03C-B1](rc49-03c-b1-software-operations.md). A referência de código é main `97a5f61518edf9d686f21dc84a23488f7dcb4770`.

## 1. Responsabilidade atual

**Codex 2 desenvolve a Central de Software — RC49-03 — e conduz o executor de software Windows.** Coordena migration/arquivos comuns com Codex 1, que cuida do Stable #83. O código NightOwl de NOC e patrimônio foi desenvolvido anteriormente e está integrado; não reconstruir suas telas sem decisão nova.

### Histórico integrado (não repetir entregas)

| Incremento | PR | Situação / migration |
| --- | --- | --- |
| 03A — inventário real, duas abas e cards/drawer; sem mock em vazio | #78 | Integrada; `agents.0058_softwarepackagerecord` |
| 03B — armazenamento privado MSI/EXE, streaming, substituição sem guardar binários antigos indefinidamente, aprovação/auditoria, download operador | #80 | Integrada; `agents.0060_rc49_software_package_uploads` |
| 03C-A — perfis, ProductCode, revisões/hash, aprovação do perfil vinculada ao artefato | #81 | Integrada; `agents.0061_rc49_software_execution_profiles` |
| 03C-B1 — API de operações tipadas, materialização `AgentJob`, download privado agente, ACK v2 | #82 | **Integrada na main `54bda321...`**, `agents.0062_rc49_software_package_operations`; CI [46012576544](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46012576544), 5/5 |
| 03C-B2 — executor MSI Windows v2 | #84 | PR aberta, sem merge, HEAD `75777e6be7033a3a79718fe7a082f695cc00fb94`; CI 46045236911 5/5 |

**Layout aprovado:** na Central existem duas abas, `Softwares coletados` e `Uploads`. Uploads: cards em **duas colunas desktop/uma mobile**, nome amigável é o link que abre drawer. MSI/EXE são cadastráveis, nome editável, substituição de arquivo no mesmo UUID, exclusão definitiva só se nunca utilizado e sem jobs/refs; trocas invalidam aprovação operacional. Sem arquivos de demonstração na lista real.

## 2. PR #82 — comportamento contratual integrado

- API operador: `POST /api/software/packages/{package_uuid}/operations/{endpoint_uuid}/`; `request_id` idempotente, `install/update/uninstall` tipados. Repetir mesma intenção devolve o mesmo job; mesma chave para outra intenção = conflito. Transação cria job, snapshot imutável e auditoria.
- `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=False` por default: job persistido permanece `queued` e não é entregue. No pull, revalidar operador/endpoint/identity UUID+machine_id, aprovação de pacote+perfil vinculada a digest/revisão, integridade/hash, capability e conflitos. O agente sem versão/capability compatível não recebe operação.
- Download privado autenticado pelo token/bearer já existente no **header**, ligado a job e endpoint correspondentes, sem public URL, tokens na query ou acesso a outros pacotes. Pacote com operação ativa não pode ser substituído/deletado.
- ACK novo de operação v2 **é rejeitado/auditado em `queued`**; só pode passar a partir de `sent`/`running`; replay já registrado idempotente. Validador importa `re`; código 3010 requer `reboot_pending=true`; sucesso depende de ProductCode/versão verificados e não somente `exit_code=0`.
- Envelope v2 é separado do `install_software` v1 (compatibilidade preservada).

## 3. PR #84 — trabalho atual e revisão de merge

- Main de código `97a5f61518edf9d686f21dc84a23488f7dcb4770`. [#83](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/83) integrada por merge normal, HEAD `d7a9b6f52d7dffee83048e5d35e777acab283656`, CI [46042230821](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46042230821) 5/5. Migration agents.0063_stable_coverage_execution_campaign depende de 0062_rc49_software_package_operations; colisão resolvida. [#84](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/84) aberta, HEAD `75777e6be7033a3a79718fe7a082f695cc00fb94`, CI [46045236911](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46045236911) 5/5 no HEAD, base incorporada `11b3ba1001486c026586571a9c393ef417820590`, sem migration. Mergeable na consulta, mas falta incorporar #83, preservar ambos os contratos em agents/views.py, repetir CI e obter autorização de merge. #86 é documental. **Sem evidência de deploy RC49, migrations operacionais, release Windows assinada RC49, ativação Stable, despacho de software ou instalação real.** Último ambiente relatado RC48/48.1; confirmar produção separadamente.
- Implementa MSI v2 install/update/uninstall: perfil imutável/autenticado por job, download streaming para staging protegido por job; tamanho, SHA-256, revisão, cadeia Authenticode e **publisher certificate pin local explícito**; invocação fixa `msiexec` sem shell nem MSI properties arbitrárias; detecção ProductCode/versão/ausência, journal idempotente e retomada após interrupção; timeout/OwnedProcess; 3010 apenas reboot pendente, sem reiniciar. A versão/capability/flag local devem ser compatíveis e opt-in.
- Tests: .NET Windows com bytes MSI sintéticos/doubles, Django software operation/ACK/profile, updater assinatura, lock/retenção e uninstaller. **Não houve instalação MSI real**, publicação Windows, mudança de flag, migração ou deploy.
- Revisão B2: corrigido ACK com agent_version (validação estrita), falhas v2 e propagação de error_code; novos ACKs queued rejeitados, v1 e replay preservados. Detecção MSI MsiGetProductInfoExW em contexto de máquina, consulta inicial de tamanho ERROR_SUCCESS; timeout HTTP de perfil/artefato subordinado ao token do job, até 1800 s. Testes sintéticos/doubles, sem instalação real. Repair Runner: timeout histórico ~20,9 s, rerun ~6,9 s, causa não comprovada. Major upgrades com mudança de ProductCode, EXE, reconciliação geral e escala pendentes. Confiança publisher por pin local opt-in; gerenciamento centralizado é proposta futura.
- Codex 2: fechar sincronização/CI da #84 e obter autorização de merge normal; depois negociar 03C-C executor EXE (argumentos estruturados por fabricante, detecção explícita antes/depois, sem flags silenciosas universais, sem executar UninstallString coletado, preservar aprovação/hash/revisão, transporte v2, verificação e compatibilidade MSI/v1). Gerenciamento centralizado de confiança proposto a encaixar; depois 03C-D escala, UI/progresso, atualização/desinstalação e reconciliação; 03E/08 FollowMe, Bitdefender futuro. Testes reais de software somente no fechamento do desenvolvimento RC49.
- #84 sem migration; 0063 já integrada pela #83.

## 4. Próximas negociações após B2

- **03C-C — EXE:** execução segura sem shell por argumentos estruturados por fabricante, detecção exata, aprovações por arquivo/perfil, validação específica; não inventar flags silenciosas universais.
- **03C-D — administração em escala:** update/uninstall além dos fluxos MSI restritos B2, reconciliação, offline, falhas, retries explicitamente recuperáveis, UI de operação/progresso e distribuição gradual; reboot atrelado à futura política RC49-04. Prova por ProductCode, versão ou detecção declarada; `UninstallString` observado no inventário **não é instrução confiável para executar**.
- **03E/08 — impressoras FollowMe:** integração com fila/driver/pacote aprovado do sistema existente, instalação/removal por jobs tipados e políticas. Requisitos específicos ainda serão negociados.
- **Análise antimalware com Bitdefender:** desejada para RC posterior; não equiparar aprovação manual, hash ou Authenticode à ausência de malware.
- A decisão do responsável é **concluir o desenvolvimento da RC49 antes dos testes operacionais reais de software**. CI sintético é ferramenta de desenvolvimento, não homologação completa.

## 5. Dependências e estado de produção

- #82 e #83 integradas; 0063 Stable depende de 0062 operações. #84 sem migration. Coordenar futuras migrations.
- Backend RC49 e migrations operacionais: **não afirmar deployment**. O histórico do chat confirma backend e agente RC48/RC48.1 em homologação; verificar versão real de produção antes de qualquer publicação.
- O próximo agente precisa ser assinado no SHA final, e capacidades somente anunciadas se executor/política local realmente suportam. `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED` permanece off por default.
- Reutilizar `AgentJob`, pull/ACK v2, `AuditEvent`, auth do agente e o worker .NET; não criar novos scheduler/job engine/transport.
- Não criar backup/VM de staging por rotina de PR; avaliar proteção proporcional somente para operação real de alto impacto. Preservar grants, flags e continuidade RC48 entre futuras RCs.
