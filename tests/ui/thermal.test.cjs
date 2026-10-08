const test=require('node:test');
const assert=require('node:assert/strict');
const {controller}=require('../../demo/static/thermal.js');
const drain=()=>new Promise(r=>setImmediate(r));
function harness({id=null,failPost=false,readStatus='running',wrongId=false,read=null,gate=null,storageFails=false,response=null}={}) {
 let clock=0,hidden=false,seq=0,pending=0,maxPending=0;const timers=new Map(),requests=[],states=[],saved=new Map(id?[['telemetry-lab:thermal-simulation-id',id]]:[]);
 const app=controller({storage:{getItem:k=>saved.get(k),setItem:(k,v)=>{if(storageFails)throw Error('storage unavailable');saved.set(k,v);}},uuid:()=> 'uuid',now:()=>clock,hidden:()=>hidden,
 schedule:(fn,delay)=>{timers.set(++seq,{fn,at:clock+delay});return seq;},cancel:key=>timers.delete(key),render:s=>states.push(s),
 fetch:async(url,options)=>{requests.push({url,options,at:clock});pending++;maxPending=Math.max(maxPending,pending);if(gate)await gate(url,options);await drain();pending--;
 if(response){const custom=response(url,options);if(custom)return custom;}
 if(options.method==='POST'&&failPost)throw Error('lost');return {ok:true,json:async()=>read?read():({simulation_id:wrongId?'other':saved.get('telemetry-lab:thermal-simulation-id'),status:readStatus,readings:[]})};}});
 return {app,requests,states,saved,timers,advance(ms){clock+=ms;},get maxPending(){return maxPending;},async tick(){const [key,t]=[...timers].sort((a,b)=>a[1].at-b[1].at)[0]||[];if(!t)return;timers.delete(key);clock=t.at;await t.fn();},visibility(value){hidden=value;app.visibility();}};
}
test('persists identity before lost launch response; reload uses GET exclusively',async()=>{const h=harness({failPost:true});await h.app.start();assert.equal(h.saved.get('telemetry-lab:thermal-simulation-id'),'uuid');assert.equal(h.requests.filter(r=>r.options.method==='POST').length,1);const reload=harness({id:'uuid'});await drain();await drain();assert(reload.requests.every(r=>r.options.method!=='POST'));});
test('passive polling continues across running scenario, bounded and without concurrent reads',async()=>{const h=harness({id:'uuid'});await drain();await drain();for(let i=0;i<60&&h.timers.size;i++)await h.tick();assert.equal(h.requests.length,45);assert.equal(h.maxPending,1);assert(h.requests.every((r,i)=>!i||r.at-h.requests[i-1].at>=1000));});
for(const status of ['completed','interrupted','expired'])test('terminal '+status+' ends polling',async()=>{const h=harness({id:'uuid',readStatus:status});await drain();await drain();assert.equal(h.timers.size,0);assert.equal(h.requests.length,1);});
test('hidden page pauses reads; visible page does not silently renew exhausted lifetime',async()=>{const h=harness({id:'uuid'});await drain();await drain();h.visibility(true);assert.equal(h.timers.size,0);h.visibility(false);assert.equal(h.timers.size,1);});
test('wrong scenario correlation does not become evidence',async()=>{const h=harness({id:'uuid',wrongId:true});await drain();await drain();assert.equal(h.states.at(-1).evidence,null);assert.match(h.states.at(-1).message||h.states.at(-2).message,/incertain/);});
test('replay sends exact string and reading correlation header',async()=>{const h=harness();const body='{"temperature_c":19.00}';await h.app.replay({body,correlation_id:'reading'});assert.equal(h.requests[0].options.body,body);assert.equal(h.requests[0].options.headers['X-Correlation-ID'],'reading');});
test('storage failure prevents POST entirely',async()=>{const h=harness({storageFails:true});await h.app.start();assert.equal(h.requests.length,0);assert.match(h.states.at(-1).message,/annulé/);});
test('manual refresh cannot bypass rolling 45 in 60s or one-second cadence',async()=>{const h=harness({id:'uuid'});await drain();await drain();for(let i=0;i<70;i++){await h.app.refresh();h.advance(700);}assert(h.requests.length<=45);assert(h.requests.every((r,i)=>!i||r.at-h.requests[i-1].at>=1000));});
test('refresh and resume cannot overlap an outstanding GET',async()=>{let release;const wait=new Promise(r=>release=r);const h=harness({id:'uuid',gate:()=>wait});await h.app.refresh();await h.app.resume();assert.equal(h.requests.length,1);release();await drain();await drain();assert.equal(h.maxPending,1);assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0);});
test('explicit resume follows interrupted proof and never repeats automatically',async()=>{const h=harness({id:'uuid',readStatus:'interrupted',failPost:true});await drain();await drain();await h.app.resume();for(let i=0;i<8&&h.timers.size;i++)await h.tick();const posts=h.requests.filter(r=>r.options.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].url,'/thermal-simulations/uuid/resume');assert.equal(posts[0].options.body,undefined);assert.equal(h.saved.get('telemetry-lab:thermal-simulation-id'),'uuid');});
test('resume without recovered server evidence has no effect',async()=>{const h=harness();await h.app.resume();assert.equal(h.requests.length,0);});
test('visibility return after lifetime does not restart polling or POST',async()=>{const h=harness({id:'uuid'});await drain();await drain();h.visibility(true);h.advance(61000);h.visibility(false);assert.equal(h.timers.size,0);assert.equal(h.requests.length,1);});
test('lost GET preserves the last persisted snapshot',async()=>{let unavailable=false;const proof={simulation_id:'uuid',status:'active',readings:[{sequence:1,temperature_c:19,processed_at:'time'}]};const h=harness({id:'uuid',read:()=>{if(unavailable)throw Error('unreachable');return proof;}});await drain();await drain();unavailable=true;await h.tick();assert.equal(h.states.at(-1).evidence,proof);assert.match(h.states.at(-1).message,/incertain/);});
test('dictionary bodies cannot be replayed as newly serialized exact bodies',async()=>{const h=harness();await h.app.replay({body:{temperature_c:19},correlation_id:'uuid'});assert.equal(h.requests.length,0);});
test('running snapshot with a processed first measure continues polling until terminal scenario',async()=>{let status='active';const h=harness({id:'uuid',read:()=>({simulation_id:'uuid',status,readings:[{sequence:1,processed_at:'time',temperature_c:19}]})});await drain();await drain();assert.equal(h.timers.size,1);await h.tick();assert.equal(h.requests.length,2);status='completed';await h.tick();assert.equal(h.timers.size,0);});
function browserPage(proof,{compact=false}={}){
 const fs=require('node:fs'),vm=require('node:vm');const nodes=new Map(),timers=new Map();let timer=0,clock=Date.now();class BrowserDate extends Date {static now(){return clock;}};
 function node(){return {attributes:{},setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k];},focus(){document.activeElement=this;},textContent:'',value:'',disabled:false,children:[],events:{},addEventListener(k,v){this.events[k]=v;},replaceChildren(){this.children=[];},append(v){this.children.push(v);}};}
 const document={hidden:false,getElementById(k){if(!nodes.has(k))nodes.set(k,node());return nodes.get(k);},createElement:node,createElementNS:node,addEventListener(){}};
 const fetch=async()=>{const value=typeof proof==='function'?proof():proof;return {ok:true,json:async()=>value};};
 vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../../demo/static/thermal.js'),'utf8'),{document,window:{fetch,matchMedia:()=>({matches:compact,addEventListener(){}})},localStorage:{getItem:()=> 'uuid',setItem(){}},crypto:{randomUUID:()=> 'new'},Date:BrowserDate,AbortSignal:{timeout(){}},setTimeout(fn){timers.set(++timer,fn);return timer;},clearTimeout(k){timers.delete(k);}});
 return {nodes,timers,document,advance(ms){clock+=ms;}};
}
test('render shows only processed temperature, separate persisted decision and applied command, exact selected body',async()=>{
 const body=' {"temperature_c":19.00} ';
 const p=browserPage({simulation_id:'uuid',status:'active',temperature_c:29,multiplier:2,
 readings:[{sequence:1,processed_at:'persisted',temperature_c:19,body,correlation_id:'uuid'},{sequence:2,temperature_c:26,body:'{"temperature_c":26}',correlation_id:'uuid'}],
 decisions:[{decision_id:'decision',recommended_hvac_mode:'heating'}],
 commands:[{sequence:1,heater_on:true,applied_at:'applied'},{sequence:2,heater_on:false,applied_at:null}]});
 await drain();await drain();
 assert.match(p.nodes.get('thermal-temperature').textContent,/19/);assert.doesNotMatch(p.nodes.get('thermal-temperature').textContent,/29|26/);
 assert.match(p.nodes.get('thermal-decision').textContent,/decision/);assert.match(p.nodes.get('thermal-command').textContent,/Chauffage activé/);assert.doesNotMatch(p.nodes.get('thermal-command').textContent,/Arrêt appliqué/);
 p.nodes.get('thermal-reading').value='1';p.nodes.get('thermal-reading').events.change();assert.equal(p.nodes.get('thermal-body').textContent,body);
 assert.equal(p.nodes.get('thermal-resume').disabled,true);
});
test('terminal snapshot without processed measurement never projects internal server temperature',async()=>{
 const p=browserPage({simulation_id:'uuid',status:'completed',temperature_c:27,multiplier:1,readings:[{sequence:1,temperature_c:27}],commands:[]});await drain();await drain();assert.equal(p.nodes.get('thermal-temperature').textContent,'— °C');assert.equal(p.timers.size,0);
});
for(const status of ['active','interrupted'])test('new launch cannot overwrite '+status+' identity',async()=>{const h=harness({id:'uuid',readStatus:status});await drain();await drain();await h.app.start();assert.equal(h.saved.get('telemetry-lab:thermal-simulation-id'),'uuid');assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0);});
test('unknown persisted launch identity survives start attempt without a second POST',async()=>{const h=harness({failPost:true,read:()=>{throw Error('unavailable');}});await h.app.start();await h.app.start();assert.equal(h.saved.get('telemetry-lab:thermal-simulation-id'),'uuid');assert.equal(h.requests.filter(r=>r.options.method==='POST').length,1);});
test('409 adopts live scenario and only recovers through GET',async()=>{const h=harness({response:(url,options)=>options.method==='POST'?{ok:false,status:409,json:async()=>({detail:{simulation_id:'live-uuid'}})}:null});await h.app.start();assert.equal(h.saved.get('telemetry-lab:thermal-simulation-id'),'live-uuid');assert.equal(h.requests.filter(r=>r.options.method==='POST').length,1);assert.equal(h.requests.at(-1).url,'/thermal-simulations/live-uuid');});
test('failed new creation preserves previous completed proof under its original identity',async()=>{const proof={simulation_id:'old',status:'completed',readings:[{temperature_c:22,processed_at:'time'}]};let fail=false;const h=harness({id:'old',read:()=>{if(fail)throw Error('unavailable');return proof;},failPost:true});await drain();await drain();fail=true;await h.app.start();assert.equal(h.states.at(-1).evidence,proof);assert.equal(h.states.at(-1).id,'uuid');assert.equal(h.states.at(-1).canStart,false);});
test('resume within cadence still schedules fresh GET after interrupted evidence',async()=>{let status='interrupted';const h=harness({id:'uuid',read:()=>({simulation_id:'uuid',status,readings:[]})});await drain();await drain();await h.app.resume();assert.equal(h.timers.size,1);status='active';await h.tick();assert.equal(h.requests.filter(r=>r.options.method!=='POST').length,2);assert.equal(h.states.at(-1).evidence.status,'active');assert.equal(h.timers.size,1);});
test('manual terminal refresh within cadence still retrieves fresh evidence',async()=>{const h=harness({id:'uuid',readStatus:'completed'});await drain();await drain();await h.app.refresh();assert.equal(h.timers.size,1);await h.tick();assert.equal(h.requests.length,2);assert.equal(h.timers.size,0);});
test('runtime unavailable presentation labels applied evidence historical and shows measured final off',async()=>{const p=browserPage({simulation_id:'uuid',status:'completed',multiplier:1,runtime:{status:'unavailable'},readings:[{sequence:2,processed_at:'time',temperature_c:22,heater_on:false,final:true}],decisions:[{decision_id:'stop',recommended_hvac_mode:'off'}],commands:[{sequence:2,reading_sequence:2,heater_on:false,applied_at:'time'}]});await drain();await drain();assert.match(p.nodes.get('thermal-decision').textContent,/Arrêt demandé/);assert.match(p.nodes.get('thermal-command').textContent,/Arrêt appliqué.*État actuel incertain/);assert.match(p.nodes.get('thermal-effect').textContent,/Mesure finale : chauffage éteint/);});
test('definitive 404 of cached identity permits explicit fresh launch without automatic POST',async()=>{
 const h=harness({id:'old-missing',response:(url,options)=>url==='/thermal-simulations/old-missing'&&options.method!=='POST'?{ok:false,status:404,json:async()=>({detail:'simulation_not_found'})}:null});
 await drain();await drain();assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0);assert.equal(h.states.at(-1).canStart,true);
 await h.app.start();assert.equal(h.saved.get('telemetry-lab:thermal-simulation-id'),'uuid');assert.equal(h.requests.filter(r=>r.options.method==='POST').length,1);assert.equal(JSON.parse(h.requests.find(r=>r.options.method==='POST').options.body).simulation_id,'uuid');
});
test('503 cached identity stays uncertain and forbids fresh launch',async()=>{
 const h=harness({id:'uuid',response:()=>({ok:false,status:503,json:async()=>({detail:'unavailable'})})});await drain();await drain();await h.app.start();assert.equal(h.states.at(-1).canStart,false);assert.equal(h.requests.filter(r=>r.options.method==='POST').length,0);
});

