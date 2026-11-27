# RC49-03B — Upload e gerenciamento de instaladores

## Escopo

Transforma a aba **Uploads** da Central de Software 03A em catálogo de MSI/EXE com binário privado, cadastro, edição, substituição, aprovação, revogação, auditoria e download autenticado pelo operador. Não instala, distribui, atualiza ou remove software em endpoints. Não altera protocolo/agente Windows, jobs, políticas, NOC ou terminal.

## Armazenamento e validação

- O binário fica em `NIGHTOWL_SOFTWARE_PACKAGE_STORAGE_ROOT`, fora de `MEDIA_ROOT`, `STATIC_ROOT` e `STATICFILES_DIRS`. Em ambiente `DEBUG`, há um diretório local privado padrão. Fora de `DEBUG`, a variável precisa ser definida explicitamente; sem ela, escrita e download falham fechados.
- O nome no disco é UUID gerado pelo servidor e não deriva do nome fornecido pelo operador. A API nunca retorna a chave de armazenamento.
- A aplicação lê `UploadedFile.chunks()` em blocos de 1 MiB, grava primeiro em `.staging`, calcula tamanho e SHA-256 e verifica cabeçalhos MSI Compound File ou EXE PE/COFF. A extensão deve corresponder ao formato detectado. Não executa o arquivo.
- O padrão de tamanho máximo é 1 GiB (`NIGHTOWL_SOFTWARE_PACKAGE_MAX_UPLOAD_BYTES`), limitado pela aplicação a no máximo 1 GiB. O limiar de memória multipart é 1 MiB; uploads maiores usam arquivo temporário Django e staging privado.
- MSI é reconhecido como Compound File com assinaturas das entradas de diretório padrão `_Tables`, `_Columns`, `_StringPool` e `_StringData`; EXE exige cabeçalho PE/COFF com característica executável. São verificações estruturais, não análise de malware ou prova de que o instalador conclui sua instalação.
- O staging e o destino ficam no mesmo volume. A promoção usa rename atômico; a transação só aponta o cadastro ao destino completo. A substituição promove o novo binário, grava auditoria e estado em transação e remove o arquivo anterior após o commit. Em falha de commit, o novo destino é removido e o artefato anterior permanece associado.
- Pastas privadas POSIX não podem ser acessíveis por outros usuários; arquivos temporários são criados pelo mecanismo seguro do sistema. Implantação deve configurar ownership/permissões do volume para a conta do backend, sem colocá-lo sob diretórios públicos.

### Requisito de infraestrutura HTTP

Antes de habilitar uploads em produção, configurar o volume privado persistente com capacidade e backup/retention aprovados e alinhar o proxy ao limite da aplicação. Para Nginx, `client_max_body_size` precisa ser pelo menos `1025m` para permitir overhead multipart sobre um binário de 1 GiB; confirmar também limites de proxy e espaço temporário do Django. Nenhuma configuração de Nginx, volume ou ACL de produção foi alterada nesta PR.

## Contrato HTTP

Todas as rotas usam sessão autenticada, middleware CSRF para mutações, JSON de erro sem detalhes internos e respostas `private, no-store`.

| Método e rota | Uso | Permissão técnica |
|---|---|---|
| `GET /software/data/packages/` | listar metadados atuais | operador técnico, mantendo contrato da 03A |
| `GET /software/packages/<uuid>/` | detalhe e até 100 eventos de auditoria | operador técnico |
| `POST /software/packages/create/` | multipart `file`, `friendly_name`, `version?`, `description?` | `add_softwarepackagerecord` |
| `POST /software/packages/<uuid>/update/` | editar somente nome, versão, descrição, com `revision` | `change_softwarepackagerecord` |
| `POST /software/packages/<uuid>/replace/` | multipart `file`, `revision`, `reason` | `change_softwarepackagerecord` |
| `POST /software/packages/<uuid>/approve/` | `revision`, `reason` | `approve_softwarepackage` |
| `POST /software/packages/<uuid>/revoke/` | `revision`, `reason` | `revoke_softwarepackage` |
| `POST /software/packages/<uuid>/delete/` | `revision`, `reason` | `delete_softwarepackagerecord` |
| `GET /software/packages/<uuid>/download/` | download attachment autenticado | `download_softwarepackage` |

