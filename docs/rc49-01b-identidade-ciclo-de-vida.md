# RC49-01B — Identidade patrimonial automática e ciclo de vida dos endpoints

Decisão aprovada pelo responsável em 25/11/2026. Complementa RC49-01A e substitui, para esta fase, suas regras de cadastro exclusivamente manual e remoção fora do escopo. Implementação autorizada em branch própria pelo Codex 2; merge e deploy dependem de revisão. Codex 1 mantém terminal e serviços; coordenar qualquer alteração compartilhada do agente e instalador.

## Conclusão do desenvolvimento — 25/11/2026

As entregas de identidade patrimonial automática e remoção verificada do agente foram integradas à `main`:

- PR #64 — identidade patrimonial SMBIOS, integrada pelo merge commit `d70870990f26f02054b6c4d5d56eec1de1911223`.
- PR #65 — remoção verificada e auditoria durável, integrada pelo merge commit `3264af7e5401489d134b9006a10fcdf90b07e0f9`.

A cadeia de migrations da RC49-01B é `0052_rc49_hardware_identity` → `0053_rc49_agent_removal_audit` → `0054_rc49_removal_receipt_digest`. Os testes SQLite e PostgreSQL, concorrência PostgreSQL, contrato Windows, E2Es Windows/.NET e gates visuais passaram nos HEADs integrados. A segunda tentativa Windows da #65 passou no mesmo SHA; a falha anterior da sonda Chrome/CDP permanece uma intermitência sem causa comprovada.

O desenvolvimento está concluído. Continuam pendentes para homologação operacional: coleta SMBIOS em hardware físico representativo, troca de placa-mãe, remoção completa em VM descartável, estados offline/falha parcial e retomada, preservação de evidências e reinstalação com nova identidade operacional. A conclusão do CI não substitui esses ensaios. Não executar o processamento inicial da frota nem habilitar ações operacionais sem o procedimento de implantação aprovado.

## Identidade patrimonial

- A aba Registros permanece dentro de Endpoints. Não criar menu Ativos nem novas telas de alertas, tarefas, atividades ou eventos. Revisão dessas telas fica para depois; NOC permanece no roadmap.
- UUID patrimonial interno permanece imutável. UUID SMBIOS (Win32_ComputerSystemProduct.UUID) é a principal evidência física; serial BIOS/sistema, fabricante e modelo complementam. Machine ID e UUID do endpoint identificam a instalação; hostname/IP/usuário não identificam hardware.
- UUID SMBIOS não é garantia absoluta: normalizar, rejeitar ausente, malformado, zero, sentinelas/genéricos e detectar duplicidade; não associar VMs clonadas ou evidência ambígua. Cadastro automático inicial limitado a equipamentos físicos identificáveis; VMs excluídas inicialmente. Não inferir classificação só pelo hostname.
- Criar patrimônio provisório e vínculo automaticamente para identidade válida, única e consistente, inclusive frota existente. Reconhecer patrimônio após reinstalação/formatação sem confirmação individual. UUID coincidente com serial ausente pode vincular se demais evidências forem consistentes; divergência de serial, UUID alterado com serial coincidente, múltiplos candidatos ou instalações simultâneas incompatíveis ficam em revisão. Ausência de UUID válido gera alerta, não fallback automático por hostname/serial.
- Reconciliar dados legados sem duplicar patrimônios RC49-01A; manter códigos PROV e histórico. Processamento idempotente, transacional, constraints e testes de concorrência. Fazer prévia somente leitura da população existente antes de habilitar cadastro em massa; revisão de impacto e proteção normal do banco, com checkpoint adicional se a operação de dados tiver grande impacto. Nenhuma execução sobre frota real nesta PR.
- Botão Validar vínculo: solicitar inventário atualizado via mecanismo existente, aguardar resultado real e reavaliar; indicar pendência/offline/erro. Proteger identificadores contra edição manual. Preservar snapshots de evidência e histórico; troca de placa-mãe ou divergência não altera identidade patrimonial silenciosamente. Reconciliação excepcional auditada mediante evidência suficiente, sem edição livre de UUID/serial; caso inconclusivo permanece em revisão.
- Alertar ausência/divergência na aba Registros, reutilizando mecanismos existentes sem criar nova tela. Não confundir último usuário observado com responsável patrimonial.

## Remover agente — operação única

- Substituir as ações separadas uninstall/purge por uma ação Remover agente no frontend. Reutilizar modal de confirmação administrativa já usado na instalação, com usuário/senha e validação efetiva, sem persistir credenciais. Manter RBAC e identidade do alvo.
- Orquestrar mecanismos existentes de desinstalação e purge em um fluxo idempotente, restrito aos artefatos pertencentes ao NightOwl. Limpar serviços, tarefas, processos, arquivos, configuração e credenciais locais do agente; desinstalador independente pode permanecer.
- Só excluir definitivamente o endpoint operacional após confirmação verificável de desinstalação e limpeza, inclusive quando o agente principal já tiver parado. Reutilizar receipt/ACK ou mecanismo suportado; não tratar ausência de heartbeat como sucesso. Se offline, aguardar retorno; falha ou resultado incerto permanece pendente/retry visível. Não apagar canal de entrega antes do término.
- Após sucesso confirmado, revogar credenciais e remover AgentMachine e dados operacionais dependentes (inventário, telemetria, etc.) conforme mapa de FKs/retention; não apagar registros de outros endpoints nem histórico patrimonial. Encerrar vínculo preservando UUID patrimonial, registros e snapshots. Não deixar endpoint removido na listagem ou registro operacional duplicado. Não fabricar receipts. Prevenir corrida com novo enrollment e impedir que mensagem tardia recrie endpoint excluído; reinstalação legítima deve poder criar nova identidade operacional e recuperar patrimônio.
- Preservar log durável independente da FK do endpoint, sem senha/tokens: administrador que solicitou, data/hora da solicitação e conclusão, hostname, UUID do endpoint, machine ID, patrimônio quando existente, usuário do computador removido (último usuário observado, com origem/data se disponível; ausência explícita), resultado da desinstalação e limpeza e identificadores de correlação. Não criar tela de logs nesta fase.
- Testar exclusão e reinstalação, offline, falhas parciais, retries, concorrência, autorização, confirmação, auditoria após exclusão, preservação patrimonial e ausência de resíduos em VM piloto. Não executar remoções reais sem autorização específica; não alterar terminal ou controle de serviços.

## Entrega

Separar em incrementos/PRs se necessário: identidade automática e remoção unificada, ambos pertencentes à 01B. Verificar coleta SMBIOS e contrato do desinstalador existentes antes de modificar agente; coordenar com Codex 1 e evitar release duplicada. Migrações aditivas quando necessárias, testes SQLite/PostgreSQL e Windows pertinentes, metodologia API-first, homologação visual humana. Entregar código, PRs, plano de aplicação e impactos, sem deploy. Nomenclatura das empresas, AD, chamados, manutenção especializada e relatórios continuam postergados; NOC é próxima frente proposta após 01B.
