(() => {
    const root = document.getElementById("rollout-operations");
    if (!root) return;
    const select = document.getElementById("rollout-campaign");
    const message = document.getElementById("rollout-message");
    let current = null, busy = false, generation = 0;
    const labels = {approve: "Aprovar", start: "Iniciar", pause: "Pausar", resume: "Retomar", abort: "Abortar", prepare: "Preparar", prepare_next_wave: "Preparar proxima onda"};
    const icons = {approve: "check", start: "play", pause: "pause", resume: "play", abort: "square", prepare: "list-checks", prepare_next_wave: "arrow-right"};
    function text(tag, value) { const el = document.createElement(tag); el.textContent = value ?? "-"; return el; }
    function button(action, wave = null) {
        const el = text("button", labels[action]); el.type = "button"; el.className = action === "abort" ? "btn-danger" : "btn-secondary";
        const icon = document.createElement("i"); icon.dataset.lucide = icons[action]; el.prepend(icon);
        const gated = action === "start" || (action === "resume" && (!wave || wave.resume_state === "running"));
        el.disabled = busy || (gated && !(current.orchestrator_enabled && current.automatic_enabled));
        if (el.disabled && gated) el.title = "Execucao desabilitada pelos flags operacionais.";
        el.addEventListener("click", () => mutate(action, wave)); return el;
    }
    function render() {
        const summary = document.getElementById("rollout-summary"); summary.replaceChildren();
        const values = {ID: current.id, Release: current.release_version, Estado: current.state, Coorte: current.cohort_hash.slice(0, 12),
            Criada: current.created_at, Aprovada: current.approved_at, Iniciada: current.started_at,
            Concorrencia: current.concurrency_limit, Targets: `${current.total} / ${current.eligible} elegiveis / ${current.excluded} excluidos`,
            Runtime: JSON.stringify(current.target_counts), "Onda atual": current.current_wave,
            Orchestrator: current.orchestrator_enabled ? "ON" : "OFF", "Automatic rollout": current.automatic_enabled ? "ON" : "OFF", Motivo: current.reason};
        Object.entries(values).forEach(([label, value]) => summary.append(text("dt", label), text("dd", value)));
        const actions = document.getElementById("rollout-actions"); actions.replaceChildren();
        const mutable = root.dataset.canChange === "true";
        const campaignActions = {draft: ["approve", "abort"], ready: ["start", "abort"], running: ["pause", "abort"], paused: ["resume", "abort"]};
        if (mutable) (campaignActions[current.state] || []).forEach(a => actions.append(button(a)));
        const waves = document.getElementById("rollout-waves"); waves.replaceChildren();
        current.waves.forEach(wave => {
            const row = document.createElement("tr");
            [wave.sequence, wave.state, wave.target_count, JSON.stringify(wave.counts), `${wave.observation_seconds}s`, `${wave.started_at || "-"} / ${wave.completed_at || "-"}`].forEach(v => row.append(text("td", v)));
            const cell = document.createElement("td");
            const predecessors = current.waves.filter(w => w.sequence < wave.sequence).every(w => w.state === "completed");
            const otherActive = current.waves.some(w => w.id !== wave.id && ["running", "observing", "paused"].includes(w.state));
            if (mutable && current.state === "running") {
                if (wave.state === "pending" && predecessors && !otherActive) cell.append(button("prepare", wave));
                if (wave.state === "ready" && predecessors && !otherActive) cell.append(button("start", wave));
                if (wave.state === "running") cell.append(button("pause", wave));
                if (wave.state === "paused") cell.append(button("resume", wave));
            }
            row.append(cell); waves.append(row);
        });
        const targets = document.getElementById("rollout-targets"); targets.replaceChildren();
        current.targets.forEach(target => { const row = document.createElement("tr");
            [target.hostname, `${target.snapshot_version} / ${target.current_version}`, target.state, target.initial_reason, target.blocker, target.job_id, target.bucket].forEach(v => row.append(text("td", v))); targets.append(row); });
        if (window.lucide) window.lucide.createIcons();
    }
    async function reload() {
        const id = select.value, version = ++generation;
        if (!id) {
            current = null;
            ["rollout-summary", "rollout-actions", "rollout-waves", "rollout-targets"].forEach(key => document.getElementById(key).replaceChildren());
            return;
        }
        const response = await fetch(`/api/agent/rollout-campaigns/${id}/`);
        if (!response.ok) throw new Error("Nao foi possivel carregar a campanha.");
        const data = await response.json(); if (version !== generation) return;
        current = data; render();
    }
    async function mutate(action, wave) {
        if (busy || !current || select.value !== current.id) return;
        const reason = document.getElementById("rollout-reason").value.trim();
        if (!reason) { message.textContent = "Informe o motivo administrativo."; return; }
        if (!window.confirm(`${labels[action]} ${wave ? `onda ${wave.sequence}` : "campanha"}?`)) return;
        busy = true; select.disabled = true; render();
        try {
            const response = await fetch(`/api/agent/rollout-campaigns/${current.id}/${wave ? `waves/${wave.id}/` : ""}actions/`, {
                method: "POST", headers: {"Content-Type": "application/json", "X-CSRFToken": root.querySelector("[name=csrfmiddlewaretoken]").value},
                body: JSON.stringify({action, reason, expected_state: current.state, expected_updated_at: current.updated_at, ...(wave ? {expected_wave_state: wave.state} : {})})});
            const data = await response.json();
            if (!response.ok) throw new Error(response.status === 409 ? "Estado alterado ou operacao bloqueada. Atualize a decisao." : data.error || "Operacao recusada.");
            message.textContent = "Operacao registrada.";
        } catch (error) { message.textContent = error.message; }
        finally {
            busy = false; select.disabled = false;
            if (current) render();
            await reload().catch(error => { message.textContent = error.message; });
        }
    }
    document.getElementById("rollout-control-form").addEventListener("submit", event => event.preventDefault());
    select.addEventListener("change", () => reload().catch(error => { message.textContent = error.message; }));
})();
