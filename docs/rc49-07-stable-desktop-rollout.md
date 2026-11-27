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
Quando a campanha original foi aprovada para grupos específicos, as rodadas
herdam esse mesmo recorte.
**Escopo desta entrega:** campanhas com conjunto fixo de alvos. A cobertura contínua dos desktops cadastrados ou tornados elegíveis após a publicação é um incremento complementar. O retorno de um alvo offline já incluído na campanha não depende desse incremento.

## Atualização de desenvolvimento 07B — 27/11/2026

A seção inicial acima documenta a **PR #75 (07A)** com alvos fixos. Não interpretar a afirmação histórica de ausência de catch-up como situação geral do código em desenvolvimento.

- **07B-1 integrada, PR #79:** `AgentRolloutCoverageRound` e migration `agents.0059_stable_coverage_round` preservam o cohort/snapshot original, sem reabrir campanhas concluídas ou abortadas.
- **07B-2 integrada #83:** merge `97a5f61518edf9d686f21dc84a23488f7dcb4770`, CI 5/5 run 46042230821. Migration 0063 depende de 0062 operações. Campanhas complementares, sem retries generalizados.
- Codex 1: 07B-3 retries/reconciliação no runner existente. Execução inicial + até 3 retries adicionais, backoff configurável 15 min, 1 h, 4 h após falha recuperável; offline/condição temporária aguardam sem consumir tentativa. Comunicação/download só permitem retry se não execução comprovada ou estado reconciliado; não reenviar cegamente resultado ambíguo. Reconciliar identidade, recibo, versão, heartbeat e saúde; distinguir already_current de atualização comprovada. Assinatura/identidade inválida, rollback ou falha permanente bloqueiam e alertam; esgotamento requer intervenção. Revalidar release vigente, aprovação, pausa/aborto, exclusões e lotes; não reabrir campanhas concluídas nem duplicar jobs. Verificar release sem elegíveis iniciais e ingresso posterior sem contornar aprovação. Depois 07B-4 UI agregada de cobertura, rodadas e tentativas.
- Stable continua opt-in com `NIGHTOWL_STABLE_DESKTOP_ROLLOUT_ENABLED=False` por padrão, restrito a desktops físicos Windows elegíveis no `stable-desktops`. 100% significa cobertura ao longo do tempo dos **elegíveis**, não instalação simultânea nem inclusão de VMs/servidores. Despacho, publicação em massa e mudanças em endpoints ainda não autorizados por este documento.

[Handoff central](rc49-handoff-contexto-20261127.md) | [Codex 1](rc49-codex1-handoff-20261127.md).
