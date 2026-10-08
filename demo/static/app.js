'use strict';
const $ = id => document.getElementById(id);
const pretty = value => JSON.stringify(value, null, 2);
const temperatureText = value => value.toLocaleString('fr-CA', {minimumFractionDigits:1, maximumFractionDigits:1}) + ' °C';
let simulation = new URL(location.href).searchParams.get('simulation');
let saved = {version:3, measurements:[]}, lastTrace = null, cacheAvailable = true;
let generation = 0, timer, deadline = 0, polls = 0, sending = false, inFlight = null;
const labels = {started:'Début de tentative observé',processed:'Traitement enregistré',
  rejected:'Rejet métier enregistré',duplicate:'Doublon reconnu',failed:'Échec de tentative — réessayable'};
async function request(url, options = {}) {
  const response = await fetch(url, {...options, cache:'no-store', signal:AbortSignal.timeout(8000)});
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}
function detail(title, data) {
  const node = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = title;
  const pre = document.createElement('pre'); pre.textContent = pretty(data);
  node.append(summary, pre); return node;
}
function paragraph(text) { const p = document.createElement('p'); p.textContent = text; return p; }
function save() {
  try { sessionStorage.setItem('telemetry-lab:simulation-v3:' + simulation, pretty(saved)); cacheAvailable = true; }
  catch { cacheAvailable = false; }
}
function validBody(body) {
  try {
    const payload = JSON.parse(body);
    return typeof body === 'string' && payload.building_id === 'demo-' + simulation &&
      typeof payload.timestamp === 'string' && Number.isFinite(Date.parse(payload.timestamp));
  } catch { return false; }
}
function restoreReceipt() {
  try {
    const current = JSON.parse(sessionStorage.getItem('telemetry-lab:simulation-v3:' + simulation));
    if (current?.version === 3 && Array.isArray(current.measurements)) {
      saved.measurements = current.measurements.map(m => ({
        id:m.id, label:m.label, confirmation:m.confirmation || null, body:validBody(m.body) ? m.body : null,
        posts:Array.isArray(m.posts) ? m.posts :[]
      }));
    } else {
      const old = JSON.parse(sessionStorage.getItem('telemetry-lab:simulation-v2:' + simulation));
      const receipt = JSON.parse(sessionStorage.getItem('telemetry-lab:receipt:' + simulation));
      if (old || receipt?.response?.correlation_id === simulation) saved.measurements = [{id:'legacy',label:'Mesure historique unique',
        body:validBody(old?.body) ? old.body : null,
        posts:Array.isArray(old?.posts) ? old.posts :
          receipt?.response?.correlation_id === simulation ? [{receipt}] :[]}];
    }
  } catch { /* Missing or unreadable cache is not evidence. */ }
  showReceipts(); updateActions();
}
const replayTarget = () => saved.measurements.at(-1);
const firstEvent = m => m?.posts[0]?.receipt?.response?.event_id;
function persisted(m) {
  const id = firstEvent(m);
  return id && lastTrace?.results.find(r => r.event_id === id && r.status === 'processed');
}
function roomState(state, message) {
  $('room').className = 'room ' + state;
  $('room-status').textContent = message;
}
function renderRoom() {
  const target = replayTarget(), result = persisted(target);
  if (!target) {
    roomState('unknown', '？ Aucun reçu initial conservé dans cet onglet : la salle ne peut attribuer un résultat à une mesure. Les traces serveur restent consultables ci-dessous.');
    return;
  }
  if (!result && target?.posts.length && !firstEvent(target)) {
    roomState('unknown', '？ Publication non confirmée : réponse HTTP absente. Aucun résultat attribué sans reçu; aucun renvoi automatique.');
    return;
  }
  if (!result) {
    roomState('pending', '◷ Résultat non confirmé pour cette mesure. La salle attend la lecture persistée liée au reçu.');
    return;
  }
  const m = result.measurement;
  $('temperature').textContent = temperatureText(m.temperature_c);
  const cooling = result.decisions.find(d => d.decision_type === 'adjust_airflow' && d.recommended_hvac_mode === 'cooling');
  if (cooling) {
    roomState('cooling', '❄ Refroidissement recommandé · mode cooling · débit ' + cooling.recommended_airflow_pct +
      ' %. Température traitée : ' + temperatureText(m.temperature_c) + ' (> 25,5 °C), avec occupation. Aucune commande HVAC appliquée.');
  } else if (!result.decisions.length) {
    roomState('neutral', '✓ Traitement réussi · aucune recommandation enregistrée. Température traitée : ' +
      temperatureText(m.temperature_c) + '; avec les valeurs de ce parcours, le refroidissement se déclenche seulement au-dessus de 25,5 °C.');
  } else {
    roomState('neutral', '✓ Résultat historique confirmé. Consulter la recommandation enregistrée ci-dessous. Aucune commande HVAC appliquée.');
  }
  $('room-status').textContent += ' Résultat ' + result.event_id + ' · lecture serveur : ' + result.fetched_at + '.';
}
function canContinue() {
  return saved.measurements.length === 2 && persisted(saved.measurements[0]) &&
    saved.measurements[1].posts.length === 0 && validBody(saved.measurements[1].body);
}
function showReceipts() {
  const posts = saved.measurements.flatMap(m => m.posts.map(p => ({measurement:m.id,...p})));
  $('receipt').textContent = posts.length ? pretty(posts) : 'Indisponible';
  const latest = posts.at(-1);
  $('receipt-status').textContent = !latest ?
    'Reçu non conservé : publication non confirmée par ce navigateur.' : latest.receipt ?
    'Réponse d’ingestion conservée pour le dernier POST. Publication confirmée par son reçu transport; traitement non déduit du reçu.' :
    'Publication non confirmée pour le dernier POST : réponse absente ou en erreur. Pas de renvoi automatique.';
  $('payload').textContent = replayTarget()?.body || 'Corps exact absent. Les valeurs normalisées du résultat ne permettent pas de le reconstruire.';
}
function updateActions() {
  const target = replayTarget();
  $('resend').disabled = sending || !target?.body || !persisted(target);
  $('continue').hidden = !canContinue(); $('continue').disabled = sending;
  $('resend-status').textContent = !target?.body ?
    'Renvoi indisponible : payload exact non conservé dans cet onglet. Consultation des preuves serveur possible.' :
    !persisted(target) ? 'Le renvoi attend le résultat enregistré corrélé au reçu de cette mesure.' :
    'Le renvoi crée un nouveau POST de la dernière mesure, avec le même corps JSON et le même timestamp. Il reçoit de nouveaux IDs d’envoi et de transport.';
  if (!cacheAvailable) $('resend-status').textContent += ' Stockage de l’onglet indisponible : corps et reçus risquent de manquer après rechargement.';
}
function originalLink(id, results) {
  if (!id || !results.some(r => r.event_id === id)) return document.createTextNode(' Résultat original non disponible dans cette lecture.');
  const link = document.createElement('a'); link.href = '#result-' + id;
  link.textContent = 'Voir le résultat original ' + id; return link;
}
function render(trace) {
  lastTrace = trace; updateActions(); renderRoom();
  $('identity').textContent = 'Simulation (correlation_id) : ' + simulation;
  $('freshness').textContent = 'Lecture serveur : ' + trace.fetched_at + ' · réception navigateur : ' + new Date().toISOString();
  for (const id of ['timeline','duplicates','submissions','result']) $(id).replaceChildren();
  // Server membership is evidence-based. Browser slots only add their own receipts.
  const groups = saved.measurements.map(m => ({label:m.label, ids:new Set(m.posts.map(p => p.receipt?.response?.event_id).filter(Boolean)), measurement:m}));
  for (const m of trace.measurements || trace.results.map(r => ({original_event_id:r.event_id,send_event_ids:[r.event_id]}))) {
    let group = groups.find(g => m.send_event_ids.some(id => g.ids.has(id)));
    if (!group) { group = {label:'Mesure enregistrée · original ' + m.original_event_id, ids:new Set()}; groups.push(group); }
    m.send_event_ids.forEach(id => group.ids.add(id));
  }
  // Unknown starts or a missing original must remain visible without a guessed assignment.
  const unassigned = trace.observations.filter(o => !groups.some(g => g.ids.has(o.event_id)));
  if (unassigned.length) groups.push({label:'Mesure non déterminée — preuves sans rattachement confirmé',ids:new Set(unassigned.map(o=>o.event_id))});
  for (const group of groups) {
    const chronology = document.createElement('div');
    const heading = document.createElement('h3'); heading.textContent = group.label; chronology.append(heading);
    if (group.measurement) {
      chronology.append(detail('Corps exact et reçus propres à cette mesure', group.measurement));
      if (group.measurement.posts.some(p => !p.receipt)) chronology.append(paragraph('POST sans réponse conservée : publication non confirmée. Aucun rattachement supposé aux preuves serveur.'));
      if (!group.measurement.posts.length) chronology.append(paragraph('Aucun envoi conservé pour cette mesure.'));
    }
    const observations = trace.observations.filter(o => group.ids.has(o.event_id));
    if (!observations.length) chronology.append(paragraph('Aucune observation de tentative disponible pour cette mesure.'));
    for (const observation of observations) {
      const node = detail((labels[observation.kind] || observation.kind) + ' · ' + observation.observed_at, observation);
      if (observation.kind === 'duplicate') {
        const explanation = paragraph('Cette tentative a reconnu une mesure déjà enregistrée. Elle ne crée pas de seconde décision. ');
        explanation.append(originalLink(observation.original_event_id, trace.results)); node.append(explanation);
        const link = paragraph('Doublon reconnu pour l’envoi ' + observation.event_id + ' · tentative ' + observation.attempt_id + ' : ');
        link.append(originalLink(observation.original_event_id, trace.results)); $('duplicates').append(link);
      }
      chronology.append(node);
    }
    $('timeline').append(chronology);
    for (const send of (trace.sends || []).filter(s => group.ids.has(s.event_id))) {
      const block = document.createElement('div'); block.append(paragraph(group.label + ' · envoi applicatif (event_id) : ' + send.event_id));
      for (const attempt of send.attempts) block.append(paragraph(
        (attempt.delivery === 'redelivery' ? 'Redélivrance observée du même message SQS' :
          attempt.delivery === 'unknown' ? 'Identité transport manquante : redélivrance indéterminée' :
          'Première tentative visible de ce message (pas nécessairement sa première livraison)') +
        ' · enveloppe : ' + attempt.envelope_id + ' · message transport : ' + (attempt.transport_message_id || 'inconnu') + ' · tentative : ' + attempt.attempt_id));
      $('submissions').append(block);
    }
  }
  if (!groups.length) $('timeline').textContent = 'Aucune observation de tentative disponible.';
  if (trace.journal_quality === 'unavailable') $('timeline').append(paragraph('Journal indisponible; le résultat métier reste une preuve indépendante.'));
  if (!(trace.sends || []).length) $('submissions').textContent = 'Aucun envoi observable dans le journal. Les reçus HTTP restent des preuves distinctes.';
  for (const result of trace.results) {
    const title = document.createElement('h3');
    title.textContent = 'Mesure · ' + temperatureText(result.measurement.temperature_c) + ' · ' + result.measurement.co2_ppm + ' ppm · ' + result.event_timestamp; $('result').append(title);
    for (const decision of result.decisions) {
      const value = paragraph(decision.recommended_airflow_pct + ' % de ventilation recommandée'); value.className = 'recommendation';
      const why = paragraph(decision.decision_type + ' · ' + decision.reason_text + ' Valeurs enregistrées : ' +
        result.measurement.co2_ppm + ' ppm, ' + temperatureText(result.measurement.temperature_c) + ', ventilation ' +
        result.measurement.airflow_pct + ' %. Aucune commande appliquée.');
      $('result').append(value, why);
    }
    if (!result.decisions.length) {
      const m = result.measurement;
      const normalScenario = result.status === 'processed' && m.co2_ppm === 700 && m.temperature_c <= 25.5 && m.occupancy === 8 && m.airflow_pct === 40;
      $('result').append(paragraph('Événement enregistré : ' + result.status + '; aucune recommandation.' +
        (normalScenario ? ' À 700 ppm, au plus 25,5 °C, 8 occupants et 40 % de ventilation, aucune des règles du parcours ne se déclenche.' : '')));
    }
    const original = detail('Inspecter le résultat relu dans PostgreSQL · ' + result.event_id, result);
    original.id = 'result-' + result.event_id; $('result').append(original);
  }
  if (!trace.results.length) $('result').textContent = 'Résultat inconnu : aucun résultat enregistré observé.';
  const active = [...saved.measurements].reverse().find(m => m.posts.length);
  const latest = active?.posts.at(-1), target = latest?.receipt?.response?.event_id;
  const terminal = target && trace.observations.find(o => o.event_id === target && ['processed','rejected','duplicate'].includes(o.kind));
  const confirmed = target && trace.results.some(r => r.event_id === target);
  if (active && !target) $('status').textContent = 'Publication non confirmée : réponse du dernier POST absente. Résultats serveur consultables séparément; aucune continuation automatique.';
  else if (terminal?.kind === 'duplicate') $('status').textContent = 'Doublon reconnu pour le dernier POST. Aucune seconde décision créée par cette tentative. ' +
    (trace.results.some(r => r.event_id === terminal.original_event_id) ? 'Résultat original consultable.' : 'Résultat original indisponible dans cette lecture.');
  else if (active?.posts.length > 1 && !confirmed) $('status').textContent = 'Résultat original conservé. Suite du dernier POST non confirmée : observation du doublon manquante.';
  else if (canContinue()) $('status').textContent = 'Mesure normale : résultat enregistré confirmé. ' + 'Scénario historique suspendu. Continuer explicitement pour envoyer la seconde mesure.';
  else if (confirmed || (!active && trace.results.length)) $('status').textContent = 'Résultat enregistré relu dans PostgreSQL. Suivi automatique terminé.';
  else if (trace.observations.some(o => o.kind === 'started' && (!active || o.event_id === target))) $('status').textContent = 'Début de tentative observé; suite non confirmée.';
  else $('status').textContent = 'Aucune preuve de traitement disponible pour le dernier envoi.';
  if (trace.truncated) $('status').textContent += ' Trace partielle (limite de lecture atteinte).';
  return Boolean(confirmed || terminal?.kind === 'duplicate' || (!active && trace.results.length));
}
function schedule(token) {
  if (token !== generation || document.hidden) return;
  if (Date.now() >= deadline || polls >= 30) {
    $('status').textContent += ' Suivi automatique expiré; vous pouvez relire les preuves.';
    if (!persisted(replayTarget())) roomState('unknown', '？ État inconnu : suivi expiré sans résultat confirmé. Relire les preuves.');
    return;
  }
  if (inFlight === token) return;
  clearTimeout(timer); timer = setTimeout(() => poll(token), Math.min(1000 * 1.5 ** polls, 5000));
}
async function poll(token) {
  if (token !== generation || document.hidden) return;
  if (Date.now() >= deadline || polls >= 30) { schedule(token); return; }
  if (inFlight === token) return;
  polls++; inFlight = token;
  try {
    const trace = await request('/query/simulations/' + encodeURIComponent(simulation));
    if (token !== generation) return;
    lastTrace = trace;
    for (const measurement of saved.measurements) {
      const result = persisted(measurement);
      if (result && !measurement.confirmation) {
        measurement.confirmation = {browser_confirmed_at:new Date().toISOString(), result};
        save();
      }
    }
    const done = render(trace);
    if (done) { deadline = 0; return; }
  } catch {
    if (token !== generation) return;
    roomState('unknown', '？ Consultation indisponible · état actuel inconnu. Les résultats déjà affichés ci-dessous restent datés.');
    $('status').textContent = 'Consultation indisponible. État actuel inconnu; les preuves déjà affichées restent datées.';
  } finally { if (inFlight === token) inFlight = null; }
  schedule(token);
}
function follow() {
  clearTimeout(timer); generation++; deadline = Date.now() + 120000; polls = 0;
  $('refresh').hidden = false; $('identity').textContent = 'Simulation : ' + simulation; poll(generation);
}
async function submitMeasurement(measurement) {
  if (sending || !validBody(measurement?.body)) return;
  sending = true; $('send').disabled = true;
  clearTimeout(timer); const token = ++generation; deadline = 0;
  const post = {browser_requested_at:new Date().toISOString(), receipt:null};
  measurement.posts.push(post); save(); showReceipts(); updateActions();
  $('status').textContent = measurement.label + ' · envoi HTTP en cours…';
  try {
    const response = await request('/ingestion/telemetry', {method:'POST',
      headers:{'Content-Type':'application/json','X-Correlation-ID':simulation},body:measurement.body});
    if (token !== generation) return;
    post.receipt = {response,browser_received_at:new Date().toISOString()};
  } catch { /* A cached pending POST is ambiguous, never retry automatically. */ }
  finally {
    sending = false; $('send').disabled = false;
    if (token === generation) { save(); showReceipts(); updateActions(); follow(); }
  }
}
async function start() {
  if (sending) return;
  simulation = crypto.randomUUID(); lastTrace = null;
  const url = new URL(location.href); url.searchParams.set('simulation', simulation); url.hash = ''; history.replaceState(null, '', url);
  for (const id of ['result','timeline','submissions','duplicates','freshness']) $(id).replaceChildren();
  // 71 equally sized intervals: inclusive 22.0–29.0 °C, in tenths.
  const temperature = (220 + Math.floor(Math.random() * 71)) / 10;
  $('temperature').textContent = temperatureText(temperature);
  roomState('pending', '◷ Mesure générée · résultat non confirmé. Aucune recommandation déduite du tirage.');
  saved = {version:3, measurements:[{
    id:'temperature', label:'Mesure de température',
    body:pretty({building_id:'demo-' + simulation,zone_id:'salle-synthetique',timestamp:new Date().toISOString(),
      temperature_c:temperature,humidity_pct:45,occupancy:8,co2_ppm:700,hvac_mode:'ventilation',airflow_pct:40}), posts:[]
  }]};
  await submitMeasurement(saved.measurements[0]);
}
$('send').addEventListener('click', start);
$('continue').addEventListener('click', () => { if (!sending && canContinue()) { return submitMeasurement(saved.measurements[1]); } });
$('resend').addEventListener('click', () => { if (!sending && persisted(replayTarget())) { return submitMeasurement(replayTarget()); } });
$('refresh').addEventListener('click', () => { if (!sending) { follow(); } });
document.addEventListener('visibilitychange', () => {
  clearTimeout(timer);
  if (!document.hidden && simulation && deadline > 0 && !sending) schedule(generation);
});
request('/environment').then(e => {$('environment').textContent = 'Local · LocalStack / SQS émulé · processus Python · révision ' + e.release_revision;})
  .catch(() => {$('environment').textContent = 'Métadonnées d’environnement indisponibles.';});
