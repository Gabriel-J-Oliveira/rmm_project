const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('AD values render as text in table and details', async () => {
    const nodes = [];
    class Node {
        constructor(tag) {
            this.tag = tag;
            this.children = [];
            this.listeners = {};
            this.dataset = {};
            this.value = '';
            this.hidden = false;
            nodes.push(this);
        }
        set textContent(value) { this.text = String(value); }
        get textContent() { return this.text || this.children.map((child) => child.textContent).join(''); }
        set innerHTML(_value) { throw new Error('Untrusted HTML insertion'); }
        appendChild(child) { this.children.push(child); return child; }
        append(...children) { this.children.push(...children); }
        replaceChildren(...children) { this.children = children.filter(Boolean); }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        setAttribute(name, value) { this[name] = value; }
        querySelector() { return { value: 'synthetic-csrf' }; }
        focus() {}
    }

    const ids = new Map();
    const document = {
        getElementById(id) {
            if (!ids.has(id)) ids.set(id, new Node(id));
            return ids.get(id);
        },
        createElement(tag) { return new Node(tag); },
        addEventListener(type, callback) { this[type] = callback; },
        querySelectorAll() { return []; },
    };
    const hostname = '<img src=x onerror=alert(1)>';
    const displayName = '<script>alert(1)</script>';
    const ou = '<b>Financeiro</b>';
    const computer = {
        hostname, fqdn: 'host<test>.control.local', ad_name: hostname,
        ou, ou_dn: ou, operating_system: 'Windows <test>', enabled: true,
        last_logon_at: null, dns_status: 'UNRESOLVED', ipv4_addresses: [],
        correlation_status: 'UNMANAGED', selectable: true,
        ad_user: { display_name: displayName, email: 'user<test>@example.test', ou },
    };
    const summary = {
        total: 1, managed: 0, unmanaged: 1, disabled: 0,
        dns_unresolved: 1, conflicts: 0, old_ad_activity: 0,
    };
    const script = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'agent_install_discovery.js'), 'utf8');
    let preflightBody;
    ids.set('ad-discovery', new Node('ad-discovery'));
    ids.get('ad-discovery').dataset.preflightUrl = '/agent-install/ad-computers/preflight/';
    vm.runInNewContext(script, {
        document,
        window: { lucide: { createIcons() {} } },
        fetch: async (url, options) => {
            if (url === '/agent-install/ad-computers/preflight/') {
                preflightBody = JSON.parse(options.body);
                return { ok: true, json: async () => ({ status: 'NOT_READY', checks: {} }) };
            }
            return { ok: true, json: async () => ({ computers: [computer], summary }) };
        },
    });
    document.DOMContentLoaded();
    await ids.get('ad-discovery-form').listeners.submit({ preventDefault() {} });
    const details = nodes.find((node) => node.tag === 'button' && node.textContent === 'Detalhes');
    assert.ok(details);
    details.listeners.click();

    const rendered = nodes.map((node) => node.text || '');
    for (const value of [hostname, displayName, ou, computer.fqdn, computer.ad_user.email]) {
        assert.ok(rendered.includes(value), `${value} was not rendered as text`);
    }
    assert.equal(nodes.some((node) => ['img', 'script', 'b'].includes(node.tag)), false);

    ids.get('ad-select-visible').checked = true;
    ids.get('ad-select-visible').listeners.change();
    ids.get('ad-prepare-button').listeners.click();
    const preflightForm = nodes.find((node) => node.tag === 'form' && node.className === 'ad-preflight-form');
    assert.ok(preflightForm);
    const password = nodes.find((node) => node.tag === 'input' && node.type === 'password');
    const username = nodes.find((node) => node.tag === 'input' && node.type === 'text');
    assert.ok(password);
    username.value = 'SyntheticAdmin';
    password.value = 'SUPER_SECRET_TEST_PASSWORD_91827';
    await preflightForm.listeners.submit({ preventDefault() {} });
    assert.equal(preflightBody.password, 'SUPER_SECRET_TEST_PASSWORD_91827');
    assert.equal(password.value, '');
    assert.equal(nodes.some((node) => ['img', 'script', 'b'].includes(node.tag)), false);
    assert.ok(nodes.some((node) => node.text === 'NÃO PRONTO PARA INSTALAÇÃO'));
});
