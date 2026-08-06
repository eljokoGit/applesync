# AppleSync

[![CI](https://github.com/eljokoGit/applesync/actions/workflows/ci.yml/badge.svg)](https://github.com/eljokoGit/applesync/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/eljokoGit/applesync)](https://github.com/eljokoGit/applesync/releases/latest)
[![Licence MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)

Sauvegarde **vérifiable** des photos et vidéos d'un iPhone vers un dossier
local, sur Windows. Sans conversion : HEIC, HEVC et MOV sont copiés tels
quels. Sans MTP : tout passe par le protocole de synchronisation d'Apple
(usbmuxd + AFC), le même que celui d'iTunes.

L'objectif du projet : pouvoir supprimer les originaux du téléphone en
confiance. La vérifiabilité prime sur tout, et un échec bruyant vaut mieux
qu'un succès douteux.

## Pourquoi pas MTP

L'accès à un iPhone via l'Explorateur Windows (MTP) **tronque
silencieusement** : sur la bibliothèque de test du projet, trois énumérations
successives du même dossier DCIM ont renvoyé 164, puis 124, puis 185 dossiers
sans lever la moindre erreur, et un inventaire « complet » a rapporté 56 Go
au lieu des 109 Go réels. Inacceptable pour une sauvegarde de référence.
AFC, lui, expose la bibliothèque avec des métadonnées fiables et une lecture
positionnable — ce qu'il faut pour un inventaire contrôlable et une reprise
à l'octet près.

## Ce que fait l'application

- **Inventaire d'abord.** Le contenu de l'appareil est énuméré **deux fois**
  et les deux passes sont comparées. La moindre divergence interrompt tout,
  avec la liste nominative des fichiers en écart. Aucune copie ne démarre sur
  un inventaire douteux.
- **Couverture complète de la bibliothèque.** `/DCIM`, mais aussi les zones
  hors DCIM où iOS range certaines photos : originaux gérés par iCloud
  (`CPLAssets`) et albums partagés (`PhotoCloudSharingData`, rangés à part).
  Un rapport recense les zones et signale toute zone non couverte.
- **Copie incrémentale et idempotente.** L'identité d'un fichier est
  (chemin, taille, date de modification) — pas seulement son nom. Les
  fichiers déjà sauvegardés ne repartent jamais.
- **Reprise à l'octet près.** L'iPhone coupe la session quand l'écran se
  verrouille : la copie reprend exactement où elle s'est arrêtée. Un fichier
  n'apparaît sous son nom définitif que complet, contrôlé et haché — jamais
  de fichier partiel déguisé en fichier valide.
- **Vérification par relecture.** Après copie, chaque fichier est **relu
  depuis le disque** et son empreinte SHA-256 comparée à celle calculée au
  transfert. La sortie est une liste de noms, pas un pourcentage.
- **Jamais de suppression.** Aucune opération d'écriture vers l'iPhone
  n'existe dans le code (c'est structurel, pas une option). Côté PC, rien
  n'est écrasé ni supprimé : les fichiers disparus du téléphone sont
  conservés et signalés au rapport.
- **Journal et rapport** par exécution, permettant de reconstituer ce qui
  s'est passé.

Fonctions annexes : détection des doublons par contenu, récupération des
albums et favoris, test de stabilité de l'inventaire.

## Prérequis

- Windows 10 ou 11, **Python 3.12+**
- Le pilote Apple à l'écoute sur `127.0.0.1:27015`, fourni par **iTunes**
  (Apple Mobile Device Support), l'app **Apple Devices**, ou les pilotes
  CopyTrans. Vérification :
  `Test-NetConnection 127.0.0.1 -Port 27015`
- Un câble USB de données (beaucoup de câbles ne font que la charge).

## Installation

**Sans Python** — télécharger `AppleSync.exe` depuis la
[dernière release](https://github.com/eljokoGit/applesync/releases/latest)
et double-cliquer.

**Avec Python 3.12+** :

```
git clone https://github.com/eljokoGit/applesync.git
cd applesync
python -m venv .venv
.venv\Scripts\python -m pip install .
```

Lancement :

```
.venv\Scripts\applesync
```

Sur Windows, `run-windows.bat` fait ces étapes d'un double-clic (création de
l'environnement au premier lancement, puis démarrage).

## Mises à jour

Au démarrage, l'application regarde s'il existe une version plus récente et
l'affiche dans un bandeau, avec un lien vers les notes de version. **Elle ne
télécharge et n'installe rien elle-même** : pour un outil de sauvegarde, une
mise à jour silencieuse serait exactement ce qu'on ne veut pas. Une seule
requête anonyme vers l'API GitHub, aucune donnée envoyée ; pour la
désactiver, mettre `"check_updates": false` dans
`%LOCALAPPDATA%\AppleSync\config.json`.

Mettre à jour :

- **exécutable** : télécharger le nouveau `AppleSync.exe` et remplacer
  l'ancien ;
- **installation Python** : `git pull` puis
  `.venv\Scripts\python -m pip install .`

Vos sauvegardes ne sont jamais touchées par une mise à jour : le manifeste
et l'historique vivent dans le dossier de destination, pas dans le logiciel.

## Utilisation

1. Brancher l'iPhone, le déverrouiller, accepter « Se fier à cet
   ordinateur ». La bannière d'état passe au vert.
2. Choisir le dossier de destination et l'**organisation** — décision à
   prendre avant la première synchronisation, figée ensuite pour cette
   destination :
   - *Miroir* : l'arborescence de l'appareil telle quelle ;
   - *Par date* : `AAAA/AAAA-MM/`, noms d'origine, option « captures
     d'écran à part » ;
   - *Archive* : `AAAA/AAAA-MM/AAAA-MM-JJ HH-MM-SS.ext` (renommage par date
     de prise de vue lue dans l'EXIF, date de fichier en repli), composantes
     vidéo des Live Photos dans `_LivePhotos/`, doublons de contenu rangés
     dans `_Doublons/`.
3. **Inventorier** : double énumération, delta présenté pour validation.
   Rien n'est écrit à cette étape.
4. **Synchroniser** : copie, puis vérification profonde automatique et
   rapport final.

Les autres boutons : **Vérifier la destination** (re-contrôle intégral à tout
moment), **Doublons** (groupes de fichiers au contenu identique — rapport
seulement, aucune suppression), **Albums** (reconstruit les albums et favoris
de l'iPhone sous forme de dossiers de copies), **Test de stabilité** (trois
inventaires avec débranchement entre chacun, pour prouver que l'énumération
est reproductible).

Interrompre est toujours sûr : bouton d'arrêt, verrouillage de l'écran ou
câble débranché mènent au même résultat — ce qui est copié est acquis, le
fichier en cours reprend à l'octet près au lancement suivant.

## Contenu du dossier de sauvegarde

```
<destination>/
  2024/2024-08/…                 photos et vidéos (selon l'organisation choisie)
  _LivePhotos/                   composantes vidéo des Live Photos
  _Doublons/                     exemplaires en double (contenu identique)
  _AlbumsPartages/               éléments des albums partagés iCloud
  _Albums/                       albums et favoris reconstruits (option)
  .applesync/
    manifest.sqlite3             ce qui a été copié : identité + SHA-256
    logs/run_*.jsonl             journal détaillé de chaque exécution
    rapports/                    rapports et ventilations CSV
```

Le manifeste vit dans la destination : la sauvegarde est autoportante et se
déplace d'un disque à l'autre sans rien perdre.

## Mode simulation (sans iPhone)

```
.venv\Scripts\applesync --simulate
.venv\Scripts\applesync --simulate --sim-fault truncate
```

Un appareil factice permet de prendre l'outil en main. `--sim-fault` injecte
les pannes réelles (énumération tronquée sans erreur, déconnexion, appareil
verrouillé) pour voir l'application refuser un inventaire douteux.

## Contribuer

Les bugs et les propositions passent par les
[tickets](https://github.com/eljokoGit/applesync/issues) ; les modalités,
les règles du projet et la procédure de publication sont dans
[CONTRIBUTING.md](CONTRIBUTING.md). Pour une faille de sécurité, voir
[SECURITY.md](SECURITY.md) — jamais de ticket public.

## Développement

```
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest tests -q
```

La suite de tests (110 tests) s'exécute intégralement sur un simulateur
d'appareil déterministe, sans matériel : arbre de fichiers reproductible,
contenus générés à la volée, et injection des pannes du terrain
(troncature silencieuse d'énumération, déconnexion en cours de route,
lecture échouant à mi-fichier, appareil verrouillé).

Le code est séparé en trois couches : `applesync/device/` (accès appareil —
un contrat abstrait, une implémentation AFC réelle, un simulateur),
`applesync/core/` (inventaire, plan, copie, vérification, rapports) et
`applesync/ui/` (interface PySide6, aucun accès appareil dans le fil
graphique).

## Limites connues

- **Windows uniquement** en pratique (chemins et lanceur) ; le cœur est du
  Python portable, mais rien n'est testé ailleurs.
- **La dépendance `pymobiledevice3` est figée à 10.3.1** : le positionnement
  dans un fichier (nécessaire à la reprise) utilise une API interne, l'API
  publique ne l'exposant pas. Une montée de version doit être re-validée.
- **Albums** : le schéma de la base Photos d'iOS n'est pas documenté et
  évolue. Le parsing procède par introspection et échoue bruyamment plutôt
  que de rendre un résultat partiel. Selon la version d'iOS, la base peut
  aussi être inaccessible — `applesync --probe-albums` le vérifie.
- **Aucun transfert PC → iPhone.** Ajouter des photos à la bibliothèque ou
  supprimer sur l'appareil n'est pas possible depuis un PC par cette voie ;
  utilisez la synchronisation photos d'iTunes / Apple Devices.
- Le renommage daté utilise l'heure locale du PC ; les photos prises dans un
  autre fuseau portent l'heure du fuseau local.

## Licence

MIT — voir [LICENSE](LICENSE).
