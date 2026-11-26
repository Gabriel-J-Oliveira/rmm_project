# NightOwl — Visão de produto, inventário de capacidades e roadmap de evolução

> Documento vivo de visão de produto. Registrado em **24/11/2026** a pedido do responsável pelo NightOwl.
> **Status:** direção geral aceita, com ressalvas e detalhes que serão discutidos em cada frente. Não converter propostas em decisões fechadas sem validação.
> **Base técnica observada:** `main` no commit `723c9c7bd7088b0ef04290403f701dc16056787f`. Revisar a matriz à medida que RC48 e RC49 evoluírem.
> **Documento complementar:** [deploy-RC49.md](deploy-RC49.md), que contém as decisões anteriores sobre agente Windows, Stable, políticas, Windows Update, AD e reinicialização.

> **Regra obrigatória de continuidade operacional:** releases, upgrades/downgrades e reinicializações devem preservar funcionalidades previamente homologadas e autorizadas. Consultar o gate e as exceções de segurança em [deploy-RC49.md](deploy-RC49.md); continuidade não renova grants nem habilita modos administrativos automaticamente.
> **Regra de trabalho:** Codex 1 conclui ajustes da RC48; Codex 2 trabalha a RC49 em branch própria, sem deploy, merge ou alterações nos artefatos da RC48.

## 1. Missão do produto

Transformar o NightOwl na **central operacional e histórica do parque de TI**: **monitorar, proteger, inventariar, administrar, executar, automatizar e rastrear** o ciclo de vida de equipamentos e instalações Windows.

O sistema deverá permitir:

1. Consultar hardware, software, sistema operacional, rede, usuários observados, capacidade e desempenho dos endpoints.
2. Enxergar em uma tela de NOC máquinas offline, com consumo alto, hardware insuficiente, problemas de disco/rede e incidentes de segurança, com dados reais e ações diretas.
3. Executar terminal remoto, diagnósticos, jobs, scripts pré-aprovados, serviços e políticas com resultado confirmado.
4. Instalar, atualizar e remover softwares; receber arquivos no NightOwl para gerar pacotes versionados e implantar em um clique em máquinas/grupos.
5. Instalar, consultar e remover impressoras/filas por endpoint, usando drivers/pacotes aprovados.
6. Gerenciar atualizações do Windows e limpeza/otimização por políticas, agenda e métricas.
7. Manter histórico **permanente do equipamento físico**, incluindo quem utilizou, mudanças de usuário e motivo, formatações, upgrades, observações, problemas, manutenção e reparos.
8. Fazer atualização automática de agente em **100% dos desktops Windows elegíveis** no canal Stable, excluindo servidores e VMs de laboratório.
9. Combinar proteção e conformidade com soluções especializadas: **Bitdefender** para bloqueio/proteção de aplicações, **AD** para identidade/grupos e **NightOwl** como ponto central de contexto, operação, histórico e evidência.

Perguntas que o produto deve responder: **O que temos? Como está? O que aconteceu? O que precisamos fazer? O que pode ser automatizado?**

## 2. Princípios de arquitetura e desenvolvimento

- **Equipamento físico (ativo) != identidade do endpoint != instalação do Windows/agente != usuário atual.** Devem se relacionar, sem substituir um ao outro.
- **Uma fonte de verdade por assunto:** cadastro patrimonial para posse e histórico; inventário/telemetria para observações técnicas; AD para identidades/grupos; Bitdefender para proteção especializada.
- **Reutilizar mecanismos existentes:** `AgentMachine`, `InventorySnapshot`, `EndpointPerformanceSample`, `AuditEvent`, `EndpointAlert`, `AgentJob`, `AutomationDefinition`, `Ticket`, `OperationalTask`, autenticação e permissões.
- **Fluxos completos antes de marcar pronto:** API/backend, agente se necessário, interface, permissão, execução e confirmação real, não apenas CI verde ou mock.
- **Não voltar a desabilitar funcionalidade aprovada em cada RC** sem razão técnica. Smoke curto das regressões; testes focados no incremento.
- **Não confiar em hostname** como identidade única: há máquinas físicas e VMs com nomes repetidos. Serial, tag patrimonial, UUID, SID AD e machine ID também exigem tratamento de ausência/duplicidade.
- **Resultados confirmados:** job criado/entregue não significa executado; política definida não significa aplicada; heartbeat/usuário logado não significa responsável patrimonial.
- **Sem pipelines duplicados:** estender protocolo v2/jobs existentes e seus controles de auditoria em vez de criar transports, schedulers e filas paralelas.
- **Execução objetiva na homologação:** não criar staging/checkpoint/backup adicional para pequenas alterações; reservar proteção especial para operações com grande impacto no banco ou na frota.
- **Separar escopo aprovado de ideação:** aqui há backlog amplo, não autorização para executar todas as funcionalidades.

