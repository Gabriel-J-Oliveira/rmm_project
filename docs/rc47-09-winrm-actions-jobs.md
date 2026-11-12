# RC47-09 — WinRM em Ações e Jobs

## Auditoria

| Capacidade | Estado antes da PR | Evidência/limite |
|---|---|---|
| Teste remoto | Implementado | `winrm_connectivity` salva cada teste e separa TCP, WS-Man e autenticação. TCP aberto não confirma WS-Man nem autenticação. |
| Segurança do alvo | Implementada | Verifica endpoint gerenciado, FQDN/SID do AD, UUID do endpoint, correspondência de DNS com o IP registrado e limita tempo, resposta e frequência. |
| Recuperação | Implementada | Operador staff, endpoint offline elegível, identidade única, preflight WinRM, credencial temporária e heartbeat novo da mesma identidade. Resultado incerto falha fechado. |
| Estado local | Implementado | Exibe dados recebidos no inventário e sinaliza estado antigo; não substitui a verificação remota. |
| Histórico de operações | Parcial | Os registros existiam em `RemoteInstallJob`, mas o endpoint de status retornava somente a última recuperação, sem histórico de conectividade. |
| Interface | Parcial | Verificação e recuperação já estavam em Ações e Jobs. Faltava apresentar tentativas anteriores persistidas. |

## Alteração desta PR

O endpoint de status staff-only retorna no máximo dez registros persistidos de `winrm_connectivity` e `offline_recovery` daquele endpoint. A resposta contém estado, etapa, horário, operador, códigos seguros e os três resultados de conectividade permitidos. Não retorna o JSON bruto de diagnóstico ou credenciais. A consulta de “última recuperação” existente continua independente da lista limitada, preservando seu comportamento.

A aba Ações e Jobs apresenta essa lista, diferencia ausência de histórico de falha ao consultar o status e atualiza os registros depois de um teste. A lista é limitada visualmente e se ajusta à largura da tela. Nenhum botão ou mecanismo de execução foi duplicado. O cache estático do CSS foi invalidado.

## Segurança preservada

- Somente operador staff ativo pode consultar os registros ou solicitar as operações existentes.
- Nenhum destino, porta ou endereço é fornecido pelo navegador.
- A verificação remota continua limitada a HTTPS/5986 com cadeia confiável e alvo vinculado ao endpoint.
- Autenticação continua “não verificada” no teste de conectividade; somente o fluxo de recuperação pode validar credenciais.
- Senhas não entram no histórico, na resposta de status ou no armazenamento do navegador.
- Nenhum serviço, listener, firewall, GPO, flag ou endpoint real foi alterado ou acionado por esta PR.

## Homologação pendente

Em endpoint elegível de domínio Windows, confirmar em ambiente autorizado: teste TCP/WS-Man HTTPS, challenge sem inferir autenticação, recuperação com conta autorizada, confirmação por heartbeat novo, persistência do histórico, timeout/resultado incerto e permissões de operador. HTTP/5985 permanece sujeito à política explícita existente. Esses casos não foram executados contra endpoints reais nesta etapa.
