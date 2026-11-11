# RC47-07B — Terminal administrativo

## Estado de autorização (RC47-07C)

O modo administrativo continua desligado por padrão e agora possui autorização independente do ConPTY assistido:

- `TerminalAccessGrant.allow_conpty` autoriza somente o modo `conpty`.
- `TerminalAccessGrant.allow_admin_auth` autoriza somente `admin_auth` e tem default `False`.
- Além da permissão geral `agents.open_terminalsession`, `admin_auth` exige a permissão Django explícita `agents.open_admin_terminalsession`, a flag do servidor, o grant ativo/não expirado para o operador e endpoint exatos, endpoint com identidade única e capability recente `remote_terminal_admin_auth:1`.
- A permissão administrativa deve estar atribuída diretamente ao operador ou a um grupo. Superuser/staff não a recebem implicitamente para esse gate. A migration aditiva não atribui permissões a usuários ou grupos.
- O mesmo gate é reavaliado na criação e durante a sessão/WSS; revogar/desabilitar/expirar o grant ou perder identidade/capability bloqueia a próxima validação. A permissão de ConPTY não é substituta nem fallback.

Esta alteração não habilita flags, grants, agente, backend de execução ou política de consentimento do ConPTY. Não altera o agente Windows. O modo assistido RC47-07A permanece separado.

## Fluxo auditado

1. Django autoriza usuário técnico, permissão, grant por operador/endpoint, endpoint ativo, identidade `machine_id` única, capability do agente e flag `NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED`.
2. A sessão guarda operador, endpoint, `machine_id`, hash da identidade do agente, modo, expiração e estado. Os tickets são hashes no banco, de uso único e com validade curta. O payload de credenciais não é persistido nem incluído na auditoria.
3. O relay WSS autenticado encaminha `windows_auth` em memória entre navegador e agente; impõe TLS, sessão viva, expiração, limite de mensagens e buffers. Não registra o conteúdo do frame.
4. O agente usa `LogonUser`, valida token de administrador/elevado e recusa SYSTEM/service SIDs; inicia o processo com esse token e compara SID, sessão e Logon ID do processo filho. Não substitui a identidade por SYSTEM.
5. O agente copia a senha para buffer nativo temporário e zera o `char[]` e o buffer nativo. O fluxo Python/WSS e o navegador ainda manipulam representações transitórias imutáveis na memória; não existe garantia de zeragem física dessas cópias gerenciadas.

## Mudanças de UX e credenciais

- O botão abre diretamente o modal de credenciais. Cada sessão começa com os campos vazios; não há lista de usuários nem opção para lembrar.
- Ao iniciar, os campos são limpos. O estado de conexão aparece imediatamente; a identidade exibida só vem do `process_ready` validado pelo agente. Falha, cancelamento, fechamento, expiração/desconexão e navegação limpam campos e referências mantidas pela UI.
- Na primeira carga, são removidas apenas chaves legadas com prefixo `nightowl.terminal.accounts.` de `localStorage`. Outras chaves não são enumeradas para leitura nem alteradas. A UI nova não grava credenciais em armazenamento web, URLs, eventos de métrica ou mensagens de erro.
- `autocomplete="off"`/`new-password` e atributos de exclusão de autofill desencorajam preenchimento por gerenciadores, mas o site não controla completamente extensões/gerenciadores do navegador. O JavaScript também não consegue garantir apagamento físico de strings imutáveis da memória; a garantia é ausência de persistência pela aplicação e descarte rápido das referências.
- O terminal usa xterm com scrollback limitado a 2.000 linhas, OSC 52 bloqueado, cópia por seleção/atalho/menu, colagem sem Enter por padrão e ação explícita de colar/executar (com confirmação para conteúdo multilinha). Ctrl+C permanece sob o tratamento padrão do xterm como interrupção remota.

## Limitações e gate de homologação

- O protocolo atual não emite um ACK positivo separado entre aceitação de credenciais e `process_ready`. A UI mostra “Autenticando no Windows” até a confirmação do agente e “Iniciando terminal” somente após `ready`, durante a montagem local do xterm; não inventa confirmação intermediária.
- As métricas `nightowl:terminal-metric` são eventos transitórios no DOM com etapa, duração e ID de sessão, sem credenciais, identidade digitada, comandos ou saída. Esta alteração não instala um consumidor nem persiste as métricas; portanto não fornece percentis reais de latência.
- Não foi feita homologação de credenciais válidas/elevação/ConPTY administrativo em uma estação Windows real. Antes de habilitar `admin_auth`, validar uma VM autorizada, a conta efetivamente usada, identidade do processo, expiração/revogação e encerramento. Não habilitar em produção nesta PR.
- O navegador envia a credencial ao processo Django pelo canal WSS terminado no servidor para que o agente a receba. O servidor precisa ser tratado como parte da fronteira de confiança durante esse trânsito, embora não persista nem registre o segredo. Uma arquitetura que remova essa exposição exigiria desenho separado (por exemplo, credencial cifrada ponta a ponta ao agente), fora deste escopo.
