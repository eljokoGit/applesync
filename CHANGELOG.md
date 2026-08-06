# Journal des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Ce projet suit le [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

## [1.0.0] — 2026-08-05

Première version publique.

### Ajouté

- Inventaire à double énumération : le contenu de l'appareil est parcouru
  deux fois et les passes comparées ; toute divergence interrompt le
  traitement avec la liste nominative des écarts.
- Couverture complète de la bibliothèque : `/DCIM`, zone iCloud
  (`PhotoData/CPLAssets`) et albums partagés
  (`PhotoData/PhotoCloudSharingData`, rangés dans `_AlbumsPartages/`).
- Copie incrémentale et idempotente, identité fondée sur
  (chemin, taille, date de modification).
- Reprise à l'octet près après verrouillage de l'écran ou débranchement ;
  aucun fichier partiel ne peut porter un nom définitif.
- Vérification par relecture intégrale du disque et comparaison SHA-256,
  avec liste nominative des écarts.
- Trois organisations de destination, figées par destination : miroir,
  par date, archive (renommage horodaté d'après l'EXIF, `_LivePhotos/`,
  `_Doublons/`).
- Récupération des albums et favoris depuis la base Photos de l'appareil,
  avec analyse défensive du schéma.
- Détection des doublons par contenu (rapport, aucune suppression).
- Test de stabilité : trois inventaires successifs comparés.
- Journal JSONL et rapport Markdown par exécution, ventilation CSV
  mois × extension par inventaire.
- Simulateur d'appareil déterministe avec injection de pannes
  (troncature silencieuse, déconnexion, lecture interrompue, verrouillage) —
  110 tests exécutables sans matériel.
- Vérification de mise à jour au démarrage, en consultation seule et
  désactivable.

### Sécurité

- Aucune opération d'écriture vers l'appareil n'existe dans le code : la
  suppression ou la modification de données sur l'iPhone est impossible par
  construction, pas par choix d'exécution.

[Non publié]: https://github.com/eljokoGit/applesync/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/eljokoGit/applesync/releases/tag/v1.0.0