const {plotData,confirmedCompletion}=require('../../demo/static/thermal.js');
const visibilityProof=()=>({simulation_id:'uuid',created_at:'2026-10-08T00:00:00Z',status:'active',runtime:{status:'available'},readings:[
 {simulation_id:'uuid',sequence:1,observed_at:'2026-10-08T00:00:01Z',processed_at:'2026-10-08T00:00:10Z',temperature_c:22.4,body:' {"temperature_c":22.400} ',correlation_id:'uuid'},
 {simulation_id:'uuid',sequence:2,observed_at:'2026-10-08T00:00:04Z',processed_at:'2026-10-08T00:00:11Z',temperature_c:21.2,body:'{"temperature_c":21.2}',correlation_id:'uuid'},
 {simulation_id:'other',sequence:3,observed_at:'2026-10-08T00:00:05Z',processed_at:'2026-10-08T00:00:12Z',temperature_c:40},
 {simulation_id:'uuid',sequence:4,observed_at:'2026-10-08T00:00:06Z',temperature_c:50}
]});
test('curve uses attributed processed observations and real observation times, including descending values',()=>{
 const points=plotData(visibilityProof());assert.deepEqual(points.map(p=>p.seconds),[1,4]);assert.deepEqual(points.map(p=>p.reading.temperature_c),[22.4,21.2]);
});
test('completion requires completed status and final processed off evidence; threshold alone cannot succeed',()=>{
 const p=visibilityProof();for(const status of ['active','expired','interrupted','completed']){p.status=status;assert.equal(confirmedCompletion(p),false);}
 p.readings[1].final=true;p.readings[1].heater_on=false;
 for(const status of ['active','expired','interrupted']){p.status=status;assert.equal(confirmedCompletion(p),false);}
 p.status='completed';assert.equal(confirmedCompletion(p),true);delete p.readings[1].processed_at;assert.equal(confirmedCompletion(p),false);
});
test('curve point supports keyboard and click selection of exact persisted body; accessible text lists observation and processing dates',async()=>{
 const proof=visibilityProof(),p=browserPage(proof);await drain();await drain();
 const circles=p.nodes.get('thermal-chart').children.filter(n=>n.attributes.role==='button');assert.equal(circles.length,2);
 let prevented=false;circles[0].events.keydown({key:'Enter',preventDefault(){prevented=true;}});assert(prevented);assert.equal(p.nodes.get('thermal-body').textContent,proof.readings[0].body);
 circles[1].events.click();assert.equal(p.nodes.get('thermal-body').textContent,proof.readings[1].body);
 assert.match(p.nodes.get('thermal-measurements').children[0].textContent,/1.000 s.*observée 2026.*traitée 2026/);
 assert.match(p.nodes.get('thermal-outcome').textContent,/Atteindre 22 °C ne suffit pas/);
 assert.match(p.nodes.get('thermal-freshness').textContent,/observation.*00:00:04Z.*traitement.*00:00:11Z/);
});

