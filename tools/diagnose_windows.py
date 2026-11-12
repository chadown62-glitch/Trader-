"""Cross-platform, offline diagnostics. Running on Linux does not validate Windows."""
import json
import platform
import sqlite3
import sys
import tempfile
from pathlib import Path


def diagnose():
    from tools.validation.reproducibility import runtime
    from sentinelle.market.sources import SyntheticSource
    from sentinelle.services.core import App
    checks={}
    with tempfile.TemporaryDirectory(prefix='sentinelle-diagnostic-') as directory:
        root=Path(directory)
        for iteration in range(2):
            app=App(root,start=False,source=SyntheticSource(['BTC/EUR'],days=2),
                    settings_override={'market':{'source':'synthetic'},'general':{'quote':'EUR','watchlist':['BTC/EUR']}})
            try:
                account=app.main_account()
                checks[f'wallet_restart_{iteration}']=account['initial_balance']==30. and account['quote']=='EUR'
                checks[f'performance_{iteration}']=app.performance()['capital']['initial']==30.
                checks[f'sqlite_integrity_{iteration}']=app.db.scalar('PRAGMA integrity_check')=='ok'
            finally:app.db.close()
    from sentinelle.execution.live import LiveBroker
    from sentinelle.safety import RealTradingForbidden
    try:LiveBroker(None,None,None)
    except RealTradingForbidden:
        checks['live_broker_inert']=True
    else:checks['live_broker_inert']=False
    return {'platform':platform.platform(),'windows_actually_tested':sys.platform=='win32','runtime':runtime(),
            'sqlite':sqlite3.sqlite_version,'gpu_required':False,'checks':checks,
            'ok':all(checks.values()),'limits':['Does not validate browser rendering, Telegram, public connectivity or a long model training run.']}


if __name__=='__main__':
    result=diagnose();print(json.dumps(result,ensure_ascii=False,indent=2));sys.exit(0 if result['ok'] else 1)
