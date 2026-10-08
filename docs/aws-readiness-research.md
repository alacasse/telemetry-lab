# Contraintes AWS pour la démonstration

Recherche documentaire du **2026-10-08**, fondée sur les sources officielles AWS et le code local. Aucun compte AWS interrogé, aucune ressource créée et aucune exécution cloud attestée. Cette note propose des options; elle ne constitue pas une décision de déploiement.

## État de préparation

| Objectif | État observé | Ce qui reste à établir |
| --- | --- | --- |
| Démonstration locale | Parcours et validations documentés | Les résultats consignés ne sont pas une nouvelle exécution des tests |
| Backend de télémétrie sur AWS | Terraform, quatre bundles Lambda, migrations et smoke tests présents | Corriger IAM et le parcours de livraison, vérifier le compte/région, puis prouver le traitement réel |
| Thermostat utilisable par une URL HTTPS | Interface et comportement existants en local | Héberger l'interface, exposer ses routes, adapter le runtime et les commandes à AWS |
| Utilisateurs indépendants | Une pièce globale et conflits de révision gérés | Identité, propriété des pièces et isolation des données absentes du parcours actuel |

Sources locales : [validation](validation.md), [préparation AWS](aws-preparation.md), [composition HTTP](../demo/app.py), [routes du thermostat](../demo/thermal_routes.py), [runtime](../demo/thermal_runtime.py), [transport des commandes](../demo/thermal_queue.py), [livraison](../scripts/deploy-staging.sh).

Pour un premier jalon, une pièce partagée à accès contrôlé limite les changements de modèle. Conserver le backend Lambda/SQS/RDS et lui ajouter le parcours navigateur et un seul simulateur supervisé permettrait de démontrer les services AWS réels. C'est une proposition à comparer avec un hébergement sur une seule machine; son coût et son exploitation ne sont pas encore chiffrés.

## Runtime thermique

