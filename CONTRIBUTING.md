# Contribuer à AppleSync

Merci de l'intérêt porté au projet. Les contributions sont bienvenues :
signalements de bugs, propositions, corrections, traductions.

## Signaler un bug

Ouvrez un [ticket](https://github.com/eljokoGit/applesync/issues) en
utilisant le modèle « Rapport de bug ». Les éléments les plus utiles :

- la version d'AppleSync (affichée dans la barre d'état), de Windows, d'iOS
  et le modèle d'iPhone ;
- ce que vous attendiez, ce qui s'est produit ;
- le message d'erreur complet (bouton « Détails » de la fenêtre d'erreur) ;
- si possible un extrait du journal, dans
  `<destination>/.applesync/logs/run_*.jsonl`.

**Ne joignez jamais de photo ni le fichier `manifest.sqlite3`** : le journal
et les rapports contiennent des noms de fichiers, ce qui suffit presque
toujours au diagnostic.

## Environnement de développement

```
git clone https://github.com/eljokoGit/applesync.git
cd applesync
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest tests -q
```

Aucun iPhone n'est nécessaire : toute la logique se teste sur le simulateur
d'appareil (`applesync/device/simulator.py`), qui produit un arbre de
fichiers déterministe et sait injecter les pannes du terrain.

Parcours complet de l'interface, sans matériel :

```
.venv\Scripts\python scripts\ui_smoke.py <dossier_de_captures>
```

## Règles du projet

Ces règles ne sont pas négociables — elles définissent ce qu'est ce logiciel.

1. **Aucune écriture vers l'appareil.** Le contrat `DeviceSession`
   (`applesync/device/base.py`) ne comporte aucune méthode d'écriture ou de
   suppression. Une contribution qui en ajouterait une serait refusée.
2. **Aucune donnée n'est écrasée ni supprimée dans la destination.** Les
   conflits sont résolus par un nom versionné (`.~2`), jamais par
   remplacement.
3. **Échec bruyant.** Un résultat partiel ne doit jamais passer pour
   complet : on lève une exception explicite plutôt que de rendre un objet
   incomplet, et les écarts sont listés par nom, pas résumés en pourcentage.
4. **Toute nouveauté est testée sur le simulateur**, y compris son
   comportement en cas de panne.
5. **Toute opération longue montre sa progression** (barre animée dès le
   démarrage d'une phase, pourcentage dès qu'un compteur existe).

## Style

- Python 3.12, bibliothèque standard privilégiée.
- Code, commentaires, messages d'interface et documentation en français ;
  les identifiants restent en anglais quand c'est l'usage.
- Les commentaires expliquent le *pourquoi*, pas le *comment*.

## Pull requests

- Une intention par pull request.
- `pytest` doit passer intégralement ; l'intégration continue le vérifie sur
  Windows et Linux.
- Ajoutez une entrée dans `CHANGELOG.md` sous « Non publié ».

## Publier une version (mainteneurs)

1. Mettre à jour `__version__` dans `applesync/__init__.py` et la section
   correspondante de `CHANGELOG.md`.
2. Committer, puis étiqueter : `git tag v1.2.0 && git push --tags`.
3. Le workflow de publication vérifie que l'étiquette correspond à la
   version du code, construit les paquets et crée la release GitHub.
