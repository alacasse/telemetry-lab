# Phase 2 — Contrat réalisé du moteur exclusif

Implémentation locale du **8 octobre 2026**. Ce document remplace la proposition de
phase 2. Les exécutions et leurs limites sont consignées dans [validation](validation.md).
Le périmètre adopté est une pièce commune consultable et modifiable par plusieurs
visiteurs, avec un seul moteur autorisé. AWS, les pièces individuelles et la
correction du stockage partagé entre onglets restent hors périmètre.

## Autorité persistante

La migration `0007_shared_room_authority` crée la ligne permanente `shared-room`
dans `thermal_authority`. Son identité ne dépend pas des simulations. Elle conserve
l'UUID du propriétaire, une génération croissante, `expires_at`, `renewed_at`,
`clock_passed_at`, le PID et la révision exécutée. La libération retire le propriétaire
et l'expiration sans supprimer la ligne ni réinitialiser la génération.

Le bail dure **10 secondes** et se renouvelle toutes les **2 secondes**. L'acquisition
incrémente la génération et exige une autorité libre ou expirée. Un bail expiré ne
peut pas être renouvelé. `PgAuthority` exige PostgreSQL; `RoomClock` et les fonctions
de publication exigent une autorité injectée. Les tests SQLite fournissent une
doublure explicite; ils ne prouvent pas l'exclusion réelle.

Chaque transaction protégée prend d'abord `SELECT … FOR UPDATE` sur cette ligne,
puis vérifie propriétaire, génération et expiration avec `clock_timestamp()` lu
après le verrou. Le verrou reste détenu jusqu'au commit ou rollback. Les protections
couvrent récupération, initialisation, tick, application de commandes, sélection
des publications, enregistrement des reçus et nettoyage final. Les écritures
légitimes de l'API et du worker restent indépendantes.

