"""Exercise text persistence with a cp1252 default, without claiming a Windows run."""
import io
import json

from sentinelle.alpha.velocity import ChallengeConfig, preregister


def test_preregistration_unicode_roundtrip_with_cp1252_default(tmp_path, monkeypatch):
    original = io.open

    def cp1252_open(file, mode='r', buffering=-1, encoding=None, errors=None,
                    newline=None, closefd=True, opener=None):
        if 'b' not in mode and encoding is None:
            encoding = 'cp1252'
        return original(file, mode, buffering, encoding, errors, newline, closefd, opener)

    monkeypatch.setattr(io, 'open', cp1252_open)
    destination = tmp_path / 'données-α'
    names = ['α-stratégie', '策略']
    cfg = ChallengeConfig()
    preregister(cfg, destination, names)
    raw = (destination / 'preregistration.json').read_bytes()
    assert json.loads(raw.decode('utf-8'))['learners'] == names
    # Read the existing UTF-8 document using the real preregistration path too.
    assert preregister(cfg, destination, names)['learners'] == names
