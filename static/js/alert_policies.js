(() => {
    const form = document.querySelector('[data-alert-policy-form]');
    if (!form) return;

    const metrics = JSON.parse(document.getElementById('alert-policy-metrics').textContent);
    const field = (name) => form.elements.namedItem(name);
    const summary = form.querySelector('[data-policy-summary]');
    const label = (name) => field(name).selectedOptions[0]?.textContent.trim() || '';

    function show(name, visible) {
        const wrapper = field(name).closest('[data-policy-field]');
        wrapper.hidden = !visible && !wrapper.hasAttribute('data-field-error');
        field(name).disabled = wrapper.hidden;
    }

    function update() {
        const metric = metrics[field('metric').value];
        const endpointScope = field('scope').value === 'endpoints';
        const duration = Number(field('duration_minutes').value);
        show('threshold', !metric.binary);
        show('recovery_threshold', !metric.binary);
        show('antivirus_condition', metric.binary);
        show('job_window_minutes', metric.window);
        show('min_coverage_percent', metric.coverage);
        show('max_age_minutes', !metric.no_freshness);
        show('endpoints', endpointScope);

        for (const unit of form.querySelectorAll('[data-policy-unit]')) {
            unit.textContent = metric.unit || '';
        }
        const threshold = field('threshold').value;
        const recovery = field('recovery_threshold').value;
        const condition = field('antivirus_condition').value;
        const hasLimits = metric.binary ? Boolean(condition) : threshold !== '' && recovery !== '';
        if (!metric.binary && hasLimits && Number(recovery) >= Number(threshold)) {
            summary.textContent = 'A recuperação deve ocorrer abaixo do limite de disparo.';
            return;
        }
        if (!hasLimits || (metric.window && !field('job_window_minutes').value)) {
            summary.textContent = 'Defina os limites ou a condição para visualizar o comportamento.';
            return;
        }
        const scope = endpointScope ? `${field('endpoints').selectedOptions.length} endpoint(s)` : label('scope');
        const state = field('enabled').checked ? 'Ativa' : 'Inativa';
        const delay = duration > 0 ? ` por pelo menos ${duration} min` : '';
        const window = metric.window ? ` nos últimos ${field('job_window_minutes').value} min` : '';
        const trigger = metric.binary ? label('antivirus_condition') : `${label('metric')} ≥ ${threshold} ${metric.unit}${window}`;
        const normal = metric.binary ? 'proteção ativa confirmada' : `≤ ${recovery} ${metric.unit}`;
        const visual = field('notify_visual').checked ? 'ligado' : 'desligado';
        const sound = field('notify_sound').checked ? 'ligado' : 'desligado';
        summary.textContent = `${state} · ${scope}. ${trigger}${delay} gera alerta ${label('severity')}. Recuperação com ${normal}. Visual ${visual}, som ${sound}.`;
    }

    form.addEventListener('input', update);
    form.addEventListener('change', update);
    update();
})();
