'use strict';
// This controller owns only the thermal section and its independent durable identity.
(function () {
  const terminal = new Set(['completed', 'stopped', 'interrupted', 'expired']);
  const cacheKey = 'telemetry-lab:thermal-simulation-v2';
  function controller({fetch, storage, uuid, now, schedule, cancel, hidden, render}) {
    let id=null, mode='heating', transition=null, history=[], cachedEvidence=null;
    try { const raw=storage.getItem(cacheKey); if(raw){const saved=JSON.parse(raw);if(saved.version===2 && typeof saved.id==='string'){id=saved.id;mode=saved.mode==='cooling'?'cooling':'heating';transition=saved.transition || null;history=Array.isArray(saved.history) ? saved.history : [];cachedEvidence=saved.evidence || null;}} } catch {}
    if(!id)try{id=storage.getItem('telemetry-lab:thermal-simulation-id');}catch{}
    let evidence=cachedEvidence,busy=false,timer,lastMessage='',uncertain=Boolean(id),absent=false,authorized=false;
    let until=0,count=0,lastRead=-Infinity;const reads=[];
    const save=()=>{try{storage.setItem(cacheKey,JSON.stringify({version:2,id,mode,transition,history,evidence}));storage.setItem('telemetry-lab:thermal-simulation-id',id);return true;}catch{return false;}};
    const show=message=>{if(message)lastMessage=message;render({id,mode,requestedMode:transition?.mode,transition:transition?.phase,history,evidence,busy,uncertain,canContinue:Boolean(transition)&&!busy,canAbandon:transition?.phase==='expired'&&!busy,canStart:!transition&&(!id||absent||(!uncertain&&['completed','stopped','expired'].includes(evidence?.status))),message:lastMessage});};
    const budget=()=>{while(reads.length&&reads[0]<=now()-60000)reads.shift();return reads.length<45;};
    const stop=()=>{cancel(timer);timer=null;};
    function arm(force=false){stop();if(!hidden()&&id&&(force||!terminal.has(evidence?.status)||transition?.phase==='creating')&&now()<until&&count<45&&budget())timer=schedule(read,Math.max(1000,lastRead+1000-now()));}
    async function request(url,options={}){const response=await fetch(url,{...options,cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok){const error=Error('HTTP '+response.status);error.status=response.status;try{error.detail=(await response.json()).detail;}catch{}throw error;}return response.json();}
    async function read(){if(busy||hidden()||!id||now()<lastRead+1000||now()>=until||count>=45||!budget())return;
      const requestedId=id;busy=true;lastRead=now();count++;reads.push(now());show('Consultation des preuves…');
      try{const next=await request('/thermal-simulations/'+encodeURIComponent(requestedId));if(id!==requestedId)return;if(next.simulation_id!==requestedId)throw Error('Identité serveur incompatible');evidence=next;mode=next.mode || mode;uncertain=false;absent=false;
        if(transition?.phase==='creating'){transition=null;authorized=false;save();}
        if(transition&&next.status==='expired'){transition.phase='expired';authorized=false;save();}
        if(!save())show('Stockage indisponible : preuves affichées conservées en mémoire.');
        show('État serveur : '+({active:'en cours',running:'en cours',stopping:'arrêt en cours',stopped:'arrêté sur demande',completed:'terminé',interrupted:'interrompu',expired:'expiré'}[next.status]||next.status)+'.');
      }catch(error){if(id!==requestedId)return;absent=error.status===404;uncertain=!absent;show(absent?'Scénario absent du serveur (404). Aucune confirmation d’arrêt; anciennes preuves conservées.':'Lecture indisponible : état incertain. Dernières preuves conservées; aucune reprise automatique.');}
      finally{busy=false;show();if(authorized&&transition?.phase==='waiting'&&!uncertain&&!absent&&evidence?.stop_confirmed===true&&['completed','stopped'].includes(evidence.status))await createSuccessor();else arm();}
    }
    function windowRead(){if(busy)return;until=now()+60000;count=0;if(now()<lastRead+1000){arm(true);return;}return read();}
    async function postCreate(predecessor){busy=true;show('Création en cours…');try{const accepted=await request('/thermal-simulations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({simulation_id:id,mode,...(predecessor?{predecessor_simulation_id:predecessor}:{})})});if(accepted.simulation_id!==id)throw Error('Identité serveur incompatible');}catch(error){uncertain=true;authorized=false;const existing=error.status===409&&error.detail?.simulation_id;if(typeof existing==='string'&&existing){id=existing;mode=error.detail.mode || mode;transition=null;save();show('Un scénario existe déjà. Identité récupérée; consultation par GET uniquement.');}else show('Réponse absente : création incertaine. Identité conservée; récupération par GET uniquement.');}finally{busy=false;await windowRead();}}
    async function createSuccessor(){if(busy||!transition||transition.phase==='expired')return;const old={id,mode,transition,history};const predecessor=id;history=[...history,...(evidence?[evidence]:[])];id=uuid();mode=transition.mode;transition={phase:'creating',mode,predecessor};uncertain=true;absent=false;if(!save()){({id,mode,transition,history}=old);authorized=false;show('Identité non conservée : lancement annulé.');return;}await postCreate(predecessor);}
    async function continueTransition(){if(busy||!transition||transition.phase==='expired')return;authorized=true;
      if(transition.phase==='creating'){await postCreate(transition.predecessor);return;}
      if(uncertain||absent||evidence?.simulation_id!==id){await windowRead();return;}
      if(evidence.status==='expired'){transition.phase='expired';authorized=false;save();show('Échéance atteinte : bascule bloquée.');return;}
      if(evidence.stop_confirmed===true&&['completed','stopped'].includes(evidence.status)){await createSuccessor();return;}
      transition.phase='waiting';if(!save()){authorized=false;show('Transition non conservée : arrêt annulé.');return;}busy=true;stop();show('Arrêt en cours; attente de la commande appliquée et du relevé final traité.');try{await request('/thermal-simulations/'+encodeURIComponent(id)+'/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch{show('Réponse d’arrêt absente : vérification par GET; aucune nouvelle création sans preuve.');}finally{busy=false;await windowRead();}}
    async function selectMode(nextMode){if(!['heating','cooling'].includes(nextMode)||busy||transition||nextMode===mode)return;
      if(!id || (!uncertain && evidence?.status === 'expired')){mode=nextMode;show('Mode choisi pour un prochain lancement indépendant explicite.');return;}transition={phase:'waiting',mode:nextMode};if(!save()){transition=null;show('Transition non conservée : bascule annulée.');return;}await continueTransition();}
    function abandonTransition(){
      if(busy || transition?.phase !== 'expired')return;
      const previous=transition;transition=null;authorized=false;
      if(!save()){transition=previous;show('Stockage indisponible : abandon non conservé.');return;}
      show('Bascule expirée abandonnée. Preuves conservées; un lancement indépendant exige un nouveau clic explicite.');
    }
    async function action(resume){if(busy||transition||(!resume&&id&&!absent&&(uncertain||!['completed','stopped','expired'].includes(evidence?.status))))return;stop();if(resume){if(!id||evidence?.status!=='interrupted'||uncertain)return;busy=true;try{await request('/thermal-simulations/'+encodeURIComponent(id)+'/resume',{method:'POST'});}catch{uncertain=true;show('Réponse absente : reprise incertaine.');}finally{busy=false;await windowRead();}return;}
      const old=id, oldHistory=history;if(evidence?.simulation_id===id)history=[...history,evidence];id=uuid();if(!save()){id=old;history=oldHistory;show('Identité non conservée : lancement annulé.');return;}uncertain=true;absent=false;await postCreate();}
    async function replay(reading){if(busy||typeof reading?.body!=='string'||!reading.body||!(reading.correlation_id||reading.envelope?.correlation_id))return;busy=true;show();try{return {receipt:await request('/ingestion/telemetry',{method:'POST',headers:{'Content-Type':'application/json','X-Correlation-ID':reading.correlation_id||reading.envelope?.correlation_id},body:reading.body})};}catch{return {uncertain:true};}finally{busy=false;show();arm();}}
    show(transition?'Bascule conservée : relire les preuves puis continuer explicitement.':'');if(id)windowRead();return {start:()=>action(false),resume:()=>action(true),selectMode,continue:continueTransition,abandon:abandonTransition,refresh:windowRead,replay,visibility(){stop();if(!hidden())arm();}};
  }
  function processedReadings(snapshot) {
    return (snapshot?.readings || []).filter(r => (r.status === 'processed' || r.processed_at) && Number.isFinite(r.temperature_c)
      && (!r.simulation_id || r.simulation_id === snapshot.simulation_id)
      && (!r.correlation_id || r.correlation_id === snapshot.simulation_id));
  }
  function plotData(snapshot) {
    const origin = Date.parse(snapshot?.created_at);
    return processedReadings(snapshot).filter(r => (r.simulation_id || r.correlation_id) === snapshot.simulation_id)
      .map(r => ({reading:r, seconds:(Date.parse(r.observed_at) - origin) / 1000}))
      .filter(p => Number.isFinite(p.seconds) && p.seconds >= 0).sort((a,b)=>a.seconds-b.seconds || a.reading.sequence-b.reading.sequence);
  }
  function confirmedCompletion(snapshot) {
    return snapshot?.status === 'completed' && processedReadings(snapshot).some(r => (r.simulation_id || r.correlation_id) === snapshot.simulation_id && r.final === true && r.heater_on === false && (snapshot.mode !== 'cooling' || r.cooler_on === false));
  }
  function milestones(snapshot) {
    const events = [];
    for (const d of snapshot?.decisions || []) events.push({at:d.generated_at, kind:'decision', text:'Décision persistée · '+(d.recommended_hvac_mode === 'off' ? 'arrêt demandé' : d.recommended_hvac_mode === 'heating' ? 'chauffage demandé' : d.recommended_hvac_mode === 'cooling' ? 'refroidissement demandé' : 'recommandation')+' · '+d.decision_id});
    for (const c of snapshot?.commands || []) if (c.applied_at && (!c.simulation_id || c.simulation_id === snapshot.simulation_id)) events.push({at:c.applied_at, kind:'application', text:'Application simulée · '+(c.command_type === 'cooling.start' ? 'refroidissement activé' : c.heater_on ? 'chauffage activé' : 'arrêt appliqué')+' · commande '+c.sequence+(c.origin === 'user' ? ' · demande utilisateur '+c.stop_request_id : ' · décision '+c.decision_id)});
    for (const r of processedReadings(snapshot)) if (r.final) events.push({at:r.processed_at, kind:'effect', text:'Effet final traité · mesure '+r.sequence+' · '+r.temperature_c.toFixed(1)+' °C · '+(snapshot.mode === 'cooling' ? (r.cooler_on === false ? 'refroidissement éteint' : 'arrêt non confirmé') : (r.heater_on === false ? 'chauffage éteint' : 'arrêt non confirmé'))+' · observée '+(r.observed_at || 'date non confirmée')});
    return events.sort((a,b)=>(Date.parse(a.at)||Infinity)-(Date.parse(b.at)||Infinity));
  }
  if (typeof module !== 'undefined') { module.exports = {controller, processedReadings, plotData, confirmedCompletion, milestones}; return; }
  const el = id => document.getElementById('thermal-' + id);
  let snapshot, message = '', selected = '', currentBusy = false;
  const bodyText = r => typeof r?.body === 'string' ? r.body : 'Aucun corps exact disponible.';
  function reading() { return snapshot?.readings?.find(r => String(r.sequence) === selected); }
  const svgNode = (tag, attributes, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key,value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (text) node.textContent = text;
    return node;
  };
  let chartSignature = '', focusedSequence;
  function chart(proof) {
    const signature = JSON.stringify([proof?.simulation_id, proof?.created_at, proof?.readings, proof?.decisions, proof?.commands]);
    if (signature === chartSignature) return;
    chartSignature = signature;
    const focused = document.activeElement;
    focusedSequence = focused?.getAttribute?.('data-reading');
    const compact = Boolean(window.matchMedia?.('(max-width: 480px)').matches);
    const width = compact ? 360 : 710, left = 54, right = width-18;
    const plot = el('chart'); plot.setAttribute('viewBox','0 0 '+width+' 260'); plot.replaceChildren(); el('measurements').replaceChildren();
    el('milestones').replaceChildren();
    for (const event of milestones(proof)) {
      const row=document.createElement('li'), elapsed=(Date.parse(event.at)-Date.parse(proof.created_at))/1000;
      const label=event.text.split(' · ').slice(0,2).join(' · ');
      row.textContent=label+(Number.isFinite(elapsed) ? ' · '+elapsed.toFixed(1)+' s depuis création' : ' · date non confirmée');
      row.setAttribute('title',event.text+' · '+(event.at || 'date non confirmée'));
      row.setAttribute('class','thermal-milestone '+event.kind); el('milestones').append(row);
    }
    const observations = plotData(proof), points = observations.filter(p=>p.seconds <= 30), temperatures = points.map(p=>p.reading.temperature_c);
    const low = Math.min(proof?.mode === 'cooling' ? 22 : 19, ...temperatures)-0.5, high = Math.max(proof?.mode === 'cooling' ? 25 : 22, ...temperatures)+0.5;
    const end = 30;
    const x = seconds => left + seconds / end * (right-left), y = temperature => 196 - (temperature-low)/(high-low)*168;
    plot.append(svgNode('line',{x1:left,x2:right,y1:y(22),y2:y(22),class:'thermal-target'}));
    plot.append(svgNode('text',{x:left+12,y:18},'Cible 22 °C'));
    plot.append(svgNode('text',{x:8,y:32},high.toFixed(1)+' °C'));
    plot.append(svgNode('text',{x:8,y:196},low.toFixed(1)+' °C'));
    plot.append(svgNode('line',{x1:left,x2:right,y1:196,y2:196,class:'thermal-axis'}));
    for (const seconds of [0,10,20,30]) {
      plot.append(svgNode('line',{x1:x(seconds),x2:x(seconds),y1:196,y2:201,class:'thermal-axis'}));
      plot.append(svgNode('text',{x:x(seconds),y:219,'text-anchor':seconds === 0 ? 'start' : seconds === 30 ? 'end' : 'middle'},seconds+' s'));
    }
    plot.append(svgNode('text',{x:width/2,y:244,'text-anchor':'middle'},'Secondes réelles depuis la création'));
    if (points.length) plot.append(svgNode('polyline',{points:points.map(p=>x(p.seconds)+','+y(p.reading.temperature_c)).join(' '),class:'thermal-line'}));
    for (const point of points) {
      const r = point.reading;
      if (point === points[0] || (point === points.at(-1) && points.length > 1)) plot.append(svgNode('text',{x:x(point.seconds),y:y(r.temperature_c)-(compact ? 22 : 12),'text-anchor':point === points[0] ? 'start' : 'end'},r.temperature_c.toFixed(1)+' °C'));
      const description = 'Mesure '+r.sequence+' · '+point.seconds.toFixed(3)+' s · '+r.temperature_c+' °C · observée '+r.observed_at+' · traitée '+r.processed_at;
      const circle = svgNode('circle',{cx:x(point.seconds),cy:y(r.temperature_c),r:compact ? 5 : 6,tabindex:0,role:'button','data-reading':r.sequence,'aria-label':description+' · inspecter le corps exact',class:'thermal-point'});
      const choose = () => {selected=String(r.sequence);el('reading').value=selected;selection();if (el('body').parentElement) el('body').parentElement.open=true;};
      circle.addEventListener('click',choose);
      circle.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();choose();}});
      circle.append(svgNode('title',{},description));plot.append(circle);
      if (focusedSequence === String(r.sequence)) circle.focus();
    }
    for (const point of observations) { const r=point.reading, row=document.createElement('li'); row.textContent='Mesure '+r.sequence+' · '+point.seconds.toFixed(3)+' s · '+r.temperature_c.toFixed(1)+' °C · observée '+r.observed_at+' · traitée '+r.processed_at+(point.seconds > 30 ? ' · hors de la fenêtre graphique de 30 s' : ''); el('measurements').append(row); }
    el('chart-note').textContent=points.length ? points.length+' points traités attribués au scénario. Les segments relient les observations; aucune mesure intermédiaire calculée.' : 'Aucun point traité dans la fenêtre avec identité et horodatage d’observation confirmés.';
    const outside = observations.length-points.length;
    if (outside) el('chart-note').textContent += ' '+outside+' observation(s) au-delà de 30 s conservée(s) dans la version texte et le sélecteur; aucune position comprimée sur la courbe.';
  }
  function selection() { const current=reading(); el('selected').textContent=current ? 'Mesure '+current.sequence+' · observation '+(current.observed_at || 'non datée')+' · traitement '+(current.processed_at || 'non confirmé') : 'Sélectionner un point par clic, Entrée ou Espace pour inspecter son corps exact.'; el('body').textContent = bodyText(reading()); el('replay').disabled = currentBusy || typeof reading()?.body !== 'string' || !reading()?.body || !(reading()?.correlation_id || reading()?.envelope?.correlation_id); }
  const app = controller({fetch:window.fetch.bind(window), storage:localStorage, uuid:()=>crypto.randomUUID(), now:()=>Date.now(),
    schedule:setTimeout, cancel:clearTimeout, hidden:()=>document.hidden,
    render(state) {
      currentBusy = state.busy; if (snapshot?.simulation_id !== state.evidence?.simulation_id) selected=''; snapshot = state.evidence; chart(snapshot); if (state.message) message = state.message;
      el('status').textContent = message || 'Aucun scénario thermique lancé.';
      el('identity').textContent = state.id ? 'Simulation thermique : ' + state.id + (snapshot && snapshot.simulation_id !== state.id ? ' · dernières preuves du scénario ' + snapshot.simulation_id : '') : '';
      el('start').disabled = state.busy || !state.canStart;
      el('start').textContent = state.mode === 'cooling' ? 'Lancer le refroidissement' : 'Lancer le chauffage';
      el('mode').value = state.requestedMode || state.mode; el('mode').disabled = state.busy || Boolean(state.transition);
      el('continue').disabled = !state.canContinue || state.transition === 'expired';
      el('abandon').disabled = !state.canAbandon;
      el('transition').textContent = state.transition ? (state.transition === 'expired' ? 'Bascule bloquée : échéance atteinte sans confirmation.' : 'Bascule vers '+(state.requestedMode === 'cooling' ? 'refroidissement' : 'chauffage')+' · '+({waiting:'arrêt en cours',creating:'création en cours'}[state.transition] || 'en attente')+' · continuation explicite après rechargement.') : '';
      el('history').textContent = state.history.length ? JSON.stringify(state.history,null,2) : 'Aucun scénario précédent conservé.';
      el('refresh').disabled = state.busy || !state.id;
      el('resume').disabled = state.busy || !state.id || state.canStart || state.uncertain || snapshot?.status !== 'interrupted';
      if (!snapshot) {
        el('temperature').textContent = '— °C'; el('proof').textContent = 'Aucune preuve.';
        el('decision').textContent = 'Décision persistée : aucune preuve.'; el('command').textContent = 'Commande appliquée : aucune preuve.';
        el('effect').textContent = 'Effet mesuré : aucune preuve.'; el('outcome').textContent='Aucune réussite confirmée.'; el('freshness').textContent='Aucune preuve mesurée datée.'; el('reading').replaceChildren(); selected = ''; selection(); return;
      }
      el('proof').textContent = JSON.stringify(snapshot,null,2);
      const readings = snapshot.readings || [];
      const processed = processedReadings(snapshot);
      const latest = processed.at(-1);
      el('temperature').textContent = latest ? latest.temperature_c.toLocaleString('fr-CA',{minimumFractionDigits:1,maximumFractionDigits:1}) + ' °C' : '— °C';
      el('timing').textContent = 'Multiplicateur serveur : ' + snapshot.multiplier + ' · cadence : 1 s réelle · durée maximale : 30 s';
      const decisions = snapshot.decisions || [];
      el('decision').textContent = decisions.length ? 'Décision persistée : ' + decisions.map(d => (['heating','cooling'].includes(d.recommended_hvac_mode) ? 'Démarrage demandé' : d.recommended_hvac_mode === 'off' ? 'Arrêt demandé' : 'Commande recommandée') + ' · décision ' + d.decision_id + (d.generated_at ? ' · ' + d.generated_at : '')).join('; ') : 'Décision persistée : aucune preuve.';
      const applied = (snapshot.commands || []).filter(c => c.applied_at);
      el('command').textContent = applied.length ? 'Commandes appliquées : ' + applied.map(c => (c.command_type === 'cooling.start' ? 'Refroidissement activé (simulation)' : c.heater_on ? 'Chauffage activé (simulation)' : 'Arrêt appliqué (simulation)') + ' · commande ' + c.sequence + (c.origin === 'user' ? ' · demande utilisateur ' + c.stop_request_id : ' · mesure ' + c.reading_sequence) + ' · ' + c.applied_at).join('; ') : 'Commande appliquée : aucune preuve.';
      const runtime = snapshot.runtime?.status;
      if (state.uncertain || runtime !== 'available') el('status').textContent += ' État actuel incertain; dernières preuves persistées conservées. Runtime ' + (runtime === 'unavailable' ? 'indisponible' : 'non confirmé') + '.';
      if (state.uncertain || runtime !== 'available') el('command').textContent += ' État actuel incertain : dernière preuve persistée, runtime ' + (runtime === 'unavailable' ? 'indisponible' : 'non confirmé') + '.';
      const actuator = snapshot.mode === 'cooling' ? 'refroidissement' : 'chauffage';
      el('outcome').textContent = snapshot.status === 'stopped' && snapshot.stop_confirmed === true ? 'Arrêt sur demande confirmé; aucune réussite à la cible annoncée.' : confirmedCompletion(snapshot) ? 'Réussite confirmée : scénario terminé et relevé final traité, '+actuator+' éteint.' : snapshot.status === 'expired' ? 'Échéance atteinte : aucune réussite confirmée.' : snapshot.status === 'interrupted' ? 'Scénario interrompu : aucune réussite confirmée.' : 'En attente du scénario terminé et de son relevé final traité, '+actuator+' éteint. Atteindre 22 °C ne suffit pas.';
      if (snapshot.simulation_id !== state.id) el('outcome').textContent += ' Preuve historique du scénario '+snapshot.simulation_id+'; elle ne confirme pas le nouveau lancement.';
      el('freshness').textContent = latest ? 'Dernière preuve mesurée : observation '+(latest.observed_at || 'non datée')+' · traitement '+(latest.processed_at || 'date non confirmée')+'.'+(state.uncertain || runtime !== 'available' || snapshot.simulation_id !== state.id ? ' Preuves conservées du scénario '+snapshot.simulation_id+'; état actuel incertain.' : '') : 'Aucune preuve mesurée datée.';
      el('effect').textContent = latest ? 'Effet mesuré : dernière température traitée ci-dessus. ' + processed.length + ' mesure(s) traitée(s).' + (latest.final && (snapshot.mode === 'cooling' ? latest.cooler_on === false : latest.heater_on === false) ? ' Mesure finale : '+actuator+' éteint dans la simulation.' : '') : 'Effet mesuré : aucune mesure traitée.';
      el('reading').replaceChildren();
      for (const r of readings) { const option = document.createElement('option'); option.value = String(r.sequence); option.textContent = 'Mesure ' + r.sequence + ' · ' + (r.processed_at ? 'traitée' : 'traitement non confirmé'); el('reading').append(option); }
      if (!readings.some(r => String(r.sequence) === selected)) selected = readings.length ? String(readings.at(-1).sequence) : '';
      el('reading').value = selected; selection(); if (state.busy) el('replay').disabled = true;
    }});
  el('mode').addEventListener('change',()=>app.selectMode(el('mode').value)); el('continue').addEventListener('click',app.continue); el('abandon').addEventListener('click',app.abandon);
  el('start').addEventListener('click',app.start); el('refresh').addEventListener('click',app.refresh); el('resume').addEventListener('click',app.resume);
  el('reading').addEventListener('change',()=> {selected=el('reading').value; selection();});
  el('replay').addEventListener('click', async()=> {const result=await app.replay(reading()); el('replay-status').textContent=result?.receipt ? 'Publication reçue; traitement à confirmer. Reçu : ' + JSON.stringify(result.receipt) : 'Réponse absente : renvoi incertain. Aucun nouvel envoi automatique.';});
  window.matchMedia?.('(max-width: 480px)').addEventListener('change',()=>{chartSignature='';chart(snapshot);});
  document.addEventListener('visibilitychange',()=>app.visibility());
})();
