"""Archives d'actualités : lecture des annonces de retrait, entrée causale après publication, rééchantillonnage par annonce."""

import httpx
import numpy as np
import pandas as pd
import pytest

from sentinelle.scalp import annonces, data


def test_only_spot_delisting_titles_are_kept_with_their_tokens():
    assert annonces.tokens_of("Binance Will Delist EPIC, QUICK on 2026-10-28") == (["EPIC", "QUICK"], "2026-10-28")
    assert annonces.tokens_of("Binance Will Delist BTCST, DREP and MDX on 2023-01-06") == (["BTCST", "DREP", "MDX"], "2023-01-06")
    assert annonces.tokens_of("Binance Futures Will Delist PTBUSDT and MITOUSDT USDⓈ-M Perpetual Contracts (2026-10-23)") is None
    assert annonces.tokens_of("Notice of Removal of Spot Trading Pairs - 2026-11-20") is None
    assert annonces.tokens_of("Binance Margin And Loans Token Delist on 2026-10-02") is None


def _bars(start_ms, minutes, path):
    t = start_ms + np.arange(minutes) * annonces.MINUTE
    return pd.DataFrame({"o": path, "c": path}, index=pd.Index(t, name="t"))


def test_entry_is_the_first_minute_after_publication_and_nothing_before():
    start = 1_700_000_000_000 // annonces.MINUTE * annonces.MINUTE
    n = 3 * 1440
    path = np.full(n, 100.0)
    publish = start + 1440 * annonces.MINUTE + 30_000  # 30 s après le début d'une minute
    entry_index = 1441  # la minute suivante
    path[entry_index:] = 80.0  # la chute arrive à la minute d'entrée : déjà dans le prix d'entrée
    path[entry_index + 59:] = 72.0  # clôture de la 60e minute après l'entrée
    r = annonces.window_returns(_bars(start, n, path), publish)
    assert r["r_1h"] == pytest.approx(np.log(72 / 80)) and r["r_24h"] == pytest.approx(np.log(72 / 80))
    assert r["r_avant_24h"] == pytest.approx(np.log(80 / 100))
    assert annonces.window_returns(_bars(start, 1500, path[:1500]), publish) is None  # 24 h non couvertes


def test_cluster_bootstrap_resamples_announcements_not_tokens():
    frame = pd.DataFrame({"code": ["a", "a", "a", "b"], "x": [-0.3, -0.3, -0.3, 0.1]})
    out = annonces.cluster_bootstrap_mean(frame, "x", draws=500, seed=1)
    assert out["annonces"] == 2 and out["evenements"] == 4 and out["moyenne"] == pytest.approx(-0.2)
    assert 0 < out["p_unilaterale"] < 1  # deux annonces seulement : l'incertitude reste grande


def test_the_reserved_period_is_excluded_without_loading_anything(tmp_path):
    ev = {"code": "x", "title": "t", "jetons": ["ABC"], "retrait_le": "2026-10-28",
          "publication_ms": int(pd.Timestamp("2026-10-01T12:00:00Z").timestamp() * 1000), "derniere_mise_a_jour_ms": None}
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        rows = annonces.event_study([ev], tmp_path, client)
    assert rows[0]["exclu"] == "période réservée"
    assert data.RESERVED_FROM.startswith("2026-09-01")


def test_transient_transport_errors_are_retried_and_http_errors_are_not(monkeypatch):
    monkeypatch.setattr(annonces.time, "sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.RemoteProtocolError("coupure")
        return "ok"

    assert annonces.retry(flaky) == "ok" and len(calls) == 3
    with pytest.raises(httpx.HTTPStatusError):
        annonces.retry(lambda: httpx.Response(404, request=httpx.Request("GET", "https://x.test")).raise_for_status())