**Garantie : aucune mutation protégée de l'ancienne génération ne peut être
commitée après la prise de contrôle commitée du successeur.** Une transaction
admise avant expiration peut finir après celle-ci; elle bloque alors la prise de
contrôle jusqu'à sa fin. Le bail ne constitue pas une annulation rétroactive des
transactions admises. Cette garantie utilise les [verrous de ligne PostgreSQL](https://www.postgresql.org/docs/16/explicit-locking.html#LOCKING-ROWS).

## Démarrage, panne et arrêt

`initialize_runtime` réalise dans une même transaction : acquisition, verrou
consultatif de création existant, récupération des simulations, puis initialisation
optionnelle. L'ordre est autorité → verrou consultatif → simulations. Une erreur
annule toutes les écritures de cette transaction. Les boucles commencent après le
commit. Une autorité occupée entraîne un message explicite et le **code 75**, avant
récupération, initialisation ou consommation de commandes.

Le renouvellement utilise un pool distinct. Les pools locaux du moteur et de
l'observation ont une attente de connexion et un verrou SQL limités à **1 seconde**;
connexion, instruction et transaction inactive sont limitées à **2 secondes**.

Les transactions protégées d'une même instance passent désormais par une admission
locale commune, avant la création de session et jusqu'au commit ou rollback.
Le renouvellement utilise cette même admission, prioritaire lorsqu'il attend, et
la conserve jusqu'à sa confirmation locale. Une transaction sœur attend donc sans
consommer le pool ni demander un verrou PostgreSQL. Cette attente vérifie le délai
existant de cinq secondes; elle n'ajoute aucun budget et ne transforme aucune
erreur de base en reprise. Un client externe reste soumis aux délais SQL existants.
Les transactions de sélection à vide, la cadence d'horloge et les expirations
métier restent inchangées. L'[analyse et la comparaison](thermal-availability-2026-10-08.md)
distinguent cette correction du moteur de la mitigation SAM antérieure.

Une erreur de base, une autorité perdue ou **5 secondes sans renouvellement
confirmé** rend le processus définitivement inactif. Chaque admission et chaque
renouvellement vérifie aussi ce délai monotone. La confirmation du renouvellement
vérifie et met à jour sa date sous un même verrou local, avec une seule lecture du
temps : une longue suspension ne peut pas remettre ce délai à zéro. Reconnexion
et `SIGCONT` ne réactivent pas une instance déchue.

Les seuls échecs de transport conservent leurs reprises. Aucun appel réseau ne
garde le verrou d'autorité. La consommation vérifie l'autorité dans une transaction
avant de lire la file; les publications sélectionnent le travail sous autorité,
puis envoient hors transaction. L'effet ou le reçu est enregistré dans une nouvelle
transaction protégée. Les corps exacts et identités sont conservés, et l'acquittement
suit le commit. Un envoi déjà en vol peut rester ambigu : aucune garantie de
livraison réseau exactement une fois n'est revendiquée.

À l'arrêt normal, les boucles cessent d'admettre de nouveaux travaux. Les threads
partagent **4 secondes au total** pour se terminer. Récupération et libération ne
s'exécutent que si les threads sont arrêtés et l'autorité encore valide. Après une
erreur fatale, une perte d'autorité ou un thread restant vivant, aucun nettoyage
métier tardif n'est exécuté.

Un arrêt normal réveille et annule les admissions locales en attente sans les
qualifier de panne d'autorité. Les transactions déjà admises peuvent se terminer;
les boucles quittent ensuite sans engager une nouvelle transaction. Le nettoyage
final utilise son contrôle transactionnel direct seulement après leur arrêt.

La phase 3 centralise ce cycle dans `packages/thermal/process.py`, utilisé par le
lanceur local et le service autonome. La borne de quatre secondes inclut désormais
le nettoyage protégé et la fermeture des transports : un watchdog termine le
processus avec le code 1 si cette borne est atteinte. `SIGTERM` et `SIGINT`
partagent la même échéance, même lorsqu'ils sont répétés. Cette borne n'est ni une
nouvelle durée de bail ni une modification des expirations métier.

Le lanceur observe passivement PostgreSQL avant chaque redémarrage. Une autorité
occupée le fait attendre; une observation impossible ne permet aucun démarrage et
ne consomme aucune des trois tentatives. Un processus sortant avec 75 termine ces
tentatives automatiques. Après remplacement, la simulation est `interrupted`
jusqu'à une reprise explicite. Les nouvelles ancres ne rattrapent pas thermiquement
le temps d'arrêt.

## Observation et compatibilité HTTP

| État | Preuve PostgreSQL |
| --- | --- |
| `available` | Bail valide et passage réussi de l'horloge datant d'au plus 3 secondes |
| `unavailable` | Autorité absente, expirée ou progression absente/trop ancienne |
| `unknown` | Observation impossible |

Un passage réussi de l'horloge est enregistré même sans pièce active ou pendant
une interruption. L'observation fait une lecture sans verrou, via un pool séparé
dans la composition HTTP, y compris lorsqu'une route détient déjà une simulation.
Elle ne crée, ne récupère et ne reprend aucune simulation.

Les champs existants `status`, `process_id`, `release_revision` et `source` restent
présents; `source` vaut désormais `postgresql-authority`. S'ajoutent `owner_id`,
`generation`, `expires_at`, `renewed_at`, `clock_passed_at`, `pid`, `revision` et
`authority_free`. Ce dernier renseigne le lanceur : une progression ancienne peut
rendre le moteur indisponible sans rendre son bail libre. `runtime.json` reste un
diagnostic du lanceur et ne prouve plus la disponibilité HTTP.

## Pièce partagée et validation

Alice et Bob observent la même pièce. Deux réglages concurrents avec la même
révision donnent une acceptation et un conflit HTTP 409; après relecture, chacun
peut soumettre une nouvelle intention. Il n'existe pas de propriété de la pièce par
visiteur, ni d'attribution personnelle des réglages. Le problème connu de la clé
`localStorage` partagée entre onglets reste ouvert.

Les tests PostgreSQL utilisent deux processus indépendants pour les acquisitions,
le transfert et la suspension/réactivation. Ils vérifient les deux ordres
transactionnels, le rollback du démarrage, la perte de connexion, l'observation et
le refus du nettoyage tardif. Un contrôle HTTP avec deux clients conserve le verrou
d'autorité pendant les réglages pour prouver que leur observation ne le réclame pas.

Les sous-processus des scénarios de perte de reçu acquièrent chacun leur propre
génération. Les scénarios historiques gardent leur expiration de 30 secondes.
Les deux fenêtres de crash/redélivrance tournent dans une composition séparée
(`--test-crash-windows`), en complément de `--test`, afin de conserver le budget de
trois redémarrages par lanceur. Le sous-processus appliquant une commande libère son
bail avant de quitter sans acquitter; la perte de reçu de mesure utilise une vraie
sortie brutale avec attente d'expiration du bail.

La migration doit être appliquée **moteurs arrêtés**, avec redémarrage des anciens
superviseurs empêché. Les validations jetables appliquent les migrations avant de
lancer les moteurs. Les commandes de reproduction figurent dans le [runbook local](local-runbook.md).
Une réussite locale ne prouve ni un déploiement AWS, ni un remplacement ECS réel,
ni une capacité de charge multiusager.