## 3. Inventário funcional do código atual

Classificações:
- **Base funcional:** há coleta, modelo, API ou fluxo real implementado; não implica que todos os cenários estejam homologados.
- **Parcial:** há implementação aproveitável, mas faltam UI, integração, autorização, execução real ou abrangência do requisito.
- **Ausente:** não há fluxo de produto correspondente identificado na base inspecionada.

| Área | Situação | Evidência e lacuna principal |
| --- | --- | --- |
| Cadastro de endpoints Windows e identidade do agente | Base funcional | `agents/models.py:AgentMachine`, enrollment, heartbeat, lifecycle; não é cadastro patrimonial permanente |
| Inventário técnico de hardware | Base funcional | `InventorySnapshot`, collectors, telas hardware/capacidade; enriquecer componentes, mudanças e ciclo de vida |
| Inventário de software instalado | Base funcional | `InventorySnapshot.installed_software`, `dashboard/views.py:software_inventory`; catálogo de operações ainda incompleto |
| Telemetria CPU, memória, disco e rede | Base funcional | `EndpointPerformanceSample`, RC46-48 continuous; dados reais confirmados no piloto |
| Alertas e detecção de mudanças | Base funcional | `agents/alerting.py`, `change_detection.py`; expandir regras, priorização e análises |
| Análise de capacidade | Parcial | `dashboard/capacity_views.py`; utiliza pressões/percentis, mas não fecha recomendações patrimoniais e todos os sinais de rede |
| Tela NOC | Parcial | `noc_view` usa endpoints/alertas reais com dados disponíveis; `static/js/noc.js` recarrega página a cada 30s e ainda possui ações mock |
| Diagnósticos e jobs | Base funcional | `AgentJob`, pull/start/result/ACK, ping, inventário, discos, software, logs e outros |
| Terminal ConPTY | Parcial | implementação e grants existentes, mas liberações/sessões administrativas ainda pendentes |
| Consulta/controle de serviços | Parcial | consulta real, UI e ações restritas; RC48 tem trabalho de otimização em andamento pelo Codex 1 |
| Políticas de execução | Parcial | `AutomationDefinition/Occurrence`, `job_automations.html`; ainda centradas em diagnósticos |
| Políticas de software/detecção | Parcial | `SoftwarePolicy`, violações e escopo; bloqueio efetivo de apps deve preferir Bitdefender |
| Rollout do agente | Parcial | releases assinadas, canais e campanhas; Stable 100% ainda não é fluxo operacional completo |
| Scripts pré-aprovados | Parcial | `agents/script_library.py`: catálogo estático e script de diagnóstico; faltam upload, versões, aprovação e operação em escala |
| Instalação de software | Parcial | `SoftwareInstallation.cs`: MSI pré-aprovado e assinado, limite de 100 MB, catálogo local; sem repositório/upload completos |
| Central de software | Parcial / protótipo | `static/js/software_center.js` ainda usa `MockNightowlApi`; não representar ações mock como deploy real |
| Atualização e remoção de software | Ausente como fluxo completo | detecção de alterações existe; não há operação generalizada e validada |
| Instalação e remoção de impressoras | Ausente | não identificado contrato tipado de impressão |
| Windows Update automatizado | Ausente para instalação | `windows_update_scan` só consulta; faltam download/instalação/reboot e reconciliador |
| Histórico patrimonial e de usuários responsáveis | Ausente | `last_logged_user` e evento de troca do usuário logado são **observações**, não registros de cessão |
| Registro de formatação, upgrades e manutenção física | Parcial como contexto | `Ticket` e `OperationalTask` servem de base, mas não há ciclo patrimonial próprio |
| Integração com AD por grupo de computadores | Parcial | descoberta de computador LDAP/SID/OU; falta vínculo robusto de membro/grupo e alvos dinâmicos |
| Segurança integrada | Parcial | detecção Defender/antivírus e políticas; falta integração de postura real com Bitdefender |
| Linha do tempo unificada do ativo | Ausente | eventos técnicos existem por endpoint, mas não atravessam reformatos/trocas de agent ID |

