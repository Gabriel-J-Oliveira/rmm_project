# NightOwl — Handoff Codex 2 (27/11/2026)

> Fonte central: [rc49-handoff-contexto-20261127.md](rc49-handoff-contexto-20261127.md). Consulte [deploy-RC49](deploy-RC49.md), [visão do produto](nightowl-visao-produto-roadmap-rc49.md) e os documentos [03A](rc49-03a-central-software.md), [03B](rc49-03b-upload-gerenciamento-instaladores.md), [03C-A](rc49-03c-a-perfis-execucao.md) e [03C-B1](rc49-03c-b1-software-operations.md). A referência inicial de código é `main 54bda321be6a91d5369d134d841c24130a644947`.

## 1. Responsabilidade atual

**Codex 2 desenvolve a Central de Software — RC49-03 — e conduz o executor de software Windows.** Coordena migration/arquivos comuns com Codex 1, que cuida do Stable #83. O código NightOwl de NOC e patrimônio foi desenvolvido anteriormente e está integrado; não reconstruir suas telas sem decisão nova.

### Histórico integrado (não repetir entregas)

| Incremento | PR | Situação / migration |
| --- | --- | --- |
| 03A — inventário real, duas abas e cards/drawer; sem mock em vazio | #78 | Integrada; `agents.0058_softwarepackagerecord` |
| 03B — armazenamento privado MSI/EXE, streaming, substituição sem guardar binários antigos indefinidamente, aprovação/auditoria, download operador | #80 | Integrada; `agents.0060_rc49_software_package_uploads` |
| 03C-A — perfis, ProductCode, revisões/hash, aprovação do perfil vinculada ao artefato | #81 | Integrada; `agents.0061_rc49_software_execution_profiles` |
| 03C-B1 — API de operações tipadas, materialização `AgentJob`, download privado agente, ACK v2 | #82 | **Integrada na main `54bda321...`**, `agents.0062_rc49_software_package_operations`; CI [46012576544](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46012576544), 5/5 |
| 03C-B2 — executor MSI Windows v2 | **#84** | **PR ABERTA, SEM MERGE**, HEAD `8f5fc0d25f4e3acd7b349950fd1585522555651d`; CI [46019901096](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46019901096) 5/5 |

**Layout aprovado:** na Central existem duas abas, `Softwares coletados` e `Uploads`. Uploads: cards em **duas colunas desktop/uma mobile**, nome amigável é o link que abre drawer. MSI/EXE são cadastráveis, nome editável, substituição de arquivo no mesmo UUID, exclusão definitiva só se nunca utilizado e sem jobs/refs; trocas invalidam aprovação operacional. Sem arquivos de demonstração na lista real.

## 2. PR #82 — comportamento contratual integrado

- API operador: `POST /api/software/packages/{package_uuid}/operations/{endpoint_uuid}/`; `request_id` idempotente, `install/update/uninstall` tipados. Repetir mesma intenção devolve o mesmo job; mesma chave para outra intenção = conflito. Transação cria job, snapshot imutável e auditoria.
- `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=False` por default: job persistido permanece `queued` e não é entregue. No pull, revalidar operador/endpoint/identity UUID+machine_id, aprovação de pacote+perfil vinculada a digest/revisão, integridade/hash, capability e conflitos. O agente sem versão/capability compatível não recebe operação.
- Download privado autenticado pelo token/bearer já existente no **header**, ligado a job e endpoint correspondentes, sem public URL, tokens na query ou acesso a outros pacotes. Pacote com operação ativa não pode ser substituído/deletado.
- ACK novo de operação v2 **é rejeitado/auditado em `queued`**; só pode passar a partir de `sent`/`running`; replay já registrado idempotente. Validador importa `re`; código 3010 requer `reboot_pending=true`; sucesso depende de ProductCode/versão verificados e não somente `exit_code=0`.
- Envelope v2 é separado do `install_software` v1 (compatibilidade preservada).

