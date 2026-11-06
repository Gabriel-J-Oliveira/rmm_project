# RC46 — plano de laboratório: terminal assistido e WinRM

Estado da auditoria: o terminal assistido usa ConPTY, credenciais temporárias de uso único, autorização `TerminalAccessGrant` por operador/endpoint e aprovação local do Tray. O processo Windows é criado sob o token interativo explicitamente autorizado, com verificações de SID, sessão, integridade e privilégios; SYSTEM e elevação são rejeitados. O relay limita sessões, frames e buffers; logout, revogação, expiração e desconexão são tratados pelo ciclo de vida existente. Há testes locais/CI, mas **não há homologação positiva nessa VM através do proxy Linux**.

O relay `HUB` e seu owner são locais a um processo/event loop. O Gunicorn WSGI atual não aceita WebSocket. O servidor ASGI/WebSocket não está declarado em `requirements.txt`; `uvicorn`, `websockets` e `daphne` não estavam disponíveis no ambiente local desta revisão. Não foi instalado ou iniciado nenhum serviço. Verificar a disponibilidade e versão autorizada de um servidor ASGI com implementação WebSocket na venv Linux antes de seguir; não tratar o WSGI ou a flag como substituto. Testar a combinação efetivamente instalada; esta revisão não comprova comportamento de proxy nem disponibilidade no servidor.

## Desenho proposto: um relay, backend existente

Manter Gunicorn/Django/PostgreSQL e suas rotas HTTP existentes. Uma única instância ASGI `config.asgi:application` atende exclusivamente `/ws/terminal/`, no mesmo host, sem Redis ou Channels; ambos os peers de uma sessão devem alcançar esse único processo. O Nginx encaminha o restante do tráfego como já configurado. Não usar balanceamento ou reload com dois processos ASGI simultâneos; reiniciar o relay encerra sessões. O processo não deve aceitar conexões externas diretamente.

Exemplo **para revisão**, não aplicar sem confrontar `systemctl cat nightowl`, a venv, a unidade e o ambiente efetivos. `EnvironmentFile` é um marcador: usar o mesmo ambiente confiável do serviço Django, inclusive banco, segredo e configurações, sem copiá-los para o repositório. Validar que a porta está livre e definir os limites de acesso local da organização:

```ini
[Unit]
Description=NightOwl terminal ASGI pilot
After=network.target

[Service]
User=nightowl
Group=nightowl
WorkingDirectory=/opt/nightowl
EnvironmentFile=/CAMINHO/REVISADO/DO/AMBIENTE_EXISTENTE
ExecStart=/opt/nightowl/.venv/bin/uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
UMask=0077

[Install]
WantedBy=multi-user.target
```

Exemplo de bloco no **servidor virtual HTTPS que realmente termina TLS**, sem alterar a rota HTTP atual. Verificar antes a topologia OpenResty/Nginx, `Host`, certificados e o proxy confiável; não aceitar cabeçalhos de encaminhamento arbitrários de redes externas. Se TLS terminar antes deste Nginx, esta configuração não é automaticamente válida:

```nginx
location ^~ /ws/terminal/ {
    proxy_pass http://127.0.0.1:8765;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_buffering off;
    proxy_read_timeout 75s;
    proxy_send_timeout 75s;
}
```

O relay exige `scope.scheme=wss`, Host permitido, Origin HTTPS igual ao Host para o navegador e subprotocolo `nightowl-terminal.v1`. Confirmar com teste real que a versão ASGI instalada interpreta o proxy de loopback e gera `wss`; não confiar em cabeçalhos enviados diretamente por clientes. A confiança no hop local pressupõe host e usuários locais confiáveis: revisar o acesso à porta loopback antes do piloto. Nunca incluir tickets em URLs, logs ou evidências. Usar `nginx -t` antes de qualquer reload posteriormente autorizado. Confirmar que só a rota WebSocket mudou e que Gunicorn, heartbeat, inventário, telemetria e jobs continuam independentes.

## Requisitos do piloto assistido

1. Confirmar o endpoint por UUID `c97045f2-35ff-48f1-afa5-095173fbb3dd` e machine_id completo confiável; há outro registro com o mesmo hostname. O IP `192.168.104.65` não é identidade. Verificar versão, heartbeat e Tray na sessão Windows interativa.
2. Só após autorização operacional e análise da conta, preparar no backend os flags necessários para o modo assistido: `NIGHTOWL_TERMINAL_ENABLED`, `NIGHTOWL_TERMINAL_SINGLE_PROCESS`, `NIGHTOWL_TERMINAL_CONPTY_ENABLED` e `NIGHTOWL_TERMINAL_BROWSER_ENABLED`; mantê-los desativados até a janela de teste. `SINGLE_PROCESS` refere-se ao relay WebSocket único, não elimina os workers WSGI. Manter `NIGHTOWL_TERMINAL_ADMIN_AUTH_ENABLED=false`. As configurações de controle devem ser consistentes no WSGI e no ASGI.
3. Um operador técnico ativo precisa de `agents.open_terminalsession` e `TerminalAccessGrant` habilitado, não expirado, para esse UUID e com `allow_conpty=true`. A exclusão/revogação do grant deve fechar a sessão. Não criar grants ou alterar permissões nesta revisão.
4. No agente, confirmar em configuração efetiva e heartbeat: `remoteTerminalTransportEnabled`, `terminalConPtyEnabled`, `terminalAuthorizedUserSid`, `terminalWindowsSessionId` e shell fechado `powershell51`. Para serviço SYSTEM, o gate `terminalServiceConsentHomologated` também é necessário e só pode ser ligado após homologação expressa do consentimento. Esses ajustes são apenas um plano; nenhuma VM foi alterada. Verificar flags existentes, SID/sessão explícitos e capacidades `remote_terminal_transport:1` e `remote_terminal_conpty:1` no heartbeat novo. Não inferir autorização só pela presença da flag.
5. A identidade da sessão precisa ser interativa, não SYSTEM, não administrativa/elevada e sem privilégios perigosos conforme `TerminalUserIdentity.ValidateFacts`; o usuário deve aprovar no Tray. Não selecionar automaticamente usuário conectado nem contornar o gate. Se não houver identidade elegível ou o serviço não puder obter o token da sessão indicada, bloquear e registrar a falha sem alterar privilégios.

