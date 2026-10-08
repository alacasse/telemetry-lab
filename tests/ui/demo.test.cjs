// Dependency-free controller contracts; native browser rendering is checked separately.
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../demo/static/app.js'), 'utf8');
const unknown = {results:[], observations:[], journal_quality:'observed', fetched_at:'server-time'};
const result = (id, co2) => ({event_id:id,status:'processed',event_timestamp:'sensor-time',
  measurement:{co2_ppm:co2,temperature_c:23,airflow_pct:40},
  decisions:co2===700?[]:[{recommended_airflow_pct:55,decision_type:'increase_ventilation',reason_text:'CO2 high'}]});
const normal = {...unknown, results:[result('event-1',700)]};
const complete = {...unknown, results:[...normal.results,result('event-2',1400)]};
const duplicate = {...complete, observations:[{kind:'duplicate',event_id:'event-3',original_event_id:'event-2',attempt_id:'attempt-3'}],
  measurements:[{original_event_id:'event-1',send_event_ids:['event-1']},{original_event_id:'event-2',send_event_ids:['event-2','event-3']}]};
const drain = () => new Promise(resolve => setImmediate(resolve));
function page({simulation='existing',hidden=false,sendFails=false,storage=new Map(),storageFails=false,
  read=()=>unknown,queueRead=null,random=()=>0,sendRead=null,eventOffset=0}={}) {
  const nodes=new Map(),timers=new Map(),requests=[];
  let now=0,sequence=0,posts=0,identities=0;
  function node() {return {textContent:'',hidden:false,children:[],events:{},
    addEventListener(name,callback){this.events[name]=callback;},
    replaceChildren(){this.children=[];this.textContent='';},append(...children){this.children.push(...children);}};}
  const document={hidden,events:{},getElementById(id){if(!nodes.has(id)) nodes.set(id,node());return nodes.get(id);},
    createElement:node,createTextNode:text=>({textContent:text}),
    addEventListener(name,callback){(this.events[name]??=[]).push(callback);}};
  const context={document,URL,Date:class extends Date {constructor(value=now){super(value);} static now(){return now;}},
    location:{href:'http://localhost/'+(simulation?'?simulation='+simulation:'')},history:{replaceState(){}},
    Math:Object.assign(Object.create(Math), {random}),
    crypto:{randomUUID:()=> ++identities === 1 ? 'new-simulation' : 'new-simulation-' + identities},sessionStorage:{getItem:key=>storage.get(key)||null,
      setItem(key,value){if(storageFails)throw Error('storage');storage.set(key,value);}},AbortSignal:{timeout(){}},
    setTimeout(callback,delay){const id=++sequence;timers.set(id,{callback,at:now+delay});return id;},clearTimeout(id){timers.delete(id);},
    async fetch(url,options){requests.push({url,options,at:now});
      if(url==='/environment')return {ok:true,json:async()=>({release_revision:'test'})};
      if(url==='/queue-observation')return {ok:true,json:async()=>queueRead?queueRead():({
        source:'SQS.GetQueueAttributes',environment:'local',runtime:'sqs-compatible-emulator',quality:'approximate',sample_quality:'approximate',
        counts:{available:0,in_flight:0,delayed:0},age_seconds:0,stale_after_seconds:30,observed_at:'sample-time'})};
      if(url==='/ingestion/telemetry') {
        posts++;if(sendRead)await sendRead(posts);if(sendFails===true||sendFails===posts)throw Error('lost HTTP response');
        return {ok:true,json:async()=>({correlation_id:'new-simulation',event_id:'event-'+(posts+eventOffset),envelope_id:'envelope-'+posts,transport_telemetry-lab:receipt:{message_id:'transport-'+posts}})};
      }
      return {ok:true,json:async()=>read()};
    }};
  vm.runInNewContext(code,context);
  return {nodes,requests,timers,document,storage,
    async click(id){await nodes.get(id).events.click();await drain();},
    async visibility(hidden){document.hidden=hidden;document.events.visibilitychange.forEach(fn=>fn());await drain();},
    async tick(){if(!timers.size)return;const [id,t]=[...timers].sort((a,b)=>a[1].at-b[1].at)[0];timers.delete(id);now=t.at;await t.callback();await drain();},
    async exhaust(){for(let i=0;timers.size&&i<100;i++)await this.tick();assert.equal(timers.size,0);}
  };
}
const posts=p=>p.requests.filter(r=>r.url==='/ingestion/telemetry');
const text=node=>node.textContent+' '+(node.children||[]).map(text).join(' ');
const loadedLegacy=p=>JSON.parse(p.storage.get('telemetry-lab:simulation-v3:existing'));
const loaded=p=>JSON.parse(p.storage.get('telemetry-lab:simulation-v3:new-simulation'));
async function running(options={}) {
  let proof=unknown;
  const p=page({simulation:null,read:()=>proof,...options});
  await p.click('send');
  return {p,set(value){proof=value;}};
}