## 3. PR #84 — trabalho atual e revisão de merge

- Branch `codex/rc49-03c-b2-msi-executor`, HEAD observado `8f5fc0d25f4e3acd7b349950fd1585522555651d`; base `54bda321...`. PR aberta, CI 5/5 aprovado **no HEAD** (não prova merge nem instalação real).
- Implementa MSI v2 install/update/uninstall: perfil imutável/autenticado por job, download streaming para staging protegido por job; tamanho, SHA-256, revisão, cadeia Authenticode e **publisher certificate pin local explícito**; invocação fixa `msiexec` sem shell nem MSI properties arbitrárias; detecção ProductCode/versão/ausência, journal idempotente e retomada após interrupção; timeout/OwnedProcess; 3010 apenas reboot pendente, sem reiniciar. A versão/capability/flag local devem ser compatíveis e opt-in.
- Tests: .NET Windows com bytes MSI sintéticos/doubles, Django software operation/ACK/profile, updater assinatura, lock/retenção e uninstaller. **Não houve instalação MSI real**, publicação Windows, mudança de flag, migração ou deploy.
- **Próximos passos:** revisar contrato de perfil/executor, política real de assinatura (qual CA/publisher e pin aceito), privilégio/identidade do serviço Windows, armazenamento temporário e limpeza, permissões NTFS, behavior interrupção e replays; validar qualidade do CI. Sincronizar com `main` atual após qualquer merge; conferir cuidadosamente `agents/views.py`, compartilhado com #83. Não integrar automaticamente sem autorização do responsável.
- **Não é necessário criar migration na #84** segundo diff reportado; não tocar numeração 0063 usada futuramente pela #83 sem necessidade comprovada.

## 4. Próximas negociações após B2

- **03C-C — EXE:** execução segura sem shell por argumentos estruturados por fabricante, detecção exata, aprovações por arquivo/perfil, validação específica; não inventar flags silenciosas universais.
- **03C-D — administração em escala:** update/uninstall além dos fluxos MSI restritos B2, reconciliação, offline, falhas, retries explicitamente recuperáveis, UI de operação/progresso e distribuição gradual; reboot atrelado à futura política RC49-04. Prova por ProductCode, versão ou detecção declarada; `UninstallString` observado no inventário **não é instrução confiável para executar**.
- **03E/08 — impressoras FollowMe:** integração com fila/driver/pacote aprovado do sistema existente, instalação/removal por jobs tipados e políticas. Requisitos específicos ainda serão negociados.
- **Análise antimalware com Bitdefender:** desejada para RC posterior; não equiparar aprovação manual, hash ou Authenticode à ausência de malware.
- A decisão do responsável é **concluir o desenvolvimento da RC49 antes dos testes operacionais reais de software**. CI sintético é ferramenta de desenvolvimento, não homologação completa.

## 5. Dependências e estado de produção

- A migration `agents.0062_rc49_software_package_operations` já entrou na `main` pelo #82. Codex 1 ainda tem `0062_stable_coverage_execution_campaign` na PR #83, que precisa ser renumerada para **0063** e depender da 0062 integrada **se ainda não aplicada em DB persistente**. Caso contrário, reconciliar sem reescrever migração aplicada.
- Backend RC49 e migrations operacionais: **não afirmar deployment**. O histórico do chat confirma backend e agente RC48/RC48.1 em homologação; verificar versão real de produção antes de qualquer publicação.
- O próximo agente precisa ser assinado no SHA final, e capacidades somente anunciadas se executor/política local realmente suportam. `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED` permanece off por default.
- Reutilizar `AgentJob`, pull/ACK v2, `AuditEvent`, auth do agente e o worker .NET; não criar novos scheduler/job engine/transport.
- Não criar backup/VM de staging por rotina de PR; avaliar proteção proporcional somente para operação real de alto impacto. Preservar grants, flags e continuidade RC48 entre futuras RCs.
