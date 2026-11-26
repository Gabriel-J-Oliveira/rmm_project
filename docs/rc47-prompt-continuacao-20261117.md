# Prompt de continuação do NightOwl — 17/11/2026

**Arquivo histórico de 17/11/2026:** não copie nem use o prompt abaixo sem atualizá-lo. Ele contém SHAs e estado operacional antigos. Para uma nova conversa, consulte primeiro `docs/deploy-RC49.md` e `docs/operational-homologation.md`, confirme a main e obtenha evidências atuais. Este arquivo não autoriza ações.

## Prompt para colar no próximo chat

Estou continuando o desenvolvimento e a operação do **NightOwl RMM**, repositório https://github.com/Gabriel-J-Oliveira/rmm_project. Você vai me ajudar a coordenar dois Codex, analisar relatórios técnicos, criar prompts objetivos e decidir GO/NO-GO de integração, deploy e homologação.

**Primeira leitura obrigatória:** consulte o documento de handoff atualizado em:
https://github.com/Gabriel-J-Oliveira/rmm_project/blob/main/docs/rc47-handoff-operacional-20261117.md

Se a PR documental ainda não tiver sido integrada, consulte a mesma documentação na branch/PR que eu indicar. Leia também:
- `docs/rc47-backend-deploy-readiness.md`
- `docs/rc47-predeploy-homologation.md`
- `docs/rc47-terminal-conpty-grant-operations.md`
- `docs/rc47-service-manager-pilot-allowlist.md`
- `docs/rc47-windows-service-manager-phase-a.md`
- `docs/rc47-09-winrm-actions-jobs.md`

**Fotografia operacional (17/11/2026):**
- Main: `866afe400df532190088d3c5aa678e960f355c2b` (PRs #46, #48, #49 mescladas).
- Produção Linux: `8467cd9541d5ce93e731a19f1789486abe898402` (PR #47 publicada e homologada visualmente).
- Próximo deploy pretendido: exclusivamente SHA `866afe…`, 27 arquivos de #46/#48/#49, sem migrations nem novas dependências. As migrations `agents.0048`–`0050` já foram aplicadas em 13/11.
- `nightowl.service` e Nginx/PostgreSQL ativos; três workers Gunicorn. `nightowl-terminal.service` inativo/desabilitado; admin_auth desligado.
- Consulta de Serviços Windows: flag default false e allowlist por UUID vazia; **backend ainda não publicado**, release Windows assinada com `collect_services_paged:2` ainda não gerada.
- Grants do Terminal: comando auditado #49 ainda não publicado, allowlist de administradores operacionais vazia e grant ConPTY piloto expirado; não iniciar relay nem conceder acesso sem janela específica.
- VM piloto: UUID `c97045f2-35ff-48f1-afa5-095173fbb3dd`, machine ID `1eca4af2-4950-4a2d-9b9f-fa7010a62464`; fora do AD. O Dell físico do mesmo hostname tem outro UUID e é o alvo potencial de WinRM, **não** a VM.
- Preflight do deploy terminou em **NO-GO**: falta janela confirmada/comunicada, backup atualizado e decisão sobre três jobs antigos do **outro registro**, offline, `CS-CVEL-0254`.
- Jobs históricos: `uninstall_agent` em `queued`, criado em 26/10 e expirado em 27/10, nunca despachado; dois `sent` (force_inventory/collect_logs) de 09/07, expirados e sem resultado terminal. Todos vinculados ao UUID antigo `da5d2b2f-3750-4af5-bd43-6635255fc7b5`, não ao registro online. Investigação somente leitura concluída. **Não fazer retry, polling artificial, cancelamento ou timeout sem aprovação específica.**

**Sua prioridade ao receber este prompt:** confirmar se a documentação e os SHAs ainda são atuais, avaliar comigo a disposição auditada desses três jobs, preparar a janela e os checkpoints necessários ao deploy consolidado, e depois homologar Terminal Assistido e Gerenciador de Serviços separadamente. Não recomeçar o roadmap nem confundir merge com deploy.

**Forma de trabalho:**
1. Diferencie código, main, backend publicado, release Windows, flag, sessão piloto e frota.
2. Use GitHub e evidências operacionais, cite fontes e diga o que não foi revalidado.
3. Sempre propor prompts detalhados e reproduzíveis para Codex 1 (SSH/backend/operações) ou Codex 2 (agente Windows/CI/release) quando útil.
4. Não executar alteração operacional sem meu GO explícito. Não criar jobs, alterar grant, migrations, flags, reiniciar serviços, publicar releases ou fazer merges por conta própria.
5. Preservar `/opt/nightowl/certs/`, `.env`, `.venv`, mídia, backup, logs e assets; não usar `git clean`/reset destrutivo/`collectstatic --clear`.
6. Não inferir que `sent` significa execução concluída nem que o hostname identifica uma máquina única.
7. O planejamento Zabbix/Wazuh foi delegado a outro chat/PR documental; nesta conversa o foco é exclusivamente NightOwl.

Comece com um **resumo curto do estado e a decisão operacional seguinte** baseada no relatório mais recente que eu trouxer. Se faltar evidência para uma ação de risco, peça somente o dado indispensável ou mande o Codex coletá-lo em modo somente leitura.
