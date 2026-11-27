# RC49-07: rollout Stable para desktops

O perfil Stable é opt-in (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED`, falso por padrão). Ele depende dos gates existentes de orquestração, execução automática e de um agendamento operacional dos comandos existentes de processamento, reconciliação e governança.

Classifique desktops físicos Windows 10/11 gerenciados no grupo `stable-desktops`. Modelo/fabricante não informados, servidores, VMs reconhecidas e grupos protegidos (incluindo piloto e laboratório) ficam fora. Não use hostname para classificar. O perfil respeita canal, política, janela, versões, assinatura e grupos; não altera essas configurações.

Publicar, promover ou despausar uma release Stable a 100%, com as permissões de release e campanha, congela os alvos elegíveis em uma campanha e inicia sua primeira onda. A concorrência da onda (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE`, padrão 25) é o limite de lote, não o percentual de cobertura. Alvos offline no snapshot aguardam heartbeat sem ocupar vaga. Novas máquinas inscritas depois do snapshot não são silenciosamente adicionadas. Uma release sem alvos elegíveis não abre campanha.

Use a API atual de campanha para progresso, motivos, pausa, retomada e cancelamento. Progresso corresponde a resultados reconciliados, não a jobs apenas criados. Pausa impede novo despacho, mas não cancela execução já entregue; jobs em fila conservam a validade existente. Cancelar não recria campanha automaticamente. Perfil, identidade, versão, assinatura e política são verificados no despacho. O fluxo direto de atualização automática Stable fica bloqueado enquanto o perfil está ativo; atualização manual explícita permanece independente.

**Cobertura complementar (07B-2):** o ciclo existente de `process_agent_rollouts --run-once`
descobre desktops novos ou recém-elegíveis depois que a campanha Stable original aprovada
termina. Não reabre nem modifica o snapshot original. Cada rodada registra observações
novas ou alteradas; quando há alvos a atualizar, a rodada aponta para uma campanha
executora distinta da mesma release. Exclusões temporárias são reconsideradas, e
`already_current` exige um heartbeat recente e não conta como instalação executada.

O catch-up exige a release Stable mais recente por versão, assinada, publicada,
despausada e com rollout de 100%. Uma release mais nova pausada impede fallback.
Campanha ativa (inclusive pausada) impede nova campanha; campanha abortada veta
novas rodadas automáticas para a release. Despacho e entrega das complementares
revalidam a release aplicável e os controles existentes. Jobs já entregues continuam
sujeitos à reconciliação normal, sem cancelamento retroativo.

Os alvos anteriormente aprovados não voltam a entrar em campanhas complementares,
e jobs ativos impedem seleção pelo avaliador existente. Um alvo offline já
incluído na campanha original continua sob despacho normal; novos alvos offline
aguardam heartbeat antes da seleção complementar. Nenhum retry automático é
introduzido. Se a publicação não abriu campanha original, não há aprovação de
campanha a reutilizar: o operador precisa aprovar uma primeira campanha pelo fluxo
existente. O aprovador deve continuar autorizado a criar e alterar campanhas.
