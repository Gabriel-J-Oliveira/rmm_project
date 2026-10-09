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
        const statuses = { queued: "Aguardando agente", sent: "Aplicando", running: "Aplicando", completed: "Concluido", failed: "Falhou" };
        const requestedText = requested ? (requested.telemetryEnabled ? "Habilitada" : "Desabilitada") + " / " + requested.telemetrySampleSeconds + "s / " + requested.telemetryFlushSeconds + "s" : "-";
        const config = effective || requested || { telemetryEnabled: true, telemetrySampleSeconds: 300, telemetryFlushSeconds: 900 };
        function row(label, value) { return "<div><dt>" + label + "</dt><dd>" + escape(value) + "</dd></div>"; }
        return '<section class="endpoint-monitoring" style="max-width:800px"><h2>Monitoramento</h2><dl class="endpoint-fact-list">'
            + row("Telemetria reportada", state)
            + row("Coleta", effective ? effective.telemetrySampleSeconds + " segundos" : "Nao reportado")
            + row("Envio", effective ? effective.telemetryFlushSeconds + " segundos" : "Nao reportado")
            + row("Reportada em", data.reported_at)
            + row("Aplicada em", data.applied_at)
            + row("Ultimas amostras recebidas", data.last_received_at)
            + row("Solicitada: estado / coleta / envio", requestedText)
            + row("Job", statuses[data.job_status] || data.job_status || "Nenhum")
            + row("Amostras posteriores a aplicacao", data.samples_after_application ? "Recebidas" : "Ainda nao confirmadas")
            + '</dl><form data-telemetry-form><fieldset' + (!data.compatible || pending ? " disabled" : "") + ' style="border:0;padding:0;display:grid;gap:16px">'
            + '<label><input type="checkbox" name="telemetryEnabled"' + (config.telemetryEnabled ? " checked" : "") + '> Telemetria habilitada</label>'
            + '<label>Coleta (segundos)<input type="number" name="telemetrySampleSeconds" min="60" max="3600" step="1" required value="' + escape(config.telemetrySampleSeconds) + '" style="display:block;width:100%;max-width:280px"></label>'
            + '<label>Envio (segundos)<input type="number" name="telemetryFlushSeconds" min="60" max="86400" step="1" required value="' + escape(config.telemetryFlushSeconds) + '" style="display:block;width:100%;max-width:280px"></label>'
            + '<button type="submit" class="copy-btn" style="justify-self:start"><i data-lucide="save"></i>Aplicar configuracao</button></fieldset></form>'
            + (!data.compatible ? '<p class="muted">Agente compativel necessario: ' + escape(data.minimum_version) + ' ou posterior, com suporte reportado.</p>' : "") + '</section>';
    }
    const api = { render };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else scope.NightOwlMonitoring = api;
})(typeof window !== "undefined" ? window : globalThis);
