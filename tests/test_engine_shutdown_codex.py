"""A running simulated engine must shut down cleanly and be joinable."""
from sentinelle.market.sources import SyntheticSource
from sentinelle.services.core import App


def test_synthetic_engine_can_stop_and_join(tmp_path):
    source = SyntheticSource(["BTC/EUR"], days=5)
    app = App(tmp_path, source=source, settings_override={
        "market": {"source": "synthetic"},
        "general": {"watchlist": ["BTC/EUR"]},
    })
    assert app.engine.ready.wait(10)
    app.close()
    assert not app.engine.is_alive()
