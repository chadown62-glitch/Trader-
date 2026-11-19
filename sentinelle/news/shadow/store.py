"""Append-only causal observations in a separate SQLite database, never the trading ledger."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
import json
import math
from pathlib import Path
import sqlite3
import threading
import time

from .parsers import canonical, sha, dependent

SCHEMA = '''
CREATE TABLE IF NOT EXISTS observations(
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, item_key TEXT NOT NULL, kind TEXT NOT NULL,
 version INTEGER NOT NULL, payload TEXT NOT NULL, payload_sha TEXT NOT NULL, raw_sha TEXT NOT NULL,
 received_at REAL NOT NULL, stored_at REAL NOT NULL, published_at REAL, available_at REAL NOT NULL,
 UNIQUE(source,item_key,version));
CREATE INDEX IF NOT EXISTS observations_asof ON observations(available_at,source,item_key,version);
CREATE TABLE IF NOT EXISTS fetches(
 id INTEGER PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL, status TEXT NOT NULL,
 received_at REAL NOT NULL, stored_at REAL NOT NULL, raw_sha TEXT, source_updated_at TEXT,
 http_status INTEGER, error TEXT, retry_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS poll_state(source TEXT PRIMARY KEY, next_at REAL NOT NULL, failures INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS analyses(
 id INTEGER PRIMARY KEY, observation_id INTEGER NOT NULL, available_at REAL NOT NULL,
 engine TEXT NOT NULL, payload TEXT NOT NULL, payload_sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decision_contexts(
 id INTEGER PRIMARY KEY, decision_key TEXT NOT NULL UNIQUE, as_of REAL NOT NULL,
 recorded_at REAL NOT NULL, protocol_sha TEXT NOT NULL, baseline TEXT NOT NULL,
 context TEXT NOT NULL, context_sha TEXT NOT NULL);
'''


@dataclass(frozen=True)
class MarketContext:
    as_of: float
    analysis_version: str
    mode: str
    calendar: list
    releases: list
    news: list
    sources: list
    warnings: list
    advisory: dict

    def to_dict(self):
        return asdict(self)


class ShadowStore:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        self.path = str(path)
        if self.path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.execute('PRAGMA journal_mode=WAL')
        self.conn.execute('PRAGMA synchronous=FULL')
        self.conn.execute('PRAGMA busy_timeout=5000')
        self.conn.executescript(SCHEMA)
        for table in ('observations', 'fetches', 'analyses', 'decision_contexts'):
            for operation in ('UPDATE', 'DELETE'):
                self.conn.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'immutable shadow record'); END")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.conn.execute('BEGIN IMMEDIATE')
            try:
                yield
            except BaseException:
                self.conn.execute('ROLLBACK')
                raise
            else:
                self.conn.execute('COMMIT')

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args)]

    def close(self):
        self.conn.close()

    def append(self, source, entries, raw_sha, received_at, *, stored_at=None):
        stored = self.clock() if stored_at is None else stored_at
        if not all(math.isfinite(t) for t in (stored, received_at)) or stored < received_at:
            raise ValueError('Receipt/storage clocks invalid')
        count = 0
        with self.transaction():
            for kind, key, payload in entries:
                published = payload.get('published_at')
                revision = payload.get('revision_published_at')
                if (published is not None and not math.isfinite(published)) or (revision is not None and not math.isfinite(revision)):
                    raise ValueError('Invalid publication time')
                text = canonical(payload)
                fingerprint = sha(text.encode())
                last = self.conn.execute('SELECT * FROM observations WHERE source=? AND item_key=? ORDER BY version DESC LIMIT 1', (source, key)).fetchone()
                if last and last['payload_sha'] == fingerprint:
                    continue
                if last and stored < last['stored_at']:
                    raise ValueError('Cannot backdate a revision')
                version = last['version']+1 if last else 1
                self.conn.execute('INSERT INTO observations(source,item_key,kind,version,payload,payload_sha,raw_sha,received_at,stored_at,published_at,available_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                  (source,key,kind,version,text,fingerprint,raw_sha,received_at,stored,published,max(stored,received_at,published or 0,revision or 0)))
                count += 1
        return count

    def add_analysis(self, observation_id, payload, engine, *, available_at=None):
        from ..service import validate_analysis
        stamp = self.clock() if available_at is None else available_at
        row = self.rows('SELECT * FROM observations WHERE id=?', (observation_id,))[0]
        if not math.isfinite(stamp) or stamp < row['available_at']:
            raise ValueError('Analysis cannot precede its input')
        for key in ('sentiment', 'importance', 'confidence'):
            if not isinstance(payload.get(key), (int,float)) or not math.isfinite(payload[key]):
                raise ValueError('Nonfinite or absent analysis score')
        clean = validate_analysis(payload, {'BTC','ETH','SOL'})
        # Every generated interpretation is a hypothesis, never an official release value.
        clean['facts'] = []
        clean['hypotheses'] = [clean.pop('summary_fr')]
        clean['trading_authority'] = False
        text = canonical(clean)
        with self.transaction():
            self.conn.execute('INSERT INTO analyses(observation_id,available_at,engine,payload,payload_sha) VALUES(?,?,?,?,?)',
                              (observation_id,stamp,engine,text,sha(text.encode())))

    def context(self, as_of, registry):
        if not math.isfinite(as_of):
            raise ValueError('Invalid as_of')
        rows = self.rows('SELECT * FROM observations WHERE available_at<=? ORDER BY id', (as_of,))
        latest = {}
        for row in rows:
            latest[(row['source'],row['item_key'])] = row
        calendar, releases, news, sources, warnings = [], [], [], [], []
        for source, spec in registry.items():
            attempts = self.rows('SELECT * FROM fetches WHERE source=? AND stored_at<=? ORDER BY id DESC LIMIT 1', (source,as_of))
            r = attempts[0] if attempts else None
            age = as_of-r['received_at'] if r else None
            state = 'missing' if not r else ('fresh' if r['status']=='ok' and 0<=age<=spec['max_age'] else 'unavailable_or_stale')
            sources.append({'id':source,'url':spec['url'],'official':spec['official'],'dependency_group':spec['dependency_group'],
                            'state':state,'last_received_at':r['received_at'] if r else None,
                            'last_stored_at':r['stored_at'] if r else None,'http_status':r['http_status'] if r else None,
                            'error':r['error'] if r else None,'raw_sha256':r['raw_sha'] if r else None})
        freshness = {r['id']:r['state'] for r in sources}
        for row in latest.values():
            payload = json.loads(row['payload'])
            entry = {**payload,'observation_id':row['id'],'source':row['source'],'version':row['version'],
                     'received_at':row['received_at'],'stored_at':row['stored_at'],'available_at':row['available_at'],
                     'raw_sha256':row['raw_sha'],'content_sha256':row['payload_sha'],
                     'fresh':freshness.get(row['source'])=='fresh'}
            analyses = self.rows('SELECT * FROM analyses WHERE observation_id=? AND available_at<=? ORDER BY id DESC LIMIT 1', (row['id'],as_of))
            entry['analysis'] = {**json.loads(analyses[0]['payload']),'available_at':analyses[0]['available_at'],'engine':analyses[0]['engine']} if analyses else None
            if row['kind']=='calendar':
                # Retain tentative events in the view; do not turn them into fabricated instants.
                if (entry['scheduled_at'] is None and as_of-entry['available_at']<=7*86400) or (entry['scheduled_at'] is not None and entry['scheduled_at'] >= as_of-86400):
                    calendar.append(entry)
            elif row['kind']=='release':
                releases.append(entry)
            elif row['kind']=='news' and as_of-entry['available_at']<=86400 and (entry['published_at'] is None or as_of-entry['published_at']<=86400):
                entry['publication_quality']='known' if entry['published_at'] is not None else 'unknown'
                news.append(entry)
        for event in calendar:
            event['surprise']=None
            event['surprise_status']='No explicit matched official release/period; no actual inferred'
        calendar.sort(key=lambda e: (e['scheduled_at'] is None,e['scheduled_at'] or 0,e['title']))
        clusters = []
        for article in news:
            cluster = next((g for g in clusters if dependent(g['representative']['title'],article['title'])),None)
            if cluster is None:
                cluster = {'representative':article,'members':[]};clusters.append(cluster)
            cluster['members'].append(article)
        for group in clusters:
            members=group['members'];groups={m['dependency_group'] for m in members if m.get('origin_verified')}
            signs={1 if m['analysis']['sentiment']>0 else -1 for m in members if m['analysis'] and m['analysis']['sentiment']!=0}
            for m in members:
                m['independent_source_groups']=len(groups)
                m['independence_verified']=False
                m['contradictory']=len(signs)>1
                m['cluster']=group['representative']['observation_id']
        high = [e for e in calendar if e['impact']=='High' and e['status']!='cancelled' and e['scheduled_at'] is not None
                and abs(e['scheduled_at']-as_of)<=1800 and e['fresh']]
        uncertain = [e for e in calendar if e['scheduled_at'] is None and e['impact']=='High']
        no_calendar = freshness.get('forex_factory')!='fresh' or not calendar
        uncertain += [e for e in calendar if e['impact']=='Unknown']
        news_risk = [n for n in news if n['rumor'] or not n['origin_verified'] or n.get('contradictory') or n.get('publication_quality')=='unknown' or n.get('suspicious_instruction')]
        if high:warnings.append('Annonce à fort impact dans la fenêtre de 30 minutes : abstention shadow recommandée.')
        if no_calendar: warnings.append('Calendrier shadow absent ou périmé : risque macro inconnu, aucune nouvelle règle appliquée au portefeuille.')
        if uncertain: warnings.append('Heure d’annonce incertaine : ne pas supposer une fenêtre sans risque.')
        if news_risk: warnings.append('Rumeurs, origine non vérifiée ou analyses contradictoires : abstention shadow recommandée.')
        advisory={'calendar_would_abstain':bool(high or uncertain or no_calendar),
                  'news_would_abstain':bool(news_risk),'near_high_impact_ids':[e['observation_id'] for e in high],
                  'volatility_regime':'unknown_not_supplied','directional_crypto_signal':None,'can_trade':False,'live_filters_changed':False}
        return MarketContext(as_of,'macro-shadow-v1','shadow_only',calendar,releases,news,sources,warnings,advisory)

    def record_decision(self, decision_key, as_of, baseline, protocol_sha, registry):
        """Pure annotation of a committed paper decision: never create an order."""
        with self.transaction():
            old = self.conn.execute('SELECT context,protocol_sha,baseline FROM decision_contexts WHERE decision_key=?', (decision_key,)).fetchone()
            if old:
                if old['protocol_sha']!=protocol_sha or old['baseline']!=canonical(baseline):
                    raise ValueError('Cannot reuse a decision with a changed protocol or baseline')
                return json.loads(old['context'])
            context = self.context(as_of,registry).to_dict()
            action = baseline.get('kind', 'refusé')
            if action not in ('pris','refusé'):
                raise ValueError('Only committed paper decisions can be annotated')
            context['arms'] = {'prices_only':action,
                               'prices_calendar':'refusé' if context['advisory']['calendar_would_abstain'] else action,
                               'prices_calendar_news':'refusé' if (context['advisory']['calendar_would_abstain'] or context['advisory']['news_would_abstain']) else action}
            text = canonical(context)
            self.conn.execute('INSERT INTO decision_contexts(decision_key,as_of,recorded_at,protocol_sha,baseline,context,context_sha) VALUES(?,?,?,?,?,?,?)',
                              (decision_key,as_of,self.clock(),protocol_sha,canonical(baseline),text,sha(text.encode())))
            return context