**Importante:** "ausente" qualifica o fluxo completo de produto solicitado, não necessariamente a inexistência de qualquer função utilitária isolada.

## 4. Arquitetura de domínio desejada

```text
Asset / Equipamento físico (identidade patrimonial estável)
  |-- AssetAssignment (responsável, datas, setor, motivo da transferência)
  |-- AssetLifecycleEvent (formatação, upgrade, nota, transferência, retirada, defeito)
  |-- AssetMaintenance (envio, prestador, protocolo, diagnóstico, peças, retorno)
  |-- AssetAgentIdentity (ligações históricas AgentMachine <-> ativo)
  |       |-- AgentMachine (instalação/identidade operacional)
  |              |-- InventorySnapshot / Telemetry / Alerts / AgentJob
  |-- Ticket / OperationalTask (vínculos opcionais com ocorrência e ativo)
  |-- Visão unificada: NOC, capacidade, software, execução, histórico
```

### Regras fundamentais

1. O **Asset** permanece mesmo após formatação, troca de hostname ou novo `machine_id`.
2. Não apagar histórico de atribuição quando equipamento mudar de usuário.
3. Último usuário logado **não** define automaticamente quem é o responsável pelo patrimônio.
4. Mudança de serial, modelo, placa-mãe ou UUID não implica automaticamente novo ativo: registrar evento e revisar identidade.
5. **Associação automática só quando inequívoca**; serial genérico/ausente/duplicado, hostname homônimo e conflito vão para reconciliação manual.
6. Apenas uma atribuição de responsável atual por ativo, mantendo todas as anteriores fechadas.
7. Vínculo de endpoint a ativo deve guardar início/fim, fonte, evidência, autor e motivo; mudanças precisam preservar o histórico.
8. Eventos devem distinguir origem `manual`, `agent`, `ad`, `ticket` e `system`; não inventar motivo de formatação ou manutenção a partir de telemetria.
9. Acesso técnico e edição patrimonial separados por permissões, com histórico de quem alterou.
10. Reutilizar `Ticket`, `OperationalTask` e `AuditEvent` por vínculo, evitando cópias independentes de chamados e jobs.

## 5. RC49 — Frentes de entrega a manter no radar

| Frente | Objetivo | Situação da decisão |
| --- | --- | --- |
| **RC49-01 Ativos e histórico** | Cadastro patrimonial, responsável, transferências, formatações, upgrades, reparos, manutenção e vínculo com agentes | **Primeira frente escolhida para iniciar com Codex 2** |
| **RC49-02 NOC operacional** | Fila de atenção com criticidade, pressão sustentada CPU/RAM, disco, rede, ações verdadeiras e atualização incremental | **Desenvolvimento concluído e integrado**; homologação visual/operacional pendente |
| **RC49-03 Central de software real** | Upload de pacote, aprovação, versão/hash, distribuição, install/update/uninstall e conferência | Direção proposta |
| **RC49-04 Políticas inteligentes** | Windows Update, limpeza, métricas/condições, grupos AD/OU, agendas e reboot configurável por política | Conceito aprovado; opções finas abertas |
| **RC49-05 Biblioteca de scripts** | Cadastro/upload versionado, aprovação, autorização, execução pontual/em grupo, logs e histórico | Direção proposta |
| **RC49-06 Serviços/terminal** | Contrato .NET completo de serviços antes da UI; finalizar UI na própria RC49; terminal assistido/admin | Decisão de serviços já confirmada; detalhes de terminal em discussão |
| **RC49-07 Stable integral** | 100% dos desktops Windows elegíveis atualizados automaticamente, inclusive offline quando voltarem | **Decisão confirmada**; não atingir servidores/VMs de teste |
| **RC49-08 Impressoras** | Catálogo de filas/drivers e instalar/remover/consultar por job tipado | Direção proposta |
| **RC49-09 Postura de segurança** | Consolidar Bitdefender, alertas e conformidade, sem duplicar bloqueio de aplicações | Direção proposta |
| **RC49-10 Operação e confiabilidade** | Configuração persistente, scheduler de análises, correções reais de jobs/terminal e recuperação após reboot | Direção proposta, transversal |

**Planejamento amplo não significa que todas as dez frentes caibam numa única publicação final.** Dividir por incrementos verificáveis, mantendo as restantes visíveis, sem trocar a visão do produto.

### Diretrizes preservadas de discussões anteriores

