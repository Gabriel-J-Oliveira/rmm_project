(function (scope) {
    "use strict";
    function escape(value) {
        return String(value == null ? "-" : value).replace(/[&<>"']/g, function (char) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
        });
    }
    function render(data) {
        data = data || {};
        const effective = data.effective;
        const requested = data.requested;
        const pending = ["queued", "sent", "running"].includes(data.job_status);
        const state = effective ? (effective.telemetryEnabled ? "Habilitada" : "Desabilitada") : "Nao reportado";
        const continuous = data.continuous;
        const statuses = { queued: "Aguardando agente", sent: "Aplicando", running: "Aplicando", completed: "Concluido", failed: "Falhou" };
        const requestedText = requested ? (requested.telemetryEnabled ? "Habilitada" : "Desabilitada") + " / " + requested.telemetrySampleSeconds + "s / " + requested.telemetryFlushSeconds + "s" : "-";
        const config = effective || requested || { telemetryEnabled: true, telemetrySampleSeconds: 300, telemetryFlushSeconds: 900 };
        const metrics = data.last_metrics || {};
        function number(value, unit) { return Number.isFinite(value) ? value.toFixed(1) + ' ' + unit : 'Nao informado'; }
        function row(label, value) { return "<div><dt>" + label + "</dt><dd>" + escape(value) + "</dd></div>"; }
        return '<div class="endpoint-monitoring"><div class="endpoint-monitoring-top-grid"><section class="panel endpoint-dense-card endpoint-monitoring-summary"><header><h2>Resumo da telemetria</h2></header><dl class="endpoint-fact-list endpoint-monitoring-facts">'
            + row("CPU", number(metrics.cpu_percent, '%'))
            + row("Memoria utilizada", number(metrics.memory_used_percent, '%'))
            + row("Disco: leitura / escrita", number(metrics.disk_read_bytes_per_sec, 'B/s') + ' / ' + number(metrics.disk_write_bytes_per_sec, 'B/s'))
            + row("Rede: recebido / enviado (contadores)", number(metrics.network_received_bytes, 'bytes') + ' / ' + number(metrics.network_sent_bytes, 'bytes'))
            + row("Medicao coletada em", data.last_sample_at)
            + row("Telemetria reportada", state)
            + row("Modo no ultimo heartbeat", data.reported_mode || "Nao reportado")
            + row("Modo reportado em", data.mode_reported_at)
            + row("Coleta", continuous ? continuous.sample_seconds + " segundos (continuo)" : effective ? effective.telemetrySampleSeconds + " segundos" : "Nao reportado")
            + row("Envio", continuous ? continuous.upload_seconds + " segundos (resumos)" : effective ? effective.telemetryFlushSeconds + " segundos" : "Nao reportado")
            + row("Reportada em", data.reported_at)
            + row("Aplicada em", data.applied_at)
            + row("Ultimas amostras recebidas", data.last_received_at)
            + row("Solicitada: estado / coleta / envio", requestedText)
            + row("Job", statuses[data.job_status] || data.job_status || "Nenhum")
            + row("Amostras posteriores a aplicacao", data.samples_after_application ? "Recebidas" : "Ainda nao confirmadas")
            + '</dl>' + (data.reported_mode === 'legacy' ? '<p class="muted endpoint-monitoring-legacy-note">O modo legacy reportado não envia amostras ao monitoramento ao vivo. Confirme o modo atual do agente antes de iniciar.</p>' : '') + '</section><section class="panel endpoint-dense-card endpoint-telemetry-config"><header><h2>Configuração da telemetria</h2></header><form data-telemetry-form><fieldset' + (!data.compatible || pending ? " disabled" : "") + '>'
            + '<label class="endpoint-telemetry-toggle"><input type="checkbox" name="telemetryEnabled"' + (config.telemetryEnabled ? " checked" : "") + '> Telemetria habilitada</label>'
            + '<label>Coleta do perfil legado (segundos)<input type="number" name="telemetrySampleSeconds" min="60" max="3600" step="1" required' + (continuous ? ' readonly' : '') + ' value="' + escape(config.telemetrySampleSeconds) + '"></label>'
            + '<label>Envio do perfil legado (segundos)<input type="number" name="telemetryFlushSeconds" min="60" max="86400" step="1" required' + (continuous ? ' readonly' : '') + ' value="' + escape(config.telemetryFlushSeconds) + '"></label>'
            + '<button type="submit" class="copy-btn"><i data-lucide="save"></i>Aplicar configuracao</button></fieldset></form>'
            + (!data.compatible ? '<p class="muted">Agente compativel necessario: ' + escape(data.minimum_version) + ' ou posterior, com suporte reportado.</p>' : "") + '</section></div><div class="endpoint-monitoring-live" data-live-placement></div></div>';
    }
    const api = { render };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else scope.NightOwlMonitoring = api;
})(typeof window !== "undefined" ? window : globalThis);