Une invocation Lambda standard est limitée à **900 secondes**. Après la fin du runtime et des extensions, Lambda peut geler l'environnement; sa réutilisation ne garantit pas la continuité d'un traitement. La documentation distingue désormais des variantes Managed Instances et Durable Functions : elles ne sont pas configurées dans ce dépôt. [Timeout Lambda](https://docs.aws.amazon.com/lambda/latest/dg/configuration-timeout.html), [Cycle de vie](https://docs.aws.amazon.com/lambda/latest/dg/lambda-runtime-environment.html).

**Constat local :** `demo/thermal_runtime.py` entretient plusieurs boucles de threads, dont un tick toutes les 0,05 seconde. Terraform ne prévoit que quatre fonctions standard, avec des délais de 15, 15, 60 et 120 secondes; aucun runtime thermique cloud n'y est déclaré. **Déduction :** transposer ces threads derrière un handler qui retourne ne fournirait pas une horloge continue fiable.

**Option à décider :** un service ECS sur Fargate pour le processus continu. Le scheduler ECS maintient le nombre demandé de tâches et remplace les tâches arrêtées ou défaillantes; AWS le recommande pour les services et applications de longue durée. Il peut utiliser Fargate. Cela exige encore la définition du conteneur, du réseau, des contrôles de santé, de la reprise persistante et de l'exclusion de deux horloges simultanées pendant un remplacement. Ces éléments applicatifs sont des travaux proposés, pas des garanties ECS. [Services ECS](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ecs_services.html).

## Concurrence Lambda et SQS

**Blocage IAM constaté :** `infrastructure/staging/modules/iam/main.tf` donne au worker les accès au secret DB/KMS et les policies de logs/VPC/tracing, mais aucune permission SQS de consommation. Or le mapping existant nécessite `sqs:ReceiveMessage`, `sqs:DeleteMessage` et `sqs:GetQueueAttributes` dans le rôle d'exécution. Prévoir une policy limitée à la file cible avant tout plan de déploiement. [Configuration Lambda/SQS](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html), [Policy AWS de référence](https://docs.aws.amazon.com/aws-managed-policy/latest/reference/AWSLambdaSQSQueueExecutionRole.html).

AWS impose de conserver **100 unités non réservées** : la capacité réservable est la concurrence non réservée du compte moins 100. Les réservations plafonnent aussi les fonctions. **Constat local :** Terraform réserve 2 + 1 + 1 + 1 = **5** unités. Leur admissibilité dépend du quota et des réservations existantes du compte/région, inconnus ici; un quota total de 100 ne permettrait pas ces cinq réservations. [Concurrence réservée](https://docs.aws.amazon.com/lambda/latest/dg/configuration-concurrency.html).

Pour une file standard en mode SQS normal, Lambda commence avec cinq invocations simultanées et peut croître jusqu'à 1 250 par mapping, sous réserve des autres limites. Le paramètre de concurrence maximale SQS est indépendant de la réservation Lambda et accepte **2 à 1 000**; il ne doit pas dépasser la réservation disponible pour ses mappings. **Constat local :** le worker réserve 1, son mapping a un batch de 5 et aucun plafond SQS. Cette configuration mérite une révision pour éviter le throttling; ajouter un plafond SQS de 1 ne serait pas une correction valide. Le batch représente des messages par invocation, pas cinq unités de concurrence. [Scaling SQS/Lambda](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-scaling.html).

## API navigateur et identité

Une HTTP API appelée depuis une autre origine peut gérer CORS et répondre automatiquement aux préflights `OPTIONS`; sa configuration CORS remplace les en-têtes CORS du backend. **Constat local :** le module API Gateway ne configure ni CORS ni authorizer JWT et ne publie aucune route `/thermal-simulations`. CORS seul ne définit pas l'identité d'un utilisateur. [CORS HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-cors.html).

**Option à décider :** un authorizer JWT, un fournisseur d'identité et des permissions de routes. API Gateway vérifie notamment signature, issuer, audience et dates; des scopes de routes permettent de limiter les jetons acceptés. AWS recommande des scopes pour distinguer les usages des jetons d'accès et des ID tokens. L'adaptation des contrats d'authentification applicatifs reste à concevoir. [JWT HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html).

## Fichiers statiques

**Option à décider :** CloudFront avec origine bucket S3 privée et OAC. OAC exige une origine S3 régulière : un endpoint S3 de site web est une origine personnalisée et ne supporte ni OAC ni OAI. La policy du bucket peut autoriser le principal CloudFront uniquement pour la distribution concernée; la signature `always` assure HTTPS entre CloudFront et S3. OAC protège l'origine, et ne constitue pas à lui seul une authentification des visiteurs. [Origine S3 et OAC](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html).

**Constat local :** les buckets bootstrap servent à l'état Terraform et aux bundles Lambda. Ils ne définissent pas un hébergement de `demo/static/`; aucune distribution CloudFront n'est déclarée. Les routes API, l'origine du navigateur et son identité doivent être décidées ensemble avant de préparer un plan AWS.

## Limites de préparation du dépôt

Ces observations viennent de la lecture des sources, sans exécution AWS :

- `demo/app.py` et `demo/thermal_runtime.py` refusent explicitement staging. La présence des bundles Lambda et des routes de télémétrie ne fournit pas la démonstration navigateur ni les routes thermostat.
- Le runtime local, sa file de commandes et son observation par fichier/PID nécessitent une adaptation cloud. Le modèle actuel possède une pièce courante partagée; l'isolation par utilisateur n'est pas acquise. Une démonstration contrôlée d'une seule pièce est une proposition de périmètre à valider.
- `scripts/deploy-staging.sh` calcule sa révision indépendamment du manifest des bundles, avec repli `local`, puis appelle `terraform apply -auto-approve` sans plan sauvegardé et revu. Il exporte les sorties; migrations et smoke tests restent des étapes séparées. Ce script doit être durci avant une utilisation autorisée.
- La version PostgreSQL RDS épinglée et les quotas Lambda doivent être vérifiés pour le compte et la région choisis. Les workflows GitHub existants ne constituent pas une intégration de déploiement AWS.

## Critères d'un premier essai utilisateur sur AWS

- Une URL HTTPS ouvre la page sans installation locale et applique le contrôle d'accès choisi.
- Un réglage traverse les services AWS réels; le navigateur affiche un relevé traité et les preuves de commande correspondantes.
- Le rechargement conserve l'état; l'arrêt et le remplacement du simulateur rendent l'interruption visible et respectent la reprise explicite.
- Deux navigateurs ont le comportement annoncé : pièce partagée avec gestion des conflits, ou pièces isolées si ce périmètre est retenu.
- Le déploiement utilise une révision identifiable, applique les migrations et vérifie le fonctionnement; restauration et suppression sont décrites.

Le coût doit inclure les composants persistants, pas seulement les appels Lambda : RDS, endpoints privés, hébergement du simulateur, stockage et observabilité. Les destinataires de budget sont vides par défaut dans [les variables staging](../infrastructure/staging/variables.tf); les seuils déclarés ne sont pas un devis ni un plafond de dépenses.

L'étape suivante est une décision d'architecture et de périmètre, puis les corrections de préparation et une validation locale appropriée. Le compte, les quotas et les versions régionales restent à vérifier. La création de ressources payantes n'est pas autorisée par cette demande d'évaluation; aucun résultat de cette note ne constitue une preuve de déploiement.
