# RC47 — contenção dos logs JSONL

O sidecar `.lock` é mantido aberto durante expiração de arquivos, inspeção da cauda, rotação e escrita. A implementação anterior aguardava 40 intervalos de 10 ms e repetia qualquer `IOException`. Em Windows local, cinco testes com um escritor segurando o lock por 700 ms produziram erro de compartilhamento 32 após 624–640 ms; o escalonamento tornou o tempo real maior que os 400 ms nominais. Isso reproduz a limitação, mas não comprova a causa exclusiva da falha original do publicador.

Com quatro threads e três processos concorrentes, rotação e leitor, cinco execuções locais verificaram 421 registros únicos cada. As esperas máximas observadas chegaram a 2.063 ms, incluindo contenção e escalonamento; p95 por grupo de concorrentes chegou a 252 ms. A maior rotação observada foi 6,03 ms e o trecho instrumentado da seção crítica, excluindo o fechamento, chegou a 15,06 ms. O bloqueio controlado durou pelo menos 800 ms. As métricas de espera são de relógio de parede e não isolam o tempo de CPU nem o atraso de agendamento. Não são promessa de latência em produção.

A nova política usa prazo monotônico de cinco segundos, com verificação antes de uma nova tentativa; o valor oferece margem sobre a espera máxima observada sem espera infinita. Apenas conflitos de compartilhamento Windows 32/33 (ou EAGAIN em plataformas Unix) são repetidos; outros erros propagam. O prazo pode ser ultrapassado pelo escalonamento ou por chamada do sistema, e não garante justiça entre processos. No prazo, uma `IOException` explícita mantém a falha original como causa. O sidecar permanece estável e `FileShare.None` é preservado.

A suíte mantém testes de acesso negado, disco cheio simulado, escrita parcial, sanitização, proteção de arquivos e fila pendente. O novo teste sincroniza threads e processos, usa leitor e rotação simultâneos, verifica todas as identidades sem perdas ou duplicações, cobre timeout curto injetado, prazo público de cinco segundos e aquisição posterior. O CI Windows executa o projeto Shared.Tests completo.

O publicador `-DryRun` exige a configuração e chave de assinatura autorizadas. Nesta máquina, a configuração padrão e as variáveis de assinatura não estavam disponíveis; a tentativa terminou em `validation_failed` por ausência de `SigningKeyId`, antes do builder. Não houve release publicada. Após o merge, executar na máquina de publicação configurada, com a árvore limpa e o trust existente:

```powershell
cd <caminho-do-repositorio>
git checkout main
git pull --ff-only
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\Publish-NightOwlAgentRelease.ps1 -Version '0.1.1.0-rc47' -DryRun
```

Não usar `-SkipTests` nem publicar automaticamente. Confirmar o resultado do manifesto e da assinatura no fluxo oficial antes da publicação autorizada separadamente.
