"""Détection de doublons par CONTENU (SHA-256), pas par nom.

Le manifeste stocke le SHA-256 de chaque fichier copié ou adopté : deux
entrées de même hachage (et même taille, double contrôle) sont des doublons
de contenu, quel que soit leur nom ou leur dossier.

Sortie : un rapport nominatif par groupes — l'application ne supprime JAMAIS
rien d'elle-même, ni sur l'iPhone (impossible par construction) ni dans la
destination. Le ménage éventuel appartient à l'utilisateur, la liste en main.

Disponible seulement après synchronisation (les hachages naissent à la
copie) ; aucun iPhone requis.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from applesync.core.manifest import Manifest, ManifestEntry


@dataclass(frozen=True)
class DuplicateGroup:
    sha256: str
    size: int
    entries: tuple[ManifestEntry, ...]     # ≥ 2, triées par date de synchro

    @property
    def wasted_bytes(self) -> int:
        """Octets récupérables si on ne gardait qu'un exemplaire."""
        return self.size * (len(self.entries) - 1)


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup] = field(default_factory=list)
    scanned_count: int = 0

    @property
    def duplicate_count(self) -> int:
        return sum(len(g.entries) - 1 for g in self.groups)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)

    def to_markdown(self) -> str:
        from applesync.core.report import fmt_bytes

        lines = ["# Doublons de contenu (SHA-256 identiques)", ""]
        lines.append(f"- Fichiers examinés (au manifeste) : {self.scanned_count}")
        if not self.groups:
            lines.append("- **Aucun doublon de contenu.**")
            return "\n".join(lines)
        lines.append(f"- Groupes de doublons : {len(self.groups)}")
        lines.append(
            f"- Exemplaires excédentaires : {self.duplicate_count} "
            f"({fmt_bytes(self.wasted_bytes)} récupérables)"
        )
        lines.append("")
        lines.append(
            "L'application ne supprime rien : liste fournie pour décision "
            "manuelle. Chemins relatifs à la destination."
        )
        lines.append("")
        for g in self.groups:
            lines.append(
                f"## {fmt_bytes(g.size)} × {len(g.entries)} — `{g.sha256[:16]}…`"
            )
            for e in g.entries:
                quand = time.strftime("%Y-%m-%d", time.localtime(e.synced_at))
                lines.append(
                    f"- `{e.local_path}` (source iPhone : `{e.source_path}`, "
                    f"synchronisé le {quand})"
                )
            lines.append("")
        return "\n".join(lines)


def find_duplicates(manifest: Manifest) -> DuplicateReport:
    """Groupes d'entrées du manifeste partageant (sha256, taille)."""
    by_hash: dict[tuple[str, int], list[ManifestEntry]] = defaultdict(list)
    entries = manifest.all_entries()
    for e in entries:
        by_hash[(e.sha256, e.size)].append(e)

    groups = [
        DuplicateGroup(
            sha256=key[0],
            size=key[1],
            entries=tuple(sorted(v, key=lambda e: (e.synced_at, e.local_path))),
        )
        for key, v in by_hash.items()
        if len(v) > 1
    ]
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return DuplicateReport(groups=groups, scanned_count=len(entries))
