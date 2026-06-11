# Night Owl - ambiente de desenvolvimento

Este projeto ainda mantem compatibilidade com `requirements.txt` e com a `venv` tradicional ja existente.

## Opcao atual: venv tradicional

```powershell
.\venv\Scripts\activate
python manage.py check
python manage.py runserver 0.0.0.0:8000
```

## Opcao incremental: uv

Se o `uv` ainda nao estiver instalado no Windows, instale sem exigir privilegios administrativos:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Depois feche e abra o terminal novamente, ou atualize o `PATH` da sessao conforme indicado pelo instalador.

Verifique:

```powershell
uv --version
```

Crie o ambiente gerenciado pelo `uv`:

```powershell
uv venv
```

Instale as dependencias a partir do arquivo atual:

```powershell
uv pip install -r requirements.txt
```

Valide o projeto:

```powershell
uv run python manage.py check
```

Aplique migrations:

```powershell
uv run python manage.py migrate
```

Rode o servidor acessivel pela rede local:

```powershell
uv run python manage.py runserver 0.0.0.0:8000
```

## Observacoes

- A pasta `venv/` atual nao precisa ser apagada.
- O banco `db.sqlite3` nao deve ser apagado.
- Esta etapa nao migra o projeto para `pyproject.toml`; o arquivo `requirements.txt` continua sendo a fonte de dependencias.

## Rotinas de manutencao operacional

Execute manualmente as rotinas centrais do Night Owl com:

```powershell
python manage.py run_maintenance_tasks
```

Modo de validacao sem gravar alteracoes nas rotinas que suportam dry-run:

```powershell
python manage.py run_maintenance_tasks --dry-run
```

Executar apenas uma rotina:

```powershell
python manage.py run_maintenance_tasks --only evaluate_software_policies
```

Pular uma rotina:

```powershell
python manage.py run_maintenance_tasks --skip detect_changes
```

Exemplos futuros de agendamento, ainda sem implementar nesta fase:

Windows Task Scheduler:

```powershell
python manage.py run_maintenance_tasks
```

Linux cron:

```bash
*/5 * * * * /path/to/venv/bin/python manage.py run_maintenance_tasks
```

Producao futura:

- systemd timer
- Celery beat
- supervisor