test('chronology uses decision generated_at and separates simulated application from final processing',()=>{
 const {milestones}=require('../../demo/static/thermal.js');const proof=visibilityProof();proof.readings[1].final=true;proof.readings[1].heater_on=false;
 proof.decisions=[{decision_id:'stop',generated_at:'2026-10-08T00:00:06Z',recommended_hvac_mode:'off'}];proof.commands=[{simulation_id:'uuid',sequence:1,decision_id:'stop',heater_on:false,applied_at:'2026-10-08T00:00:08Z'}];
 const events=milestones(proof);assert.deepEqual(events.map(e=>e.kind),['decision','application','effect']);assert.deepEqual(events.map(e=>e.at),['2026-10-08T00:00:06Z','2026-10-08T00:00:08Z','2026-10-08T00:00:11Z']);
});
test('failed fresh launch labels prior dated proof historical and retains exact chart/body',async()=>{
 const proof=visibilityProof();proof.status='completed';proof.readings[1].final=true;proof.readings[1].heater_on=false;let unavailable=false;
 const p=browserPage(()=>{if(unavailable)throw Error('unavailable');return proof;});await drain();await drain();
 unavailable=true;await p.nodes.get('thermal-start').events.click();
 assert.match(p.nodes.get('thermal-identity').textContent,/new.*dernières preuves du scénario uuid/);
 assert.match(p.nodes.get('thermal-outcome').textContent,/Preuve historique.*ne confirme pas le nouveau lancement/);
 assert.match(p.nodes.get('thermal-freshness').textContent,/00:00:04Z.*état actuel incertain/);
 assert.equal(p.nodes.get('thermal-chart').children.filter(n=>n.attributes.role==='button').length,2);
});
test('focused curve point survives newly processed observations during polling',async()=>{
 const proof=visibilityProof(),p=browserPage(proof);await drain();await drain();const first=p.nodes.get('thermal-chart').children.find(n=>n.attributes.role==='button');first.focus();
 proof.readings.push({simulation_id:'uuid',sequence:5,temperature_c:20,observed_at:'2026-10-08T00:00:07Z',processed_at:'2026-10-08T00:00:13Z'});
 p.advance(1000);await [...p.timers.values()][0]();
 const updated=p.nodes.get('thermal-chart').children.filter(n=>n.attributes.role==='button');assert.equal(updated.length,3);assert.equal(updated[0].attributes['data-reading'],'1');assert.equal(p.document.activeElement,updated[0]);
});
test('Space selects point and an outstanding read keeps exact replay disabled',async()=>{
 const proof=visibilityProof();let release,blocked=false;const gate=new Promise(resolve=>release=resolve);
 const p=browserPage(()=>blocked?gate:proof);await drain();await drain();
 blocked=true;p.advance(1000);const pending=p.nodes.get('thermal-refresh').events.click();
 const point=p.nodes.get('thermal-chart').children.find(n=>n.attributes.role==='button');let prevented=false;
 point.events.keydown({key:' ',preventDefault(){prevented=true;}});assert(prevented);
 assert.equal(p.nodes.get('thermal-body').textContent,proof.readings[0].body);assert.equal(p.nodes.get('thermal-replay').disabled,true);
 release(proof);await pending;assert.equal(p.nodes.get('thermal-replay').disabled,false);
});

