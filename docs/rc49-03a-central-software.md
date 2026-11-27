# RC49-03A — Central de Software: interface e dados reais

## Escopo implementado

Esta etapa substitui a Central de Software demonstrativa por uma página com duas áreas: **Softwares coletados** e **Uploads**. A página usa exclusivamente snapshots reais de inventário e metadados persistidos. Não cria dados de demonstração quando o inventário está vazio.

### Softwares coletados

- Agrega `InventorySnapshot.installed_software` por nome e fabricante, consolidando versões, total de endpoints e lista dos computadores relacionados.
- Pesquisa por nome, fabricante, versão e hostname; o filtro de atualidade diferencia inventários atuais e antigos.
- A atualidade é calculada pelo horário de coleta, com limite configurável em `NIGHTOWL_SOFTWARE_INVENTORY_STALE_HOURS` (default de 24 horas, alinhado ao limite existente de inventário obsoleto). O horário de recebimento também é apresentado para distinguir coleta de transporte.
- Endpoints sem snapshot aparecem na métrica de inventário ausente; uma lista vazia permanece vazia e informa esse estado.
- Os detalhes são somente leitura e ligam cada endpoint pela identidade UUID.

### Uploads e persistência

- A migration `agents.0058_softwarepackagerecord` (dependente de `0057_admin_terminal_access_grant`) cria `SoftwarePackageRecord`, identificado por UUID, com nome amigável, nome original, MSI/EXE, versão, tamanho, SHA-256, estado/signatário da assinatura, estado de aprovação/revogação, autores e horários.
- O modelo guarda apenas os metadados atuais. Não contém campo de arquivo, armazenamento binário, versões obrigatórias do pacote nem endpoint de escrita.
- A lista e o drawer são somente leitura. O botão **Adicionar arquivo de instalação** permanece desabilitado; não há upload ou confirmação simulada.
- As evidências históricas de operações futuras devem usar `AuditEvent` com UUID do pacote e snapshot dos metadados relevantes, sem manter indefinidamente versões binárias substituídas. Esse fluxo pertence a incremento posterior.

### Segurança e compatibilidade

- A página e os endpoints JSON exigem usuário técnico ativo; as regras de autenticação existentes continuam aplicadas pelo middleware global.
- Consultas GET são `never_cache`; nenhum endpoint desta etapa grava dados, cria jobs ou aciona agente.
- A Central não usa `MockNightowlApi`. Políticas de Software permanecem em `/software-policies/` e seus contratos não foram modificados.
- Não houve mudança no agente Windows, protocolo de jobs, updater, NOC ou terminal. A migration é aditiva e não habilita flags.

## Validação

- Testes Django cobrem inventário real/agregado, versões e fabricante, atualidade, ausência de inventário sem fallback, busca, autorização e metadados do pacote.
- O gate visual Windows usa banco Django isolado, dados sintéticos identificados como fixtures e Chrome for Testing já fixado pelo workflow. Verifica duas abas, drawer por link amigável, cartões em duas colunas no desktop e uma coluna no mobile, e grava capturas desktop/mobile para artefato de CI.
- A fixture de navegador não é evidência de homologação operacional nem consulta endpoints reais.

## Fora do escopo / dependências da 03B

- Upload, armazenamento, substituição e exclusão de arquivos.
- Validação real de assinatura e origem do signatário durante cadastro.
- Aprovação/revogação interativas e auditoria de mutações.
- Instalação, atualização, remoção, dispatch de jobs, seleção de alvos ou reconciliação pós-instalação.
- Definir storage privado, limites de arquivo, autenticação do agente para download, política de malware, extensões permitidas e RBAC de aprovação/distribuição.

Nenhuma migration operacional, deploy ou operação de software em endpoint foi realizada nesta etapa.

## Nota de continuidade — 27/11/2026

Este documento descreve **apenas o escopo histórico da 03A**, quando o botão de upload ainda estava desabilitado. **Não representa o estado atual da Central de Software.** A 03B (PR #80) já integrou upload privado MSI/EXE, aprovação/revogação, substituição e auditoria; a 03C-A (PR #81) integrou perfis; a B1 (PR #82) integrou operações e download autenticado; a B2 está em PR #84 sem merge. Consultar [handoff central](rc49-handoff-contexto-20261127.md) e [documento 03B](rc49-03b-upload-gerenciamento-instaladores.md) antes de inferir que a UI ainda é mock/readonly.
