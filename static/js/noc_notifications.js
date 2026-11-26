(() => {
    const center = document.querySelector('[data-notification-center]');
    if (!center) return;

    const url = center.dataset.url;
    const operator = center.dataset.user;
    const list = center.querySelector('[data-notification-list]');
    const count = center.querySelector('[data-notification-count]');
    const status = center.querySelector('[data-notification-status]');
    const empty = center.querySelector('[data-notification-empty]');
    const sound = center.querySelector('[data-notification-sound]');
    const audioButton = center.querySelector('[data-notification-audio]');
    const audioStatus = center.querySelector('[data-notification-audio-status]');
    const csrf = center.querySelector('input[name=csrfmiddlewaretoken]')?.value || '';
    const seenKey = `nightowl.notifications.seen.${operator}`;
    const memorySeen = new Set();
    let cursor = null;
    let inFlight = false;
    let pendingEntries = null;
    let audioContext = null;
    let audioReady = false;
    let lastSignature = '';

    function escapeSameOriginPath(value) {
        try {
            const target = new URL(value, window.location.origin);
            return target.origin === window.location.origin ? target.pathname + target.search + target.hash : null;
        } catch {
            return null;
        }
    }

    function readSeen() {
        try {
            const values = JSON.parse(window.localStorage.getItem(seenKey) || '[]');
            return new Set(Array.isArray(values) ? values.filter((item) => typeof item === 'string').slice(-100) : []);
        } catch {
            return new Set(memorySeen);
        }
    }

    function writeSeen(values) {
        const trimmed = Array.from(values).slice(-100);
        trimmed.forEach((item) => memorySeen.add(item));
        try { window.localStorage.setItem(seenKey, JSON.stringify(trimmed)); } catch { /* In-memory dedupe remains active in this tab. */ }
    }

    function withDeliveryLock(callback) {
        if (navigator.locks?.request) {
            return navigator.locks.request(`nightowl-notification-delivery-${operator}`, {mode: 'exclusive'}, callback);
        }
        return callback();
    }

    function showToast(entry) {
        let stack = document.querySelector('.noc-notification-toasts');
        if (!stack) {
            stack = document.createElement('div');
            stack.className = 'noc-notification-toasts';
            stack.setAttribute('aria-live', 'polite');
            stack.setAttribute('aria-label', 'Novas notificações');
            document.body.append(stack);
        }
        const toast = document.createElement('div');
        toast.className = `alert-toast toast-${entry.severity === 'critical' ? 'critical' : 'info'}`;
        const link = document.createElement('a');
        const safePath = escapeSameOriginPath(entry.url);
        link.textContent = `${entry.title} · ${entry.endpoint}`;
        if (safePath) link.href = safePath;
        else link.removeAttribute('href');
        toast.append(link);
        stack.append(toast);
        window.setTimeout(() => toast.remove(), 6500);
    }

    function playTone() {
        if (!audioReady || !audioContext || audioContext.state !== 'running') {
            audioStatus.textContent = 'Ative o áudio neste navegador para ouvir os avisos.';
            return;
        }
        const oscillator = audioContext.createOscillator();
        const gain = audioContext.createGain();
        oscillator.type = 'sine';
        oscillator.frequency.value = 740;
        gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.035, audioContext.currentTime + 0.025);
        gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.18);
        oscillator.connect(gain);
        gain.connect(audioContext.destination);
        oscillator.start();
        oscillator.stop(audioContext.currentTime + 0.19);
    }

    async function deliverNotices(notices) {
        if (!Array.isArray(notices) || !notices.length) return;
        await withDeliveryLock(async () => {
            const seen = readSeen();
            let shouldSound = false;
            for (const notice of notices) {
                if (!notice || typeof notice.id !== 'string' || seen.has(notice.id)) continue;
                seen.add(notice.id);
                if (notice.visual === true) showToast(notice);
                if (notice.sound === true) shouldSound = true;
            }
            writeSeen(seen);
            if (shouldSound) playTone();
        });
    }

    function updateEntries(entries) {
        const safeEntries = Array.isArray(entries) ? entries : [];
        const signature = JSON.stringify(safeEntries);
        if (signature === lastSignature) return;
        if (list.contains(document.activeElement)) {
            pendingEntries = safeEntries;
            return;
        }
        const scrollTop = list.scrollTop;
        list.replaceChildren();
        for (const entry of safeEntries) {
            const item = document.createElement('li');
            item.className = 'noc-notification-entry';
            item.classList.toggle('is-read', entry.read === true);
            const link = document.createElement('a');
            const safePath = escapeSameOriginPath(entry.url);
            if (safePath) link.href = safePath;
            link.textContent = `${entry.title || 'Alerta'} · ${entry.endpoint || 'Endpoint'}`;
            const meta = document.createElement('span');
            const when = new Date(entry.occurred_at);
            meta.textContent = `${entry.severity || 'info'} · ${Number.isNaN(when.valueOf()) ? 'horário indisponível' : when.toLocaleString()}`;
            item.append(link, meta);
            if (entry.read !== true && typeof entry.id === 'string') {
                const button = document.createElement('button');
                button.type = 'button';
                button.dataset.notificationRead = entry.id;
                button.textContent = 'Marcar como lida';
                item.append(button);
            } else {
                const readLabel = document.createElement('span');
                readLabel.className = 'noc-notification-read-label';
                readLabel.textContent = 'Lida';
                item.append(readLabel);
            }
            list.append(item);
        }
        lastSignature = signature;
        list.scrollTop = scrollTop;
        empty.hidden = safeEntries.length !== 0;
        pendingEntries = null;
    }

    list.addEventListener('focusout', () => window.setTimeout(() => {
        if (pendingEntries && !list.contains(document.activeElement)) updateEntries(pendingEntries);
    }, 0));

    function updatePreferences(value) {
        const preference = ['off', 'critical', 'all'].includes(value) ? value : 'off';
        sound.value = preference;
        audioButton.hidden = preference === 'off';
        if (preference === 'off') audioStatus.textContent = '';
    }

    async function refresh() {
        if (inFlight) return;
        inFlight = true;
        const query = new URL(url, window.location.origin);
        if (cursor) query.searchParams.set('since', cursor);
        try {
            const response = await fetch(query, {headers: {Accept: 'application/json'}, cache: 'no-store'});
            if (response.redirected || !response.ok) throw new Error('notification read failed');
            const payload = await response.json();
            if (!Array.isArray(payload.entries) || !Number.isFinite(Number(payload.unread))) throw new Error('invalid notification response');
            updatePreferences(payload.sound);
            count.textContent = Number(payload.unread) > 99 ? '99+' : String(Number(payload.unread));
            count.hidden = Number(payload.unread) <= 0;
            center.querySelector('summary').setAttribute('aria-label', `${payload.unread} notificações não lidas`);
            status.textContent = `Atualizado ${new Date(payload.read_at).toLocaleTimeString()}`;
            updateEntries(payload.entries);
            if (cursor) await deliverNotices(payload.notices);
            cursor = payload.read_at;
        } catch {
            status.textContent = 'Falha ao atualizar. Dados anteriores mantidos.';
        } finally {
            inFlight = false;
        }
    }

    async function post(data) {
        const body = new FormData();
        Object.entries(data).forEach(([key, value]) => body.set(key, value));
        const response = await fetch(url, {method: 'POST', body, headers: {'X-CSRFToken': csrf, Accept: 'application/json'}});
        const payload = await response.json();
        if (!response.ok || response.redirected) throw new Error(payload.detail || 'notification action rejected');
        await refresh();
    }

    list.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-notification-read]');
        if (!button || button.disabled) return;
        button.disabled = true;
        try { await post({action: 'read', event: button.dataset.notificationRead}); }
        catch { status.textContent = 'Não foi possível marcar a notificação como lida.'; button.disabled = false; }
    });
    center.querySelector('[data-notification-read-all]').addEventListener('click', async () => {
        try { await post({action: 'read_all'}); }
        catch { status.textContent = 'Não foi possível marcar as notificações como lidas.'; }
    });
    sound.addEventListener('change', async () => {
        const value = sound.value;
        try { await post({action: 'sound', sound: value}); }
        catch { status.textContent = 'Não foi possível salvar a preferência de som.'; }
    });
    audioButton.addEventListener('click', async () => {
        const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextConstructor) {
            audioStatus.textContent = 'Áudio indisponível neste navegador.';
            return;
        }
        try {
            audioContext ||= new AudioContextConstructor();
            await audioContext.resume();
            audioReady = audioContext.state === 'running';
            audioStatus.textContent = audioReady ? 'Áudio ativado neste navegador.' : 'O navegador ainda não liberou o áudio.';
        } catch {
            audioStatus.textContent = 'Não foi possível ativar o áudio neste navegador.';
        }
    });

    refresh();
    window.setInterval(refresh, 10000);
})();