test('old single measurement v2 cache stays readable and replayable without inventing second measurement',async()=>{
  const body=JSON.stringify({building_id:'demo-existing',timestamp:'2026-10-07T00:00:00Z',co2_ppm:1400});
  const storage=new Map([['telemetry-lab:simulation-v2:existing',JSON.stringify({body,posts:[{telemetry-lab:receipt:{response:{event_id:'event-1'}}}]})]]);
  const p=page({storage,read:()=>({...unknown,results:[result('event-1',1400)]})});await drain();
  assert.equal(p.nodes.get('continue').hidden,true);await p.click('resend');assert.equal(posts(p)[0].options.body,body);
});
test('new tab and tranche 1 receipt preserve consultation but never reconstruct exact payload',async()=>{
  const storage=new Map([['telemetry-lab:receipt:existing',JSON.stringify({response:{correlation_id:'existing',event_id:'event-1'}})]]);
  for(const cache of [new Map(),storage]){
    const p=page({storage:cache,read:()=>complete});await drain();
    assert.equal(p.nodes.get('resend').disabled,true);assert.match(p.nodes.get('payload').textContent,/Corps exact absent/);
    await p.click('resend');assert.equal(posts(p).length,0);
  }
});
test('late response of older simulation cannot replace active scenario or advance it',async()=>{
  let resolve;
  const p=page({read:()=>new Promise(r=>{resolve=r;})});await drain();
  const old=resolve;await p.click('send');old(temperatureTrace(28));await drain();
  assert.equal(posts(p).length,1);assert.doesNotMatch(text(p.nodes.get('result')),/55 %/);
  assert.equal(p.nodes.get('room').className,'room pending');
  assert.doesNotMatch(p.nodes.get('room-status').textContent,/cooling|Résultat event/);
  assert.match(p.nodes.get('identity').textContent,/new-simulation/);
  assert.equal(p.nodes.get('resend').disabled,true);
  assert.equal(loaded(p).measurements[0].confirmation,undefined);
});
test('consultation failures remain unknown with bounded polling',async()=>{
  const p=page({read:()=>{throw Error('503');}});await drain();await p.exhaust();
  assert.match(p.nodes.get('status').textContent,/Consultation indisponible/);
  assert.match(p.nodes.get('status').textContent,/expiré/);
  assert.ok(p.requests.filter(r=>r.url.startsWith('/query')).length<=30);
});
test('hidden tabs suspend both loops and repeated visibility events do not duplicate reads',async()=>{
  const p=page({hidden:true});await drain();assert.equal(p.requests.length,1);
  await p.visibility(false);await p.visibility(false);
  assert.equal(p.timers.size,2);await p.visibility(true);assert.equal(p.timers.size,0);
});
test('queue samples poll more slowly than traces, stop, and age after automatic polling ends',async()=>{
  const p=page();await drain();await p.exhaust();
  const q=p.requests.filter(r=>r.url==='/queue-observation');
  assert.ok(q.length<=9);assert.ok(q.length<p.requests.filter(r=>r.url.startsWith('/query')).length);
  for(let i=1;i<q.length;i++)assert.ok(q[i].at-q[i-1].at>=15000);
  assert.match(p.nodes.get('queue-status').textContent,/périmé/);
  assert.match(p.nodes.get('queue-status').textContent,/terminé/);
  assert.doesNotMatch(p.nodes.get('status').textContent,/Résultat enregistré/);
});
test('queue unavailability retains old sample, reports staleness and does not fail the scenario',async()=>{
  let fail=false;
  const p=page({read:()=>complete,queueRead:()=>{if(fail)throw Error('down');return {
    quality:'approximate',counts:{available:8,in_flight:2,delayed:1},age_seconds:0,stale_after_seconds:30};}});
  await drain();fail=true;await p.exhaust();
  assert.match(p.nodes.get('queue-counts').textContent,/≈ 8/);assert.match(p.nodes.get('queue-status').textContent,/indisponible.*périmé/);
  assert.match(p.nodes.get('status').textContent,/Résultat enregistré/);
});
test('queue without a sample never substitutes zeros; manual refresh cannot bypass cooldown',async()=>{
  const p=page({queueRead:()=>{throw Error('down');}});await drain();
  await p.click('queue-refresh');await p.click('queue-refresh');
  assert.equal(p.requests.filter(r=>r.url==='/queue-observation').length,1);
  assert.match(p.nodes.get('queue-counts').textContent,/inconnus/);assert.doesNotMatch(p.nodes.get('queue-counts').textContent,/≈ 0/);
});
test('a second POST and redelivery of that message remain distinct within the high measurement',async()=>{
  const p=page({read:()=>({...duplicate,sends:[{event_id:'event-3',attempts:[
    {attempt_id:'a',envelope_id:'envelope-3',transport_message_id:'transport-3',delivery:'first_observed'},
    {attempt_id:'b',envelope_id:'envelope-3',transport_message_id:'transport-3',delivery:'redelivery'},
    {attempt_id:'c',envelope_id:'envelope-3',transport_message_id:null,delivery:'unknown'}]}]})});
  await drain();
  const submissions=text(p.nodes.get('submissions'));
  assert.match(submissions,/Redélivrance observée du même message SQS/);
  assert.match(submissions,/redélivrance indéterminée/);
  assert.equal((submissions.match(/transport-3/g)||[]).length,2);
  assert.equal(p.nodes.get('timeline').children.length,2);
});

