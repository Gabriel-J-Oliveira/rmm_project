@echo off
setlocal

cd /d "%~dp0"
uv run python manage.py runserver 0.0.0.0:8000
