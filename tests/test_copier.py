"""Copie : octets exacts, reprise à l'octet près, jamais de partiel déguisé."""

import pytest

from applesync.core.copier import CopyCancelled, CopyError, copy_file
from applesync.core.journal import Journal
from applesync.device.base import DeviceDisconnectedError, FileReadError
from applesync.device.simulator import (
    FaultPlan,
    SimProfile,
    SimulatedBackend,
    content_sha256,
)


def _journal(dest):
    return Journal(dest, "test-run")


def test_copie_simple_octets_exacts(backend, dest):
    f = backend.tree[0]
    with backend.connect(backend.INFO.udid) as s:
        res = copy_file(s, f, dest, f.path, _journal(dest))
    target = dest / f.path
    assert target.exists()
    assert target.stat().st_size == f.size
    assert res.sha256 == content_sha256(backend.profile.seed, f.path, f.size)
    assert int(target.stat().st_mtime) == f.mtime
    assert res.resumed_from == 0
    # Aucun résidu
    assert not (dest / (f.path + ".part")).exists()
    assert not (dest / (f.path + ".part.meta.json")).exists()


def test_lecture_echoue_a_mi_fichier_laisse_un_part(dest):
    prof = SimProfile.small()
    tree_probe = SimulatedBackend(prof).tree
    f = tree_probe[2]
    faults = FaultPlan(fail_read_path=f.path, fail_read_at_byte=f.size // 2)
    backend = SimulatedBackend(prof, faults)

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(FileReadError):
            copy_file(s, f, dest, f.path, _journal(dest))

    # Le fichier final n'existe PAS ; le .part et son sidecar restent
    assert not (dest / f.path).exists()
    part = dest / (f.path + ".part")
    assert part.exists()
    assert part.stat().st_size == f.size // 2


def test_deconnexion_a_mi_fichier_puis_reprise_hash_correct(dest):
    """LE scénario écran-verrouillé : coupure à mi-fichier, reprise à l'octet
    près, et le SHA-256 final est celui du fichier complet."""
    prof = SimProfile.small()
    tree_probe = SimulatedBackend(prof).tree
    f = tree_probe[4]
    cut = f.size // 3
    faults = FaultPlan(
        fail_read_path=f.path, fail_read_at_byte=cut, fail_read_as_disconnect=True
    )
    backend = SimulatedBackend(prof, faults)

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(DeviceDisconnectedError):
            copy_file(s, f, dest, f.path, _journal(dest))
    part = dest / (f.path + ".part")
    assert part.exists() and part.stat().st_size == cut
    assert not (dest / f.path).exists()

    # Reconnexion sans panne : la copie reprend exactement à `cut`
    backend2 = SimulatedBackend(prof)  # même seed → mêmes octets
    with backend2.connect(backend2.INFO.udid) as s:
        res = copy_file(s, f, dest, f.path, _journal(dest))
    assert res.resumed_from == cut
    assert res.bytes_copied_this_run == f.size - cut
    assert res.sha256 == content_sha256(prof.seed, f.path, f.size)
    assert (dest / f.path).stat().st_size == f.size


def test_reprise_refusee_si_identite_source_changee(dest):
    """Un .part d'une ancienne version du fichier ne doit jamais être complété
    par les octets de la nouvelle : identité différente → repart de zéro."""
    prof = SimProfile.small()
    backend = SimulatedBackend(prof)
    f = backend.tree[6]
    cut = f.size // 2
    faults = FaultPlan(
        fail_read_path=f.path, fail_read_at_byte=cut, fail_read_as_disconnect=True
    )
    backend_panne = SimulatedBackend(prof, faults)
    with backend_panne.connect(backend_panne.INFO.udid) as s:
        with pytest.raises(DeviceDisconnectedError):
            copy_file(s, f, dest, f.path, _journal(dest))

    # Le fichier change sur l'iPhone (remplacé : autre taille, autre mtime)
    remplacant = backend.replace_file(f.path, f.size + 100, f.mtime + 60)
    with backend.connect(backend.INFO.udid) as s:
        res = copy_file(s, remplacant, dest, remplacant.path, _journal(dest))
    assert res.resumed_from == 0          # PAS de reprise sur l'ancien partiel
    assert res.sha256 == content_sha256(prof.seed, f.path, remplacant.size)


def test_annulation_propre_puis_reprise(dest):
    prof = SimProfile.small()
    backend = SimulatedBackend(prof)
    f = max(backend.tree, key=lambda x: x.size)  # le plus gros pour plusieurs blocs
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2   # laisse passer ~2 contrôles puis interrompt

    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(CopyCancelled):
            copy_file(s, f, dest, f.path, _journal(dest), cancel=cancel,
                      chunk_size=16_384)
    part = dest / (f.path + ".part")
    assert part.exists()
    done = part.stat().st_size
    assert 0 < done < f.size

    with backend.connect(backend.INFO.udid) as s:
        res = copy_file(s, f, dest, f.path, _journal(dest))
    assert res.resumed_from == done
    assert res.sha256 == content_sha256(prof.seed, f.path, f.size)


def test_cible_occupee_refusee(backend, dest):
    f = backend.tree[0]
    target = dest / f.path
    target.parent.mkdir(parents=True)
    target.write_bytes(b"occupant")
    with backend.connect(backend.INFO.udid) as s:
        with pytest.raises(CopyError):
            copy_file(s, f, dest, f.path, _journal(dest))
    assert target.read_bytes() == b"occupant"  # jamais écrasé