if (simulation) { restoreReceipt(); follow(); }

// Queue sampling has an independent, slower and bounded lifecycle. It never changes
// simulation state. Freshness still ages after polling stops or a tab was hidden.
let queueTimer, queueAgeTimer, queueDeadline = Date.now() + 120000, queuePolls = 0;
let queueBusy = false, queueNextRead = 0, queueSample = null, queueReceivedAt = 0, queueFailed = false;
function renderQueue() {
  clearTimeout(queueAgeTimer);
  const s = queueSample;
  const age = s?.age_seconds == null ? null : s.age_seconds + Math.max(0, Date.now() - queueReceivedAt) / 1000;
  const stale = age !== null && age >= s.stale_after_seconds;
  $('queue-counts').textContent = s?.counts ? 'Disponibles ≈ ' + s.counts.available + ' · En vol ≈ ' + s.counts.in_flight + ' · Différés ≈ ' + s.counts.delayed : 'Compteurs inconnus : aucun échantillon disponible.';
  $('queue-status').textContent = (queueFailed || s?.quality === 'unavailable' ? 'Lecture indisponible. ' : 'Compteurs approximatifs. ') +
    (stale ? 'Échantillon périmé. ' : age === null ? 'Fraîcheur inconnue. ' : 'Échantillon récent (cohérence éventuelle). ') +
    (Date.now() >= queueDeadline || queuePolls >= 9 ? 'Suivi automatique terminé; relecture manuelle possible.' : 'Lecture au plus toutes les 15 s, pendant 120 s.');
  $('queue-provenance').textContent = s ? 'Source : ' + s.source + ' · environnement : ' + s.environment + ' / ' + s.runtime +
    ' · qualité : ' + (queueFailed ? 'unavailable' : s.quality) + ' · échantillon : ' + s.sample_quality +
    ' · lecture des attributs : ' + (s.observed_at || 'inconnue') + ' · dernière tentative serveur : ' + s.attempted_at +
    ' · réponse serveur : ' + s.fetched_at + ' · âge à réception : ' + (s.age_seconds == null ? 'inconnu' : Math.round(s.age_seconds)) + ' s' : 'Source attendue : SQS.GetQueueAttributes · environnement local · qualité indisponible.';
  $('queue-proof').textContent = s ? pretty(s) : 'Indisponible';
  if (!document.hidden && age !== null && !stale) queueAgeTimer = setTimeout(renderQueue, Math.max(1, (s.stale_after_seconds - age) * 1000));
}
function scheduleQueue() {
  clearTimeout(queueTimer);
  if (document.hidden || queueBusy) return;
  if (Date.now() >= queueDeadline || queuePolls >= 9) { renderQueue(); return; }
  queueTimer = setTimeout(pollQueue, Math.max(1, queueNextRead - Date.now()));
}
async function pollQueue() {
  if (document.hidden || queueBusy || Date.now() >= queueDeadline || queuePolls >= 9) { renderQueue(); return; }
  if (Date.now() < queueNextRead) { scheduleQueue(); return; }
  queueBusy = true; queuePolls++;
  try { queueSample = await request('/queue-observation'); queueReceivedAt = Date.now(); queueFailed = false; }
  catch { queueFailed = true; }
  finally { queueBusy = false; queueNextRead = Date.now() + 15000; renderQueue(); scheduleQueue(); }
}
$('queue-refresh').addEventListener('click', () => {
  if (queueBusy) return;
  queueDeadline = Date.now() + 120000; queuePolls = 0; pollQueue();
});
document.addEventListener('visibilitychange', () => {
  clearTimeout(queueTimer); clearTimeout(queueAgeTimer);
  if (!document.hidden) { renderQueue(); scheduleQueue(); }
});
pollQueue();
