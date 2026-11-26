# RC49-07: rollout Stable para desktops

O perfil Stable é opt-in (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED`, falso por padrão). Ele depende dos gates existentes de orquestração, execução automática e de um agendamento operacional dos comandos existentes de processamento, reconciliação e governança.

Classifique desktops físicos Windows 10/11 gerenciados no grupo `stable-desktops`. Modelo/fabricante não informados, servidores, VMs reconhecidas e grupos protegidos (incluindo piloto e laboratório) ficam fora. Não use hostname para classificar. O perfil respeita canal, política, janela, versões, assinatura e grupos; não altera essas configurações.

Publicar, promover ou despausar uma release Stable a 100%, com as permissões de release e campanha, congela os alvos elegíveis em uma campanha e inicia sua primeira onda. A concorrência da onda (`NIGHTOWL_STABLE_DESKTOP_ROLLOUT_BATCH_SIZE`, padrão 25) é o limite de lote, não o percentual de cobertura. Alvos offline no snapshot aguardam heartbeat sem ocupar vaga. Novas máquinas inscritas depois do snapshot não são silenciosamente adicionadas. Uma release sem alvos elegíveis não abre campanha.

Use a API atual de campanha para progresso, motivos, pausa, retomada e cancelamento. Progresso corresponde a resultados reconciliados, não a jobs apenas criados. Pausa impede novo despacho, mas não cancela execução já entregue; jobs em fila conservam a validade existente. Cancelar não recria campanha automaticamente. Perfil, identidade, versão, assinatura e política são verificados no despacho. O fluxo direto de atualização automática Stable fica bloqueado enquanto o perfil está ativo; atualização manual explícita permanece independente.

**Escopo desta entrega:** campanhas com conjunto fixo de alvos. A cobertura contínua dos desktops cadastrados ou tornados elegíveis após a publicação é um incremento complementar. O retorno de um alvo offline já incluído na campanha não depende desse incremento.
