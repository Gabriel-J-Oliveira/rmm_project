# RC46 — fechamento e handoff para RC47

## Estado verificado

- **APROVADO:** PR #15 foi integrada anteriormente; o projeto declara Uvicorn e `websockets` para o relay ASGI de processo único, e o teste local do handshake não autenticado retornou 403. Em 07/11/2026, a PR #21 foi integrada na `main` (`1f50a8b396d31d8dd313c4e91eee5cdfb96ddf9f`), seguida pela PR #16 PATHEXT (`133ef73f41a496daff05007ce31f01f3fc999754`) e pela PR #22 Ações e Jobs (`bde4a740757ee54597e42a7eaed887f905b79bce`).
- **NÃO HOMOLOGADO:** a investigação de `Get-Date` em ConPTY restrito no runner Windows está na [issue #19](https://github.com/Gabriel-J-Oliveira/rmm_project/issues/19), com testes e diagnósticos preservados em `codex/rc46-conpty-get-date-investigation`, commit `428b18e57ad4edb1c90e9715d8fc15d92025a796`. O cmdlet falhou em sessões isolada e sequencial no CI, mas passou localmente; sua causa permanece desconhecida e é independente da alteração de PATHEXT da PR #16. O terminal assistido **não está homologado para distribuição ampla** até a investigação e os testes operacionais pendentes serem concluídos. A alteração no agente requer nova release Windows assinada; não substituir a RC46 existente.
- **APROVADO:** conforme evidência operacional informada pelo operador, na VM RC46 houve sessão WSS/ConPTY assistida com consentimento Tray, identidade não elevada, entrada/saída e Ctrl+C. Isso não é homologação do modo administrativo.
- **APROVADO:** conforme resultado informado pelo operador, `query_service`, `environment_diagnostic` e `flush_dns` concluíram na VM piloto, cada um com ACK único. Heartbeat e monitoramento ao vivo receberam dados reais.
- **NÃO HOMOLOGADO:** modo `admin_auth`, encerramento de filhos em todos os caminhos, revogação e logout em infraestrutura real; validar com conta de laboratório autorizada. GPO/SYSTEM permanece NO-GO.
- **NÃO HOMOLOGADO:** WinRM em endpoint elegível do domínio com certificado confiável, allowlists e autenticação. Testes de preparação do firewall e política são simulados; não representam alteração ou homologação de AD.
- **NÃO HOMOLOGADO:** a verificação da CA e do certificado do upstream pelo Nginx Proxy Manager exige evidência operacional. Certificado público válido e certificado interno renovado até 2028 foram informados pelo operador; verificar cadeia, nome e política do NPM no ambiente.

## Validação local e riscos

No Windows local: dez E2Es reais sem skips em três execuções (Chrome e ConPTY; clique de consentimento simulado), 38 testes Django de terminal, 75 testes focados de ações, telemetria e instalação; build .NET e testes simulados do updater/rollback, além de testes PowerShell 5.1 de no-op, staging, assinatura inválida e WinRM simulado. `manage.py check`, verificação de migrations e `git diff --check` passaram. Esses resultados locais não substituem os gates atuais das PRs integradas.

A PR #16 foi limitada ao PATHEXT, aos dez E2Es existentes e ao teste adicional de executáveis e isolamento. Seu HEAD `36c61881af5d17078cd75e6a04bc5b07c2e567f5` passou os cinco checks atuais, incluindo Windows .NET/terminal E2E, deployment contract, navegador isolado, SQLite e PostgreSQL. Foi integrada pela merge commit `133ef73f41a496daff05007ce31f01f3fc999754`. Isso integra o código, mas não atualiza por si só agentes instalados: ainda é necessária uma nova release Windows assinada e homologada.

A [PR #21](https://github.com/Gabriel-J-Oliveira/rmm_project/pull/21) integrou o Chrome for Testing `156.0.8078.80` fixado e verificado por SHA-256 no CI Windows (merge `1f50a8b396d31d8dd313c4e91eee5cdfb96ddf9f`). Três execuções independentes passaram com os dez E2Es e uma verificação após rebase também passou. O smoke real, a conta não administrativa e a sonda CDP permanecem obrigatórios. A causa interna da falha histórica do Chrome pré-instalado não foi identificada; não há alegação de correção desse executável.

A PR #22 reorganizou a aba Ações e Jobs e integrou visualmente os fluxos existentes de WinRM, terminal, categorias de ações e histórico, preservando gates de autorização. O HEAD `063ea31c36ea73aa75c33e3e5bdf53cf65c4a9cc`, após atualização sobre a `main`, passou os cinco checks (SQLite, PostgreSQL, Windows deployment contract, browser comparison e Windows .NET/terminal E2E) e foi integrada pela merge commit `bde4a740757ee54597e42a7eaed887f905b79bce`. A publicação em produção ainda precisa de deploy e verificação operacional.

A investigação de `Get-Date` permanece independente e a issue #19 continua aberta. O terminal assistido continua sem homologação para distribuição ampla até concluir essa investigação e os testes operacionais pendentes. As PRs #16 e #22 estão integradas; seus efeitos no agente instalado e na interface de produção dependem, respectivamente, de nova release assinada/homologada e do deploy backend/frontend.

## Operação, reversão e próxima release

Antes de qualquer deploy autorizado: confirmar SHA implantado, backup restaurável do banco e arquivos, migrações aplicáveis e versão das dependências. Manter Gunicorn para HTTP e apenas um processo ASGI para o relay; não compartilhar sessões entre workers. Para reverter o relay, encerrar sessões e desativar grants/flags temporários após a janela de piloto, restaurar a rota Nginx anterior, validar `nginx -t`, recarregar conforme o procedimento autorizado e parar apenas o serviço ASGI dedicado. Para reverter o backend, restaurar código, dependências e banco somente segundo backup e plano compatíveis, sem presumir reversibilidade de migrations.

Uma nova release Windows deve conter PATHEXT integrado, versão nova, assinatura e trust válidos, manifesto e hashes verificados, e passar instalação, atualização, repair e rollback em laboratório antes de qualquer rollout. Não alterar o padrão do instalador nem converter automaticamente a fila legada de telemetria.

## Baseline RC47

RC47-00 permanece uma auditoria de coletores, sem execução nesta etapa. Preservar o planejamento RC47-01 a RC47-07 em `docs/deploy-RC47.md`, incluindo migração controlada para `continuous` e scroll, seleção/cópia, colagem por botão direito, execução explícita e Ctrl+C no frontend do terminal. Consultar também `docs/rc46-integration.md`, `docs/rc46-release-handoff.md`, `docs/rc46-dec04d.md` e `docs/rc46-terminal-winrm-piloto.md`.
