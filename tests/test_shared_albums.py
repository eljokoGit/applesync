"""Albums partagés iCloud (PhotoCloudSharingData) : couverts, rangés à part.

Ils contiennent des photos d'autres personnes : dans les organisations
datées ils vont dans _AlbumsPartages/ (structure par album conservée),
jamais mélangés à l'archive, et jamais « rangés en doublon »."""

import time

from applesync.core.engine import SyncEngine
from applesync.core.layout import ArchiveLayout, DateLayout, shared_target
from applesync.device.base import RemoteFile
from applesync.device.simulator import SimProfile, SimulatedBackend

T = int(time.mktime((2024, 8, 15, 14, 30, 22, 0, 0, -1)))


def test_cible_a_part_dans_les_organisations_datees():
    f = RemoteFile(path="PhotoCloudSharingData/GUID-42/100SHARE/IMG_1.JPG",
                   size=100, mtime=T)
    assert shared_target(f.path) == "_AlbumsPartages/GUID-42/100SHARE/IMG_1.JPG"
    assert DateLayout(False).target_for(f) == \
        "_AlbumsPartages/GUID-42/100SHARE/IMG_1.JPG"
    lay = ArchiveLayout()
    lay.begin([f])
    assert lay.target_for(f) == "_AlbumsPartages/GUID-42/100SHARE/IMG_1.JPG"
    # Les fichiers normaux, eux, ne sont pas concernés
    assert shared_target("100APPLE/IMG_1.JPG") is None


def test_partages_couverts_en_archive_sans_datation(dest):
    backend = SimulatedBackend(SimProfile.small())
    backend.add_file("PhotoCloudSharingData/GUID-1/IMG_S1.JPG", 3000, T)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok
    assert (dest / "_AlbumsPartages/GUID-1/IMG_S1.JPG").exists()

    prepared2 = engine.prepare(backend.INFO.udid)
    assert not prepared2.plan.to_copy          # idempotent


def test_partage_identique_a_la_phototheque_pas_range_en_doublon(dest):
    """La même photo présente dans la pellicule ET dans un album partagé :
    les deux copies restent à leur place, aucune ne part en _Doublons."""
    backend = SimulatedBackend(SimProfile.small())
    src = backend.tree[0]
    backend.clone_file(src.path, "PhotoCloudSharingData/GUID-1/CLONE.HEIC",
                       src.mtime + 10)

    engine = SyncEngine(backend, dest, ArchiveLayout())
    report = engine.execute(engine.prepare(backend.INFO.udid))
    assert report.status == "terminé"
    assert report.verification.ok
    assert not report.duplicates_routed
    assert (dest / "_AlbumsPartages/GUID-1/CLONE.HEIC").exists()
    assert not (dest / "_Doublons").exists()
