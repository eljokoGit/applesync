"""AppleSync — synchronisation iPhone → PC via le protocole Apple (usbmuxd/AFC).

Sauvegarde de référence : la vérifiabilité prime sur tout.
Un échec bruyant vaut mieux qu'un succès douteux.
"""

# Source unique de la version : pyproject.toml la lit ici (version dynamique),
# et le workflow de release vérifie qu'elle correspond à l'étiquette git.
__version__ = "1.0.0"
APP_NAME = "AppleSync"
