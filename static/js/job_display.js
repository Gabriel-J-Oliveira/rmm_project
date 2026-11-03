(function (root) {
    'use strict';
    function status(job) {
        const raw = job.rawStatus || job.raw_status || job.status || '';
        const names = {queued:'Em fila',pending:'Em fila',sent:'Entregue ao agente',dispatched:'Entregue ao agente',
            running:'Executando',completed:'Concluído com sucesso',failed:'Falhou',expired:'Expirado',
            timed_out:'Tempo esgotado',cancelled:'Cancelado',canceled:'Cancelado',duplicate:'Resultado duplicado',
            unsupported:'Não suportado',invalid_parameters:'Parâmetros inválidos',interrupted:'Interrompido',
            rolled_back:'Falhou; rollback executado',rollback_failed:'Falhou; rollback falhou',outcome_unknown:'Resultado não confirmado'};
        return names[raw] || raw || 'Estado não informado';
    }
    function milestones(job) {
        const values = [];
        if (job.createdAt || job.created_at) values.push({label:'Criado',at:job.createdAt || job.created_at});
        if (job.dispatchedAt || job.dispatched_at) values.push({label:'Entregue ao agente',at:job.dispatchedAt || job.dispatched_at});
        if (job.startedAt || job.started_at) values.push({label:'Execução iniciada',at:job.startedAt || job.started_at});
        if (job.finishedAt || job.finished_at) values.push({label:status(job),at:job.finishedAt || job.finished_at});
        return values;
    }
    function unconfirmed(job) {
        const raw = job.rawStatus || job.raw_status || job.status || '';
        return raw === 'outcome_unknown' || (!!(job.isStale || job.is_stale) && ['queued','pending','sent','dispatched','running'].includes(raw));
    }
    // The current backend progressPercentage fields are stage estimates, not measurements.
    // No current jobs contract provides a provenance-marked measured percentage.
    function measuredProgress() { return null; }
    root.NightOwlJobDisplay = {status, milestones, unconfirmed, measuredProgress};
})(typeof window !== 'undefined' ? window : globalThis);
