(() => {
    'use strict';
    const root = document.querySelector('[data-endpoint-detail]');
    const form = root && root.querySelector('[data-winrm-check]');
    if (!form) return;
    const output = root.querySelector('[data-winrm-remote-result]');
    const button = form.querySelector('button[type="submit"]');

    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (window.location.protocol !== 'https:' || button.disabled) return;
        button.disabled = true;
        if (output) output.textContent = 'Verificando TCP e WS-Man...';
        document.dispatchEvent(new CustomEvent('nightowl:winrm-checking'));
        try {
            const response = await fetch(form.action, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'X-CSRFToken': form.elements.csrfmiddlewaretoken.value, 'Accept': 'application/json'}
            });
            const data = await response.json();
            if (!response.ok) {
                const code = typeof data.error_code === 'string' ? data.error_code : 'CHECK_UNAVAILABLE';
                if (output) output.textContent = `Verificação falhou: ${code}.`;
                document.dispatchEvent(new CustomEvent('nightowl:winrm-checked', {detail: null}));
                return;
            }
            document.dispatchEvent(new CustomEvent('nightowl:winrm-checked', {detail: data}));
            if (output) output.textContent = `TCP ${data.tcp}; WS-Man ${data.wsman}; autenticação ${data.authentication}; última verificação ${data.checked_at || 'sem horário informado'}. ${data.error || ''}`;
        } catch (_) {
            if (output) output.textContent = 'Verificação sem resposta. Consulte a conectividade HTTPS do NightOwl e tente novamente mais tarde.';
            document.dispatchEvent(new CustomEvent('nightowl:winrm-checked', {detail: null}));
        } finally {
            button.disabled = false;
        }
    });
})();
