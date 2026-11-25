(() => {
    const button = document.querySelector('[data-noc-tv]');
    if (!button) return;

    const screen = document.documentElement;
    const body = document.body;
    const label = button.querySelector('[data-noc-tv-label]');
    const status = document.querySelector('[data-noc-tv-status]');
    let native = false;
    let pending = false;

    function render(active, message = '') {
        body.classList.toggle('noc-tv-mode', active);
        button.classList.toggle('is-active', active);
        button.setAttribute('aria-pressed', String(active));
        label.textContent = active ? 'Sair do Modo TV' : 'Modo TV';
        status.textContent = message;
    }

    function restore() {
        native = false;
        render(false);
        button.focus({preventScroll: true});
    }

    button.addEventListener('click', async () => {
        if (pending) return;
        if (body.classList.contains('noc-tv-mode')) {
            if (native && document.fullscreenElement === screen) {
                try {
                    await document.exitFullscreen();
                } catch {
                    status.textContent = 'Não foi possível sair pela página. Use Esc.';
                    return;
                }
            }
            restore();
            return;
        }
        pending = true;
        try {
            if (!screen.requestFullscreen || document.fullscreenEnabled === false) {
                render(true, 'Apresentação na página. Use Esc para sair.');
                return;
            }
            await screen.requestFullscreen();
            native = document.fullscreenElement === screen;
            if (native) render(true);
            else render(true, 'Apresentação na página. Use Esc para sair.');
        } catch {
            render(true, 'Apresentação na página. Use Esc para sair.');
        } finally {
            pending = false;
        }
    });

    document.addEventListener('fullscreenchange', () => {
        if (native && document.fullscreenElement !== screen) restore();
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || native || !body.classList.contains('noc-tv-mode')) return;
        // The existing drawer handles the first Escape when its details are open.
        if (document.querySelector('[data-noc-drawer].is-open')) return;
        restore();
    });
})();
