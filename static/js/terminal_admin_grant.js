(() => {
    const form = document.querySelector('[data-admin-grant] form');
    if (!form) return;
    const status = form.querySelector('[role="status"]');
    const buttons = [...form.querySelectorAll('button[type="submit"]')];
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const action = event.submitter?.value;
        if (!['grant', 'revoke'].includes(action)) return;
        const {machine_id, reason, approval_ref, ttl_minutes, beneficiary, csrfmiddlewaretoken} =
            Object.fromEntries(new FormData(form));
        const data = {action, machine_id, reason, approval_ref, beneficiary};
        if (action === 'grant' && ttl_minutes !== undefined) data.ttl_minutes = Number(ttl_minutes);
        buttons.forEach(button => { button.disabled = true; });
        status.textContent = '';
        try {
            const response = await fetch(form.action, {
                method: 'POST', credentials: 'same-origin', redirect: 'error',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfmiddlewaretoken},
                body: JSON.stringify(data)
            });
            if (!response.ok) {
                status.textContent = response.status === 403 ? 'Operação não autorizada.' :
                    'Não foi possível alterar a autorização. Confira os dados e o estado do piloto.';
                return;
            }
            const grant = await response.json();
            status.textContent = grant.enabled ?
                (grant.expires_at ? 'Autorização concedida até ' + new Date(grant.expires_at).toLocaleString() :
                    'Autorização persistente concedida.') :
                'Autorização revogada.';
            form.reset();
        } catch (_) {
            status.textContent = 'Não foi possível confirmar a operação.';
        } finally {
            buttons.forEach(button => { button.disabled = false; });
        }
    });
})();