- Canal `development`: VMs/laboratório e homologação individual.
- Canal `stable`: ao publicar versão assinada, atualizar automaticamente **100% dos desktops Windows gerenciados elegíveis**; a execução pode usar lotes técnicos, mas não deve limitar a cobertura a uma porcentagem.
- Políticas de execução têm **ação + alvos + agenda + condições + tratamento de offline/falha + resultado + reboot**.
- Campo da UI: **"Se necessário reiniciar, como será feito?"**; escolha feita por política. Modos finais e limites ainda serão definidos.
- Alvos: todos, domínio, OU, endpoints individuais e, quando implementado, **grupos de segurança de contas de computadores AD**.
- O gerenciador de serviços deve ter o **contrato do agente fechado antes da UI**, ainda dentro da RC49.
- Bitdefender permanece preferencial para regras de bloqueio/permissão de apps.

## 6. Primeira entrega: RC49-01A — Base patrimonial e histórico operacional mínimo

**Responsável de execução:** Codex 2, em branch isolada da `main`. **Codex 1** permanece com RC48.

**Objetivo:** primeira fatia **utilizável**, não um esquema de tabelas sem uso. Um técnico deve conseguir registrar um equipamento, atribuir/transferir o responsável, anotar evento de formatação/problema, registrar ida/volta de manutenção e consultar o histórico em ordem cronológica.

### Escopo aprovado para iniciar agora

**Modelos e migrações aditivas** (nomes sugeridos, ajustáveis por convenção Django):

- `Asset`: UUID, tipo, identificação patrimonial opcional, fabricante/modelo/serial observados e/ou editados, localização/setor, estado de ciclo de vida, descrição/notas, carimbos e autor.
- `AssetAssignment`: ativo, responsável identificado por conta AD quando houver e/ou identificação textual, setor/localidade, atribuído em, devolvido em (nullable), razão, operador. Garantir uma responsabilidade atual sem apagar passado.
- `AssetLifecycleEvent`: ativo, tipo de evento (`formatted`, `upgraded`, `issue_noted`, `transfer`, `observation`, `retired`, etc.), data do fato, descrição, motivo, fonte e autor; referência opcional a Ticket/OperationalTask.
- `AssetMaintenance`: ativo, problema/motivo, prestador/local, protocolo, envio, previsão, retorno, status, serviço/peças e observações; poderá referenciar Ticket/OperationalTask.
- `AssetAgentIdentity`: vínculo explícito entre `Asset` e `AgentMachine`, com datas de início/fim, fonte, operador e evidência; **não** reescrever `AgentMachine.id` ou `machine_id`.

### Funcionalidades do primeiro incremento

1. Tela básica de **Ativos**: listagem, busca por patrimônio/serial/modelo/hostname atual (se vinculado), filtros simples, criar/editar.
2. **Detalhe do ativo**: ficha, responsável atual, agente vinculado se existir, eventos/atribuições e manutenção; ordenação cronológica, com origem de cada informação.
3. Formulário de **atribuição e transferência**: encerra a atribuição atual e cria outra de maneira atômica, com motivo/data; histórico continua legível.
4. Registrar **formatação**, observação, defeito ou upgrade manualmente, com ator e data; não presumir que a formatação foi detectada automaticamente.
5. Abrir/fechar **manutenção**: prestador, protocolo, saída, retorno, causa e resultado. Uma única manutenção ativa por ativo como regra de negócio proposta.
6. **Vinculação manual por UUID do endpoint**, com prévia de hostname, serial, domínio, machine ID e conflitos; impedir vínculo ativo duplicado por endpoint. Não forçar vínculo apenas por serial, FQDN ou hostname.
7. **Linha do tempo** com os registros patrimoniais e, quando houver vínculo, atalhos para dados existentes do endpoint e chamados/tarefas. Não copiar histórico técnico bruto ou criar tabelas duplicadas de telemetria.
8. Permissões adequadas para leitura/edição e auditoria das mutações; a tela deve indicar quando faltam dados, sem fabricar valores.
9. Testes Django para criação, permissões, transferências, timeline, conflitos de vínculo, formatação, abertura/retorno de manutenção e não alteração do endpoint existente.

### Restrições deste incremento

