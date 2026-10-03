document.addEventListener('DOMContentLoaded', function () {
    const toggle = document.getElementById('agent-advanced-toggle');
    const content = document.getElementById('agent-advanced-content');
    if (!toggle || !content) return;

    toggle.addEventListener('click', function () {
        const expanded = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', String(!expanded));
        content.hidden = expanded;
    });
});