Superusuário satisfaz as permissões Django; todas as ações ainda exigem usuário ativo e autorização de operador técnico. A consulta da Central continua usando a autorização técnica existente da 03A para não quebrar leitura já homologada. Downloads e mutações têm permissões próprias. As permissões novas precisam ser atribuídas aos grupos de operador no procedimento normal de identidade; não são concedidas automaticamente.

## Estados e auditoria

- Criação sempre inicia em **Rascunho** e exige binário e nome amigável.
- Aprovação revalida tamanho e SHA do artefato armazenado, exige motivo e usuário autorizado. Não verifica Authenticode e não representa aprovação antimalware; `signature_status` permanece `unknown` sem verificação criptográfica.
- Revogação exige motivo. Substituir binário exige motivo, invalida aprovação/signatário, limpa dados de aprovação/revogação e cria nova revisão; o UUID do cadastro permanece estável.
- Edição otimista exige `revision` atual e evita sobrescrever atualização concorrente. Substituições serializam por `select_for_update`; operações com a mesma revisão resultam em uma vencedora e outra resposta `409 stale_revision` no PostgreSQL.
- `AuditEvent` retém snapshots antes/depois, hash, nome, tipo, tamanho, operador e motivo. O binário anterior não é mantido como versão histórica.
- Exclusão definitiva exige motivo, ausência de histórico de instalação e ausência de operação ativa. O evento de exclusão persiste com snapshot mesmo após apagar o cadastro e o arquivo. Pacotes utilizados devem ser revogados, não apagados.
- Substituição/exclusão verifica `AgentJob` de instalação com `software_package_id` (ou `package_id`) no payload. A 03C deve criar jobs dentro de transação após `select_for_update` do mesmo cadastro e registrar o UUID top-level, mantendo o bloqueio de corrida entre criação de job e substituição.

## 03C — contrato futuro

O download atual é para operador pela sessão autenticada. A 03C pode adicionar autenticação por identidade/token do agente ao mesmo recurso/serviço privado e validar UUID/machine_id, aprovação e SHA antes do envio. Não criar URL pública nem segundo transporte. No futuro, cada operação deve capturar UUID, SHA e nome do artefato em auditoria/job; proteger arquivos referenciados por jobs `queued`, `sent` ou `running`; atualizar o histórico de uso antes de permitir exclusão. Instalação silenciosa/detecção/reboot continuam definidos por instalador, sem parâmetros universais para EXE.

## Migration

A mudança de esquema acrescenta campos ao `SoftwarePackageRecord` e permissões na migration `0060_rc49_software_package_uploads`. Após a integração da PR #79, a cadeia vigente é `0058_softwarepackagerecord` → `0059_stable_coverage_round` → `0060_rc49_software_package_uploads`. A migration 0060 depende de 0059 e foi gerada contra a main atualizada; validar o grafo completo e aplicar desde a base antes de publicar.

## Limitações e homologação

- Não há análise antimalware nem validação Authenticode nesta fase.
- A detecção MSI valida assinatura/header OLE Compound File e EXE valida cabeçalho PE/COFF; isso identifica estrutura, não atesta que o pacote é seguro ou instalável.
- Configuração de storage privado e `client_max_body_size`, espaço em disco, filesystem, backup, permissões, exposição via Nginx e download autenticado precisam de homologação do backend Linux antes de liberar operadores.
- A remoção física após substituição/exclusão ocorre no `on_commit`; falhas de unlink deixam órfão privado e geram log sanitizado, sem quebrar a referência nova. Uma rotina operacional de reconciliação de órfãos poderá ser definida se a homologação demonstrar necessidade.
- Nenhum pacote real foi carregado, aprovado, baixado ou distribuído; nenhuma migration foi aplicada em produção.
