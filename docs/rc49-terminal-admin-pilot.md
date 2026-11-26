# RC49: terminal administrativo e Gerenciador de Serviços

## Escopo da candidata

- Revisar o código de backend, o relay, os estáticos e o pacote Windows no mesmo SHA. A correção da tarefa do Tray e a instrumentação da PR #62 fazem parte da mesma candidata.
- Não habilitar a modalidade administrativa por padrão. Não distribuir para a frota nem publicar uma versão independente de diagnóstico.
- O piloto é o endpoint `c97045f2-35ff-48f1-afa5-095173fbb3dd`, Machine ID `1eca4af2-4950-4a2d-9b9f-fa7010a62464`. Não selecionar por hostname.
- Antes de ativar o piloto, confirmar compatibilidade de backend e agente: o novo limite dedicado do frame de autenticação exige ambos os lados atualizados para aceitar entradas maiores que 1 KiB. Nenhuma migration foi adicionada por esta entrega; verificar as migrations pendentes do SHA consolidado separadamente.

## Controles e evidência

| Requisito | Implementação |
| --- | --- |
| Abertura e credenciais | Modal com domínio padrão CONTROL, domínio explícito e UPN aceitos. Os campos de identidade e senha não fazem parte da submissão HTML; o popup do PowerShell só aparece após confirmação da identidade do processo. |
| Autorização Django | Flag global, modo relay de processo único, permissões `agents.open_terminalsession` e `agents.open_admin_terminalsession` (a segunda exige atribuição explícita), grant administrativo ativo e restrito por operador/endpoint, identidade única e capabilities recentes. Sem fallback para ConPTY assistido. |
| Autenticação Windows | O agente chama `LogonUserW` no endpoint e confirma token elevado de administrador, não SYSTEM. Usa `CreateProcessAsUserW` e confere SID, Logon ID e Session ID do filho antes de liberá-lo. Não exige RDP, Tray, sessão interativa anterior nem perfil local provisionado. Exige que a política do Windows permita o tipo de logon utilizado. |
| Segredos | O HTTP de criação não recebe senha. O relay WSS confiável encaminha um único frame transitório, limitado; não grava payloads de autenticação. O agente zera buffers da senha. Navegador, TLS e Python podem manter cópias imutáveis transitórias; não se promete zeragem física. Não capturar frames nem enviar credenciais em tickets, chat ou logs. |
| Cabeçalho | Hostname do endpoint. Nome do administrador resolvido pelo SID autenticado ou nome da conta obtido do token. Responsável registrado só é exibido com permissão patrimonial, vínculo atual com ativo confirmado e último evento de troca já vigente. Sem esses dados, “Não identificado”; nunca usar último login como responsável. |
| I/O e encerramento | PowerShell sem scripts de perfil, limites de sessão/I/O, controle de revogação no relay, encerramento pelo navegador e pelo agente. Job Object possui a árvore de processos; observar o fechamento real no piloto. |
| Assistido | Fluxo mantido separado: grant próprio, identidade Windows da sessão e consentimento manual do Tray. O painel assistido continua acessível quando os dois modos estão autorizados. |

## Homologação API-first e reversão

1. Conferir as versões, identidade e heartbeat do piloto. Antes de habilitar, confirmar que a flag administrativa está desligada e inventariar todos os grants administrativos ativos: depois do GO, somente o par operador/piloto autorizado pode ficar elegível. Não usar o comando de grants ConPTY para habilitar administração.
2. Numa janela aprovada, promover o SHA de backend e a release assinada pelo fluxo normal. Manter rollout automático em 0% e sem campanha. Se for preciso despausar para uma instalação manual, autorizar somente o despacho escolhido, confirmar UUID/Machine ID antes da operação e repausar após o despacho. Ativar a configuração administrativa do agente apenas no piloto. Conferir capacidade real `remote_terminal_admin_auth:1` e manter o relay local com um worker.
3. Pela API, verificar flag, permissões, grant e bloqueio em outro endpoint. Confirmar que o grant administrativo não concede acesso assistido. Verificar que a sessão administrativa não tem prazo de consentimento do Tray; a assistida deve continuar aguardando autorização do usuário remoto.
4. No navegador, um operador autorizado informa uma conta de teste aprovada pela política corporativa. Na VM, manter Tray desligado e nenhuma sessão RDP aberta para essa conta. Testar credencial inválida, conta sem elevação e conta administrativa válida sem perfil existente. Em sucesso, verificar identidade real com `whoami`, hostname, PowerShell, resize e encerramento. Conferir eventos do Windows e NightOwl sem coletar senha, tickets ou conteúdo do terminal.
5. Revogar ou expirar o grant durante uma sessão e comprovar o fechamento dos dois peers e da árvore Windows. Confirmar que nova sessão é negada. Em falha, fechar a sessão, revogar o grant, desligar as flags administrativas ativadas para o piloto e reverter configuração/release pelo procedimento aprovado, sem alterar o consentimento assistido.

## Spooler, frente independente

Executar `scripts/Configure-NightOwlPilotSpoolerControl.ps1 -WhatIf` em PowerShell 7 elevado no piloto. A aplicação muda apenas as três operações do `Spooler` e reinicia o serviço do agente para recarregar a configuração; não toca no Spooler nem cria jobs. Conferir heartbeat e GET de disponibilidade antes de solicitar autorização separada para a execução real.

Após autorização, executar uma operação por vez, conferir job, ACK, resultado e estado Windows. Se o serviço iniciou em Running, provar parada, início e reinício, restaurando o estado original. Confirmar que serviços não autorizados e outros endpoints continuam bloqueados.
