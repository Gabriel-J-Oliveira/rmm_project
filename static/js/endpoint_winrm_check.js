(() => {
    const form = document.querySelector('[data-winrm-check]');
    if (!form) return;
    const output = document.querySelector('[data-winrm-remote-result]');
    const button = form.querySelector('button');
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (window.location.protocol !== 'https:') return;
        button.disabled = true;
        output.textContent = 'Verificação em andamento...';
        try {
            const response = await fetch(form.action, {method: 'POST', credentials: 'same-origin',
                headers: {'X-CSRFToken': form.elements.csrfmiddlewaretoken.value}});
            const data = await response.json();
            output.textContent = response.ok
                ? `TCP ${data.tcp}; WS-Man ${data.wsman}; autenticação ${data.authentication}; ${data.checked_at}. ${data.error || ''}`
                : `Verificação indisponível: ${data.error_code || 'erro'}`;
        } catch (_) { output.textContent = 'Verificação indisponível.'; }
        finally { button.disabled = false; }
    });
})();
