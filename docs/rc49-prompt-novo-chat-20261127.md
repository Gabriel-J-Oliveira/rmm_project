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

- **`main` de código:** `54bda321be6a91d5369d134d841c24130a644947`, após integrar #82.
- **Codex 1 / PR #83**, 07B-2 catch-up Stable, HEAD `175acca35b10d11f4294dc66b93cdb688be5274a`, CI 5/5, SEM merge. Migration `0062_stable_coverage_execution_campaign` conflitante com a `0062_rc49_software_package_operations` já na main. Renumerar para `0063` e ajustar dependência **somente se a migration não foi aplicada em banco persistente**. Confirmar merge normal da main e diffs de `agents/views.py`.
- **Codex 2 / PR #84**, executor MSI Windows 03C-B2, HEAD `8f5fc0d25f4e3acd7b349950fd1585522555651d`, CI 5/5, SEM merge; sem nova migration. Há execução MSI tipada testada por doubles e dados sintéticos, mas não release assinada nem teste de instalação real.
- A PR #82 B1 já está integrada. Backend possui operações `install/update/uninstall` tipadas, idempotência de `AgentJob`, download privado autenticado e ACK verificado. Operações v2 ainda `queued` rejeitam ACK novo. **Despacho está desabilitado por padrão**.
- Não presumir aplicação operacional das migrations RC49, deploy em produção, Stable ativado ou endpoints atualizados.

## 3. As decisões que você deve preservar

- Objetivo do NightOwl: monitoramento, proteção, controle, inventário e histórico patrimonial do parque, execução remota, software, políticas, NOC, scripts e impressoras.
- **Registros patrimoniais dentro do Endpoint**, não menu Ativos. Identidade física estável separada da identidade de instalação/agente e do usuário observado. Patrimônio amigável, único, pesquisável e provisório; duas empresas ainda terão nomenclaturas definidas depois.
- Central de Software com duas abas (Softwares coletados / Uploads), cards de upload em duas colunas no desktop e uma no mobile, nome amigável abre drawer. MSI/EXE sem versionamento perpétuo do binário, mas hashes e histórico auditados; proteger arquivos em uso; aprovação do perfil vinculada a hash/revisão. EXE não tem parâmetros silenciosos universais. A instalação só é sucesso com detecção real, nunca apenas ACK/exit code.
- Stable deve alcançar **100% dos desktops físicos Windows elegíveis no canal stable**, gradualmente, inclusive novos e offline quando voltarem; excluir servidores, VMs de teste e grupos protegidos. `development` para VM piloto/testes. Pausa/cancelamento, assinatura, identidade, versão e saúde permanecem necessários.
- Políticas futuras: Windows Update de qualidade/segurança com respeito a GPO/WSUS, limpeza controlada de disco, condições locais, agenda e opção `Se necessário reiniciar, como será feito?`; escopo futuro por AD de **contas de computadores**, OU e grupos; Bitdefender como proteção especializada; impressoras via FollowMe.
- Reutilizar motor Django `AgentJob`, pull/ACK, auditores e worker .NET. Não criar outros schedulers/transports ou executar shell arbitrário.
- Preservar funcionalidades previamente autorizadas a cada nova RC e validar delta, não recomeçar homologações desnecessariamente. Evitar backups/checkpoints repetidos em mudanças pequenas; reservar para intervenções de grande impacto real.
- Nosso ambiente está em homologação; desenvolvimento por branch/PR e validação automatizada, mas **nenhum merge, deploy, alteração de flags, publicação de agentes ou execução real sem autorização específica**. Não confundir CI sintético com homologação em VM.
- Os dois Codex trabalham em paralelo: **Codex 1 = Stable / 07B**; **Codex 2 = Central de Software / 03C**. À medida que essas frentes encerrarem, negociar incrementos seguintes, sem iniciar tudo de uma vez.
- O usuário quer respostas diretas, decisões explicadas e prompts completos executáveis. Uma implementação autorizada deve ir até PR/CI, não parar após migrations parciais.

## 4. O que me entregar neste novo chat

Apresente um **quadro atual de RC49** com itens integrados, PRs abertas e o que falta; destaque conflitos técnicos atuais e a sequência recomendada dos merges. Separe claramente o próximo trabalho do Codex 1 e do Codex 2, dependências de migrations e possíveis testes necessários.

Depois, indique **a próxima decisão que precisamos tomar** e proponha um pequeno plano para cada frente. Não abra PRs, altere código, publique releases, faça deploy ou acione endpoints nesta primeira resposta; nossa primeira tarefa é sincronizar entendimento e retomar com confiança.

Se algo no GitHub contradisser este prompt, **priorize o estado verificado do repositório**, explicite a mudança e atualize a análise.
