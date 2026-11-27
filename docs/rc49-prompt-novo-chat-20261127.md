# Prompt de retomada — novo chat NightOwl (27/11/2026)

> Copie o texto abaixo integralmente para o novo chat, preferencialmente com GitHub conectado. Esta é uma instrução de leitura/diagnóstico, **não uma autorização de merge ou deploy**.

---

Quero que você seja meu copiloto técnico e de produto para o **NightOwl RMM**, sem misturar esse contexto com outros projetos. O repositório principal é [Gabriel-J-Oliveira/rmm_project](https://github.com/Gabriel-J-Oliveira/rmm_project).

**Primeiro trabalho: recuperar e validar o contexto existente no GitHub. Não comece implementando código.**

## 1. Leitura obrigatória

Leia, nesta ordem, os arquivos da branch `main`:

1. [`docs/rc49-handoff-contexto-20261127.md`](rc49-handoff-contexto-20261127.md) — fonte primária do handoff e estado das frentes.
2. [`docs/deploy-RC49.md`](deploy-RC49.md) — compromissos de produto e roadmap.
3. [`docs/nightowl-visao-produto-roadmap-rc49.md`](nightowl-visao-produto-roadmap-rc49.md) — visão completa do RMM.
4. [`docs/rc49-codex1-handoff-20261127.md`](rc49-codex1-handoff-20261127.md) — Stable e responsabilidades do Codex 1.
5. [`docs/rc49-codex2-handoff-20261127.md`](rc49-codex2-handoff-20261127.md) — Central de Software e responsabilidades do Codex 2.
6. [`docs/rc49-07-stable-desktop-rollout.md`](rc49-07-stable-desktop-rollout.md), [`docs/rc49-03c-b1-software-operations.md`](rc49-03c-b1-software-operations.md), e os documentos 03A/03B/03C-A para contratos e histórico.
7. Se precisar de contexto de NOC, patrimônio e continuidade, ler os documentos RC49-01A/01B, RC49-02, grants do terminal e RC49-10 listados no handoff.

Leia o documento completo, não apenas o título ou a primeira seção. **As notas de 27/11 no final dos documentos prevalecem sobre descrições históricas de andamento**, mas não apagam decisões anteriores. Se algum arquivo não estiver acessível, explique exatamente qual e trabalhe com a evidência restante; não invente seu conteúdo.

## 2. Confirme GitHub antes de responder

Consulte a `main` atual, todas as PRs abertas, SHA HEAD/base, alterações, jobs CI, dependências de migrations e conflitos. A última fotografia antes da atualização documental era:

- **Main de código:** `97a5f61518edf9d686f21dc84a23488f7dcb4770`, após #83.
- Codex 1: 07B-3 retries/reconciliação no runner existente. Execução inicial + até 3 retries adicionais, backoff configurável 15 min, 1 h, 4 h após falha recuperável; offline/condição temporária aguardam sem consumir tentativa. Comunicação/download só permitem retry se não execução comprovada ou estado reconciliado; não reenviar cegamente resultado ambíguo. Reconciliar identidade, recibo, versão, heartbeat e saúde; distinguir already_current de atualização comprovada. Assinatura/identidade inválida, rollback ou falha permanente bloqueiam e alertam; esgotamento requer intervenção. Revalidar release vigente, aprovação, pausa/aborto, exclusões e lotes; não reabrir campanhas concluídas nem duplicar jobs. Verificar release sem elegíveis iniciais e ingresso posterior sem contornar aprovação. Depois 07B-4 UI agregada de cobertura, rodadas e tentativas. #83 integrada no SHA acima, CI 46042230821 5/5, migration 0063 depende de 0062.
- Main de código `97a5f61518edf9d686f21dc84a23488f7dcb4770`. [#83](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/83) integrada por merge normal, HEAD `d7a9b6f52d7dffee83048e5d35e777acab283656`, CI [46042230821](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46042230821) 5/5. Migration agents.0063_stable_coverage_execution_campaign depende de 0062_rc49_software_package_operations; colisão resolvida. [#84](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/84) aberta, HEAD `75777e6be7033a3a79718fe7a082f695cc00fb94`, CI [46045236911](https://github.com/Gabriel-J-Oliveira/rmm_project/actions/runs/46045236911) 5/5 no HEAD, base incorporada `11b3ba1001486c026586571a9c393ef417820590`, sem migration. Mergeable na consulta, mas falta incorporar #83, preservar ambos os contratos em agents/views.py, repetir CI e obter autorização de merge. #86 é documental. **Sem evidência de deploy RC49, migrations operacionais, release Windows assinada RC49, ativação Stable, despacho de software ou instalação real.** Último ambiente relatado RC48/48.1; confirmar produção separadamente.
- Revisão B2: corrigido ACK com agent_version (validação estrita), falhas v2 e propagação de error_code; novos ACKs queued rejeitados, v1 e replay preservados. Detecção MSI MsiGetProductInfoExW em contexto de máquina, consulta inicial de tamanho ERROR_SUCCESS; timeout HTTP de perfil/artefato subordinado ao token do job, até 1800 s. Testes sintéticos/doubles, sem instalação real. Repair Runner: timeout histórico ~20,9 s, rerun ~6,9 s, causa não comprovada. Major upgrades com mudança de ProductCode, EXE, reconciliação geral e escala pendentes. Confiança publisher por pin local opt-in; gerenciamento centralizado é proposta futura.
- Codex 2: fechar sincronização/CI da #84 e obter autorização de merge normal; depois negociar 03C-C executor EXE (argumentos estruturados por fabricante, detecção explícita antes/depois, sem flags silenciosas universais, sem executar UninstallString coletado, preservar aprovação/hash/revisão, transporte v2, verificação e compatibilidade MSI/v1). Gerenciamento centralizado de confiança proposto a encaixar; depois 03C-D escala, UI/progresso, atualização/desinstalação e reconciliação; 03E/08 FollowMe, Bitdefender futuro. Testes reais de software somente no fechamento do desenvolvimento RC49.
- A PR #82 B1 já está integrada. Backend possui operações `install/update/uninstall` tipadas, idempotência de `AgentJob`, download privado autenticado e ACK verificado. Operações v2 ainda `queued` rejeitam ACK novo. **Despacho está desabilitado por padrão**.
- Não presumir aplicação operacional das migrations RC49, deploy em produção, Stable ativado ou endpoints atualizados.

## 3. As decisões que você deve preservar

- Objetivo do NightOwl: monitoramento, proteção, controle, inventário e histórico patrimonial do parque, execução remota, software, políticas, NOC, scripts e impressoras.
- **Registros patrimoniais dentro do Endpoint**, não menu Ativos. Identidade física estável separada da identidade de instalação/agente e do usuário observado. Patrimônio amigável, único, pesquisável e provisório; duas empresas ainda terão nomenclaturas definidas depois.
- Central de Software com duas abas (Softwares coletados / Uploads), cards de upload em duas colunas no desktop e uma no mobile, nome amigável abre drawer. MSI/EXE sem versionamento perpétuo do binário, mas hashes e histórico auditados; proteger arquivos em uso; aprovação do perfil vinculada a hash/revisão. EXE não tem parâmetros silenciosos universais. A instalação só é sucesso com detecção real, nunca apenas ACK/exit code.
- Stable deve alcançar **100% dos desktops físicos Windows elegíveis no canal stable**, gradualmente, inclusive novos e offline quando voltarem; excluir servidores, VMs de teste e grupos protegidos. `development` para VM piloto/testes. Pausa/cancelamento, assinatura, identidade, versão e saúde permanecem necessários.
- Roadmap de políticas aprovado em 27/11: após Stable, 04A catálogo de ações nativas versionadas e dropdown/formulário dinâmico por schema; 04B motor de políticas, condições e agendamentos; 04C Windows Update qualidade/segurança respeitando GPO/WSUS, sem drivers/feature upgrades no primeiro recorte; 04D limpeza segura de disco por categorias permitidas com espaço antes/depois; 04E reboot (janela, aviso/adiamento, autorização técnica ou não automático); 04F escopos dinâmicos por AD de computadores, domínio, OU, grupos, endpoints e exclusões; 05 biblioteca de scripts personalizados aprovados/versionados, parâmetros tipados e auditoria. Capacidades nativas no código, políticas e parâmetros na UI; scripts aprovados alimentam a mesma página. Reutilizar AutomationDefinition/Occurrence, AgentJob, v2, RBAC e scheduler existente. Defaults de manutenção ainda a definir por incremento. Não é autorização de deploy ou implementação simultânea de todo roadmap.
- Reutilizar motor Django `AgentJob`, pull/ACK, auditores e worker .NET. Não criar outros schedulers/transports ou executar shell arbitrário.
- Preservar funcionalidades previamente autorizadas a cada nova RC e validar delta, não recomeçar homologações desnecessariamente. Evitar backups/checkpoints repetidos em mudanças pequenas; reservar para intervenções de grande impacto real.
- Nosso ambiente está em homologação; desenvolvimento por branch/PR e validação automatizada, mas **nenhum merge, deploy, alteração de flags, publicação de agentes ou execução real sem autorização específica**. Não confundir CI sintético com homologação em VM.
- Os dois Codex trabalham em paralelo: **Codex 1 = Stable / 07B**; **Codex 2 = Central de Software / 03C**. À medida que essas frentes encerrarem, negociar incrementos seguintes, sem iniciar tudo de uma vez.
- O usuário quer respostas diretas, decisões explicadas e prompts completos executáveis. Uma implementação autorizada deve ir até PR/CI, não parar após migrations parciais.

## 4. O que me entregar neste novo chat

Apresente um **quadro atual de RC49** com itens integrados, PRs abertas e o que falta; destaque conflitos técnicos atuais e a sequência recomendada dos merges. Separe claramente o próximo trabalho do Codex 1 e do Codex 2, dependências de migrations e possíveis testes necessários.

Depois, indique **a próxima decisão que precisamos tomar** e proponha um pequeno plano para cada frente. Não abra PRs, altere código, publique releases, faça deploy ou acione endpoints nesta primeira resposta; nossa primeira tarefa é sincronizar entendimento e retomar com confiança.

Se algo no GitHub contradisser este prompt, **priorize o estado verificado do repositório**, explicite a mudança e atualize a análise.
