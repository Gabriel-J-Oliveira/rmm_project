const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), {spawn} = require('node:child_process'), assert = require('node:assert/strict');
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'nightowl-live-lifecycle-'));
const script = fs.readFileSync('static/js/endpoint_live.js', 'utf8');
const css = fs.readFileSync('static/css/nightowl.css', 'utf8');
const fixture = `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><style>${css}</style><body class="page-endpoint-detail"><section data-endpoint-detail data-live-allowed="yes" data-endpoint-id="synthetic"><section data-endpoint-tab-panel="monitoring"><div data-live-placement></div></section></section><script>
window.testCalls=[];window.delayGet=false;window.failStop=false;window.expire=false;window.deferred=null;
const pt={sample_id:'1',collected_at:new Date().toISOString(),metrics:{'cpu.usage_percent':22,'memory.used_percent':60,'memory.available_percent':40,'network.nic.received_bytes_per_sec':1024,'network.nic.sent_bytes_per_sec':100,'disk.os.read_latency_ms':2,'disk.os.write_latency_ms':3},quality:{}};
window.fetch=async (url,opts={})=>{let action=opts.method==='POST'?JSON.parse(opts.body).action:'get';window.testCalls.push(action);if(action==='stop'&&window.failStop)return {ok:false,json:async()=>({error:'synthetic_stop_failure'})};if(action==='get'&&window.delayGet)await new Promise(r=>window.deferred=r);return {ok:true,json:async()=> action==='get'?({state:window.expire?'expired':'active',session_id:'fixture',points:[pt],last_sample_at:pt.collected_at,stale:false}):({state:action==='stop'?'expired':'active',session_id:'fixture',expires_at:new Date(Date.now()+45000).toISOString()})}};
</script><script>${script}</script></body>`;
const html = path.join(tmp, 'fixture.html'); fs.writeFileSync(html, fixture);
(async () => {
 const proc = spawn(chromePath, ['--headless=new','--disable-gpu','--no-first-run','--remote-debugging-port=0','--user-data-dir='+path.join(tmp,'profile'), 'file:///'+html.replace(/\\/g,'/')], {stdio:'ignore'});
 try {
  let port; for(let i=0;i<100;i++){try{port=fs.readFileSync(path.join(tmp,'profile','DevToolsActivePort'),'utf8').split('\n')[0];break}catch{} await new Promise(r=>setTimeout(r,100));} assert(port,'CDP startup');
  const tabs=await (await fetch('http://127.0.0.1:'+port+'/json')).json(); const ws=new WebSocket(tabs.find(t=>t.type==='page').webSocketDebuggerUrl); await new Promise((r,j)=>{ws.onopen=r;ws.onerror=j});
  let seq=0;const waiting=new Map(); ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id&&waiting.has(m.id)){const p=waiting.get(m.id);waiting.delete(m.id);m.error?p.reject(Error(m.error.message)):p.resolve(m.result)}};
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++seq;waiting.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))});
  const evaluate=async expression=>{const result=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(result.exceptionDetails) throw Error(result.exceptionDetails.text);return result.result.value};
  await new Promise(r=>setTimeout(r,300));
  await evaluate("document.querySelector('[data-live-toggle]').click()"); await new Promise(r=>setTimeout(r,200));
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"), /Recebendo amostras/);
  await evaluate("document.querySelector('[data-endpoint-tab-panel]').hidden=true; document.querySelector('[data-endpoint-tab-panel]').hidden=false");
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"), /Recebendo amostras/);
  assert.equal(await evaluate("document.querySelectorAll('.endpoint-live').length"),1);
  await evaluate("document.querySelector('[data-live-tip]').focus()");
  assert.match(await evaluate("document.querySelector('[data-chart-tooltip]').textContent"), /22/);
  for(const [name,width,height] of [['desktop',1440,1000],['mobile',390,844]]){await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<500});assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true);let image=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});fs.writeFileSync(path.join(os.tmpdir(),'nightowl-live-'+name+'-charts.png'),Buffer.from(image.data,'base64'));}
  await evaluate('window.delayGet=true'); await new Promise(r=>setTimeout(r,5200));
  assert.equal(await evaluate('!!window.deferred'),true,'delayed response');
  await evaluate("document.querySelector('[data-live-toggle]').click()"); await new Promise(r=>setTimeout(r,100));
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"),/Análise encerrada/);
  assert.equal(await evaluate("document.querySelector('[data-live-body]').hidden"),false);
  await evaluate('window.deferred()'); await new Promise(r=>setTimeout(r,100));
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"),/Análise encerrada/);
  await evaluate("document.querySelector('[data-live-clear]').click()");
  assert.equal(await evaluate("document.querySelector('[data-live-body]').hidden"),true);
  await evaluate('window.delayGet=false;window.failStop=true'); await evaluate("document.querySelector('[data-live-toggle]').click()"); await new Promise(r=>setTimeout(r,100));
  await evaluate("document.querySelector('[data-live-clear]').click()"); await new Promise(r=>setTimeout(r,100));
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"),/Não foi possível encerrar/);
  assert.equal(await evaluate("document.querySelector('[data-live-body]').hidden"),false);
  await evaluate('window.failStop=false');await evaluate("document.querySelector('[data-live-clear]').click()");await new Promise(r=>setTimeout(r,100));
  assert.equal(await evaluate("document.querySelector('[data-live-body]').hidden"),true);
  await evaluate('window.expire=false');await evaluate("document.querySelector('[data-live-toggle]').click()");await new Promise(r=>setTimeout(r,100));
  await evaluate('window.expire=true');await new Promise(r=>setTimeout(r,5200));
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"),/Sessão expirada/);
  assert.equal(await evaluate("document.querySelector('[data-live-body]').hidden"),false);
  await evaluate("document.querySelector('[data-live-clear]').click()");
  await evaluate('window.expire=true');await evaluate("document.querySelector('[data-live-toggle]').click()");await new Promise(r=>setTimeout(r,100));
  assert.match(await evaluate("document.querySelector('[data-live-state]').textContent"),/Sessão expirada/);
  ws.close(); console.log('PASS browser: start, freeze, stale response, clear, stop failure, expiry, desktop/mobile');
 } finally {proc.kill()}
})().catch(err=>{console.error(err);process.exitCode=1});