## Sequência de homologação autorizada posteriormente

- Antes: backup e rollback aprovados, teste local do servidor ASGI/WebSocket e Nginx, `nginx -t`, isolamento do acesso local e plano para interromper o relay. Validar WSS/Origin/Host/TLS e ausência de ticket em URL/logs, sem conteúdo do terminal em evidências.
- Permissão: operador e endpoint autorizados passam; outro operador, outro endpoint, grant expirado/revogado e replay falham. A abertura sem consentimento não inicia processo.
- Conexão: solicitar sessão, aprovar no Tray e confirmar SID, sessão e identidade não elevada antes da entrada. Registrar horários de solicitação, consentimento e pronto; coletar somente metadados.
- Operação: executar entrada e saída interativas simples, Unicode e resize; verificar saída incremental. Medir RTT de eco e tempo até primeira saída para comandos conhecidos, sem salvar conteúdo. Os eventos de frontend `nightowl:terminal-metric` trazem durações `request_to_authorized`, `input_to_next_render`, `enter_to_next_render` e `receive_to_render`; o próximo render pode ser eco, não prova de conclusão do comando. Só apresentar p50/p95 com amostras suficientes e identificadas como medições locais.
- Encerramento: cancelar, fechar navegador, logout, revogar grant e testar timeout/desconexão. Confirmar sessão encerrada, tickets inutilizáveis, Job Object, shell e filhos controlados finalizados; nova sessão requer nova aprovação. Testar carga mínima sem afetar heartbeat/jobs.
- Rollback: encerrar sessões e desabilitar flags do piloto; remover a rota específica de WebSocket após restaurar o bloco anterior, validar `nginx -t` e então recarregar Nginx conforme o procedimento operacional. Parar/desabilitar apenas o serviço ASGI piloto; manter Gunicorn e demais serviços. Confirmar que sessões antigas não retomam, APIs HTTP e agentes permanecem operacionais. Não reverter banco ou desfazer ações externas executadas no terminal sem plano próprio.

## Administrativo e WinRM

`admin_auth` é separado, com senha por sessão intermediada transitoriamente pelo backend confiável e validação do token administrativo efetivo; não usa o consentimento Tray do modo assistido. Manter bloqueado por padrão até homologação real dedicada com conta autorizada, proteção de credenciais, confirmação da identidade do processo e encerramento completo. Nenhuma credencial é necessária para este plano, e não há fallback SYSTEM.

WinRM não é transporte do ConPTY. Na VM workgroup, recuperação vinculada a identidade AD não se aplica. Em endpoint de domínio elegível: validar objeto AD ativo e único, SID/endpoint, FQDN e IP DNS correlacionados; listener HTTPS 5986, certificado com SAN do FQDN e cadeia confiável (`WINRM_CA_TRUST_PATH`); firewall restrito; teste TCP, WS-Man e autenticação separadamente. Um 401/403 ou TCP aberto não comprova autenticação. Recuperação exige credencial autorizada, verificação de identidade e heartbeat novo; não iniciá-la nesta revisão. O fallback HTTP 5985 é opt-in com allowlist explícita e não deve ser habilitado automaticamente. `PrepareWinRM` não cria listener nem inicia serviço; apenas prepara firewall quando os pré-requisitos estão presentes.

## Limites desta revisão

Sem acesso ao servidor Linux ou à VM, não se confirma servidor ASGI, proxy efetivo, flags, grants, identidade Windows ou latências reais. O procedimento acima é proposta sujeita à revisão operacional, não deployment. Nenhuma mudança funcional do agente foi necessária nesta preparação; eventual correção no executável, Tray ou ConPTY exigirá nova release Windows assinada e homologada, sem sobrescrever a RC46 atual.

O teste local nesta revisão usou processo Windows interativo não elevado e consentimento simulado; para 100 marcadores locais mediu p50 8,0 ms e p95 9,5 ms, sem rede. Isso não mede a VM, o Nginx ou WSS e não é previsão de produção. Os testes de contrato do backend e loopback não comprovam o servidor ASGI recomendado nem o proxy efetivamente implantado.
