'use strict';
(function () {
  const key='telemetry-lab:thermostat-v1';
  function color(value) { const fraction=Math.min(1,Math.max(0,(value-15)/15));return `hsl(${240*(1-fraction)} 75% 25%)`; }
  // These are dated milestones, never a claim about a message's present location.
  function flowSummary(evidence) {
    const reading=evidence?.latest_reading, command=evidence?.latest_command;
    const receipt=reading?.transport_receipt, transport=receipt?.transport_receipt;
    const milestone=(service,label,at,confirmed)=>({service,label,at:confirmed&&at?at:null,confirmed:!!(confirmed&&at)});
    return {
      readingLabel:reading?'Mesure n° '+(reading.sequence??'inconnu')+(Number.isFinite(reading.temperature_c)?' · '+reading.temperature_c.toFixed(1)+' °C':''):'Aucune mesure traitée disponible',
      commandLabel:command?'Commande n° '+(command.sequence??'inconnu')+' · '+(command.command_type||'type inconnu'):'Aucune commande enregistrée',
      commandOrigin:command?(command.reading_sequence!=null?'Issue de la mesure n° '+command.reading_sequence+'. ':'Mesure d’origine inconnue. ')+'Réglage n° '+(command.settings_revision??'inconnu')+'.':'Une mesure peut être traitée sans déclencher de commande.',
      reading:[
        milestone('Simulateur','Mesure observée',reading?.observed_at,!!reading),
        milestone('Ingestion HTTP','Réponse acceptée consignée',reading?.published_at,receipt?.status==='accepted'&&!!receipt?.event_id),
        milestone('SQS · mesures','Publication confirmée',transport?.observed_at,transport?.source==='SQS.SendMessage'&&!!transport?.message_id),
        milestone('Worker → PostgreSQL','Traitement enregistré',reading?.processed_at,!!reading?.processed_event_id),
      ],
      command:[
        milestone('PostgreSQL','Commande enregistrée',command?.created_at,!!command),
        milestone('SQS · commandes','Publication confirmée',command?.published_at,!!command?.transport_message_id),
        milestone('Simulateur',command?.status==='cancelled'?'Commande annulée':command?.status==='applied'?'Application confirmée':'Application non confirmée',command?.applied_at,command?.status==='applied'),
      ],
    };
  }
  function controller({fetch,storage,uuid,schedule,cancel,hidden,render}) {
    let evidence=null,operation=null,busy=false,uncertain=true,timer=null,message='',cursor=null,page=null,pageKind=null,pageSession=null,storageUnavailable=false,pendingAction=null,actionRetryable=false;
    try {const cached=JSON.parse(storage.getItem(key)||'null');evidence=cached?.evidence||null;operation=cached?.operation||null;pendingAction=cached?.pendingAction||null;}catch{storageUnavailable=true;}
    const save=()=>{try{storage.setItem(key,JSON.stringify({evidence,operation,pendingAction}));return true;}catch{storageUnavailable=true;return false;}};
    const stopping=()=>evidence?.phase==='stopping'||evidence?.phase==='stop_pending'||evidence?.status==='stopping';
    const show=()=>render({evidence,operation,pendingAction,actionRetryable,busy,uncertain,message,storageUnavailable,page,cursor,pageKind,pageSession,controlsDisabled:busy||uncertain||!!operation||stopping()||evidence?.policy!=='thermostat'||evidence?.status!=='active'});
    const stop=()=>{cancel(timer);timer=null;};
    const arm=()=>{stop();if(!hidden())timer=schedule(refresh,evidence?.phase==='idle'&&!uncertain&&!operation?5000:1000);};
    async function request(url,options={}) {const response=await fetch(url,{...options,cache:'no-store',signal:AbortSignal.timeout(8000)});if(!response.ok){const error=Error('HTTP '+response.status);error.status=response.status;throw error;}return response.json();}
    const base=()=>'/thermal-simulations/'+encodeURIComponent(evidence.simulation_id);
    async function refresh() {
      if(busy||hidden())return;stop();actionRetryable=false;busy=true;show();
      try {
        const next=await request('/thermal-simulations/current');
        if(next.policy!=='thermostat'){uncertain=true;message='Une session historique existe. Aucun remplacement automatique.';return;}
        evidence=next;uncertain=false;
        if(pendingAction){
          if(next.simulation_id===pendingAction.simulation_id&&(pendingAction.kind==='new'||pendingAction.kind==='resume'&&['active','stopping','stopped'].includes(next.status)&&(pendingAction.resume_generation===undefined||next.resume_generation>pendingAction.resume_generation)||pendingAction.kind==='stop'&&['stopping','stopped'].includes(next.status))){pendingAction=null;message='Action retrouvée par lecture.';}else{actionRetryable=next.runtime?.status==='available'&&(pendingAction.kind==='new'?next.simulation_id===pendingAction.predecessor_id&&['completed','stopped','expired'].includes(next.status):next.simulation_id===pendingAction.simulation_id&&next.status===pendingAction.prior_status&&(pendingAction.resume_generation===undefined||next.resume_generation===pendingAction.resume_generation));uncertain=true;message='Action incertaine conservée; aucun renvoi automatique.';}
        }
        if(operation){
          if(operation.simulation_id!==evidence.simulation_id){uncertain=true;message='La demande incertaine appartient à une autre session.';}
          else {try {const record=await request(base()+'/settings/'+encodeURIComponent(operation.operation_id));if(record.operation_id!==operation.operation_id)throw Error('Identité incompatible');operation=null;message='Demande retrouvée par lecture.';}catch(error){operation.missing=error.status===404;save();uncertain=true;message='Demande non confirmée. Identité conservée; aucun renvoi automatique.';}}
        }else if(!pendingAction)message='';
        if(next.runtime?.status!=='available'){uncertain=true;message='Runtime indisponible. Dernières preuves datées conservées; état incertain.';}
        save();
      }catch(error){actionRetryable=error.status===404&&pendingAction?.kind==='new';uncertain=true;message=error.status===404?'Thermostat absent. Création explicite disponible dans Détails.':'Lecture indisponible. Dernières preuves datées conservées; état incertain.';}
      finally{busy=false;show();arm();}
    }
    async function settings(mode,target) {
      if(busy||uncertain||operation||stopping()||evidence?.status!=='active')return;
      if(!['heating','cooling'].includes(mode)||!Number.isFinite(target)||target<15||target>30||target*2!==Math.round(target*2)){message='Consigne requise : 15 à 30 °C par pas de 0,5.';show();return;}
      if(mode===evidence.mode&&target===evidence.target_c)return;
      operation={simulation_id:evidence.simulation_id,operation_id:uuid(),expected_revision:evidence.settings_revision,mode,target_c:target};
      if(!save()){operation=null;message='Stockage indisponible : demande annulée avant envoi pour conserver son identité.';show();return;}
      await sendSetting();
    }
    async function sendSetting(){
      if(busy||!operation||operation.rejected)return;
      stop();busy=true;uncertain=true;show();
      const {simulation_id,operation_id,expected_revision,mode,target_c}=operation;
      const body={operation_id,expected_revision,mode,target_c};
      try{const next=await request('/thermal-simulations/'+encodeURIComponent(simulation_id)+'/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(next.simulation_id!==simulation_id)throw Error('Identité incompatible');evidence=next;operation=null;uncertain=next.runtime?.status!=='available';message=uncertain?'Runtime indisponible. Dernières preuves conservées.':'Réglage enregistré.';save();}
      catch(error){if(error.status===409){operation.rejected=true;save();}message=error.status===409?'Réglage en conflit. Relire explicitement la demande et les preuves.':'Réponse perdue : demande incertaine conservée, aucun renvoi automatique.';}
      finally{busy=false;show();arm();}
    }
    async function retry(){if(pendingAction){if(actionRetryable)await sendAction();return;}if(!operation?.missing||operation.rejected||evidence?.runtime?.status!=='available')return;await sendSetting();}
    async function exception(action) {
      if(busy||operation||pendingAction)return;
      if(action==='resume'&&(uncertain||evidence?.status!=='interrupted'))return;
      if(action==='stop'&&(uncertain||evidence?.status!=='active'))return;
      if(action==='new'&&evidence&& !['completed','stopped','expired'].includes(evidence.status))return;
      pendingAction={kind:action,simulation_id:action==='new'?uuid():evidence.simulation_id,predecessor_id:evidence?.simulation_id,prior_status:evidence?.status,...(action==='resume'&&Number.isInteger(evidence.resume_generation)?{resume_generation:evidence.resume_generation}:{})};if(!save()){pendingAction=null;message='Stockage indisponible : action annulée avant envoi.';show();return;}
      await sendAction();
    }
    async function sendAction(){
      if(busy||!pendingAction)return;
      const action=pendingAction.kind;
      stop();actionRetryable=false;busy=true;uncertain=true;show();
      try {await request(action==='new'?'/thermal-simulations':'/thermal-simulations/'+encodeURIComponent(pendingAction.simulation_id)+'/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:action==='new'?JSON.stringify({simulation_id:pendingAction.simulation_id,policy:'thermostat',mode:'heating'}):'{}'});message='Action envoyée; attente de lecture.';save();}
      catch{message='Réponse incertaine. Relire les preuves; aucun renvoi automatique.';}
      finally{busy=false;show();arm();}
    }
    async function history(kind,next=false) {
      if(busy||!evidence||!['readings','commands','settings','decisions'].includes(kind))return;
      const session=evidence.simulation_id;
      if(next&&(cursor===null||pageKind!==kind||pageSession!==session)){cursor=null;page=null;pageKind=null;pageSession=null;show();return;}
      if(!next){cursor=null;page=null;pageKind=kind;pageSession=session;}
      stop();busy=true;show();try{page=await request(base()+'/history?kind='+kind+'&cursor='+encodeURIComponent(next?cursor:0));pageKind=kind;pageSession=session;cursor=page.next_cursor??null;}catch{message='Historique indisponible; dernière page conservée.';}finally{busy=false;show();arm();}
    }
    async function replay(index){const item=page?.items?.[index];const correlation=item?.correlation_id||item?.envelope?.correlation_id;if(busy||typeof item?.body!=='string'||!correlation)return;stop();busy=true;show();try{await request('/ingestion/telemetry',{method:'POST',headers:{'Content-Type':'application/json','X-Correlation-ID':correlation},body:item.body});message='Corps exact renvoyé; reçu disponible après lecture.';}catch{message='Renvoi incertain; aucun nouvel envoi automatique.';}finally{busy=false;show();arm();}}
    function abandon(){if(busy||!operation?.rejected)return;operation=null;save();message='Demande rejetée abandonnée explicitement. Relire avant un nouveau réglage.';show();}
    show();refresh();return {refresh,settings,history,replay,abandon,retry,resume:()=>exception('resume'),stop:()=>exception('stop'),start:()=>exception('new'),visibility(){stop();if(!hidden())refresh();}};
  }
  if(typeof module!=='undefined'){module.exports={controller,color,flowSummary};return;}
  const el=name=>document.getElementById('thermostat-'+name);let state;
  const renderedFlow={session:null,reading:null,command:null};
  function renderFlow(next) {
    const flow=flowSummary(next.evidence);
    document.getElementById('flow-reading-label').textContent=flow.readingLabel;
    document.getElementById('flow-command-label').textContent=flow.commandLabel;
    document.getElementById('flow-command-origin').textContent=flow.commandOrigin;
    document.getElementById('flow-status').textContent=!next.evidence?'En attente de preuves du parcours.':next.uncertain?'Lecture incertaine · dernières preuves conservées.':'Étapes datées du dernier relevé et de la dernière commande.';
    for(const kind of ['reading','command']) {
      const context=JSON.stringify([next.evidence?.simulation_id,next.evidence?.[kind==='reading'?'latest_reading':'latest_command']?.sequence]);
      const signature=JSON.stringify([context,flow[kind]]);
      const previous=renderedFlow[kind];
      if(previous?.signature===signature)continue;
      const incoming=!next.uncertain&&renderedFlow.session===next.evidence?.simulation_id&&previous!==null;
      const list=document.getElementById('flow-'+kind);
      list.replaceChildren(...flow[kind].map((step,index)=>{
        const pulse=incoming&&(previous.context!==context||JSON.stringify(previous.steps[index])!==JSON.stringify(step));
        const item=document.createElement('li');item.className='flow-step'+(step.confirmed?' confirmed':'')+(pulse&&step.confirmed?' flow-pulse':'');
        const service=document.createElement('strong');service.textContent=step.service;
        const proof=document.createElement('span');proof.textContent=step.confirmed?step.label:step.label==='Commande annulée'?step.label:'Preuve non disponible';
        item.append(service,proof);
        if(step.at){const time=document.createElement('time');time.dateTime=step.at;time.title=step.at;const date=new Date(step.at);time.textContent=Number.isNaN(date.valueOf())?step.at:date.toLocaleString('fr-CA');item.append(time);}
        return item;
      }));
      if(next.evidence)renderedFlow[kind]={signature,context,steps:flow[kind]};
    }
    if(next.evidence)renderedFlow.session=next.evidence.simulation_id;
  }
  let storage;try{storage=window.localStorage;}catch{storage={getItem(){throw Error('unavailable');},setItem(){throw Error('unavailable');}};}
  const app=controller({fetch:window.fetch.bind(window),storage,uuid:()=>crypto.randomUUID(),schedule:setTimeout,cancel:clearTimeout,hidden:()=>document.hidden,render(next){
    state=next;const e=next.evidence,r=e?.latest_reading;
    const temp=r?.temperature_c;
    el('temperature').textContent=Number.isFinite(temp)?temp.toFixed(1)+' °C':'— °C';if(Number.isFinite(temp))el('temperature').style.color=color(temp);
    el('applied').textContent=(!r||typeof r.heater_on!=='boolean'||typeof r.cooler_on!=='boolean')?'Action mesurée : inconnue.':'Dernier relevé : '+(r.heater_on&&r.cooler_on?'état incohérent des actionneurs':r.heater_on?'chauffage actif':r.cooler_on?'refroidissement actif':'actionneurs éteints')+(next.uncertain?' · état actuel incertain.':'.');
    renderFlow(next);
    if(document.activeElement!==el('target'))el('target').value=e?.target_c??22;
    for(const mode of ['cooling','heating']){el(mode).setAttribute('aria-pressed',String(e?.mode===mode));el(mode).disabled=next.controlsDisabled;}
    el('target').disabled=next.controlsDisabled;
    const phase={idle:'Au repos confirmé',start_requested:'Démarrage demandé',starting:'Démarrage demandé',start_pending:'Démarrage demandé',acting:'Action en cours',heating:'Chauffage en cours',cooling:'Refroidissement en cours',stopping:'Arrêt en attente',stop_pending:'Arrêt en attente'}[e?.phase]||e?.phase||'';
    el('status').textContent=next.message||(!r?'En attente d’une mesure traitée.':e?.status==='interrupted'?'Session interrompue; reprise explicite dans Détails.':e?.status==='stopped'?'Session arrêtée. Nouvelle session disponible dans Détails.':e?.status==='expired'?'Session expirée.':phase);
    el('date').textContent=r?'Observation : '+r.observed_at+' · traitement : '+r.processed_at+(next.uncertain?' · état actuel incertain':'')+(next.storageUnavailable?' · stockage indisponible, preuves conservées en mémoire':''):'Aucune preuve mesurée datée.'+(next.storageUnavailable?' Stockage indisponible.':'');
    el('proof').textContent=JSON.stringify(e,null,2);el('operation').textContent=JSON.stringify(next.operation||next.pendingAction,null,2)||'Aucune demande locale.';
    el('refresh').disabled=next.busy;el('resume').disabled=next.busy||next.uncertain||!!next.operation||e?.status!=='interrupted';el('stop').disabled=next.busy||next.uncertain||!!next.operation||e?.status!=='active';el('new').disabled=next.busy||!!next.operation||!!next.pendingAction||!!e&&!['completed','stopped','expired'].includes(e.status);
    el('kind').disabled=next.busy;el('history').disabled=next.busy||!e;el('next').disabled=next.busy||next.cursor===null||next.pageKind!==el('kind').value||next.pageSession!==e?.simulation_id;
    if(!next.page){el('history-proof').textContent='Aucune page consultée.';el('reading').replaceChildren();}
    if(next.page){el('history-proof').textContent=JSON.stringify(next.page,null,2);const select=el('reading');const value=select.value;select.replaceChildren();next.page.items.forEach((item,index)=>{const option=document.createElement('option');option.value=index;option.textContent='Mesure '+(item.sequence??index)+' · '+(item.observed_at||'date inconnue');select.append(option);});select.value=value||'0';}el('replay').disabled=next.busy||!next.page?.items?.some(item=>typeof item.body==='string');el('abandon').disabled=next.busy||!next.operation?.rejected;el('retry').disabled=next.busy||!(next.actionRetryable||next.operation?.missing&&!next.operation?.rejected&&e?.runtime?.status==='available');
  }});
  el('target').addEventListener('change',()=>app.settings(state.evidence?.mode||'heating',Number(el('target').value)));
  for(const mode of ['cooling','heating'])el(mode).addEventListener('click',()=>app.settings(mode,Number(el('target').value)));
  for(const [name,action] of [['refresh','refresh'],['resume','resume'],['stop','stop'],['new','start']])el(name).addEventListener('click',()=>app[action]());
  el('retry').addEventListener('click',()=>app.retry());el('abandon').addEventListener('click',()=>app.abandon());el('replay').addEventListener('click',()=>app.replay(Number(el('reading').value)));
  el('history').addEventListener('click',()=>app.history(el('kind').value));el('next').addEventListener('click',()=>app.history(el('kind').value,true));
  el('kind').addEventListener('change',()=>app.history(el('kind').value));document.addEventListener('visibilitychange',()=>app.visibility());
})();