test('compact SVG uses readable coordinate scale and larger interactive points without changing measurements',async()=>{
 const p=browserPage(visibilityProof(),{compact:true});await drain();await drain();
 const chart=p.nodes.get('thermal-chart');assert.equal(chart.attributes.viewBox,'0 0 360 260');
 assert.equal(chart.children.filter(n=>n.attributes.role==='button')[0].attributes.r,'5');
 assert.match(p.nodes.get('thermal-measurements').children[0].textContent,/22.4 °C/);
});

for(const compact of [false,true])test('fixed 0–30 second axis keeps observed point positions stable through later measurements and completion '+(compact?'mobile':'desktop'),async()=>{
 const proof=visibilityProof();proof.readings=proof.readings.slice(0,1);const p=browserPage(proof,{compact});await drain();await drain();
 const chart=p.nodes.get('thermal-chart'), initial=chart.children.find(n=>n.attributes.role==='button').attributes.cx;
 const labels=chart.children.filter(n=>['0 s','10 s','20 s','30 s'].includes(n.textContent)).map(n=>n.textContent);assert.deepEqual(labels,['0 s','10 s','20 s','30 s']);
 const expected=54+1/30*((compact?360:710)-18-54);assert.equal(Number(initial),expected);
 proof.readings.push({...proof.readings[0],sequence:2,observed_at:'2026-10-08T00:00:10Z',temperature_c:23});
 p.advance(1000);await [...p.timers.values()][0]();assert.equal(chart.children.find(n=>n.attributes.role==='button').attributes.cx,initial);
 proof.status='completed';proof.readings[1].final=true;proof.readings[1].heater_on=false;
 p.advance(1000);await [...p.timers.values()][0]();assert.equal(chart.children.find(n=>n.attributes.role==='button').attributes.cx,initial);
});
test('observations beyond 30 seconds remain explicit textual evidence without squeezing the fixed chart',async()=>{
 const proof=visibilityProof();proof.readings.push({...proof.readings[0],sequence:5,observed_at:'2026-10-08T00:00:31Z'});
 const p=browserPage(proof);await drain();await drain();assert.equal(p.nodes.get('thermal-chart').children.filter(n=>n.attributes.role==='button').length,2);
 assert.match(p.nodes.get('thermal-chart-note').textContent,/1 observation.*au-delà de 30 s/);
 assert.match(p.nodes.get('thermal-measurements').children.at(-1).textContent,/31.000 s.*hors de la fenêtre/);
 assert(p.nodes.get('thermal-reading').children.some(n=>n.value==='5'));
});

