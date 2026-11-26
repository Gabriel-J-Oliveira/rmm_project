(function () {
    var root = document.querySelector("[data-noc-root]");
    if (!root) return;

    var operational = window.NightOwlOperational;
    var refreshEverySeconds = 10;
    var content = root.querySelector("[data-noc-content]");
    var interval = root.querySelector("[data-noc-interval]");
    var status = root.querySelector("[data-noc-refresh-status]");
    var inFlight = false;
    var deferredContent = null;
    var readingAt = Date.now();
    var refreshAt = readingAt;
    var remaining = refreshEverySeconds;
    var countdown = document.querySelector("[data-noc-countdown]");
    var refreshButton = document.querySelector("[data-noc-refresh]");
    var autoButton = document.querySelector("[data-noc-auto]");
    var drawer = document.querySelector("[data-noc-drawer]");
    var drawerBackdrop = document.querySelector("[data-noc-drawer-backdrop]");
    var drawerClose = document.querySelector("[data-noc-drawer-close]");
    var toast = document.querySelector("[data-noc-toast]");
    var autoRefreshEnabled = true;

    function renderCountdown() {
        if (countdown) {
            countdown.textContent = autoRefreshEnabled ? remaining + "s" : "pausado";
        }
    }

    function showToast(message) {
        if (operational) {
            operational.showToast(message || "Ação registrada.", { target: toast, timeout: 3200 });
            return;
        }
        if (!toast) return;
        toast.textContent = message || "Ação registrada.";
        toast.hidden = false;
        clearTimeout(showToast.timer);
        showToast.timer = setTimeout(function () {
            toast.hidden = true;
        }, 3200);
    }

    function openDrawer(card) {
        if (!drawer) return;
        drawer.querySelector("[data-noc-drawer-kind]").textContent = card.dataset.drawerKind || "Contexto";
        drawer.querySelector("[data-noc-drawer-title]").textContent = card.dataset.title || "Item do NOC";
        drawer.querySelector("[data-noc-drawer-subtitle]").textContent = card.dataset.subtitle || "Monitoramento ao vivo";
        drawer.querySelector("[data-noc-drawer-status]").textContent = card.dataset.status || "--";
        drawer.querySelector("[data-noc-drawer-endpoint]").textContent = card.dataset.endpoint || "--";
        drawer.querySelector("[data-noc-drawer-ip]").textContent = card.dataset.ip || "--";
        drawer.querySelector("[data-noc-drawer-user]").textContent = card.dataset.user || "--";
        drawer.querySelector("[data-noc-drawer-body]").textContent = card.dataset.body || "Sem resumo operacional.";
        var link = drawer.querySelector("[data-noc-drawer-url]");
        if (link) {
            link.href = card.dataset.url || "#";
            link.hidden = !card.dataset.url;
        }
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        if (drawerBackdrop) drawerBackdrop.hidden = false;
        if (window.lucide && typeof window.lucide.createIcons === "function") {
            window.lucide.createIcons();
        }
    }

    function closeDrawer() {
        if (!drawer) return;
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        if (drawerBackdrop) drawerBackdrop.hidden = true;
    }

    function nocSummaryText() {
        var lines = ["Resumo NOC NightOwl"];
        document.querySelectorAll(".noc-summary-strip article, .noc-kpi").forEach(function (item) {
            var label = item.querySelector("span") ? item.querySelector("span").textContent.trim() : "";
            var value = item.querySelector("strong") ? item.querySelector("strong").textContent.trim() : "";
            if (label || value) {
                lines.push(label + ": " + value);
            }
        });
        return lines.join("\n");
    }

    function applyContent() {
        if (deferredContent === null || content.contains(document.activeElement)
            || content.querySelector("[data-noc-open-drawer]:hover, .noc-card-actions:hover")
            || (drawer && drawer.classList.contains("is-open"))) return;
        var alertsList = content.querySelector(".noc-all-alerts .noc-list");
        var scrollTop = alertsList ? alertsList.scrollTop : 0;
        content.innerHTML = deferredContent;
        alertsList = content.querySelector(".noc-all-alerts .noc-list");
        if (alertsList) alertsList.scrollTop = scrollTop;
        deferredContent = null;
        if (window.lucide) window.lucide.createIcons();
    }

    async function refresh() {
        if (inFlight) return;
        inFlight = true;
        refreshButton.disabled = true;
        var url = new URL(window.location.href);
        url.searchParams.set("fragment", "1");
        try {
            var response = await fetch(url, { headers: { Accept: "application/json" } });
            if (!response.ok || response.redirected) throw new Error("NOC read failed");
            var payload = await response.json();
            readingAt = Date.parse(payload.read_at);
            if (typeof payload.html !== "string" || !Number.isFinite(readingAt)) {
                throw new Error("Invalid NOC response");
            }
            status.textContent = "Última leitura " + new Date(readingAt).toLocaleTimeString();
            deferredContent = payload.html;
            applyContent();
        } catch (error) {
            status.textContent = "Falha na atualização. Dados anteriores mantidos.";
        } finally {
            refreshAt = Date.now();
            refreshButton.disabled = false;
            inFlight = false;
        }
    }

    refreshButton.addEventListener("click", refresh);
    interval.addEventListener("change", function () {
        refreshEverySeconds = Number(interval.value);
        refreshAt = Date.now();
        remaining = refreshEverySeconds;
        renderCountdown();
    });

    document.querySelectorAll("[data-noc-copy-summary]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (operational) {
                operational.runAction("copy_summary", {
                    toastOptions: { target: toast, timeout: 3200 },
                    copyText: nocSummaryText(),
                    description: "Resumo do NOC copiado."
                });
            } else {
                showToast("Resumo do NOC preparado.");
            }
        });
    });

    if (autoButton) {
        autoButton.addEventListener("click", function () {
            autoRefreshEnabled = !autoRefreshEnabled;
            autoButton.classList.toggle("is-active", autoRefreshEnabled);
            autoButton.setAttribute("aria-pressed", autoRefreshEnabled ? "true" : "false");
            renderCountdown();
            showToast(autoRefreshEnabled ? "Auto-refresh ligado." : "Auto-refresh pausado.");
        });
    }

    content.addEventListener("submit", async function (event) {
        var form = event.target;
        if (!form.matches("[data-noc-action]")) return;
        event.preventDefault();
        var button = form.querySelector("button[type=submit]");
        if (!button || button.disabled) return;
        button.disabled = true;
        try {
            var response = await fetch(form.action, {method: "POST", body: new FormData(form), headers: {Accept: "application/json"}});
            var payload = await response.json();
            if (response.redirected || !response.ok) {
                showToast(payload.detail || "A ação foi recusada.");
                return;
            }
            showToast(payload.detail || "Ação registrada.");
            await refresh();
        } catch (error) {
            showToast("Não foi possível confirmar o resultado da ação.");
        } finally {
            button.disabled = false;
        }
    });

    content.addEventListener("click", function (event) {
        if (event.target.closest("a, button, form, input")) return;
        var card = event.target.closest("[data-noc-open-drawer]");
        if (card) openDrawer(card);
    });
    content.addEventListener("keydown", function (event) {
        if (!event.target.matches("[data-noc-open-drawer]")) return;
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openDrawer(event.target);
        }
    });

    if (drawerClose) drawerClose.addEventListener("click", closeDrawer);
    if (drawerBackdrop) drawerBackdrop.addEventListener("click", closeDrawer);
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") closeDrawer();
    });

    renderCountdown();
    window.setInterval(function () {
        applyContent();
        var age = root.querySelector("[data-operational-age]");
        if (age) age.textContent = Math.max(0, Math.floor((Date.now() - readingAt) / 1000)) + "s";
        root.querySelectorAll("[data-noc-start]").forEach(function (node) {
            var seconds = Math.max(0, Math.floor(((node.dataset.nocEnd ? Date.parse(node.dataset.nocEnd) : Date.now()) - Date.parse(node.dateTime)) / 1000));
            if (Number.isFinite(seconds)) {
                var minutes = Math.floor(seconds / 60);
                node.textContent = minutes + " min " + seconds % 60 + " s";
            }
        });
        remaining = Math.max(0, refreshEverySeconds - Math.floor((Date.now() - refreshAt) / 1000));
        if (autoRefreshEnabled && remaining === 0) refresh();
        renderCountdown();
    }, 1000);
}());
