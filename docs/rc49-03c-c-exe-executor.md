# RC49-03C-C — Executor EXE Windows

## Escopo

Este incremento estende o contrato de operações de software v2 para executar perfis EXE aprovados. Ele reutiliza o `install_software` AgentJob, a autenticação e o transporte privado de perfil/artefato da 03C-B1, além do journal e do coordenador de processos do agente. O fluxo MSI v2 e o protocolo legado v1 permanecem separados e compatíveis.

Nenhuma operação é despachada ou executada durante o desenvolvimento. O backend continua com `NIGHTOWL_SOFTWARE_OPERATION_DISPATCH_ENABLED=False` por padrão, e o agente mantém `softwareExeOperationsEnabled=false` quando a opção não está explicitamente configurada.

## Perfil e vínculo de aprovação

Perfis EXE schema 2 permitem configurar `install`, `update` e `uninstall` independentemente. Cada operação tem método tipado, vetor de argumentos, códigos de saída aceitos, códigos de reboot e timeout entre 30 e 1800 segundos. O executável de desinstalação é um caminho explícito sob Program Files ou ProgramData; strings `UninstallString` do inventário não são aceitas nem executadas.

A detecção é declarativa e restrita a valor exato do registro HKLM, versão de arquivo ou serviço Windows. Instalação/atualização exigem evidência pós-execução compatível com a versão esperada; remoção exige ausência confirmada. Um serviço sem versão verificável não pode comprovar atualização. O código 3010 pode ser configurado como reboot pendente, sem reinicialização automática; 1641 é sempre rejeitado.

O servidor vincula autorização e despacho ao pacote, tipo, UUID do endpoint, `machine_id`, artefato/hash/revisão, perfil/hash/revisão, operação e capacidade EXE correspondente. Qualquer alteração invalida a aprovação vinculada e a verificação é refeita antes de fornecer perfil ou binário.

## Segurança no agente

As capabilities independentes `software_exe_install:1`, `software_exe_update:1` e `software_exe_uninstall:1` são anunciadas somente quando o executor local está habilitado, o `install_software` está permitido e há tamanho máximo e pins Authenticode válidos. O agente valida cadeia Authenticode e o hash SHA-256 do certificado do publisher contra a política local já compartilhada com MSI.

Artefatos são baixados em streaming para staging por job, limitados a 1 GiB, conferidos por tamanho e SHA-256 e executados sem shell, com argumentos estruturados. A desinstalação usa apenas o executável explicitamente configurado, verifica seu publisher e executa uma cópia no staging protegido. O processo recebe ambiente Windows isolado, com `PATH` de sistema fixo, sem herdar variáveis sensíveis do processo pai; o diretório temporário possui ACL restrita e é exclusivo por execução.

O journal EXE conserva estados de operação não confirmada. Timeout, interrupção ou resultado incerto não repetem a execução automaticamente. Um journal pendente/malformado do mesmo pacote bloqueia nova execução para revisão. Operações já no estado esperado podem retornar evidência idempotente sem executar processo.

## Compatibilidade e rollout

- Sem migration de banco nesta etapa.
- MSI schema 1 e MSI v2 permanecem suportados; `install_software` v1 conserva seu gate e caminho existentes.
- EXE requer nova versão assinada do agente; agentes antigos não anunciam as capabilities e o backend não deve despachar para eles.
- A flag global de despacho segue desligada; a opção local EXE segue desligada por padrão.
- O suporte não representa homologação com instaladores de fabricantes reais.

## Limites

Não há suporte a scripts/shell, instalação baseada em ZIP, argumentos universais, uninstallers derivados do inventário, detecção por hostname, atualização MSI que troca ProductCode, instalação em lote ou reboot automático. Regras específicas de publisher continuam na configuração de confiança já existente; gerenciamento centralizado de publishers fica para incremento posterior. Assinatura e confiança precisam estar configuradas localmente antes de qualquer capacidade EXE ser anunciada.

## Validação

Os testes usam bytes e executáveis sintéticos, delegates de execução, detecção controlada e transporte fake. Eles exercitam instalação/update/uninstall tipados, reboot pendente, evidência pós-operação, versão já presente, resultado incerto e bloqueio de replay, caminho e assinatura explícitos do uninstaller, argumentos Unicode/quoting, confiança, gate de capability e contratos Django/ACK. Nenhum resultado de teste é evidência de instalação em uma máquina real.
