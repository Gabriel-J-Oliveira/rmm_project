# RC47-08 — canal e atualização manual

## Diagnóstico

O modal recebia `agent_update_releases` filtrado exclusivamente pelo `AgentMachine.update_channel` persistido. O campo de canal era apenas informativo, portanto um endpoint em `stable` não listava releases de `development`.

O endpoint existente `api-endpoint-update-policy` já registra `AgentReleaseAudit` e serializa a avaliação da política. Porém, sua implementação tratava campos omitidos como valores falsos/vazios; enviar somente `update_channel` podia apagar `auto_update_enabled`, `update_paused`, pin e janela de manutenção. Agora a API aplica semântica parcial: somente campos presentes são atualizados; grupos só mudam se `rollout_groups` for enviado explicitamente. A requisição do modal inclui motivo auditável.

## Fluxo corrigido

1. O modal mostra versão, canal e política persistidos e oferece `stable`, `pilot` e `development`.
2. Selecionar canal não grava configuração nem cria job. `Salvar canal` faz POST somente de `update_channel` e motivo, com a sessão autenticada e CSRF existente.
3. Após confirmação do servidor, o detalhe é recarregado. As opções são montadas novamente da lista autoritativa do backend para o canal salvo.
4. A release exibe status, canal da política, canal imutável do artefato assinado, validade da assinatura, tamanho, versão mínima do updater e elegibilidade/motivo.
5. Releases pausadas permanecem visíveis. A pausa de rollout não impede uma seleção manual explícita quando a política e as demais verificações a permitem. Release revogada, assinatura/trust inválidos, incompatibilidade do updater, grupos não autorizados, endpoint pausado ou job ativo continuam bloqueando o despacho.
6. `Atualizar agente` permanece uma segunda ação. O backend reavalia elegibilidade e cria o job existente, com versão e release explícitas; a UI nunca cria job durante a troca de canal.

## Compatibilidade do updater Windows

Não foi encontrado bloqueio de bootstrap. `AgentRelease.channel` determina o canal de política/listagem; `source_channel` representa o canal contido no artefato assinado. `build_update_agent_job_payload` envia o canal do artefato em `channel` e mantém `policy_channel` como metadado administrativo. O serializador do job remove `policy_channel` antes da entrega ao agente. O updater valida o canal do manifesto assinado contra o canal de artefato recebido. Portanto, uma promoção de release entre canais não conflita com o manifesto. Esta alteração não muda o agente, updater, instalador ou artefato RC47; não exige nova release Windows assinada.

## Homologação controlada

Somente após revisão e implantação autorizada do backend/frontend:

1. Confirmar UUID e `machine_id` do endpoint antes de qualquer operação; não identificar a VM apenas pelo hostname.
2. Abrir o modal, verificar canal e política atuais, mudar para `development` e salvar. Confirmar mensagem de sucesso e auditoria com operador, endpoint, canal anterior/novo e motivo.
3. Verificar que pausa, auto-update, pin, grupos e janela mantiveram os valores anteriores. Confirmar que nenhuma tarefa foi criada pela troca de canal.
4. Reabrir/atualizar a página e confirmar canal persistido e RC47 pausada na lista. Conferir canal assinado, assinatura, hash/tamanho e updater mínimo; motivo de inelegibilidade deve ser mostrado se algum requisito falhar.
5. Não despachar atualização até autorização explícita separada. Após essa autorização, usar `Atualizar agente` e validar o job e ACK no fluxo existente.
6. Para retorno, escolher `stable`, salvar explicitamente, confirmar a auditoria e a lista de releases atualizada. Não criar job nesse passo.

O teste local não altera endpoint real, job, rollout, release, serviço Linux nem pacote Windows. Rollback da mudança do endpoint é selecionar o canal anterior e salvar; rollback de código é reverter esta PR. Nenhuma migration é necessária.
