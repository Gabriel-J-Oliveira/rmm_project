(function () {
    'use strict';
    const BASE = '/agent-install/install-batches/';
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    const errors = {
        ACTION_NOT_AVAILABLE: 'A intervenção não está mais disponível. Atualizando o estado.',
        INSTALL_ALREADY_RUNNING: 'Já existe uma instalação em andamento. Aguarde sua conclusão.',
        TARGET_MANAGED_OR_CONFLICT: 'Um computador já está gerenciado ou tem correlação ambígua.',
        TARGET_CHANGED: 'A identidade de um computador mudou. Atualize a descoberta.',
        INSTALL_RECONCILIATION_REQUIRED: 'É necessária uma nova prova de ausência do NightOwl.',
        INSTALL_RECONCILIATION_FAILED: 'Não foi possível comprovar a ausência do NightOwl.',
        NO_RETRY_TARGETS: 'Não há falhas elegíveis para repetir.',
        AD_COMPUTER_DISABLED: 'Um computador está desabilitado no Active Directory.',
        CREDENTIAL_REQUIRED: 'Informe usuário e senha para esta operação.',
    };
    function url(id, action = '') {
        if (!UUID.test(id)) throw new Error('Identificador de lote inválido.');
        return BASE + id + '/' + action;
    }
    async function request(path, data) {
        const controller = new AbortController();
        const timer = window.setTimeout(() => controller.abort(), data ? 180000 : 15000);
        try {
            const options = { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal,
                headers: { Accept: 'application/json' } };
            if (data) {
                options.method = 'POST';
                options.headers['Content-Type'] = 'application/json';
                options.headers['X-CSRFToken'] = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
                options.body = JSON.stringify(data);
            }
            const response = await fetch(path, options);
            const payload = await response.json();
            if (!response.ok) throw new Error(errors[payload?.error_code] || 'Não foi possível concluir a operação. Atualize o estado antes de tentar novamente.');
            if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error();
            if (data && path !== undefined && payload.batch_id && !UUID.test(payload.batch_id)) throw new Error();
            return payload;
        } catch (error) {
            if (Object.values(errors).includes(error.message)) throw error;
            throw new Error('Não foi possível confirmar a operação. Atualize o estado antes de tentar novamente.');
        } finally {
            window.clearTimeout(timer);
        }
    }
    function modal({ title, description, submitLabel, perform, accepted }) {
        const host = document.getElementById('install-credential-host');
        if (!host || host.hasChildNodes()) return;
        const previous = document.activeElement;
        const el = (tag, cls, text) => {
            const n = document.createElement(tag);
            n.className = cls || '';
            if (text !== undefined) n.textContent = text;
            return n;
        };
        const overlay = el('div', 'install-credential-overlay');
        const dialog = el('section', 'install-credential-modal');
        dialog.setAttribute('role', 'dialog');
        dialog.setAttribute('aria-modal', 'true');
        dialog.setAttribute('aria-labelledby', 'install-credential-title');
        const heading = el('h2', '', title); heading.id = 'install-credential-title';
        const form = el('form');
        const userLabel = el('label', '', 'Usuário administrativo');
        const user = el('input'); user.type = 'text'; user.required = true; user.autocomplete = 'off'; user.maxLength = 256;
        user.placeholder = 'DOMINIO\\usuario'; userLabel.append(user);
        const passwordLabel = el('label', '', 'Senha');
        const password = el('input'); password.type = 'password'; password.required = true; password.autocomplete = 'off'; password.maxLength = 512;
        passwordLabel.append(password);
        const feedback = el('p', 'install-feedback'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
        const actions = el('footer');
        const cancel = el('button', 'install-button', 'Cancelar'); cancel.type = 'button';
        const submit = el('button', 'install-button install-primary', submitLabel); submit.type = 'submit';
        actions.append(cancel, submit);
        form.append(userLabel, passwordLabel, el('p', 'install-muted', 'A credencial será utilizada somente durante esta operação e não será armazenada pelo NightOwl.'), feedback, actions);
        dialog.append(heading, el('p', 'install-muted', description), form); overlay.append(dialog); host.append(overlay);
        document.body.classList.add('drawer-open');
        let sending = false;
        function close() {
            password.value = ''; user.value = '';
            overlay.remove(); document.removeEventListener('keydown', keydown);
            if (!document.querySelector('.install-batch-drawer:not([hidden]), .ad-detail-drawer:not([hidden])')) document.body.classList.remove('drawer-open');
            previous?.focus();
        }
        function keydown(event) {
            if (event.key === 'Escape' && !sending) { event.preventDefault(); event.stopImmediatePropagation(); close(); }
            if (event.key === 'Tab') {
                const focusable = [...dialog.querySelectorAll('input,button')].filter(n => !n.disabled);
                const first = focusable[0], last = focusable[focusable.length - 1];
                if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
                else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            }
        }
        document.addEventListener('keydown', keydown);
        cancel.addEventListener('click', close);
        overlay.addEventListener('click', e => { if (e.target === overlay && !sending) close(); });
        form.addEventListener('submit', async event => {
            event.preventDefault(); if (sending) return;
            sending = true; submit.disabled = cancel.disabled = true;
            feedback.textContent = 'Iniciando lote e validando os computadores...';
            let credentials = { username: user.value.trim(), password: password.value };
            password.value = ''; user.value = ''; user.disabled = password.disabled = true;
            try {
                const pending = perform(credentials);
                credentials = null;
                const result = await pending;
                if (!UUID.test(result.batch_id)) throw new Error();
                close(); accepted(result.batch_id);
            } catch (error) {
                feedback.textContent = error.message || 'Não foi possível confirmar a operação. Atualize o estado antes de tentar novamente.';
                sending = false; submit.disabled = cancel.disabled = false;
                user.disabled = password.disabled = false; user.focus();
            } finally { credentials = null; password.value = ''; }
        });
        user.focus();
    }
    window.NightOwlInstallBatch = { BASE, UUID, url, request, modal };
}());