function switchHarness({cache,stopLost=false,createLost=false,storageFails=false}={}) {
 let clock=0,n=0;const saved=new Map(cache?[['telemetry-lab:thermal-simulation-v2',JSON.stringify(cache)]]:[['telemetry-lab:thermal-simulation-id','old']]);const calls=[],states=[],timers=new Map();let proof={simulation_id:'old',mode:'heating',status:'active',stop_confirmed:false,readings:[]};
 const app=controller({storage:{getItem:k=>saved.get(k),setItem(k,v){if(storageFails)throw Error('disk');saved.set(k,v);}},uuid:()=> 'successor',now:()=>clock,hidden:()=>false,schedule:(fn,delay)=>{timers.set(++n,{fn,delay});return n;},cancel:k=>timers.delete(k),render:s=>states.push(s),fetch:async(url,options)=>{calls.push({url,options});if(options.method==='POST'){if(url.endsWith('/stop')){if(stopLost)throw Error('lost stop');}else{if(createLost)throw Error('lost create');proof={simulation_id:JSON.parse(options.body).simulation_id,mode:'cooling',status:'active',readings:[]};}}return {ok:true,json:async()=>proof};}});
 return {app,calls,saved,states,timers,setProof:p=>proof=p,async tick(){clock+=1000;const [k,t]=timers.entries().next().value||[];if(t){timers.delete(k);await t.fn();}},advance(){clock+=1000;}};
}
test('mode switch serializes stop, confirmed GET, durable predecessor create; same mode is inert',async()=>{
 const h=switchHarness();await drain();await h.app.selectMode('heating');assert.equal(h.calls.length,1);await h.app.selectMode('cooling');assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);assert(h.calls.find(c=>c.options.method==='POST').url.endsWith('/stop'));
 h.setProof({simulation_id:'old',mode:'heating',status:'stopped',stop_confirmed:true,readings:[]});await h.tick();
 const posts=h.calls.filter(c=>c.options.method==='POST');assert.equal(posts.length,2);assert.deepEqual(JSON.parse(posts[1].options.body),{simulation_id:'successor',mode:'cooling',predecessor_simulation_id:'old'});
 assert.equal(JSON.parse(h.saved.get('telemetry-lab:thermal-simulation-v2')).history[0].simulation_id,'old');
});
test('lost stop response resolves with GET proof; false actuator alone cannot release successor',async()=>{
 const h=switchHarness({stopLost:true});await drain();await h.app.selectMode('cooling');h.setProof({simulation_id:'old',status:'stopped',heater_on:false,readings:[]});await h.tick();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);
 h.setProof({simulation_id:'old',status:'stopped',stop_confirmed:true,readings:[]});h.advance();await h.app.refresh();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,2);
});
for(const phase of ['waiting','creating'])test('reload '+phase+' only GETs until explicit continuation',async()=>{
 const h=switchHarness({cache:{version:2,id:phase==='creating'?'successor':'old',mode:'heating',transition:{phase,mode:'cooling',predecessor:'old'},history:[]}});await drain();assert(h.calls.every(c=>c.options.method!=='POST'));h.setProof({simulation_id:phase==='creating'?'successor':'old',mode:'heating',status:'stopped',stop_confirmed:true,readings:[]});h.advance();await h.app.refresh();assert(h.calls.every(c=>c.options.method!=='POST'));if(phase==='waiting'){await h.app.continue();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);}
});
test('expiration cancels authorized transition permanently even when late proof arrives',async()=>{
 const h=switchHarness();await drain();await h.app.selectMode('cooling');h.setProof({simulation_id:'old',status:'expired',stop_confirmed:false,readings:[]});await h.tick();h.setProof({simulation_id:'old',status:'stopped',stop_confirmed:true,readings:[]});h.advance();await h.app.refresh();await h.app.continue();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);assert.equal(h.states.at(-1).transition,'expired');
});
test('transition storage failure prevents stop and leaves prior identity',async()=>{const h=switchHarness({storageFails:true});await drain();await h.app.selectMode('cooling');assert(h.calls.every(c=>c.options.method!=='POST'));assert.equal(h.states.at(-1).id,'old');});
test('cooling completion requires explicit cooling off and requested stop is distinct',async()=>{
 const proof=visibilityProof();proof.mode='cooling';proof.status='completed';proof.readings[1].final=true;proof.readings[1].heater_on=false;assert.equal(confirmedCompletion(proof),false);proof.readings[1].cooler_on=false;assert.equal(confirmedCompletion(proof),true);proof.status='stopped';proof.stop_confirmed=true;const p=browserPage(proof);await drain();await drain();assert.match(p.nodes.get('thermal-outcome').textContent,/Arrêt sur demande confirmé/);assert.equal(confirmedCompletion(proof),false);
});
test('lost successor response preserves exact durable UUID and GET-only recovery',async()=>{
 const h=switchHarness({createLost:true});await drain();await h.app.selectMode('cooling');h.setProof({simulation_id:'old',status:'completed',stop_confirmed:true,readings:[]});await h.tick();assert.equal(JSON.parse(h.saved.get('telemetry-lab:thermal-simulation-v2')).id,'successor');const posts=h.calls.filter(c=>c.options.method==='POST');assert.equal(posts.length,2);await h.tick();assert.equal(h.calls.filter(c=>c.options.method==='POST').length,2);
});
test('cached transition 404 never creates a successor and retains predecessor evidence',async()=>{
 let clock=0;const calls=[],states=[];const proof={simulation_id:'old',status:'active',readings:[{body:'exact'}]};const app=controller({storage:{getItem:k=>k==='telemetry-lab:thermal-simulation-v2'?JSON.stringify({version:2,id:'old',mode:'heating',transition:{phase:'waiting',mode:'cooling'},history:[],evidence:proof}):null,setItem(){}},uuid:()=> 'new',now:()=>clock,hidden:()=>false,schedule:()=>1,cancel(){},render:s=>states.push(s),fetch:async(url,options)=>{calls.push({url,options});return {ok:false,status:404,json:async()=>({detail:'missing'})};}});await drain();clock=1000;await app.continue();assert(calls.every(c=>c.options.method!=='POST'));assert.equal(states.at(-1).canStart,false);assert.deepEqual(states.at(-1).evidence,proof);
});

