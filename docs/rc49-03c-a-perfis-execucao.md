# RC49-03C-A — Perfis de execução e despacho seguro

## Escopo entregue

Este incremento prepara os perfis de instalação, atualização, remoção e detecção dos pacotes MSI/EXE cadastrados na Central de Software. O drawer existente continua sendo o único ponto de configuração. A tela distingue aprovação do arquivo e aprovação operacional do perfil; substituir o binário, editar o perfil ou revogar o pacote invalida a aprovação operacional correspondente.

O perfil guarda uma revisão própria, seu SHA-256 canônico e o SHA-256/revisão do artefato ao qual foi aprovado. Os parâmetros são JSON tipado e auditado. Não são aceitos comandos de shell, linhas de comando livres, caminhos arbitrários nem `UninstallString` do inventário. Para MSI, as opções de `msiexec` ficam sob controle do futuro agente e o cadastro usa ProductCode. Para EXE, cada argumento é um item separado de uma lista JSON e precisa ser documentado pelo fabricante. O aplicativo não executa instaladores para detectar metadados.

## Contratos e permissões

- `POST /software/packages/<uuid>/profile/`: requer `configure_softwarepackageprofile`, revisão otimista, motivo e pacote com binário atual; salva perfil como rascunho.
- `POST /software/packages/<uuid>/profile/approve/`: requer `approve_softwarepackageprofile`, pacote aprovado, artefato privado íntegro e perfil ligado ao hash/revisão atuais.
- A leitura do drawer exige permissão de consulta já usada pela Central.
- O modelo acrescenta revisão do artefato, campos do perfil e aprovação do perfil pela migration `0061_rc49_software_execution_profiles`, dependente de `0060_rc49_software_package_uploads`.

`agents.software_operation_contract.prepare_software_operation_snapshot()` valida permissões, UUID e machine_id únicos, endpoint ativo/online, aprovação do pacote e perfil, integridade do binário privado, capability recente, conflito com job ativo e request id. Retorna um snapshot imutável e **não cria `AgentJob`, endpoint HTTP de despacho ou operação remota**. A configuração `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED` permanece `False` por padrão e não habilita despacho por si só.

Quando a futura RC49-03C-B implementar materialização, a revalidação do contrato e a criação do `AgentJob` terão de ocorrer sob a mesma transação externa, mantendo os locks de endpoint e pacote até o commit. A 03C-A não é autorização para executar jobs.

## Perfis suportados nesta versão

- MSI: instalação controlada por ProductCode; atualização MSI opcional; desinstalação por ProductCode; detecção por ProductCode e comparação com a versão esperada.
- EXE: argumentos de instalação/atualização/desinstalação estruturados como arrays; desinstalação somente por caminho sob macros de diretórios permitidos; detecção por chave exata de uninstall ou versão de arquivo sob diretório permitido.
- Reinicialização: `report_only`, `defer_to_policy` ou `require_operator`; nenhum perfil reinicia a máquina nesta fase.

O perfil descreve a intenção por pacote, mas sua aprovação não prova compatibilidade ou segurança do instalador. Revisão do binário e análise antimalware continuam distintas.

## Dependências da RC49-03C-B

1. Nova versão Windows assinada compatível com a versão do contrato e com capabilities de instalação, atualização e remoção; capabilities só devem ser anunciadas quando o código instalado realmente as suportar.
2. Autenticação mútua/identidade do agente e download autenticado do binário privado, com autorização por pacote e endpoint, URL de curta duração ou canal autenticado existente, limites, hash e expiração.
3. Materializador tipado que revalide e crie o `AgentJob` atomicamente com locks e idempotência. Esta PR não cria dispatch nem fila nova.
4. Executor Windows com `ProcessStartInfo.ArgumentList`/API equivalente sem shell, Job Object, timeout, captura limitada de saída, limpeza e tratamento de códigos de saída controlados.
5. Confirmação posterior por nova evidência de inventário/estado instalado e versão, não somente pelo exit code; reconciliação após reinício e estados de reboot pendente.
6. Política operacional de permissões, autorização por endpoint, rollout piloto, gestão de falhas e rollback de versão do aplicativo.

Antes da 03C-B, a versão RC49-03C-A da migration precisa estar aplicada em ambiente de validação, e deve haver teste de compatibilidade entre backend, versão/capabilities do agente e fluxo de distribuição. Não alterar produção nem agentes como parte desta PR.
