const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/endpoint_detail.js', 'utf8');

function extractFunction(name) {
  const start = source.indexOf(`    function ${name}(`);
  assert(start >= 0, `${name} must exist`);
  const end = source.indexOf('\n    function ', start + 1);
  assert(end > start, `${name} must have a following function boundary`);
  return source.slice(start, end);
}

const functions = [
  'getManualUpdateReleases', 'releaseStatusText', 'updateChannelIsPending',
  'updateModalSubmitState', 'updateChannelSaveState', 'selectedUpdateRelease',
  'populateUpdateReleaseOptions', 'handleUpdateChannelSelectionChange',
  'reloadUpdateChannelReleases', 'saveUpdateChannel'
].map(extractFunction).join('\n');

function makeNode() {
  const classes = new Set();
  const node = {
    value: '', hidden: false, disabled: false, checked: false,
    dataset: {}, children: [], className: '',
    classList: {
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
      contains(value) { return classes.has(value); },
      toggle(value, force) {
        const shouldAdd = force === undefined ? !classes.has(value) : !!force;
        if (shouldAdd) classes.add(value); else classes.delete(value);
        return shouldAdd;
      }
    },
    appendChild(child) { this.children.push(child); },
    setAttribute() {}
  };
  let text = '';
  Object.defineProperty(node, 'textContent', {
    get() { return text; },
    set(value) { text = String(value); node.children.length = 0; }
  });
  return node;
}

const stableRelease = {
  id: 'rc46', version: '0.1.1.0-rc46', channel: 'stable', source_channel: 'stable',
  status: 'published', eligible: true, metadata_complete: true, size: 100,
  minimum_updater_version: '0.1.1.0-rc6', revoked: false
};
const developmentRelease = {
  id: 'rc47', version: '0.1.1.0-rc47', channel: 'development', source_channel: 'development',
  status: 'paused', eligible: true, metadata_complete: true, size: 110,
  minimum_updater_version: '0.1.1.0-rc6', revoked: false
};

