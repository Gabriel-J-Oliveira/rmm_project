# RC46 — fechamento e handoff para RC47

## Estado verificado

- **APROVADO:** `main` inicial `138473d0473ee4aa37ed8ce57390487351ebef4d`. PR #15 integrada em `99a892daa44db157e80986b53886226618f6783b`; seus quatro checks passaram no head `c763d2b0e56a1ffc7c1bf43c27232daf8199a728`. O projeto declara Uvicorn e `websockets` para o relay ASGI de processo único; o teste local do handshake não autenticado retornou 403.
- **BLOQUEADO:** PR #16 no head `06aae505cd04da6766a7dcbeb601f5677118a31a` falhou no CI `42232091965`: SQLite, PostgreSQL e deployment passaram; no gate Windows, `whoami`, `hostname` e `ipconfig` passaram, mas a etapa `Get-Date` esgotou a espera de saída e o Chrome não abriu a porta CDP. O diagnóstico PowerShell passou. O teste local executou os quatro comandos e não herdou variável sintética do processo pai. A correção no agente depende de integração e nova release Windows assinada. Não substituir a release RC46 já assinada.
- **APROVADO:** conforme evidência operacional informada pelo operador, na VM RC46 houve sessão WSS/ConPTY assistida com consentimento Tray, identidade não elevada, entrada/saída e Ctrl+C. Isso não é homologação do modo administrativo.
- **APROVADO:** conforme resultado informado pelo operador, `query_service`, `environment_diagnostic` e `flush_dns` concluíram na VM piloto, cada um com ACK único. Heartbeat e monitoramento ao vivo receberam dados reais.
- **NÃO HOMOLOGADO:** modo `admin_auth`, encerramento de filhos em todos os caminhos, revogação e logout em infraestrutura real; validar com conta de laboratório autorizada. GPO/SYSTEM permanece NO-GO.
- **NÃO HOMOLOGADO:** WinRM em endpoint elegível do domínio com certificado confiável, allowlists e autenticação. Testes de preparação do firewall e política são simulados; não representam alteração ou homologação de AD.
- **NÃO HOMOLOGADO:** a verificação da CA e do certificado do upstream pelo Nginx Proxy Manager exige evidência operacional. Certificado público válido e certificado interno renovado até 2028 foram informados pelo operador; verificar cadeia, nome e política do NPM no ambiente.

## Validação local e riscos

No Windows local: dez E2Es reais sem skips em duas execuções (Chrome e ConPTY; clique de consentimento simulado), 38 testes Django de terminal, 75 testes focados de ações, telemetria e instalação; build .NET e testes simulados do updater/rollback, além de testes PowerShell 5.1 de no-op, staging, assinatura inválida e WinRM simulado. `manage.py check`, verificação de migrations e `git diff --check` passaram. O gate Windows da PR #16 permanece reprovado.

A execução histórica `42224344768` teve falhas em ConPTY, diagnóstico PowerShell e inicialização Chrome sem porta CDP. Os dados disponíveis não demonstram causa raiz comum nem permitem afirmar correção da intermitência. Preservar timeouts, testes obrigatórios e diagnósticos sanitizados; não usar aprovação do teste local como prova de estabilidade de runner.

## Operação, reversão e próxima release

Antes de qualquer deploy autorizado: confirmar SHA implantado, backup restaurável do banco e arquivos, migrações aplicáveis e versão das dependências. Manter Gunicorn para HTTP e apenas um processo ASGI para o relay; não compartilhar sessões entre workers. Para reverter o relay, encerrar sessões e desativar grants/flags temporários após a janela de piloto, restaurar a rota Nginx anterior, validar `nginx -t`, recarregar conforme o procedimento autorizado e parar apenas o serviço ASGI dedicado. Para reverter o backend, restaurar código, dependências e banco somente segundo backup e plano compatíveis, sem presumir reversibilidade de migrations.

Uma nova release Windows deve conter PATHEXT integrado, versão nova, assinatura e trust válidos, manifesto e hashes verificados, e passar instalação, atualização, repair e rollback em laboratório antes de qualquer rollout. Não alterar o padrão do instalador nem converter automaticamente a fila legada de telemetria.

## Baseline RC47

RC47-00 permanece uma auditoria de coletores, sem execução nesta etapa. Preservar o planejamento RC47-01 a RC47-07 em `docs/deploy-RC47.md`, incluindo migração controlada para `continuous` e scroll, seleção/cópia, colagem por botão direito, execução explícita e Ctrl+C no frontend do terminal. Consultar também `docs/rc46-integration.md`, `docs/rc46-release-handoff.md`, `docs/rc46-dec04d.md` e `docs/rc46-terminal-winrm-piloto.md`.
