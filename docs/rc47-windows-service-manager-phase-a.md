# RC47 — Gerenciador de Serviços Windows, Fase A

## Escopo

A Fase A transforma **Consultar serviço** em uma consulta somente leitura de serviços do endpoint, dentro da página existente. O fluxo continua usando `AgentJob`/`collect_services`; não foi criado executor ou fila paralela. A listagem contém apenas nome interno, nome de exibição, estado e tipo de inicialização. Conta, executável, caminho de comando e argumentos não fazem parte do contrato paginado.

## Contrato e limites

- A versão legada `collect_services` (schema 1) continua válida para clientes anteriores.
- A versão paginada é explicitamente versionada no payload (`schema_version: 2`) e exige a capability recente `collect_services_paged:2`. O heartbeat legado não é suficiente para receber esse payload.
- Cada página contém 20 registros; uma coleta aceita no máximo 40 páginas/800 serviços. O tamanho da página mantém uma linha no pior caso de campos Unicode de 256 unidades UTF-16 abaixo do limite de 64 KiB do resultado durável do job. A coleta acima do limite falha sem apresentar uma lista parcial.
- O agente mantém até quatro snapshots em memória por dez minutos. O backend emite cursor assinado por dez minutos, vinculado a endpoint UUID, `machine_id`, operador, snapshot e posição. Cursor alterado, expirado, repetido ou incompatível encerra a consulta.
- A interface acompanha cada job por até 90 segundos e cada varredura por até cinco minutos. Fechar e reabrir o modal no mesmo carregamento da página retoma o job ainda pendente; não há recuperação do ID após recarregar/navegar para outra página.
- `NIGHTOWL_SERVICE_COLLECTION_ENABLED` é explícita e desabilitada por padrão. A consulta exige usuário técnico ativo com permissões de leitura do endpoint e de consulta/criação de jobs, endpoint online e identidade única, recente e compatível com heartbeat/capability. A API usa sessão e CSRF.
- O browser não persiste resultado ou credencial. Erros expõem códigos mapeados e texto seguro; páginas parciais não são mostradas.

## Compatibilidade e piloto

O backend publicado em `b8b417f520290db7d0b86af71feb86fccd9bf12b` não inclui esta API/capability. O CS-CVEL-0281 só poderá demonstrar a Fase A depois que o backend desta PR for publicado e após uma nova versão Windows assinada contendo o coletor paginado. O backend e o agente mantêm flags desligadas por padrão; esta mudança não ativa coleta global nem altera configuração de endpoint.

Não há migration. A coleta legada, os formatos existentes de jobs e a lista allowlisted continuam preservados. O uso da Fase A não altera serviços Windows.

Os testes Django exercitam autorização, flag, online/offline, capability antiga/recente, identidade ambígua, cursor, limites, truncamento e auditoria. O E2E usa Chrome for Testing do CI, endpoint e resultados sintéticos isolados; intercepta a API de dispatch, portanto não cria jobs remotos. Screenshots desktop/mobile são anexados como artefato do workflow Windows.

## Auditoria das operações futuras

O código já possui um executor administrativo separado para iniciar/parar/reiniciar. Ele requer habilitação administrativa local, ação específica permitida para o serviço, escopo no backend/capability e validações que recusam serviços protegidos, drivers, serviços compartilhados, binários Windows e caminhos não confiáveis. A Fase A não chama esse executor, não altera permissões ou allowlists e não apresenta ações de escrita como disponíveis. A consulta usa somente o coletor interno read-only.

### Fase B — operações controladas

Definir separadamente o escopo administrativo permitido por endpoint, validar identidade/capability, confirmação do operador, auditoria, idempotência, timeout e estados reais. Implementar somente iniciar/parar/reiniciar por uma allowlist técnica explícita, com testes de recusa e sem aceitar comandos arbitrários. Não deve ser liberada até aprovação da política e homologação em VM.

### Fase C — edição de propriedades

Avaliar alteração de tipo de inicialização e demais propriedades como operação administrativa de maior risco, com autorização independente, confirmação reforçada, trilha de auditoria, recuperação/falha e testes Windows. Não presumir que os contratos da Fase B ou as permissões de consulta autorizam edição.
