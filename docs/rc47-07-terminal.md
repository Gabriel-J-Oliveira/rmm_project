# RC47-07 — Terminal assistido

Incremento de UX restrito ao terminal ConPTY assistido. O fluxo `admin_auth`, backend, grants, consentimento local, protocolo WSS e agente Windows não são alterados.

## Comportamento

- xterm mantém no máximo 2.000 linhas de scrollback. A roda, o trackpad e a barra nativa do xterm navegam o buffer; resize continua usando FitAddon. Ao encerrar ou desconectar, o terminal é descartado e o DOM da tela é limpo.
- A seleção continua sendo a seleção nativa do xterm. `Ctrl+Shift+C` e **Copiar** usam a Clipboard API; indisponibilidade/negação é informada na interface. A seleção copiada é limitada a 256 KiB e não é registrada.
- `Ctrl+Shift+V` e **Colar** inserem texto sem enviar Enter. Para evitar que quebras recebidas do clipboard executem linhas inadvertidamente, a colagem comum converte quebras em espaços. Caracteres de controle são rejeitados.
- **Colar e executar** é uma ação separada e envia um Enter. Conteúdo multilinha requer confirmação explícita antes de ser enviado. O conteúdo é limitado a 64 KiB e continua passando pelo `onData` e pelos limites/backpressure já existentes.
- Ctrl+C sem Shift não é interceptado e permanece o sinal de interrupção enviado pelo xterm. OSC 52 continua bloqueado.
- Ações de clipboard só ficam habilitadas após o evento `ready` de uma sessão ConPTY autorizada e consentida. Nada do terminal ou clipboard é salvo em storage do navegador, backend ou logs.

## Validação

`tests/frontend/test_endpoint_terminal_clipboard.cjs` cobre scrollback, clipboard aprovado/negado, limites, conteúdo multilinha, Ctrl+C, resize, limpeza e ausência de persistência usando mocks. O E2E existente `agents.test_terminal_browser` cobre navegador Chrome, agente .NET e ConPTY real em ambiente isolado; deve passar no runner Windows do CI. Essa suíte simulada não substitui homologação manual em Windows real.

## Pendências

Clipboard pode ser bloqueado pelas permissões do browser ou por contexto sem HTTPS; nesse caso a interface mostra o erro e a seleção continua disponível para uso normal do browser. A homologação final deve confirmar wheel/trackpad e cópia/colagem em uma sessão ConPTY Windows consentida. A issue #19 permanece fora do escopo.
