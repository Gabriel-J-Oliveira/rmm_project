(function () {
    "use strict";

    const root = document.querySelector("[data-endpoint-detail]");
    const panel = root && root.querySelector("[data-asset-records]");
    const content = panel && panel.querySelector("[data-asset-records-content]");
    if (!panel || !content) return;

    let loaded = false;
    let loading = false;
    let state = null;
    let selectedAsset = null;

    const messages = {
        endpoint_not_found: "Endpoint não encontrado.",
        endpoint_identity_required: "Atualize a página para validar a identidade atual do endpoint.",
        endpoint_inactive: "Endpoint inativo; o vínculo patrimonial foi bloqueado.",
        endpoint_identity_changed: "A identidade do endpoint mudou desde a abertura da página. Atualize os dados antes de continuar.",
        endpoint_already_linked: "Este endpoint já possui um patrimônio ativo.",
        serial_matches_existing_asset: "Este serial já pertence a um patrimônio ativo. Pesquise e confirme o vínculo existente.",
        inventory_serial_conflict: "O inventário contém seriais divergentes. Nenhum patrimônio foi criado.",
        duplicate_endpoint_serial_requires_review: "O serial também aparece em outro endpoint. Informe a resolução manual antes de criar o patrimônio.",
        identity_resolution_required: "A identidade do endpoint é ambígua ou incompleta. Confirme pelo UUID e informe o motivo da resolução.",
        evidence_resolution_required: "Os dados físicos divergem do patrimônio selecionado. Informe o motivo da resolução.",
        link_transfer_confirmation_required: "Já existe um vínculo ativo. Confirme a transferência e informe o motivo para preservar o histórico.",
        asset_not_found: "Patrimônio não encontrado ou inativo.",
        asset_id_invalid: "Identificador do patrimônio inválido.",
        asset_link_identity_changed: "A identidade do agente mudou desde a associação. Revise o vínculo antes de registrar novos dados.",
        asset_not_linked_to_endpoint: "Associe um patrimônio ao endpoint antes de criar registros.",
        record_type_invalid: "Tipo de registro inválido.",
        record_date_invalid: "Informe uma data válida para o acontecimento.",
        record_description_invalid: "A descrição é obrigatória e deve ter até 5.000 caracteres.",
        responsible_values_required: "Informe o responsável anterior e o novo responsável.",
        asset_identity_conflict: "A identidade patrimonial conflitou com um registro existente. Recarregue os dados e revise a associação.",
        forbidden: "Você não tem permissão para esta operação.",
        invalid_json: "A solicitação não pôde ser validada. Tente novamente."
    };

    function escapeHtml(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
    }

    function csrfToken() {
        const cookie = document.cookie.split(";").map(function (part) { return part.trim(); })
            .find(function (part) { return part.indexOf("csrftoken=") === 0; });
        return cookie ? decodeURIComponent(cookie.slice("csrftoken=".length)) : (panel.dataset.csrfToken || "");
    }

    async function request(url, options) {
        const response = await fetch(url, options || { headers: { Accept: "application/json" } });
        let payload = {};
        try { payload = await response.json(); } catch (_) { payload = {}; }
        if (!response.ok) {
            const error = new Error(messages[payload.error] || "Não foi possível concluir a operação.");
            error.payload = payload;
            throw error;
        }
        return payload;
    }

    function formatDate(value) {
        if (!value) return "Não informado";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "Data inválida" : date.toLocaleString("pt-BR");
    }

    function localDateTime() {
        const date = new Date(Date.now() - new Date().getTimezoneOffset() * 60000);
        return date.toISOString().slice(0, 16);
    }

    function warningsHtml() {
        const warnings = (state && state.identity_warnings) || [];
        const cards = warnings.map(function (warning) {
            return '<p class="endpoint-asset-alert">' + escapeHtml(warning.message) + '</p>';
        });
        ((state && state.evidence_conflicts) || []).forEach(function (conflict) {
            cards.push('<p class="endpoint-asset-alert endpoint-asset-error">' + escapeHtml(conflict.message) + '</p>');
        });
        if (state && state.inventory && state.inventory.serial_conflict) {
            cards.push('<p class="endpoint-asset-alert endpoint-asset-error">O inventário informa valores de serial divergentes; o sistema não escolherá um deles.</p>');
        }
        if (state && state.serial_conflict && !(state.inventory && state.inventory.serial_conflict)) {
            cards.push('<p class="endpoint-asset-alert endpoint-asset-error">Mais de um patrimônio ativo corresponde ao serial. Nenhum será selecionado automaticamente.</p>');
        }
        if (state && state.matching_endpoint_observations && state.matching_endpoint_observations.length) {
            cards.push('<p class="endpoint-asset-alert">O serial também foi observado em outro endpoint. Isso exige revisão explícita e não associa registros automaticamente.</p>');
        }
        if (state && state.suggestion_evidence_mismatch) {
            cards.push('<p class="endpoint-asset-alert">Fabricante ou modelo diverge da sugestão encontrada. A associação exige motivo de resolução.</p>');
        }
        return cards.join("");
    }

    function factsHtml(data, includeIdentity) {
        return '<dl class="endpoint-asset-grid">' +
            (includeIdentity ? '<div class="endpoint-asset-fact"><dt>Patrimônio provisório</dt><dd><span class="endpoint-asset-code endpoint-asset-provisional">' + escapeHtml(data.code) + '</span></dd></div>' : '') +
            '<div class="endpoint-asset-fact"><dt>Serial físico (BIOS)</dt><dd>' + escapeHtml(data.serial_number || "Não informado") + '</dd></div>' +
            '<div class="endpoint-asset-fact"><dt>Fabricante</dt><dd>' + escapeHtml(data.manufacturer || "Não informado") + '</dd></div>' +
            '<div class="endpoint-asset-fact"><dt>Modelo</dt><dd>' + escapeHtml(data.model || "Não informado") + '</dd></div>' +
            (data.inventory_collected_at ? '<div class="endpoint-asset-fact"><dt>Inventário coletado</dt><dd>' + escapeHtml(formatDate(data.inventory_collected_at)) + '</dd></div>' : '') +
            (includeIdentity ? '<div class="endpoint-asset-fact"><dt>Identidade</dt><dd>' + escapeHtml(data.identity_status === "confirmed" ? "Confirmada" : data.identity_status === "review" ? "Em revisão" : "Provisória") + '</dd></div>' : '') +
            '</dl>';
    }

    function confirmationFields(reasonRequired) {
        return '<label class="endpoint-asset-checkbox"><input type="checkbox" name="identity_confirmed" required><span>Confirmo que estou associando o endpoint UUID <code>' + escapeHtml(panel.dataset.endpointUuid) + '</code> ao patrimônio selecionado.</span></label>' +
            '<label>Motivo / evidência da associação' + (reasonRequired ? ' <strong>(obrigatório para resolver o conflito)</strong>' : ' (opcional)') +
            '<textarea name="reason" maxlength="2000" ' + (reasonRequired ? 'required minlength="8"' : '') + ' placeholder="Ex.: conferência física da etiqueta e do serial"></textarea></label>';
    }

    function recordFormHtml() {
        if (panel.dataset.recordAllowed !== "yes") {
            return '<p class="endpoint-asset-muted">Seu perfil pode consultar o histórico, mas não pode incluir registros patrimoniais.</p>';
        }
        return '<form class="endpoint-asset-form" data-asset-record-form>' +
            '<h3>Incluir registro</h3>' +
            '<label>Tipo<select name="record_type" required>' +
            '<option value="observation">Observação</option><option value="issue">Problema</option><option value="formatted">Formatação</option>' +
            '<option value="maintenance">Manutenção</option><option value="upgrade">Upgrade</option><option value="transfer">Transferência</option>' +
            '<option value="responsibility_change">Troca de responsável</option></select></label>' +
            '<label>Data do acontecimento<input type="datetime-local" name="occurred_at" value="' + localDateTime() + '" required></label>' +
            '<label>Descrição<textarea name="description" maxlength="5000" required></textarea></label>' +
            '<div class="endpoint-asset-grid"><label>Responsável anterior (opcional)<input name="previous_responsible" maxlength="255"></label>' +
            '<label>Novo responsável (opcional)<input name="new_responsible" maxlength="255"></label></div>' +
            '<div class="endpoint-asset-actions"><button class="endpoint-asset-button endpoint-asset-button--primary" type="submit">Salvar registro</button></div>' +
            '<p class="endpoint-asset-inline-status" data-asset-form-status role="status" aria-live="polite"></p></form>';
    }

    function renderRecords() {
        const records = (state.records || []).map(function (record) {
            return '<li><time datetime="' + escapeHtml(record.occurred_at) + '">' + escapeHtml(formatDate(record.occurred_at)) +
                '</time><strong>' + escapeHtml(record.label) + '</strong><p>' + escapeHtml(record.description) + '</p>' +
                ((record.previous_responsible || record.new_responsible) ? '<small>Responsável: ' + escapeHtml(record.previous_responsible || "Não informado") + ' → ' + escapeHtml(record.new_responsible || "Não informado") + '</small>' : '') +
                '<small>Incluído por ' + escapeHtml(record.author) + ' em ' + escapeHtml(formatDate(record.created_at)) + '</small></li>';
        }).join("");
        return '<section class="endpoint-asset-section"><div class="endpoint-asset-section-heading"><h3>Histórico patrimonial</h3><span class="endpoint-asset-muted">' +
            (records ? state.records.length + ' registro(s)' : 'Histórico ausente') + '</span></div>' +
            (records ? '<ol class="endpoint-asset-timeline">' + records + '</ol>' : '<p class="endpoint-asset-muted">Ainda não há registros patrimoniais para este equipamento.</p>') +
            '</section>';
    }

    function renderLinkHistory() {
        const links = state.links || [];
        if (!links.length) return '';
        return '<section class="endpoint-asset-section"><h3>Histórico de agentes associados</h3><ol class="endpoint-asset-link-history">' +
            links.map(function (link) {
                return '<li><strong>' + escapeHtml(link.hostname || "Endpoint removido") + '</strong> · UUID ' + escapeHtml(link.endpoint_id) +
                    '<br><small>Machine ID: ' + escapeHtml(link.machine_id || "Não informado") + ' · Início: ' + escapeHtml(formatDate(link.linked_at)) +
                    ' · ' + (link.ended_at ? 'Encerrado: ' + escapeHtml(formatDate(link.ended_at)) : link.current ? 'Vínculo atual' : 'Endpoint removido') +
                    (link.reason ? '<br>Motivo: ' + escapeHtml(link.reason) : '') + '</small></li>';
            }).join("") + '</ol></section>';
    }

    function linkLabel(link) {
        if (link.state === 'removed') return 'vínculo anterior com endpoint excluído';
        if (link.state === 'identity_changed') return 'vínculo anterior com identidade divergente';
        return 'vínculo atual';
    }

    function renderSearchResults() {
        const rows = (state.asset_search_results || []).map(function (asset) {
            return '<article class="endpoint-asset-candidate"><div><strong class="endpoint-asset-code endpoint-asset-provisional">' + escapeHtml(asset.code) + '</strong>' +
                '<div>' + escapeHtml([asset.manufacturer, asset.model].filter(Boolean).join(" · ") || "Fabricante/modelo não informados") + '</div>' +
                '<small>Serial: ' + escapeHtml(asset.serial_number || "Não informado") +
                (asset.active_endpoint ? ' · ' + linkLabel(asset.active_endpoint) + ': ' + escapeHtml(asset.active_endpoint.hostname) + ' (' + escapeHtml(asset.active_endpoint.id) + ')' : ' · sem endpoint atual') +
                '</small></div><button type="button" class="endpoint-asset-button" data-asset-select="' + escapeHtml(asset.id) + '">Selecionar patrimônio</button></article>';
        }).join("");
        return '<div class="endpoint-asset-search-results" data-asset-search-results>' +
            (rows || (state.asset_query ? '<p class="endpoint-asset-muted">Nenhum patrimônio ativo encontrado para esta pesquisa.</p>' : '')) + '</div>';
    }

    function renderUnlinked() {
        const inventory = state.inventory || {};
        const permissions = state.permissions || {};
        const serialDuplicate = state.matching_endpoint_observations && state.matching_endpoint_observations.length > 0;
        const mustExplain = serialDuplicate || (state.identity_warnings || []).some(function (item) {
            return ["machine_id_missing", "machine_id_invalid", "machine_id_ambiguous"].includes(item.code);
        });
        const suggestion = state.suggestion;
        return '<header><div><h2>Sem associação patrimonial</h2><p class="endpoint-asset-muted">O patrimônio só será criado ou vinculado após confirmação do operador.</p></div><button class="endpoint-asset-button" type="button" data-asset-refresh>Atualizar dados</button></header>' +
            (state.link_identity_changed ? '<p class="endpoint-asset-alert endpoint-asset-error">A identidade atual do agente difere do vínculo patrimonial registrado. Confirme o vínculo novamente para acessar o histórico.</p>' : '') +
            warningsHtml() + factsHtml(inventory, false) +
            (suggestion ? '<section class="endpoint-asset-alert"><strong>Sugestão por serial válido:</strong> ' + escapeHtml(suggestion.code) +
                (state.suggestion_active_link ? ' · ' + linkLabel(state.suggestion_active_link) + ': ' + escapeHtml(state.suggestion_active_link.hostname) + ' (' + escapeHtml(state.suggestion_active_link.endpoint_id) + ')' : '') +
                '<p>Confirme explicitamente o vínculo. O serial serve como evidência, não como chave patrimonial.</p>' +
                (permissions.link_asset ? '<button type="button" class="endpoint-asset-button" data-asset-select-suggestion>Selecionar esta sugestão</button>' : '') + '</section>' : '') +
            (permissions.create_asset && !state.link_identity_changed ? '<section class="endpoint-asset-section"><h3>Criar patrimônio provisório</h3><p class="endpoint-asset-muted">Usa somente o inventário real recebido para este endpoint. Nenhum código será criado para outros equipamentos.</p>' +
                '<form class="endpoint-asset-form" data-asset-create-form>' + confirmationFields(mustExplain) +
                '<button class="endpoint-asset-button endpoint-asset-button--primary" type="submit">Criar e associar patrimônio</button><p class="endpoint-asset-inline-status" data-asset-form-status role="status" aria-live="polite"></p></form></section>' : '') +
            (permissions.link_asset ? '<section class="endpoint-asset-section"><h3>Vincular patrimônio existente</h3><form class="endpoint-asset-search-form" data-asset-search-form>' +
                '<label>Pesquisar pelo código provisório ou serial<input type="search" name="asset_q" maxlength="100" value="' + escapeHtml(state.asset_query || "") + '"></label>' +
                '<button class="endpoint-asset-button" type="submit">Pesquisar patrimônio</button></form>' + renderSearchResults() +
                (selectedAsset ? '<form class="endpoint-asset-form" data-asset-link-form><h4>Confirmar vínculo com ' + escapeHtml(selectedAsset.code) + '</h4>' +
                    confirmationFields(mustExplain || state.link_identity_changed || state.suggestion_evidence_mismatch || Boolean(selectedAsset.active_endpoint) || selectedAsset.identity_status === "review") +
                    '<label class="endpoint-asset-checkbox"><input type="checkbox" name="transfer_confirmed"><span>Autorizo encerrar um vínculo ativo anterior, preservando o histórico.</span></label>' +
                    '<button class="endpoint-asset-button endpoint-asset-button--primary" type="submit">Confirmar vínculo neste endpoint</button><p class="endpoint-asset-inline-status" data-asset-form-status role="status" aria-live="polite"></p></form>' : '') +
                '</section>' : '<p class="endpoint-asset-muted">Seu perfil pode consultar, mas não criar ou vincular patrimônios.</p>');
    }

    function renderLinked() {
        const asset = state.asset;
        return '<header><div><h2>Registro patrimonial</h2><p class="endpoint-asset-muted">Identidade física persistente, separada da instalação atual do agente.</p></div><button class="endpoint-asset-button" type="button" data-asset-refresh>Atualizar dados</button></header>' +
            warningsHtml() + factsHtml({...asset, inventory_collected_at: state.inventory && state.inventory.inventory_collected_at}, true) +
            '<p class="endpoint-asset-muted">Endpoint associado: <code>' + escapeHtml(state.current_link.hostname || "Endpoint") + '</code> · UUID <code>' + escapeHtml(state.current_link.endpoint_id) + '</code></p>' +
            renderLinkHistory() + renderRecords() + recordFormHtml();
    }

    function render() {
        if (!state) return;
        content.innerHTML = '<div class="endpoint-asset-inline-status" role="status" aria-live="polite">' + escapeHtml(state.notice || "") + '</div>' +
            (state.state === "linked" ? renderLinked() :
             state.state === "identity_conflict" ? '<h2>Conflito de identidade</h2><p class="endpoint-asset-alert endpoint-asset-error">Há mais de um vínculo patrimonial ativo para este UUID. Nenhum histórico foi combinado.</p>' + warningsHtml() :
             state.state === "conflict" ? '<h2>Conflito de evidências patrimoniais</h2><p class="endpoint-asset-alert endpoint-asset-error">A associação automática está bloqueada. Revise os dados e resolva explicitamente qualquer divergência.</p>' + renderUnlinked() : renderUnlinked());
    }

    async function load(assetQuery) {
        if (loading) return;
        loading = true;
        content.innerHTML = '<p class="endpoint-asset-loading" role="status">Consultando registros reais do endpoint…</p>';
        const url = panel.dataset.apiUrl + (assetQuery ? "?asset_q=" + encodeURIComponent(assetQuery) : "");
        try {
            state = await request(url, { headers: { Accept: "application/json", "Cache-Control": "no-cache" } });
            state.asset_query = assetQuery || "";
            loaded = true;
            render();
        } catch (error) {
            content.innerHTML = '<h2>Não foi possível carregar os registros</h2><p class="endpoint-asset-alert endpoint-asset-error" role="alert">' + escapeHtml(error.message) + '</p><button class="endpoint-asset-button" type="button" data-asset-refresh>Tentar novamente</button>';
        } finally {
            loading = false;
        }
    }

    function setFormStatus(form, message) {
        const target = form.querySelector("[data-asset-form-status]");
        if (target) target.textContent = message || "";
    }

    async function submitMutation(form, payload) {
        const button = form.querySelector('[type="submit"]');
        if (!form.reportValidity()) return;
        if (button) button.disabled = true;
        setFormStatus(form, "Salvando com trilha de auditoria…");
        try {
            const response = await request(panel.dataset.apiUrl, {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrfToken() },
                body: JSON.stringify(payload)
            });
            selectedAsset = null;
            await load("");
            if (state) state.notice = response.asset_code ? "Patrimônio " + response.asset_code + " associado." : "Registro patrimonial salvo.";
            render();
        } catch (error) {
            if (error.payload && error.payload.error === "duplicate_endpoint_serial_requires_review") {
                state.matching_endpoint_observations = error.payload.matching_endpoint_observations || [];
                state.notice = "O serial também aparece em outro endpoint. Revise os UUIDs e informe a evidência antes de continuar.";
                render();
                const refreshedForm = content.querySelector("[data-asset-create-form]");
                if (refreshedForm) setFormStatus(refreshedForm, error.message);
                return;
            }
            setFormStatus(form, error.message);
            if (button) button.disabled = false;
        }
    }

    root.addEventListener("click", function (event) {
        const tab = event.target.closest('[data-endpoint-tab="records"]');
        if (tab && !loaded) load("");
        if (event.target.closest("[data-asset-refresh]")) load("");
        const select = event.target.closest("[data-asset-select]");
        if (select && state) {
            selectedAsset = (state.asset_search_results || []).find(function (item) { return item.id === select.dataset.assetSelect; }) || null;
            render();
        }
        if (event.target.closest("[data-asset-select-suggestion]") && state && state.suggestion) {
            selectedAsset = {
                id: state.suggestion.id, code: state.suggestion.code, serial_number: state.suggestion.serial_number,
                manufacturer: state.suggestion.manufacturer, model: state.suggestion.model,
                active_endpoint: state.suggestion_active_link && {
                    ...state.suggestion_active_link, id: state.suggestion_active_link.endpoint_id
                }
            };
            render();
        }
    });

    root.addEventListener("submit", function (event) {
        const searchForm = event.target.closest("[data-asset-search-form]");
        if (searchForm) {
            event.preventDefault();
            const query = new FormData(searchForm).get("asset_q").toString().trim();
            if (query.length < 2) {
                setFormStatus(searchForm, "Informe pelo menos dois caracteres para pesquisar.");
                return;
            }
            selectedAsset = null;
            load(query);
            return;
        }
        const createForm = event.target.closest("[data-asset-create-form]");
        if (createForm) {
            event.preventDefault();
            const form = new FormData(createForm);
            submitMutation(createForm, {
                action: "create_asset", machine_id: panel.dataset.machineId,
                identity_confirmed: form.get("identity_confirmed") === "on", reason: form.get("reason") || ""
            });
            return;
        }
        const linkForm = event.target.closest("[data-asset-link-form]");
        if (linkForm && selectedAsset) {
            event.preventDefault();
            const form = new FormData(linkForm);
            const transfer = form.get("transfer_confirmed") === "on";
            const previous = selectedAsset.active_endpoint;
            if ((state.link_identity_changed || (previous && (previous.state !== "current" ||
                previous.id !== panel.dataset.endpointUuid))) && !transfer) {
                setFormStatus(linkForm, "Marque a confirmação para encerrar o vínculo ativo anterior.");
                return;
            }
            if (!window.confirm("Confirmar associação do patrimônio " + selectedAsset.code + " ao endpoint UUID " + panel.dataset.endpointUuid + "?")) return;
            submitMutation(linkForm, {
                action: "link_asset", machine_id: panel.dataset.machineId, asset_id: selectedAsset.id,
                identity_confirmed: form.get("identity_confirmed") === "on", transfer_confirmed: transfer,
                reason: form.get("reason") || ""
            });
            return;
        }
        const recordForm = event.target.closest("[data-asset-record-form]");
        if (recordForm) {
            event.preventDefault();
            const form = new FormData(recordForm);
            submitMutation(recordForm, {
                action: "add_record", machine_id: panel.dataset.machineId,
                record_type: form.get("record_type"), occurred_at: form.get("occurred_at"),
                description: form.get("description"), previous_responsible: form.get("previous_responsible"),
                new_responsible: form.get("new_responsible")
            });
        }
    });
}());