const temperatureResult = (temperature, id='event-1') => ({...result(id,700),fetched_at:'server-time',
  measurement:{temperature_c:temperature,co2_ppm:700,occupancy:8,humidity_pct:45,hvac_mode:'ventilation',airflow_pct:40},
  decisions:temperature<=25.5?[]:[{decision_type:'adjust_airflow',recommended_hvac_mode:'cooling',recommended_airflow_pct:50}]});
const temperatureTrace = temperature => ({...unknown,results:[temperatureResult(temperature)]});
for (const temperature of [22,23,25.5,25.6,28,29]) {
  test('temperature '+temperature+' is serialized once and reacts only to the matching persisted result',async()=>{
    let draws=0;
    const {p,set}=await running({random:()=>{draws++;return (Math.round(temperature*10)-220+0.5)/71;}});
    const body=JSON.parse(posts(p)[0].options.body);
    assert.equal(body.temperature_c,temperature);
    assert.deepEqual([body.humidity_pct,body.occupancy,body.co2_ppm,body.hvac_mode,body.airflow_pct],[45,8,700,'ventilation',40]);
    assert.equal(p.nodes.get('room').className,'room pending');
    set({...unknown,observations:[{kind:'processed',event_id:'event-1'}],results:[temperatureResult(28,'other')]});
    await p.tick();assert.equal(p.nodes.get('room').className,'room pending');
    set(temperatureTrace(temperature));await p.tick();
    assert.equal(p.nodes.get('room').className,temperature<=25.5?'room neutral':'room cooling');
    assert.match(p.nodes.get('room-status').textContent,temperature<=25.5?/aucune recommandation/:/❄.*cooling.*50 %/);
    assert.equal(p.nodes.get('resend').disabled,false);
    await p.click('resend');assert.equal(posts(p).length,2);
    assert.equal(posts(p)[0].options.body,posts(p)[1].options.body);
    assert.equal(draws,1);
    assert.match(p.nodes.get('status').textContent,/observation du doublon manquante/);
    set({...temperatureTrace(temperature),observations:[{kind:'duplicate',event_id:'event-2',original_event_id:'event-1'}]});
    await p.tick();assert.match(p.nodes.get('status').textContent,/Doublon reconnu/);
    assert.equal(p.nodes.get('duplicates').children[0].children[0].href,'#result-event-1');
    assert.equal(loaded(p).measurements[0].posts.length,2);
    await p.exhaust();assert.equal(posts(p).length,2);
  });
}
test('all 71 equally sized random intervals map to distinct tenths including endpoints',async()=>{
  for(let i=0;i<71;i++) {
    const {p}=await running({random:()=>(i+0.5)/71});
    assert.equal(JSON.parse(posts(p)[0].options.body).temperature_c,(220+i)/10);
  }
  const {p}=await running({random:()=>1-Number.EPSILON});
  assert.equal(JSON.parse(posts(p)[0].options.body).temperature_c,29);
});
test('same temperature starts a new isolated building and clears the previous reaction',async()=>{
  const {p,set}=await running();set(temperatureTrace(22));await p.tick();
  set(unknown);await p.click('send');
  const [a,b]=posts(p).map(p=>JSON.parse(p.options.body));
  assert.equal(a.temperature_c,b.temperature_c);assert.notEqual(a.building_id,b.building_id);
  assert.equal(p.nodes.get('room').className,'room pending');
});
test('reaction follows stored decision, never the locally generated temperature',async()=>{
  const {p,set}=await running({random:()=>0});
  set(temperatureTrace(28));await p.tick();
  assert.equal(p.nodes.get('room').className,'room cooling');
  assert.match(p.nodes.get('room-status').textContent,/28,0 °C/);
});
test('reload reads before reaction and preserves exact bytes and timestamp for explicit resend',async()=>{
  const {p,set}=await running();set(temperatureTrace(22));await p.tick();
  const reload=page({simulation:'new-simulation',storage:p.storage,read:()=>unknown});await drain();
  assert.equal(reload.nodes.get('room').className,'room pending');
  assert.equal(posts(reload).length,0);
  await reload.exhaust();assert.equal(posts(reload).length,0);
  assert.equal(reload.nodes.get('room').className,'room unknown');
  const confirmed=page({simulation:'new-simulation',storage:p.storage,read:()=>temperatureTrace(22)});await drain();
  assert.equal(confirmed.nodes.get('room').className,'room neutral');
  await confirmed.exhaust();assert.equal(posts(confirmed).length,0);await confirmed.click('resend');
  assert.equal(posts(confirmed)[0].options.body,posts(p)[0].options.body);
});
test('lost response stays ambiguous despite matching server values and reload never replays POST',async()=>{
  const {p}=await running({sendFails:true,read:()=>temperatureTrace(28)});
  assert.match(p.nodes.get('status').textContent,/Publication non confirmée/);
  assert.notEqual(p.nodes.get('room').className,'room cooling');
  const reload=page({simulation:'new-simulation',storage:p.storage,read:()=>temperatureTrace(28)});await drain();await reload.exhaust();
  assert.equal(posts(reload).length,0);assert.equal(reload.nodes.get('resend').disabled,true);
  assert.equal(reload.nodes.get('room').className,'room unknown');
});
test('lost duplicate response does not invent proof or retry',async()=>{
  const {p,set}=await running({sendFails:2});set(temperatureTrace(22));await p.tick();
  await p.click('resend');await p.exhaust();
  assert.equal(posts(p).length,2);assert.match(p.nodes.get('status').textContent,/Publication non confirmée/);
});
test('storage failure keeps bytes in memory and warns about reload',async()=>{
  const {p,set}=await running({storageFails:true});set(temperatureTrace(22));await p.tick();
  assert.match(p.nodes.get('resend-status').textContent,/Stockage de l’onglet indisponible/);
  await p.click('resend');assert.equal(posts(p)[0].options.body,posts(p)[1].options.body);
});
test('missing journal still allows a correlated business reaction but not duplicate confirmation',async()=>{
  const {p,set}=await running();set({...temperatureTrace(28),journal_quality:'unavailable'});await p.tick();
  assert.equal(p.nodes.get('room').className,'room cooling');
  await p.click('resend');assert.match(p.nodes.get('status').textContent,/observation du doublon manquante/);
});
test('legacy v3 CO2 scenario requires explicit continuation and resends the exact second body',async()=>{
  const bodies=[700,1400].map((co2,i)=>JSON.stringify({building_id:'demo-existing',timestamp:'2026-10-07T00:00:0'+i+'Z',co2_ppm:co2}));
  const storage=new Map([['telemetry-lab:simulation-v3:existing',JSON.stringify({version:3,measurements:bodies.map((body,i)=>({
    id:i?'high':'normal',label:i?'CO2 high':'CO2 normal',body,posts:i?[]:[{telemetry-lab:receipt:{response:{event_id:'event-1'}}}]
  }))})]]);
  let proof={...unknown,results:[result('event-1',700)]};
  const p=page({storage,read:()=>proof,eventOffset:1});await drain();
  assert.equal(posts(p).length,0);assert.equal(p.nodes.get('continue').hidden,false);
  await p.click('continue');assert.equal(posts(p)[0].options.body,bodies[1]);
  assert.equal(loadedLegacy(p).measurements[1].posts[0].receipt.response.event_id,'event-2');
  proof={...unknown,results:[result('event-1',700),result('event-2',1400)]};await p.click('refresh');
  await p.click('resend');assert.equal(posts(p)[1].options.body,bodies[1]);
  assert.match(text(p.nodes.get('result')),/55 %/);
});

