const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../public/sw.js'),'utf8');
function runtime(failed=false){
  const handlers={},responses=[],writes=[],deleted=[],network=[];
  const stored=new Map([['/offline.html','public offline fallback']]);
  const cache={addAll:async urls=>writes.push(...urls),match:async key=>stored.get(key)};
  const self={location:{origin:'https://devflow.example.test'},addEventListener:(type,fn)=>handlers[type]=fn,skipWaiting:async()=>{},clients:{claim:async()=>{}}};
  vm.runInNewContext(script,{self,URL,Response,caches:{open:async()=>cache,keys:async()=>['devflow-public-offline-v0','devflow-public-offline-v1','unrelated-cache'],delete:async key=>deleted.push(key)},fetch:async request=>{network.push(request);if(failed)throw new Error('offline');return 'live network response';}});
  function request(url,method='GET',mode='cors'){let response;handlers.fetch({request:{url,method,mode},respondWith:value=>response=value});return response;}
  return {handlers,request,writes,deleted,network,responses};
}
test('only non-private public assets are pre-cached',async()=>{
  const r=runtime();let done;r.handlers.install({waitUntil:value=>done=value});await done;
  assert.deepEqual(r.writes,['/offline.html','/icons/icon-192.png','/icons/icon-512.png','/icons/apple-touch-icon.png']);
});
test('API, SSE, exports and all writes bypass the worker',()=>{
  const r=runtime(true);
  for(const url of ['/api','/api/health','/api/chat/runs','/api/repositories/repo/runs/run/events','/api/repositories/repo/answer-evaluation-sets/set/export']){
    assert.equal(r.request('https://devflow.example.test'+url),undefined);
    assert.equal(r.request('https://devflow.example.test'+url,'POST'),undefined);
  }
  assert.equal(r.request('https://devflow.example.test/','POST','navigate'),undefined);
  assert.equal(r.network.length,0);assert.equal(r.writes.length,0);
});
test('successful private navigation is not cached',async()=>{
  const r=runtime();assert.equal(await r.request('https://devflow.example.test/?private=1','GET','navigate'),'live network response');
  assert.equal(r.writes.length,0);assert.equal(r.network.length,1);
});
test('failed navigation returns public fallback only',async()=>{
  const r=runtime(true);assert.equal(await r.request('https://devflow.example.test/?private=1','GET','navigate'),'public offline fallback');
  assert.equal(r.writes.length,0);
});
test('foreign origins and unlisted static files bypass the worker',()=>{
  const r=runtime(true);
  for(const url of ['https://other.test/offline.html','https://devflow.example.test/_next/static/app.js','https://devflow.example.test/downloads/DevFlow-0.18-debug.apk','https://devflow.example.test/downloads/DevFlow-0.18.1-debug.apk','https://devflow.example.test/icons/icon-192.png?private=1'])assert.equal(r.request(url),undefined);
});
test('activation removes only older caches belonging to this worker',async()=>{
  const r=runtime();let done;r.handlers.activate({waitUntil:value=>done=value});await done;
  assert.deepEqual(r.deleted,['devflow-public-offline-v0']);
});
