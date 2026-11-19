"""ALPHA FACTORY — commande research et lecture seule des archives publiques."""
import gzip
import hashlib
import json

import numpy as np

from sentinelle.alpha.bars import align, load_history_csv
from sentinelle.alpha.markets import synthetic_universe
from tools import alpha_factory


def _write_history(path, bars, *, extra_rows=()):
    lines = ["ts,open,high,low,close,volume"]
    for i in range(len(bars)):
        lines.append(f"{bars.ts[i]},{bars.open[i]},{bars.high[i]},{bars.low[i]},{bars.close[i]},{bars.volume[i]}")
    lines += list(extra_rows)
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode()))


def test_catalogue_command_lists_levels(capsys):
    assert alpha_factory.main(["catalogue"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["version"] == "1.0.0" and out["sources"]["marketing-clip-trader"]["level"] == "claimed_result"


def test_public_history_is_read_only_hashed_and_cleaned(tmp_path):
    u = synthetic_universe(3, n_bars=300)
    b = u.bars["AAA/EUR"]
    path = tmp_path / "AAA-EUR_4h.csv.gz"
    bad = [f"{b.ts[10]},{b.open[10]},{b.high[10]},{b.low[10]},{b.close[10]},1",  # doublon
           f"{b.ts[-1] + 14_400_000},10,9,11,10,1"]  # bougie incohérente (plus haut < plus bas)
    _write_history(path, b, extra_rows=bad)
    before = path.read_bytes()
    loaded, digest = load_history_csv(path, "AAA/EUR")
    assert path.read_bytes() == before and digest == hashlib.sha256(before).hexdigest()
    assert len(loaded) == len(b) and np.allclose(loaded.close, b.close)
    assert loaded.spread_bps is None and digest in loaded.provenance


def test_alignment_never_invents_missing_bars(tmp_path):
    u = synthetic_universe(4, n_bars=200)
    a, c = u.bars["AAA/EUR"], u.bars["BBB/EUR"]
    _write_history(tmp_path / "A.csv.gz", a)
    _write_history(tmp_path / "C.csv.gz", c.prefix(150))
    la, _ = load_history_csv(tmp_path / "A.csv.gz", "AAA/EUR")
    lc, _ = load_history_csv(tmp_path / "C.csv.gz", "BBB/EUR")
    uni = align([la, lc])
    assert uni.length == 150 and np.array_equal(uni.bars["AAA/EUR"].ts, uni.bars["BBB/EUR"].ts)


def test_official_configuration_uses_seeds_never_run_during_development():
    assert set(alpha_factory.OFFICIAL.seeds).isdisjoint({1, 2, 3, 4, 5, 7, 9, 11, 21, 41, 42, 43, 101, 202})
    assert ("noise", 0.0) in alpha_factory.OFFICIAL.markets  # témoin négatif
    assert any(k == "hidden_rule" for k, _ in alpha_factory.OFFICIAL.markets)  # témoin positif


def test_ledger_names_cannot_collide_between_market_strengths():
    import inspect

    src = inspect.getsource(alpha_factory.run)
    assert "{m['kind']}-{m['edge']}-{m['seed']}" in src  # deux intensités d'un même marché : deux registres
