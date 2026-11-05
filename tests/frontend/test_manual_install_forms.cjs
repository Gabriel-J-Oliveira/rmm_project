const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/manual_install_forms.js', 'utf8');

test('independent release selectors and external token stay in the result panel', async () => {
    class Element {
        constructor() { this.children=[]; this.handlers={}; this.dataset={}; this.value=''; this.textContent=''; this.hidden=false; }
        addEventListener(name, fn) { this.handlers[name]=fn; }
        append(...items) { this.children.push(...items); }
        appendChild(item) { this.children.push(item); if (!this.value) this.value=item.value; }
        replaceChildren() { this.children=[]; this.value=''; }
        get options() { return this.children; }
    }
    const forms = ['ad','external'].map(kind => {
        const f=new Element(); f.dataset={manualInstall:kind, createUrl:'/create/', historyUrl:'/history/', trustReady:'true'};
        const channel=new Element(); channel.value='stable';
        f.elements={'[data-channel]':channel, '[data-release]':new Element(), '[data-release-message]':new Element(),
            '.agent-token-error':new Element(), '[type=submit]':new Element(), '[name=csrfmiddlewaretoken]':{value:'csrf'}};
        f.querySelector=s=>f.elements[s]; return f;
    });
    const panels={'enrollment-token-result':new Element(), 'manual-token-result':new Element()};
    let ready;
    const document={addEventListener:(_,cb)=>ready=cb, querySelectorAll:()=>forms, createElement:()=>new Element(),
        getElementById:id=>id==='deployment-release-options' ? {textContent:JSON.stringify([
            {id:'stable',version:'1',channel:'stable',status:'published'},
            {id:'dev',version:'2',channel:'development',status:'paused',paused:true}])} : panels[id]};
    const copied=[];
    const fetch=async (url,opts={}) => ({ok:true,json:async()=>opts.method==='POST' ?
        {kind:'external',manual_token:'manual_test',deployment:{id:'1',command:'deploy_test',expires_at:'2030-01-01'}} :
        {deployment:{ui_status:'confirmed',endpoint:{hostname:'PC'}}}});
    vm.runInNewContext(source,{document,fetch,navigator:{clipboard:{writeText:x=>copied.push(x)}},
        FormData:class {},setInterval:()=>1,clearInterval:()=>{},Date,JSON,encodeURIComponent});
    ready();
    forms[1].elements['[data-channel]'].value='development';
    forms[1].elements['[data-channel]'].handlers.change();
    assert.equal(forms[0].elements['[data-release]'].value,'stable');
    assert.equal(forms[1].elements['[data-release]'].value,'dev');
    await forms[1].handlers.submit({preventDefault(){}});
    const result=panels['manual-token-result'];
    assert.equal(result.hidden,false);
    assert.equal(result.children[0].children[0].children[0].children[1].textContent,'deploy_test');
    assert.equal(result.children[1].children[0].children[1].textContent,'manual_test');
    result.children[1].children[1].handlers.click();
    assert.deepEqual(copied,['manual_test']);
    assert.doesNotMatch(source,/localStorage|sessionStorage|innerHTML/);
});

test('AD issues a fresh command through the authenticated batch endpoint', async () => {
    class Element {
        constructor() { this.children=[]; this.handlers={}; this.dataset={}; this.value=''; this.textContent=''; }
        addEventListener(n, f) { this.handlers[n]=f; }
        append(...x) { this.children.push(...x); }
        appendChild(x) { this.children.push(x); if (!this.value) this.value=x.value; }
        replaceChildren(...x) { this.children=x; this.value=''; }
        get options() { return this.children; }
    }
    const form=new Element(); form.dataset={manualInstall:'ad',createUrl:'/create/',historyUrl:'/history/',trustReady:'true'};
    form.e={'[data-channel]':new Element(),'[data-release]':new Element(),'[data-release-message]':new Element(),
        '.agent-token-error':new Element(),'[type=submit]':new Element(),'[name=csrfmiddlewaretoken]':{value:'csrf'}};
    form.e['[data-channel]'].value='development'; form.querySelector=x=>form.e[x];
    const panel=new Element(); let ready, calls=0, ids=0;
    const doc={addEventListener:(_,f)=>ready=f,querySelectorAll:()=>[form],createElement:()=>new Element(),
        getElementById:id=>id==='deployment-release-options'?{textContent:JSON.stringify([{id:'r',version:'1',channel:'development'}])}:panel};
    const fetch=async(url,opts={})=>({ok:true,json:async()=>url==='/create/'?
        {kind:'ad',batch:{id:'batch',issue_url:'/issue/',expires_at:'2030-01-01'}} : url==='/issue/'?
        {deployment:{command:`deploy_${++calls}`},remaining:2-calls} : {deployments:[]}});
    vm.runInNewContext(source,{document:doc,fetch,navigator:{clipboard:{writeText(){}}},FormData:class {},
        URLSearchParams,crypto:{randomUUID:()=>`request-${++ids}`},setInterval:()=>1,clearInterval(){},Date,JSON,encodeURIComponent});
    ready(); await form.handlers.submit({preventDefault(){}});
    assert.equal(calls,1);
    assert.equal(panel.children[0].children[0].children[0].children[1].textContent,'deploy_1');
    const next=panel.children[2];
    await next.handlers.click();
    assert.equal(calls,2);
    assert.equal(panel.children[0].children[0].children[0].children[1].textContent,'deploy_2');
    assert.equal(next.disabled,true);
});
