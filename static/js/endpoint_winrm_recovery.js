(() => {
    const form = document.querySelector('[data-winrm-recovery]');
    if (!form) return;
    const password = form.elements.password;
    const status = form.querySelector('[data-winrm-recovery-status]');
    const button = form.querySelector('button[type="submit"]');
    window.addEventListener('pagehide', () => { password.value = ''; });
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (window.location.protocol !== 'https:') {
            password.value = '';
            status.textContent = 'HTTPS é obrigatório para a recuperação.';
            return;
        }
        button.disabled = true;
        status.textContent = 'Solicitação em andamento. Aguarde a confirmação do backend.';
        const payload = JSON.stringify({username: form.elements.username.value,
            password: password.value, restart_running: form.elements.restart_running.checked});
        password.value = '';
        try {
            const response = await fetch(form.action, {method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value}, body: payload});
            const result = await response.json();
            if (result.status === 'RECOVERED') status.textContent = 'Agente reconectado; novo heartbeat confirmado.';
            else if (result.status === 'HEARTBEAT_TIMEOUT') status.textContent = 'Comunicação não confirmada no prazo.';
            else status.textContent = 'Recuperação não confirmada (' + String(result.error_code || result.status || 'indisponível') + ').';
        } catch (_) {
            status.textContent = 'Resultado indisponível; a solicitação pode ainda estar em andamento.';
        } finally {
            button.disabled = false;
        }
    });
})();
