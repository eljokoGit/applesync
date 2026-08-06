"""Couche d'accès appareil.

`base` définit le contrat abstrait. Deux implémentations :
- `afc` : appareil réel via pymobiledevice3 (usbmuxd + lockdown + AFC)
- `simulator` : DCIM simulé avec injection de pannes, pour les tests
"""