function makeContext({postFails = false, refreshFails = false} = {}) {
  const channelSelect = makeNode();
  channelSelect.value = 'stable';
  channelSelect.dataset.persistedChannel = 'stable';
  const channelSave = makeNode();
  const channelStatus = makeNode();
  const reloadButton = makeNode();
  const releaseSelect = makeNode();
  const submitButton = makeNode();
  const errorBox = makeNode();
  const summaryChannel = makeNode();
  const details = makeNode();
  const calls = [];
  const endpointDetail = {
    hostname: 'PILOT-PC',
    agent: {
      version: '0.1.1.0-rc46', updateChannel: 'stable', updatePolicy: 'manual',
      canChangeChannel: true, updateReleases: [stableRelease, developmentRelease]
    },
    jobs: []
  };
  const dialog = {
    querySelector(selector) {
      return ({
        '[data-update-channel-select]': channelSelect,
        '[data-update-channel-save]': channelSave,
        '[data-update-channel-status]': channelStatus,
        '[data-update-channel-reload]': reloadButton,
        '[data-update-release-select]': releaseSelect,
        '[data-update-submit]': submitButton,
        '[data-update-modal-error]': errorBox,
        '[data-update-summary="channel"]': summaryChannel,
        '[data-update-release-details]': details,
        '[data-force-downgrade]': null,
        '[data-confirm-downgrade]': null
      })[selector] || null;
    }
  };
  const ctx = {
    root: {dataset: {endpointId: 'endpoint-123'}}, endpointDetail, realEndpointPayload: null,
    updateModal: {
      savingChannel: false, refreshingReleases: false, sending: false,
      releasesSynchronized: true, releaseOptionsChannel: 'stable', dialog
    },
    setModalError(message) { errorBox.textContent = message || ''; errorBox.hidden = !message; },
    renderReleaseDetails(release) { ctx.updateModal.selectedRelease = release || null; },
    getCookie() { return 'csrf'; }, encodeURIComponent, URLSearchParams,
    document: {createElement() { return makeNode(); }},
    compareVersions(left, right) { return String(left).localeCompare(String(right), undefined, {numeric: true}); },
    fetch(url, options) {
      calls.push({url, options});
      if (postFails) {
        return Promise.resolve({
          ok: false, redirected: false,
          headers: {get() { return 'application/json'; }},
          json: async () => ({error: 'forbidden', detail: 'Sem permissao.'})
        });
      }
      return Promise.resolve({
        ok: true, redirected: false,
        headers: {get() { return 'application/json'; }},
        json: async () => ({status: 'ok'})
      });
    },
    fetchRealEndpointPayload() {
      if (refreshFails) return Promise.reject(new Error('refresh_failed'));
      const payload = {
        endpoint: {},
        agent_update_policy: {channel: 'development', update_policy: 'manual'},
        can_change_update_policy: true,
        agent_update_releases: [developmentRelease]
      };
      ctx.realEndpointPayload = payload;
      return Promise.resolve(payload);
    },
    normalizeRealEndpointPayload(payload) {
      return {
        hostname: 'PILOT-PC', jobs: [],
        agent: {
          version: '0.1.1.0-rc46', updateChannel: payload.agent_update_policy.channel,
          updatePolicy: 'manual', canChangeChannel: true,
          updateReleases: payload.agent_update_releases
        }
      };
    },
    validateUpdateSelection() {
      const release = ctx.selectedUpdateRelease();
      if (!release || !release.eligible || release.revoked || !release.metadata_complete || !release.size || !release.minimum_updater_version) return null;
      return {release, force: false};
    }
  };
  vm.runInNewContext(`${functions}\nthis.getManualUpdateReleases = getManualUpdateReleases;\nthis.releaseStatusText = releaseStatusText;\nthis.updateChannelIsPending = updateChannelIsPending;\nthis.updateModalSubmitState = updateModalSubmitState;\nthis.updateChannelSaveState = updateChannelSaveState;\nthis.selectedUpdateRelease = selectedUpdateRelease;\nthis.populateUpdateReleaseOptions = populateUpdateReleaseOptions;\nthis.handleUpdateChannelSelectionChange = handleUpdateChannelSelectionChange;\nthis.reloadUpdateChannelReleases = reloadUpdateChannelReleases;\nthis.saveUpdateChannel = saveUpdateChannel;`, ctx);
  ctx.populateUpdateReleaseOptions('');
  ctx.updateModalSubmitState();
  return {ctx, channelSelect, channelSave, channelStatus, reloadButton, releaseSelect, submitButton, errorBox, summaryChannel, details, calls};
}

async function settle() {
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
}

