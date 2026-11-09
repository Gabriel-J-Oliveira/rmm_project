const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/endpoint_detail.js', 'utf8');
const start = source.indexOf('    function saveUpdateChannel() {');
const end = source.indexOf('    function submitUpdateFromModal()', start);
assert(start >= 0 && end > start, 'channel save handler must be available as an isolated flow');

function makeNode() {
  const classes = new Set();
  return {
    value: '', textContent: '', hidden: false, disabled: false, dataset: {}, children: [],
    classList: {
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
      contains(value) { return classes.has(value); }
    },
    appendChild(child) { this.children.push(child); }
  };
}

async function runScenario({failSave = false} = {}) {
  const channelSelect = makeNode();
  channelSelect.value = 'development';
  channelSelect.dataset.persistedChannel = 'stable';
  const channelStatus = makeNode();
  const summaryChannel = makeNode();
  const releaseSelect = makeNode();
  const errorMessages = [];
  const calls = [];
  let refreshedPayload = null;
  const dialog = {
    querySelector(selector) {
      return ({
        '[data-update-channel-select]': channelSelect,
        '[data-update-channel-status]': channelStatus,
        '[data-update-summary="channel"]': summaryChannel,
        '[data-update-release-select]': releaseSelect
      })[selector] || null;
    }
  };
  const ctx = {
    root: {dataset: {endpointId: 'endpoint-123'}},
    endpointDetail: {hostname: 'PILOT-PC', agent: {updateChannel: 'stable', canChangeChannel: true, updateReleases: []}},
    updateModal: {savingChannel: false, sending: false, dialog},
    setModalError(message) { if (message) errorMessages.push(message); },
    updateChannelSaveState() {},
    updateModalSubmitState() {},
    getCookie() { return 'csrf'; },
    encodeURIComponent,
    URLSearchParams,
    document: {createElement() { return makeNode(); }},
    fetch(url, options) {
      calls.push({url, options});
      const payload = failSave ? {error: 'forbidden', detail: 'Sem permissao.'} : {status: 'ok'};
      return Promise.resolve({
        ok: !failSave,
        redirected: false,
        headers: {get() { return 'application/json'; }},
        json: async () => payload
      });
    },
    fetchRealEndpointPayload() {
      refreshedPayload = {endpoint: {}, agent_update_policy: {channel: 'development'}, agent_update_releases: [
        {id: 'rc47', version: '0.1.1.0-rc47', channel: 'development', source_channel: 'development', status: 'paused', eligible: true}
      ]};
      ctx.realEndpointPayload = refreshedPayload;
      return Promise.resolve(refreshedPayload);
    },
    normalizeRealEndpointPayload(payload) {
      return {hostname: 'PILOT-PC', agent: {
        updateChannel: payload.agent_update_policy.channel,
        canChangeChannel: true,
        updateReleases: payload.agent_update_releases
      }};
    },
    getManualUpdateReleases() { return ctx.endpointDetail.agent.updateReleases; },
    releaseStatusText(release) { return release.status === 'paused' ? 'Pausada (rollout)' : release.status; },
    renderReleaseDetails() {}
  };
  vm.runInNewContext(source.slice(start, end) + '; this.save = saveUpdateChannel;', ctx);
  ctx.save();
  await new Promise(resolve => setImmediate(resolve));
  return {ctx, channelSelect, channelStatus, summaryChannel, releaseSelect, errorMessages, calls, refreshedPayload};
}

(async () => {
  const successful = await runScenario();
  assert.equal(successful.calls.length, 1);
  assert.equal(successful.calls[0].url, '/api/endpoints/endpoint-123/update-policy/');
  assert.deepEqual(Object.fromEntries(new URLSearchParams(successful.calls[0].options.body)), {
    update_channel: 'development',
    reason: 'Solicitacao explicita de alteracao de canal pelo modal de atualizacao manual.'
  });
  assert.equal(successful.ctx.endpointDetail.agent.updateChannel, 'development');
  assert.equal(successful.ctx.endpointDetail.agent.updateReleases[0].id, 'rc47');
  assert.equal(successful.releaseSelect.children[1].value, 'rc47');
  assert.match(successful.channelStatus.textContent, /Canal salvo/);
  assert.equal(successful.ctx.updateModal.savingChannel, false);

  const failed = await runScenario({failSave: true});
  assert.equal(failed.channelSelect.value, 'stable');
  assert.equal(failed.channelSelect.dataset.persistedChannel, 'stable');
  assert.equal(failed.ctx.endpointDetail.agent.updateChannel, 'stable');
  assert.equal(failed.refreshedPayload, null);
  assert.match(failed.errorMessages.at(-1), /Sem permissao/);
  assert.equal(failed.ctx.updateModal.savingChannel, false);
  console.log('endpoint update channel save, refresh, failure handling and no-job behavior passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
