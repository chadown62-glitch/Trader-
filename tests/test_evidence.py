import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from sentinelle.api.server import create_api
from sentinelle.services.evidence import export, probability_scores
from sentinelle.services.platform_validation import junit_summary, runtime, verify
from tests import test_observation_e2e as fixture
from tests.test_supervisor import snapshot


def test_empty_export_never_creates_installation(tmp_path):
    folder = tmp_path/'absent'
    result = export(folder)
    assert not folder.exists() and result['wallet'] is None
    assert result['status']=='NOT_ENOUGH_EVIDENCE'
    assert set(result['categories'])=={'synthetic_fixture','historical_public','prospective_recorded'}


def test_probability_bins_include_one_and_auc_ties():
    score=probability_scores([(0,0),(.5,1),(.5,0),(1,1)])
    assert score['auc']==.875
    assert score['brier']==.125
    assert sum(b['n'] for b in score['calibration'])==4
    assert score['calibration'][-1]['n']==1
    assert probability_scores([(1,1)])['auc'] is None
    assert probability_scores([])['brier'] is None
    with pytest.raises(ValueError): probability_scores([(float('nan'),1)])


def test_fixture_export_causal_categories_read_only_and_accounting(tmp_path):
    protocol=fixture.setup(tmp_path);fixture.capture(tmp_path)
    with patch('tools.prospective.datetime', fixture.FrozenDate):
        fixture.step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:fixture.NOW)
    before=snapshot(tmp_path/'app')
    with patch('socket.socket',side_effect=AssertionError('no network')):
        result=export(tmp_path/'app',now=fixture.NOW)
    assert result['status']=='NOT_ENOUGH_EVIDENCE',result['errors']
    assert result['wallet']['ledger_reconciled']
    assert result['wallet']['current_mtm_eur'] is None
    assert result['categories']['synthetic_fixture']['reports']
    assert result['categories']['synthetic_fixture']['decisions']
    assert not result['categories']['prospective_recorded']['decisions']
    assert before==snapshot(tmp_path/'app')
    json.dumps(result,allow_nan=False)


def test_later_origin_cannot_authenticate_old_prediction(tmp_path):
    protocol=fixture.setup(tmp_path);fixture.capture(tmp_path)
    with patch('tools.prospective.datetime', fixture.FrozenDate):
        fixture.step(tmp_path/'app',protocol,tmp_path/'capture',execute=True,clock=lambda:fixture.NOW)
    import sqlite3
    db=sqlite3.connect(tmp_path/'app/sentinelle.db')
    # Simulate corrupt/migrated legacy provenance; preserve immutable triggers in real operation.
    db.execute('DROP TRIGGER immutable_observation_provenance')
    db.execute('UPDATE observation_provenance SET origin="public_preflight",recorded_at=?',(fixture.NOW+1,))
    db.commit();db.close()
    report=export(tmp_path/'app',now=fixture.NOW+2)
    assert not report['categories']['prospective_recorded']['decisions']
    assert report['classification']['unproven_decisions']>0


def test_api_authenticated_and_session_not_refreshed(tmp_path):
    from sentinelle.services.core import App
    from sentinelle.market.sources import SyntheticSource
    app=App(tmp_path,start=False,source=SyntheticSource(['BTC/EUR'],days=2))
    try:
        with TestClient(create_api(app)) as client:
            assert client.get('/api/evidence').status_code==401
            assert client.post('/api/auth/setup',json={'password':'jetable-123456789'},headers={'X-Sentinelle':'1'}).status_code==200
            app.db.execute('UPDATE sessions SET last_seen=0')
            before=snapshot(tmp_path)
            with patch.object(app.hub.source,'fetch_ohlcv',side_effect=AssertionError('no fetch')):
                for url in ('/api/evidence','/api/evidence/export'):
                    response=client.get(url)
                    assert response.status_code==200
                    assert response.json()['schema']=='sentinelle.evidence/v1'
            assert before==snapshot(tmp_path)
    finally: app.db.close()


@pytest.mark.parametrize('inner',['','<testcase><skipped/></testcase>','<testcase><failure/></testcase>','<testcase><error/></testcase>'])
def test_windows_junit_missing_failed_or_skipped_refused(tmp_path,inner):
    path=tmp_path/'tests.xml';path.write_text('<testsuite>'+inner+'</testsuite>')
    with pytest.raises(ValueError): junit_summary(path)


def test_local_seal_binds_junit_runtime_and_host(tmp_path,monkeypatch):
    from sentinelle.services import platform_validation as pv
    monkeypatch.setattr(pv,'_key',lambda **_:b'test-only-not-a-financial-key')
    monkeypatch.setattr('tools.diagnose_windows.system_info',lambda:{'windows_actually_tested':True})
    junit=tmp_path/'tests.xml';junit.write_text('<testsuite>'+''.join('<testcase name="'+name+'"/>' for name in
        ['test_failed_tick_keeps_failure_not_stopped','test_second_cli_cannot_change_owner_metadata',
         'test_rehashed_derived_book_cannot_disagree_with_raw_http','test_full_get_archive_model_paper_journal_report_and_repeat']
        + ['fixture']*515)+'</testsuite>')
    path=tmp_path/'report.json';path.write_text('{}')
    result=pv.seal(path,junit)
    verify(result)
    assert runtime()['dependencies']['ccxt']
    monkeypatch.setattr(pv,'runtime',lambda:{'changed':True})
    with pytest.raises(ValueError):verify(result)


def test_corrupted_database_is_blocked_and_never_replaced(tmp_path):
    path=tmp_path/'sentinelle.db';path.write_bytes(b'corrupt sqlite')
    before=snapshot(tmp_path)
    assert export(tmp_path)['status']=='BLOCKED'
    assert before==snapshot(tmp_path)