- **Não** importar automaticamente toda a frota nem criar registros patrimoniais baseados somente em hostname ou serial.
- **Não** apagar, recriar ou mesclar `AgentMachine`, `InventorySnapshot`, `AgentJob`, `Ticket` ou `AuditEvent`.
- **Não** mudar contrato do agente Windows, updater, telemetria, NOC, políticas, software ou terminal.
- **Não** executar migrações em produção, nem deploy. Codex 2 entrega branch/PR, migração gerada/testada em ambiente local/testes e instruções curtas de aplicação posterior.
- Não alterar dados reais da frota; testes usam fixtures.
- Não exigir uma nova instalação do agente para usar a ficha patrimonial.
- Evitar coleta e armazenamento de dados pessoais excessivos: apenas identificação de responsável necessária à gestão; respeitar permissões do NightOwl.

## Conclusão do desenvolvimento da RC49-01B — 25/11/2026

RC49-01B foi integrada à main pelas PRs #64 e #65. A implementação contempla identidade patrimonial por UUID SMBIOS com serial complementar, associação automática de hardware elegível, revisão de inconsistências e remoção do agente condicionada a receipt verificável, com auditoria durável e preservação patrimonial. A cadeia de migrations é 0052 → 0053 → 0054.

SQLite, PostgreSQL, concorrência PostgreSQL, contrato de deployment Windows, E2Es Windows/.NET e gates visuais passaram. O teste real de limpeza e reinstalação ainda requer homologação em VM descartável com pacote assinado compatível. A falha histórica da sonda Chrome/CDP permanece sem causa comprovada.

### Critérios de aceite RC49-01A

- Um equipamento existe independentemente de haver agente.
- Reinstalar Windows/criar nova identidade do agente **não apaga histórico** do ativo.
- Responsável atual e anteriores, com datas e motivos, são consultáveis.
- Formatação, problema, manutenção e retorno têm trilha cronológica.
- Ligação com `AgentMachine` é reversível/histórica e não usa hostname como chave.
- Tela e fluxos realmente funcionam com banco de teste e testes automatizados.
- Alterações não interferem na RC48 em produção.
- Codex 2 publica PR com descrição dos modelos, rotas, telas, testes, migrações e limitações.

### Recorte para fases seguintes de RC49-01

- **01B:** associação patrimonial assistida pelo inventário real, detecção de conflitos, reconciliação e possíveis dados AD; nunca auto-associar com evidência fraca.
- **01C:** timeline enriquecida unindo telemetria/alertas, mudanças automáticas, tickets e tarefas por período de vinculação.
- **01D:** relatórios de uso, rotação de usuários, manutenção, garantia, patrimônio sem agente e ativos em estoque/baixa; refinamento da UI junto ao responsável.

## 7. Ordem de trabalho em paralelo

```text
Codex 1 --> RC48: otimização consulta de serviços, validação e hotfix em produção
Codex 2 --> RC49-01A: nova branch -> modelos aditivos -> UI real -> testes -> PR
                                   sem deploy / sem merge até revisão
```

Ao integrar RC49, rebasear a branch sobre a `main` atualizada, resolver eventuais conflitos e aplicar **somente migrações aditivas e revisadas**. Testes da RC48 ficam independentes.

## 8. Questões que permanecerão abertas

- Quais categorias e campos patrimoniais definitivos (patrimônio, garantia, nota fiscal, centro de custo etc.)?
- AD é obrigatório ou identificação textual é suficiente para responsáveis sem conta?
- Observações poderão ter anexos/fotos e termos de entrega na RC49-01B/C?
- Será necessário gerar termos de transferência/retorno e etiquetas de patrimônio?
- Qual política de associação automática de agentes após formatação/reinstalação?
- Qual nível de detalhe para peças/custos/garantia e manutenção externa?
- Como priorizar o NOC e a central de software após RC49-01A, considerando o andamento da RC48?
- Quais opções finais de reboot, Windows Update e limpeza serão liberadas em políticas?
- Qual catálogo, limites e homologação do upload de software; como tratar EXE, MSI, MSIX, ZIP e drivers?
- Impressoras de rede, USB, filas compartilhadas e GPOs: quais primeiras classes alvo?
- Integração Bitdefender: quais APIs, licenças e dados de postura efetivamente disponíveis?

**Regra de manutenção do documento:** toda decisão de escopo aprovada vira registro com data e justificativa; toda funcionalidade concluída precisa de evidência de teste real e link de commit/PR. Não apagar pendências apenas por avanço de versão.

## Atualização aprovada em 25/11/2026 — RC49-01B

