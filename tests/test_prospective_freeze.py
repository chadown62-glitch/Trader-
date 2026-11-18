from copy import deepcopy
from datetime import datetime, timezone
import pytest
from sentinelle.config import DEFAULT_SETTINGS
from tools.prospective import prepare,check


def test_future_protocol_locks_model_settings_and_period(tmp_path):
    model=tmp_path/'input.joblib';model.write_bytes(b'opaque model fixture')
    root=tmp_path/'frozen';cfg=deepcopy(DEFAULT_SETTINGS);cfg['general']['quote']='EUR'
    p=prepare(model,cfg,root,'2026-11-13T00:00:00+00:00','2027-02-13T00:00:00+00:00',now=datetime(2026,11,12,tzinfo=timezone.utc))
    assert check(root)==p and p['promotion_allowed'] is False
    (root/'model.joblib').write_bytes(b'changed')
    with pytest.raises(ValueError,match='model integrity'):check(root)
    with pytest.raises(ValueError):prepare(model,cfg,root,'2020-01-01','2020-02-01')


def test_prepared_real_model_adapter_refuses_early_observation(tmp_path):
    import json
    from pathlib import Path
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource
    from tools.prospective import SupervisedObserver
    root=Path(__file__).resolve().parents[1]/'research/prospective-v2'
    cfg=json.loads((root/'settings.json').read_text())
    app=App(tmp_path,start=False,source=SyntheticSource(['BTC/EUR'],days=2),settings_override=cfg)
    try:
        app._startup()  # initialize the real paper runtime with an offline source
        observer=SupervisedObserver(app,root)
        # Fixed test clock for the prospective gate; never observe public prices here.
        import tools.prospective as module
        from unittest.mock import patch
        class BeforeStart(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2026,11,12,tzinfo=timezone.utc)
        with patch.object(module,'datetime',BeforeStart):
            with pytest.raises(ValueError,match='Outside reserved'):
                observer.on_bar('BTC/EUR',None)
        assert app.db.scalar('SELECT COUNT(*) FROM fills')==0
    finally:app.db.close()
