# Politique de sécurité

## Versions suivies

| Version | Suivie |
| ------- | ------ |
| 1.0.x   | ✅     |

## Signaler une vulnérabilité

N'ouvrez **pas** de ticket public pour une faille de sécurité.

Utilisez l'onglet **Security → Report a vulnerability** du dépôt
(GitHub Private Vulnerability Reporting). Décrivez le problème, la version
concernée et, si possible, la manière de le reproduire. Vous recevrez une
réponse dès que possible ; le correctif et la divulgation seront coordonnés
avec vous.

## Périmètre

Ce logiciel lit un appareil iOS branché en USB et écrit dans un dossier
local. Sont particulièrement d'intérêt :

- tout chemin de code qui écrirait vers l'appareil (il ne doit pas en
  exister : le contrat d'accès appareil n'expose aucune écriture) ;
- toute écriture hors du dossier de destination choisi, ou tout écrasement
  d'un fichier existant de la destination ;
- toute fuite de données personnelles hors de la machine (l'application
  n'émet qu'une requête, anonyme et désactivable, vers l'API GitHub pour la
  vérification de version) ;
- toute situation où un transfert incomplet ou corrompu serait rapporté
  comme réussi.

## Hors périmètre

- Les vulnérabilités des dépendances tierces : signalez-les en amont, et
  ouvrez ici un ticket normal pour la montée de version.
- L'accès physique à la machine ou à l'appareil déverrouillé.
