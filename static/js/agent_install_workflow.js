document.addEventListener('DOMContentLoaded', () => {
    const helpWrap = document.querySelector('.agent-help-wrap');
    if (!helpWrap) return;

    const helpButton = document.getElementById('agent-help-button');
    const helpPopover = document.getElementById('agent-help-popover');
    const hoverCapable = window.matchMedia?.('(hover: hover)').matches ?? false;
    let helpPinned = false;
    let suppressHover = false;

    function showHelp(open) {
        helpPopover.hidden = !open;
        helpButton.setAttribute('aria-expanded', String(open));
    }

    helpWrap.addEventListener('mouseenter', () => {
        if (hoverCapable && !suppressHover) showHelp(true);
    });
    helpWrap.addEventListener('mouseleave', () => {
        suppressHover = false;
        if (!helpPinned) showHelp(false);
    });
    helpButton.addEventListener('click', () => {
        if (helpPinned) {
            helpPinned = false;
            suppressHover = true;
            showHelp(false);
        } else {
            helpPinned = true;
            showHelp(true);
        }
    });
    document.addEventListener('click', (event) => {
        if (!helpWrap.contains(event.target)) {
            helpPinned = false;
            suppressHover = false;
            showHelp(false);
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !helpPopover.hidden) {
            helpPinned = false;
            suppressHover = true;
            showHelp(false);
            helpButton.focus();
        }
    });

    const history = document.getElementById('agent-token-history-rows');
    const pageSizeInput = document.getElementById('agent-token-page-size');
    const pageSummary = document.getElementById('agent-token-page-summary');
    const pageNumbers = document.getElementById('agent-token-page-numbers');
    const pagePrev = document.getElementById('agent-token-page-prev');
    const pageNext = document.getElementById('agent-token-page-next');
    let page = 1;

    function node(tag, className, value) {
        const item = document.createElement(tag);
        if (className) item.className = className;
        if (value !== undefined && value !== null) item.textContent = String(value);
        return item;
    }

    function displayDate(value) {
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('pt-BR');
    }

    function renderHistory() {
        const rows = [...history.querySelectorAll('[data-token-row]')];
        const size = Number(pageSizeInput.value) || 5;
        const pages = Math.max(1, Math.ceil(rows.length / size));
        page = Math.min(page, pages);
        const start = (page - 1) * size;
        rows.forEach((row, index) => { row.hidden = index < start || index >= start + size; });
        pageSummary.textContent = `Mostrando ${rows.length ? start + 1 : 0}-${Math.min(start + size, rows.length)} de ${rows.length}`;
        pagePrev.disabled = page === 1;
        pageNext.disabled = page === pages;
        pageNumbers.replaceChildren();
        const numbers = new Set([1, pages, page - 1, page, page + 1]);
        let previous = 0;
        for (const number of [...numbers].filter((item) => item >= 1 && item <= pages).sort((a, b) => a - b)) {
            if (number > previous + 1) pageNumbers.appendChild(node('span', 'agent-token-page-ellipsis', '…'));
            const button = node('button', '', number);
            button.type = 'button';
            button.setAttribute('aria-label', `Página ${number}`);
            if (number === page) button.setAttribute('aria-current', 'page');
            button.addEventListener('click', () => { page = number; renderHistory(); });
            pageNumbers.appendChild(button);
            previous = number;
        }
    }

    pageSizeInput.addEventListener('change', () => { page = 1; renderHistory(); });
    pagePrev.addEventListener('click', () => { if (page > 1) { page--; renderHistory(); } });
    pageNext.addEventListener('click', () => { page++; renderHistory(); });
    renderHistory();

    async function copyValue(value) {
        try {
            await navigator.clipboard.writeText(value);
        } catch (_error) {
            const textarea = node('textarea');
            textarea.value = value;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            textarea.remove();
        }
        const feedback = document.getElementById('copy-feedback');
        if (feedback) {
            feedback.textContent = 'Copiado';
            feedback.hidden = false;
            feedback.classList.add('is-visible');
            window.setTimeout(() => {
                feedback.classList.remove('is-visible');
                feedback.hidden = true;
            }, 1800);
        }
    }

    function resultBlock(label, value, buttonLabel, className) {
        const block = node('div', className);
        const content = node('div', 'copy-value-content');
        content.append(node('span', '', label), node('code', '', value));
        const button = node('button', 'copy-button', buttonLabel);
        button.type = 'button';
        button.addEventListener('click', () => copyValue(value));
        block.append(content, button);
        return block;
    }

    function revokeForm(url, csrf, label) {
        const form = node('form');
        form.method = 'post';
        form.action = url;
        const csrfInput = node('input');
        csrfInput.type = 'hidden';
        csrfInput.name = 'csrfmiddlewaretoken';
        csrfInput.value = csrf;
        const button = node('button', 'danger-subtle', label);
        button.type = 'submit';
        form.append(csrfInput, button);
        return form;
    }

    function showResult(panel, data, csrf) {
        const enrollment = data.kind === 'enrollment';
        panel.replaceChildren();
        const heading = node('div', 'panel-heading');
        const title = node('div');
        title.append(node('span', 'section-kicker', enrollment ? 'Token criado' : 'Token manual criado'),
            node('h2', '', enrollment ? 'Copie este token agora' : 'Copie o token de validação manual'),
            node('p', '', 'O token completo aparece apenas uma vez. Depois, somente o prefixo fica visível.'));
        heading.appendChild(title);
        if (enrollment) {
            const actions = node('div', 'agent-created-actions');
            const commandLink = node('a', 'copy-button', 'Gerar novo comando');
            commandLink.href = '#agent-command-generator';
            actions.append(commandLink, revokeForm(data.revoke_url, csrf, 'Revogar token'));
            heading.appendChild(actions);
        }
        panel.appendChild(heading);
        panel.appendChild(node('p', 'agent-token-result-meta',
            `${enrollment ? `Domínio: ${data.allowed_domain || 'Qualquer'} · ` : ''}Expira em: ${displayDate(data.expires_at)}`));
        panel.appendChild(resultBlock(enrollment ? 'Enrollment token' : 'Manual validation token', data.token,
            'Copiar token', 'created-token-box'));
        if (enrollment) panel.appendChild(resultBlock('Comando PowerShell recomendado', data.command,
            'Copiar comando', 'terminal-block created-command-block'));
        panel.hidden = false;
    }

    function addHistoryRow(data, csrf) {
        const empty = history.querySelector('[data-token-empty]');
        if (empty) empty.remove();
        const row = node('tr');
        row.setAttribute('data-token-row', '');
        const values = [data.name, data.prefix, data.allowed_domain || '—', 'Ativo',
            displayDate(data.expires_at), `0/${data.max_uses || '∞'}`, '—', displayDate(data.created_at)];
        values.forEach((value, index) => {
            const cell = node('td');
            cell.appendChild(node(index === 0 ? 'strong' : 'span', index === 3 ? 'status-badge status-online' : '', value));
            row.appendChild(cell);
        });
        const actions = node('td');
        const copy = node('button', 'copy-button subtle', 'Copiar comando');
        copy.type = 'button';
        copy.addEventListener('click', () => copyValue(data.command));
        actions.append(copy, revokeForm(data.revoke_url, csrf, 'Revogar'));
        row.appendChild(actions);
        history.prepend(row);
        page = 1;
        renderHistory();
    }

    function bindTokenForm(formId, resultId, expectedKind) {
        const form = document.getElementById(formId);
        const panel = document.getElementById(resultId);
        const submit = form.querySelector('[type="submit"]');
        const error = form.querySelector('.agent-token-error');
        let pending = false;
        form.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (pending) return;
            if (expectedKind === 'enrollment') {
                const hours = Number(form.elements.expires_hours.value);
                if (!Number.isInteger(hours) || hours < 1 || hours > 72) {
                    error.textContent = 'A validade deve ser entre 1 e 72 horas.';
                    error.hidden = false;
                    return;
                }
            }
            pending = true;
            submit.disabled = true;
            submit.setAttribute('aria-busy', 'true');
            error.hidden = true;
            try {
                const response = await fetch(form.action || window.location.href, {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json',
                        'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value },
                    body: new FormData(form),
                });
                const data = await response.json();
                if (!response.ok || data.ok !== true || data.kind !== expectedKind) {
                    throw new Error(data.error || 'Não foi possível gerar o token.');
                }
                showResult(panel, data, form.querySelector('[name="csrfmiddlewaretoken"]').value);
                if (expectedKind === 'enrollment') {
                    addHistoryRow(data, form.querySelector('[name="csrfmiddlewaretoken"]').value);
                    const active = document.querySelector('.agent-install-metrics .metric-card:nth-child(4) .metric-value');
                    if (active) active.textContent = String((Number(active.textContent) || 0) + 1);
                }
                window.lucide?.createIcons?.();
            } catch (failure) {
                const safeErrors = new Set([
                    'Informe um nome para o token.', 'A validade deve ser entre 1 e 72 horas.',
                    'O limite de usos deve ser maior que zero.',
                    'A validade do token manual deve ser maior que zero.',
                ]);
                error.textContent = safeErrors.has(failure.message) ? failure.message :
                    'Não foi possível gerar o token. Confira os dados e tente novamente.';
                error.hidden = false;
            } finally {
                pending = false;
                submit.disabled = false;
                submit.setAttribute('aria-busy', 'false');
            }
        });
    }

    bindTokenForm('enrollment-token-form', 'enrollment-token-result', 'enrollment');
    bindTokenForm('manual-token-form', 'manual-token-result', 'manual_validation');
});