A próxima fase passa a ser **identidade patrimonial automática e ciclo de vida dos endpoints**. A decisão vigente está em [RC49-01B — Identidade e ciclo de vida](rc49-01b-identidade-ciclo-de-vida.md) e prevalece sobre as restrições históricas de cadastro exclusivamente manual e remoção fora do escopo da 01A.

- UUID SMBIOS como evidência principal, serial complementar e UUID patrimonial interno imutável; cadastro/vínculo automático de hardware físico confiável, conflitos tratados por exceção, botão Validar vínculo com coleta real, sem edição livre de identificadores.
- Unificar uninstall e purge em Remover agente, com confirmação administrativa, limpeza confirmada, exclusão definitiva dos dados operacionais e preservação dos registros patrimoniais. Offline/falha permanece pendente até confirmação. Auditoria independente do endpoint registra administrador, horários, identidades, resultado e último usuário observado do computador removido, com origem/data quando disponíveis; sem tela nova.
- Registros dentro de Endpoints; NOC continua no roadmap; revisão de Alertas, Atividades, Tarefas e Eventos fica para depois. Códigos oficiais por empresa, AD e integração de chamados postergados.
- Codex 2 implementa em branch/PR própria, sem deploy, coordenando mudanças de agente/instalador com Codex 1. Critérios e salvaguardas detalhados no documento 01B.

## Atualização aprovada em 25/11/2026 — RC49-02 NOC operacional

Escopo funcional aprovado pelo responsável em [RC49-02 — NOC operacional e políticas de alertas](rc49-02-noc-operacional.md). Codex 2 inicia 02A (Políticas de Alertas parametrizáveis), seguindo com 02B (motor e atualização assíncrona), 02C (últimos 15 alertas, cards e ações reais), 02D (modo TV) e 02E (central global com últimas 10 notificações e sons). Preservar visual existente, eliminar reload e hover do container, utilizar sinais/contratos existentes e respeitar continuidade operacional. Defaults editáveis, sem novas constantes de negócio. Desenvolvimento por PRs, sem deploy/publicação intermediária; RC49 permanece em desenvolvimento. A PR #67 registra o encerramento de desenvolvimento da 01B e deve ser conciliada sem substituir esta decisão aprovada por sua proposta anterior.

## Conclusão do desenvolvimento RC49-02 — 26/11/2026

Os cinco incrementos estão integrados à `main` em `db01bda5493d16631375126590a88459b66cecc9`, por merge commits normais e na sequência #73 → #74 → #76, após a integração anterior da #69 (02A) e #70 (02B):

- 02A — Políticas de Alertas: PR #69, merge `e7f432ea0c8711e43be93fc868f60b453459c942`.
- 02B — Motor de alertas e atualização assíncrona: PR #70, merge `bf7f7317bda151e4ead775c9248c44437760178b`.
- 02C — Últimos 15 alertas, cards e ações reais: PR #73, merge `f945f8da7dab41fe27cbbd09ba1172b490729b60`.
- 02D — Modo TV: PR #74, merge `5a08094d20241bbfdeb4761598f57f7bf7f05eb7`.
- 02E — Central de Notificações: PR #76, merge `db01bda5493d16631375126590a88459b66cecc9`.

Os cinco jobs obrigatórios passaram nos HEADs integrados das PRs #73, #74 e #76. Capturas visuais desktop/mobile foram publicadas nos artefatos dos respectivos runs e vinculadas às PRs. A 02E acrescenta a migration `dashboard.0006_noc_notification_preferences`, dependente de `agents.0056_rc49_seed_alert_policies` e `dashboard.0005_remoteinstallbatchitem_target_ad_sid`. Nenhuma migration operacional foi aplicada como parte da integração.

Permanecem pendentes homologação visual e operacional do conjunto consolidado em ambiente autorizado e aplicação das migrations apenas em deploy aprovado. O E2E Windows de terminal teve ocorrência intermitente anterior (`fresh_session`/`browser_condition_timeout`, com `database is locked`) cuja causa não foi comprovada; execuções posteriores passaram, sem demonstrar que a intermitência foi eliminada. A RC49 continua em desenvolvimento até o fechamento do roadmap; integração de código não autoriza deploy ou publicação de agente.

Próxima frente proposta para deliberação: RC49-03 — Central de Software real. A auditoria inicial encontrou inventário real misturado com fallback de dados simulados e uma UI baseada em `MockNightowlApi`; a distribuição exige contratos persistidos, confiança de pacotes, identidade/escopo, execução com confirmação e reconciliação de resultados. Nenhum escopo de implementação da RC49-03 está autorizado por este registro.
