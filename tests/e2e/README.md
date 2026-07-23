# NightOwl Agent E2E Tests

Suite local e reproduzivel para validar os fluxos criticos do NightOwl Agent antes do piloto.

Comando seguro padrao:

```powershell
powershell -ExecutionPolicy Bypass -File tests\e2e\Test-NightOwlAgentE2E.ps1
```

Modos:

```powershell
# Seguro, sem backend externo e sem tocar na instalacao real
powershell -ExecutionPolicy Bypass -File tests\e2e\Test-NightOwlAgentE2E.ps1 -Mode Simulated

# Usa backend de teste. Nao use producao.
powershell -ExecutionPolicy Bypass -File tests\e2e\Test-NightOwlAgentE2E.ps1 -Mode Integration -BackendUrl "https://nightowl-dev.example" -EnrollmentToken "TOKEN_DE_TESTE"

# Apenas roteiro/guardrail para VM descartavel. Exige confirmacao explicita.
powershell -ExecutionPolicy Bypass -File tests\e2e\Test-NightOwlAgentE2E.ps1 -Mode WindowsVm -AllowDestructive
```

Saida:

```text
tests\e2e\reports\e2e-<timestamp>.json
tests\e2e\reports\e2e-<timestamp>.txt
```

Garantias do modo `Simulated`:

- usa diretorios temporarios;
- nao para/inicia servicos reais;
- nao executa uninstall/purge real;
- nao publica artefatos;
- nao usa token real;
- falha se o pacote oficial contiver arquivos proibidos ou se o Diagnostics vazar segredo ficticio.

O modo `WindowsVm` e reservado para uma VM Windows descartavel. Ele cobre instalacao limpa, enrollment, heartbeat, inventario, jobs, update, rollback, repair, uninstall normal, reinstall e purge. Nada disso deve ser executado automaticamente na maquina de desenvolvimento.
