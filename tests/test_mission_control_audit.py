"""Independent regressions for the four defects named in the recovery handoff."""
import json
import pytest
from sentinelle.services.supervisor import State, Supervisor, instance_lock, prepare, approve, CONFIRM
from tools import supervise
from tools.public_capture import load_capture
from tools.validation.archive import encoded, digest
from tests import test_observation_e2e as fixture
from tests.test_supervisor import FakeBackend, plan, START, snapshot


def test_failed_tick_keeps_failure_not_stopped(tmp_path):
    state = State(tmp_path)
    backend = FakeBackend(lambda: START)
    backend.validate = lambda: (_ for _ in ()).throw(ValueError('invalid implementation'))
    try:
        with pytest.raises(ValueError):
            Supervisor(plan(), state, backend, clock=lambda: START).run()
        assert state.get('status') == 'failed_closed'
        assert state.get('failure')['type'] == 'ValueError'
    finally:
        state.close()


def test_second_cli_cannot_change_owner_metadata(tmp_path, monkeypatch):
    cfg = {'data': str(tmp_path)}
    monkeypatch.setattr(supervise, 'load_plan', lambda _: cfg)
    monkeypatch.setattr(supervise, 'authorize', lambda *_: cfg)
    class Backend:
        def __init__(self, *args): pass
        def close(self): pass
    monkeypatch.setattr(supervise, 'PublicBackend', Backend)
    owner = State(tmp_path/'supervisor')
    owner.set('plan', {'owner': 'first'})
    with instance_lock(owner.folder):
        before = snapshot(owner.folder)
        with pytest.raises(RuntimeError, match='already owns'):
            supervise.main(['run', '--plan', str(tmp_path/'plan'), '--approval', str(tmp_path/'approve')])
        assert snapshot(owner.folder) == before
        assert owner.get('plan') == {'owner': 'first'}
    owner.close()


def test_forged_windows_summary_without_tests_is_refused(tmp_path, monkeypatch):
    protocol = fixture.setup(tmp_path, real_model=True)
    path = tmp_path/'plan.json'
    cfg = prepare(tmp_path/'app', protocol, path)
    report = tmp_path/'report.json'
    report.write_bytes(encoded({'windows_actually_tested': True, 'installation': {'ok': True, 'not_verified': []},
                               'data_dir': cfg['data'], 'supervisor_code': cfg['code']}))
    monkeypatch.setattr('tools.diagnose_windows.system_info', lambda: {'windows_actually_tested': True})
    with pytest.raises(ValueError, match='evidence'):
        approve(path, report, tmp_path/'approval.json', CONFIRM)


def test_rehashed_derived_book_cannot_disagree_with_raw_http(tmp_path):
    fixture.capture(tmp_path)
    path = tmp_path/'capture/capture.json'
    report = json.loads(path.read_bytes())
    report['books']['BTC/EUR']['asks'][0][1] = '9999999'
    path.write_bytes(encoded(report))
    path.with_suffix('.sha256').write_text(digest(path.read_bytes()))
    with pytest.raises(ValueError, match='archived response'):
        load_capture(path.parent)


def test_stop_survives_corrupt_plan(tmp_path):
    assert supervise.main(['stop','--data',str(tmp_path)]) == 0
    state = State(tmp_path/'supervisor')
    try:
        backend = FakeBackend(lambda: START)
        backend.validate = lambda: pytest.fail('stop must precede validation')
        assert not Supervisor(plan(), state, backend, clock=lambda: START).tick()
    finally: state.close()


def test_crossing_utc_boundary_archives_without_late_prediction(tmp_path):
    clock = [START]
    state = State(tmp_path)
    backend = FakeBackend(lambda: clock[0])
    original = backend.collect
    def collect(path):
        result = original(path)
        clock[0] += 14400
        return result
    backend.collect = collect
    try:
        Supervisor(plan(),state,backend,clock=lambda:clock[0]).tick()
        assert 'observe' not in backend.calls
        assert state.db.execute('SELECT status FROM cycles').fetchone()[0] == 'history_only'
    finally: state.close()


def test_three_days_restart_gap_and_repeated_ticks_never_reobserve(tmp_path):
    clock=[START]
    backend=FakeBackend(lambda:clock[0])
    state=State(tmp_path)
    try:
        for i in range(18):
            clock[0]=START+i*14400
            if i==7:continue  # machine off: explicit coverage hole, never backfill a decision
            observer=Supervisor(plan(),state,backend,clock=lambda:clock[0])
            observer.tick();observer.tick()
            if i in (5,11):
                state.close();state=State(tmp_path)
        assert backend.calls.count('observe')==17
        assert state.db.execute("SELECT COUNT(*) FROM cycles WHERE status='observed'").fetchone()[0]==17
        assert state.db.execute('SELECT COUNT(*) FROM cycles WHERE bucket=?', (int((START+7*14400)//14400)*14400000,)).fetchone()[0]==0
    finally:state.close()


def test_sqlite_snapshot_rejects_concurrent_writer(tmp_path,monkeypatch):
    import shutil
    import sqlite3
    from sentinelle.preflight import _read_only
    path=tmp_path/'writer.db'
    db=sqlite3.connect(path);db.execute('CREATE TABLE t(x)');db.commit();db.close()
    scratch=tmp_path/'copy';scratch.mkdir()
    original=shutil.copyfile
    def copy(source,destination):
        result=original(source,destination)
        writer=sqlite3.connect(path);writer.execute('INSERT INTO t VALUES(1)');writer.commit();writer.close()
        return result
    monkeypatch.setattr(shutil,'copyfile',copy)
    with pytest.raises(ValueError,match='writer changed'):_read_only(path,scratch)


def test_invalid_clock_failure_survives_and_stop_precedes_clock_validation(tmp_path):
    state=State(tmp_path)
    try:
        observer=Supervisor(plan(),state,FakeBackend(lambda:START),clock=lambda:float('nan'))
        with pytest.raises(ValueError,match='Invalid UTC clock'):observer.run()
        assert state.get('status')=='failed_closed' and state.get('failure')['type']=='ValueError'
        (tmp_path/'STOP').touch()
        assert not observer.tick()
    finally:state.close()
