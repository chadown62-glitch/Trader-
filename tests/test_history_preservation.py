"""An unreadable user cache must never be silently replaced by fresh history."""

import pytest

from sentinelle.market.history import HistoryStore
from tests.test_history import CountingSource, HOUR


@pytest.mark.parametrize("payload", [b"not a gzip archive", b""])
def test_unreadable_cache_stops_update_without_fetch_or_overwrite(tmp_path, payload):
    source = CountingSource(100 * HOUR, 90 * HOUR)
    store = HistoryStore(tmp_path, source)
    path = store.path("BTC/EUR", "1h")
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)

    with pytest.raises(ValueError, match="Historique illisible"):
        store.update("BTC/EUR", "1h", days=1)

    assert path.read_bytes() == payload
    assert source.calls == []


def test_readable_gzip_with_missing_columns_is_preserved(tmp_path):
    import pandas as pd

    source = CountingSource(100 * HOUR, 90 * HOUR)
    store = HistoryStore(tmp_path, source)
    path = store.path("BTC/EUR", "1h")
    path.parent.mkdir(parents=True)
    pd.DataFrame({"ts": [90 * HOUR], "close": [100.]}).to_csv(path, index=False, compression="gzip")
    original = path.read_bytes()

    with pytest.raises(ValueError, match="Historique illisible"):
        store.update("BTC/EUR", "1h", days=1)

    assert path.read_bytes() == original
    assert source.calls == []
