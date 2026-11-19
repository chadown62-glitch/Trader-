"""Read-only panel view and explicit sidecar annotations of committed decisions."""
import json
from pathlib import Path
import sqlite3
import time

from .parsers import sha
from .sources import SOURCES
from .store import ShadowStore


class MacroShadow:
    def __init__(self, data_dir):
        self.data_dir=Path(data_dir)
        self.store=ShadowStore(self.data_dir/'macro-shadow.sqlite')

    def stop(self):
        self.store.close()

    def dashboard(self, as_of=None):
        stamp=time.time() if as_of is None else as_of
        context=self.store.context(stamp,SOURCES).to_dict()
        context['decisions']=self.store.rows('SELECT decision_key,as_of,protocol_sha,context_sha,context FROM decision_contexts WHERE as_of<=? ORDER BY id DESC LIMIT 20',(stamp,))
        for row in context['decisions']:row['context']=json.loads(row['context'])
        context['status']='Données insuffisantes' if not (context['calendar'] or context['news'] or context['releases']) else 'Observation parallèle uniquement'
        return context

    def annotate(self, trading_db, protocol_path):
        """Read trading DB in read-only mode; write ONLY the shadow sidecar."""
        protocol=json.loads(Path(protocol_path).read_bytes())
        begin,end=protocol['observation_start'],protocol['observation_end']
        if protocol['mode']!='shadow_only' or protocol['can_trade'] or begin>=end:
            raise ValueError('Invalid shadow protocol')
        from urllib.parse import quote
        uri='file:'+quote(str(Path(trading_db).resolve()),safe='/:')+'?mode=ro'
        with sqlite3.connect(uri,uri=True) as conn:
            conn.row_factory=sqlite3.Row
            # Only committed, paper-account decisions within the independently reserved period.
            rows=conn.execute("SELECT d.* FROM decisions d JOIN accounts a ON a.id=d.account_id WHERE a.kind='paper' AND d.kind IN ('pris','refusé') AND d.ts>=? AND d.ts<? ORDER BY d.id",(begin,end)).fetchall()
        digest=sha(Path(protocol_path).read_bytes())
        for row in rows:
            baseline=dict(row)
            key=sha(str(Path(trading_db).resolve()).encode())+':'+str(row['id'])
            self.store.record_decision(key,row['ts'],baseline,digest,SOURCES)
        return len(rows)
