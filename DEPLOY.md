# Night Owl - Deploy Linux

Este guia prepara o Night Owl para rodar em um servidor Linux com Django, Gunicorn e Nginx. Ele ainda nao configura systemd, Nginx, HTTPS ou PostgreSQL em producao; esses passos ficam para a etapa de infraestrutura.

## 1. Clonar o repositório

```bash
sudo mkdir -p /opt/nightowl
sudo chown "$USER":"$USER" /opt/nightowl
cd /opt/nightowl
git clone <URL_DO_REPOSITORIO> .
```

## 2. Criar ambiente Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configurar variáveis de ambiente

```bash
cp .env.example .env
nano .env
```

Edite pelo menos:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DATABASE_URL` ou as variáveis `POSTGRES_*`, se optar por PostgreSQL

O arquivo `.env` contem segredos e nao deve ser versionado.

## 4. Validar Django

```bash
python manage.py check
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 5. Testar com Gunicorn

```bash
gunicorn --bind 127.0.0.1:8010 config.wsgi:application
```

Depois, em outro terminal do servidor:

```bash
curl http://127.0.0.1:8010/
```

## 6. Static e media

O projeto está configurado com:

- `STATIC_URL=/static/`
- `STATIC_ROOT=BASE_DIR/staticfiles`
- `MEDIA_URL=/media/`
- `MEDIA_ROOT=BASE_DIR/media`
- Whitenoise para servir static files de forma simples quando aplicável

Em produção com Nginx, o ideal é mapear:

```nginx
location /static/ {
    alias /opt/nightowl/staticfiles/;
}

location /media/ {
    alias /opt/nightowl/media/;
}
```

## 7. Rotinas de manutenção

O comando central de rotinas operacionais pode ser executado manualmente:

```bash
python manage.py run_maintenance_tasks
```

Agendamento real fica para etapa futura. Exemplos futuros:

Linux cron:

```cron
*/5 * * * * /opt/nightowl/.venv/bin/python /opt/nightowl/manage.py run_maintenance_tasks
```

Windows Task Scheduler:

```powershell
python manage.py run_maintenance_tasks
```

## 8. Publicar downloads do agente Windows

Depois de alterar o agente .NET, o instalador ou gerar um novo pacote em:

```bash
/opt/nightowl/NightOwl.Agent.Windows/publish/downloads/agent/windows/
```

publique os arquivos estáticos servidos pelo Nginx com:

```bash
sudo /opt/nightowl/scripts/publish-nightowl-agent-downloads.sh
```

Na primeira instalação do script no servidor:

```bash
sudo mkdir -p /opt/nightowl/scripts
sudo cp scripts/publish-nightowl-agent-downloads.sh /opt/nightowl/scripts/publish-nightowl-agent-downloads.sh
sudo chmod +x /opt/nightowl/scripts/publish-nightowl-agent-downloads.sh
```

O script copia para `/opt/nightowl/downloads/agent/windows/`, valida os arquivos obrigatórios, ajusta `www-data:www-data`, permissões, checksum do ZIP e testa as URLs locais. Ele não reinicia `nightowl.service` nem altera Nginx.

## 9. Próximos passos de infraestrutura

Ainda ficam para uma próxima etapa:

- unit file do `systemd` para Gunicorn
- socket ou service do Gunicorn
- configuração real do Nginx
- HTTPS com certificado
- PostgreSQL real no servidor
- backup do banco e da pasta `media`
- timer de manutenção em produção