test('actual command_type exposes applied cooling activation in timeline and command details',async()=>{
 const proof={simulation_id:'uuid',mode:'cooling',status:'active',readings:[],commands:[{simulation_id:'uuid',sequence:1,command_type:'cooling.start',heater_on:false,cooler_on:true,applied_at:'2026-10-08T00:00:01Z',decision_id:'decision',origin:'automatic'}]};
 const p=browserPage(proof);await drain();await drain();
 assert.match(p.nodes.get('thermal-command').textContent,/Refroidissement activé/);
 assert.doesNotMatch(p.nodes.get('thermal-command').textContent,/Arrêt appliqué/);
 assert(p.nodes.get('thermal-milestones').children.some(n=>/refroidissement activé/.test(n.textContent)));
});
test('explicit abandonment of expired switch sends no POST; later independent mode launch has no predecessor',async()=>{
 const expired={simulation_id:'old',mode:'heating',status:'expired',stop_confirmed:false,readings:[{body:'old exact proof'}]};
 const h=switchHarness({cache:{version:2,id:'old',mode:'heating',transition:{phase:'expired',mode:'cooling'},history:[],evidence:expired}});h.setProof(expired);await drain();
 assert.equal(h.states.at(-1).canStart,false);assert.equal(h.states.at(-1).canAbandon,true);h.app.abandon();
 assert(h.calls.every(c=>c.options.method!=='POST'));assert.equal(h.states.at(-1).canStart,true);assert.deepEqual(h.states.at(-1).evidence,expired);assert.equal(JSON.parse(h.saved.get('telemetry-lab:thermal-simulation-v2')).transition,null);
 await h.app.selectMode('cooling');assert(h.calls.every(c=>c.options.method!=='POST'));await h.app.start();
 const posts=h.calls.filter(c=>c.options.method==='POST');assert.equal(posts.length,1);assert.deepEqual(JSON.parse(posts[0].options.body),{simulation_id:'successor',mode:'cooling'});
});
test('abandon control is disabled for unfinished non-expired transition',async()=>{
 const h=switchHarness({cache:{version:2,id:'old',mode:'heating',transition:{phase:'waiting',mode:'cooling'},history:[]}});await drain();h.app.abandon();assert.equal(h.states.at(-1).transition,'waiting');assert.equal(h.states.at(-1).canAbandon,false);assert(h.calls.every(c=>c.options.method!=='POST'));
});
