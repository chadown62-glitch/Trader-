"""Verify published replay evidence and reject corruption without recomputing or retuning it."""
import json
from pathlib import Path
from sentinelle.services.evidence import historical_baselines
from tools.validation.archive import digest


def test_replayed_arms_reproduce_metrics_and_all_financial_traces():
    folder=Path(__file__).resolve().parents[1]/'docs/validation/mesures/mission-control'
    first=historical_baselines()
    second=json.loads((folder/'reproduction/results.json').read_bytes())
    assert first['results']==second['results']
    for period in first['results']:
        assert set(period['arms'])=={'CASH','HOLD','TREND','V2'}
        assert period['arms']['CASH']['metrics']['capital_eur']==30
        for name,arm in period['arms'].items():
            trace=f"{period['start']}-{name}-trace.json"
            assert digest((folder/'baselines'/trace).read_bytes())==digest((folder/'reproduction'/trace).read_bytes())
            assert arm['realized_avoided_profit'] is None
    assert first['promotion_allowed'] is False