(async () => {
  const pending = makeContext();
  pending.releaseSelect.value = 'rc46';
  pending.channelSelect.value = 'development';
  pending.ctx.handleUpdateChannelSelectionChange();
  assert.equal(pending.ctx.updateChannelIsPending(), true);
  assert.equal(pending.submitButton.disabled, true, 'unsaved channel change blocks update');
  assert.equal(pending.channelSave.disabled, false, 'Save channel remains available');
  assert.match(pending.channelStatus.textContent, /Salve o canal selecionado/);
  assert.equal(pending.releaseSelect.children.length, 1, 'old channel releases are removed');
  assert.equal(pending.releaseSelect.value, '', 'old release selection is cleared');

  pending.channelSelect.value = 'stable';
  pending.ctx.handleUpdateChannelSelectionChange();
  assert.equal(pending.ctx.updateChannelIsPending(), false, 'returning to persisted channel cancels pending change');
  assert.equal(pending.releaseSelect.children.length, 2, 'persisted channel releases are restored');
  assert.equal(pending.releaseSelect.value, '', 'old release selection is not restored');
  assert.equal(pending.channelSave.disabled, true);
  assert.equal(pending.submitButton.disabled, true, 'explicit release choice is still required');
  pending.releaseSelect.value = 'rc46';
  pending.ctx.updateModalSubmitState();
  assert.equal(pending.submitButton.disabled, false, 'valid synchronized release enables update');

  const successful = makeContext();
  successful.channelSelect.value = 'development';
  successful.ctx.handleUpdateChannelSelectionChange();
  successful.ctx.saveUpdateChannel();
  await settle();
  assert.equal(successful.calls.length, 1, 'channel save sends one policy request and no job request');
  assert.equal(successful.calls[0].url, '/api/endpoints/endpoint-123/update-policy/');
  assert.deepEqual(Object.fromEntries(new URLSearchParams(successful.calls[0].options.body)), {
    update_channel: 'development',
    reason: 'Solicitacao explicita de alteracao de canal pelo modal de atualizacao manual.'
  });
  assert.equal(successful.ctx.endpointDetail.agent.updateChannel, 'development');
  assert.equal(successful.ctx.updateModal.releasesSynchronized, true);
  assert.equal(successful.releaseSelect.children.length, 2, 'development releases are reloaded');
  assert.equal(successful.releaseSelect.children[1].value, 'rc47');
  assert.equal(successful.releaseSelect.value, '', 'refreshed release list requires explicit selection');
  assert.equal(successful.summaryChannel.textContent, 'development');
  assert.equal(successful.submitButton.disabled, true);
  successful.releaseSelect.value = 'rc47';
  successful.ctx.updateModalSubmitState();
  assert.equal(successful.submitButton.disabled, false);

  const failedSave = makeContext({postFails: true});
  failedSave.channelSelect.value = 'development';
  failedSave.ctx.handleUpdateChannelSelectionChange();
  failedSave.ctx.saveUpdateChannel();
  await settle();
  assert.equal(failedSave.channelSelect.value, 'stable', 'failed save restores persisted channel');
  assert.equal(failedSave.ctx.endpointDetail.agent.updateChannel, 'stable');
  assert.equal(failedSave.releaseSelect.children.length, 2, 'failed save restores persisted channel list');
  assert.equal(failedSave.releaseSelect.value, '', 'failed save does not restore stale release selection');
  assert.match(failedSave.errorBox.textContent, /Sem permissao/);
  assert.equal(failedSave.ctx.updateModal.releasesSynchronized, true);

  const failedRefresh = makeContext({refreshFails: true});
  failedRefresh.channelSelect.value = 'development';
  failedRefresh.ctx.handleUpdateChannelSelectionChange();
  failedRefresh.ctx.saveUpdateChannel();
  await settle();
  assert.equal(failedRefresh.ctx.endpointDetail.agent.updateChannel, 'development', 'server-confirmed channel is retained');
  assert.equal(failedRefresh.ctx.updateModal.releasesSynchronized, false);
  assert.equal(failedRefresh.submitButton.disabled, true, 'failed refresh keeps update blocked');
  assert.equal(failedRefresh.channelSelect.disabled, true, 'channel stays locked until fresh data arrives');
  assert.equal(failedRefresh.releaseSelect.children.length, 1, 'stale release choices stay cleared');
  assert.equal(failedRefresh.reloadButton.hidden, false, 'retry reload is available');
  assert.match(failedRefresh.channelStatus.textContent, /Canal salvo/);
  failedRefresh.ctx.fetchRealEndpointPayload = function () {
    const payload = {
      endpoint: {}, agent_update_policy: {channel: 'development', update_policy: 'manual'},
      can_change_update_policy: true, agent_update_releases: [developmentRelease]
    };
    failedRefresh.ctx.realEndpointPayload = payload;
    return Promise.resolve(payload);
  };
  await failedRefresh.ctx.reloadUpdateChannelReleases();
  assert.equal(failedRefresh.ctx.updateModal.releasesSynchronized, true, 'retry unlocks after fresh authoritative data');
  assert.equal(failedRefresh.releaseSelect.children[1].value, 'rc47');
  assert.equal(failedRefresh.releaseSelect.value, '', 'retry still requires explicit release selection');
  assert.equal(failedRefresh.submitButton.disabled, true);
  failedRefresh.releaseSelect.value = 'rc47';
  failedRefresh.ctx.updateModalSubmitState();
  assert.equal(failedRefresh.submitButton.disabled, false);
  console.log('endpoint update channel pending/save/reload/failure/no-job flows passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