test('two generation clicks while publication is pending produce one payload and one POST',async()=>{
  let release,draws=0;
  const p=page({simulation:null,random:()=>{draws++;return 0;},sendRead:()=>new Promise(resolve=>{release=resolve;})});
  const first=p.click('send');await drain();
  assert.equal(p.nodes.get('send').disabled,true);
  await p.click('send');assert.equal(posts(p).length,1);assert.equal(draws,1);
  release();await first;
  assert.equal(loaded(p).measurements[0].posts.length,1);
});
test('two resend clicks while publication is pending create only one additional POST',async()=>{
  let release;
  const {p,set}=await running({sendRead:count=>count===2?new Promise(resolve=>{release=resolve;}):undefined});
  set(temperatureTrace(22));await p.tick();
  const first=p.click('resend');await drain();
  assert.equal(p.nodes.get('resend').disabled,true);
  await p.click('resend');assert.equal(posts(p).length,2);
  release();await first;
  assert.equal(loaded(p).measurements[0].posts.length,2);
});
test('read failure before result shows unknown and requires persisted proof before reaction',async()=>{
  let fail=true;
  const {p}=await running({random:()=>0.99,read:()=>{if(fail)throw Error('503');return temperatureTrace(29);}});
  assert.equal(p.nodes.get('room').className,'room unknown');
  assert.match(p.nodes.get('room-status').textContent,/Consultation indisponible/);
  assert.equal(p.nodes.get('resend').disabled,true);
  assert.doesNotMatch(text(p.nodes.get('result')),/50 %/);
  fail=false;await p.click('refresh');
  assert.equal(p.nodes.get('room').className,'room cooling');
  assert.equal(posts(p).length,1);
});
