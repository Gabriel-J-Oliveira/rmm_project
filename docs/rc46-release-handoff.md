# RC46 — handoff para implantação manual

A base anterior da `main` é `0d5d8ceb28928b6a580cdff15a5144b373093296`.
A PR de `integration/rc46` identifica o commit consolidado e o SHA resultante
do merge. A integração inclui agente, terminal, Fast Install e FRONT-01–06;
a validação local não substitui homologação nas VMs. Instalação GPO/SYSTEM
continua NO-GO. A distribuição self-contained convencional foi preservada.

## Backend e WebSocket

Antes do deploy, confirmar backup restaurável do banco e dos arquivos, o SHA
em execução, permissões e o plano de reversão. Os passos abaixo são para o
operador executar posteriormente em `/opt/nightowl`, conforme a documentação
existente; nenhum foi executado nesta tarefa. Conferir a lista de migrações e
dependências entre o SHA anterior e o merge antes de executá-las.

```bash
cd /opt/nightowl
git fetch origin main
git checkout main
git pull --ff-only origin main
source .venv/bin/activate
pip install -r requirements.txt
python manage.py showmigrations --plan
python manage.py migrate
python manage.py check
python manage.py collectstatic --noinput
sudo systemctl restart nightowl
sudo systemctl status nightowl --no-pager
```

O procedimento antigo Gunicorn/WSGI não garante WebSockets. O terminal requer
um servidor ASGI com suporte a WebSocket e **um processo/event loop** para os
dois lados da sessão; o relay é local ao processo. `config.asgi:application`
é a entrada. Um exemplo de processo único em ambiente preparado é
`python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000 --workers 1`;
Uvicorn não está incluído em `requirements.txt`, e a instalação e a unidade
systemd correspondentes dependem de decisão e configuração no servidor.
Nginx precisa encaminhar Upgrade/Connection e manter timeout WebSocket adequado,
com TLS válido em WSS; não há configuração de produção verificada aqui. Não
ativar o terminal antes de testar essa implantação e suas flags. As flags de
ações, scripts, terminal e rollout automático são desabilitadas por padrão.

Em falha, interromper o tráfego, voltar ao SHA anterior e restaurar artefatos
estáticos e, se necessário, o backup consistente do banco. Não executar
reversão genérica de migrações: avaliar reversibilidade e dados novos antes.
Executar `check`, retomar o serviço e validar comunicação de agentes legados.

## Release do agente

O builder usa assinatura RSA-PSS/SHA-256 e trust configurado. Não foram gerados
chaves ou artefatos, nem verificada uma configuração de assinatura de produção.
Depois de autorização específica, fornecer chave existente fora do repositório,
identificador, bundle público e raízes confiáveis correspondentes, além de URL
HTTPS válida e acesso autorizado. Exemplo a executar separadamente no PowerShell:

```powershell
.\scripts\Publish-NightOwlAgentRelease.ps1 -Version '0.1.1.0-rc46' -Channel development -Rollout 0 -Paused $true -SigningKeyPath '<chave-privada-existente>' -SigningKeyId '<id>' -TrustedPublicKeysPath '<bundle-publico>' -TrustRootsPath '<raizes-trust>' -PublicBaseUrl '<url-https-real>' -RemoteAlias '<alias-autorizado>'
```

Não usar `-AllowUnsignedDevelopment`, `-Force` ou opções de promoção. O builder
declara `force=false`, rollout 0 e pausado; o importador deriva `mandatory` de
`mandatory` ou `force`, ambos ausentes/falsos nesse manifesto. Após importar,
verificar também `mandatory=false`, assinatura válida, `legacy_unsigned=false`,
canal development, status pausado, percentual 0 e hash. O script valida vários
desses itens, mas não inclui assertion explícita de `mandatory`. Development
não atualiza stable/latest; conferir o estado antes e depois. A instalação manual
com release explicitamente selecionada continua sujeita às verificações de
elegibilidade e trust. Não houve publicação nesta tarefa.

## Smoke e laboratório

Após deploy autorizado: verificar serviço e logs, migrações e `check`; confirmar
heartbeat novo e identidade de agente antigo e RC46, inventário e telemetria,
job e ACK; verificar WSS e autorização com ASGI único. Não considerar online
como prova de heartbeat novo. Manter alterações administrativas desabilitadas
até habilitação controlada.

Em VMs isoladas e autorizadas: Windows 11 limpo — instalação manual silenciosa,
serviço, Tray e heartbeat; Windows 11 com AD e agente — no-op, update, repair,
falha e rollback; Windows Server 2019 com AD — instalação/recuperação WinRM.
Validar inventário, telemetria, ações e scripts aprovados. Testar terminal
ConPTY com conta administrativa real de laboratório, identidade, I/O e limpeza
de processos; os testes sintéticos não comprovam esse fluxo. Registrar logs e
restaurar snapshots conforme o procedimento do laboratório. WinRM exige validar
certificado, autenticação, portas e compatibilidade de transporte do ambiente.
