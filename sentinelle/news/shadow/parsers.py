"""Pure normalizers. External content is data; unknown values stay unknown."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from urllib.parse import urlsplit

from ..service import _clean, tokens, similarity
from ...ai.coins import detect_assets


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc(value):
    """Explicit offsets only: naive/tentative/all-day times are not instants."""
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None:
            return None
        return date.astimezone(timezone.utc).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def number(value):
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace('\u2212', '-')
    if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?)(?:%|K|M|B)?', text, re.I):
        return None
    suffix = text[-1].upper() if text[-1:] in '%kKmMbB' else ''
    unit = 'percent' if suffix == '%' else 'count'
    scale = {'K': 1000, 'M': 1000000, 'B': 1000000000}.get(suffix, 1)
    try:
        amount = Decimal((text[:-1] if suffix else text).replace(',', '')) * scale
        if not amount.is_finite() or abs(amount) > Decimal('1e18'):
            return None
    except InvalidOperation:
        return None
    return {'value': str(amount), 'unit': unit}


def difference(actual, forecast):
    a, f = number(actual), number(forecast)
    if not a or not f or a['unit'] != f['unit']:
        return None
    return {'value': str(Decimal(a['value'])-Decimal(f['value'])),
            'unit': 'percentage_points' if a['unit'] == 'percent' else a['unit']}


def event_type(title):
    low = title.lower()
    if any(w in low for w in ('cpi', 'pce', 'inflation')):
        return 'inflation'
    if any(w in low for w in ('employment', 'payroll', 'unemployment', 'claims')):
        return 'employment'
    if any(w in low for w in ('rate', 'fomc', 'monetary', 'fed ', 'ecb')):
        return 'monetary_policy'
    if 'gdp' in low:
        return 'growth'
    return 'other'


def forex(raw):
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) > 3000:
        raise ValueError('Expected weekly calendar array')
    out = []
    for row in data:
        if not isinstance(row, dict) or not row.get('title') or not row.get('country'):
            raise ValueError('Incomplete calendar row')
        if not isinstance(row['country'],str) or row['country'] not in {'USD','EUR','GBP','JPY','CHF','CAD','AUD','NZD','CNY','All'}:
            raise ValueError('Unknown calendar currency')
        if any(row.get(k) is not None and not isinstance(row[k],(str,int,float)) for k in ('forecast','previous')):
            raise ValueError('Non-scalar calendar value')
        title = _clean(str(row['title']), 200)
        scheduled = utc(row.get('date'))
        # Provider IDs support cross-day rescheduling. Without one, do not invent an identity.
        identity = str(row.get('id') or f"{row['country']}:{title}:{str(row.get('date', 'tentative'))[:10]}")
        payload = {'title': title, 'currency': str(row['country'])[:8],
                   'impact': row.get('impact') if row.get('impact') in ('High', 'Medium', 'Low', 'Holiday') else 'Unknown',
                   'scheduled_at': scheduled, 'schedule_text': str(row.get('date', 'tentative'))[:80],
                   'published_at': None, 'forecast': row.get('forecast') or None, 'previous': row.get('previous') or None,
                   'actual': None, 'actual_source': None, 'event_type': event_type(title),
                   'identity_quality': 'provider_id' if row.get('id') else 'title_currency_day',
                   'time_quality': 'explicit_offset' if scheduled is not None else 'uncertain',
                   'status': 'cancelled' if row.get('status') == 'cancelled' else 'scheduled'}
        out.append(('calendar', identity, payload))
    # Conflicting rows with an identical weak key cannot be linked reliably.
    seen = {}
    for _, key, payload in out:
        if key in seen and seen[key] != payload:
            raise ValueError('Ambiguous duplicate calendar identity')
        seen[key] = payload
    return out


def rss(raw, source):
    import feedparser
    feed = feedparser.parse(raw)
    if feed.bozo or not feed.version or len(feed.entries) > 2000:
        raise ValueError('Malformed RSS/Atom or HTML error response')
    out = []
    allowed_host = source['host']
    for item in feed.entries[:100]:
        title = _clean(item.get('title', ''), 200)
        if not title:
            continue
        link = item.get('link') or ''
        host = urlsplit(link).hostname or ''
        origin_verified = host == allowed_host or host.endswith('.'+allowed_host)
        # feedparser parsed tuples are UTC; never use local mktime (DST-dependent).
        def dated(text):
            if not text:return None
            try:
                date=parsedate_to_datetime(text)
                return date.timestamp() if date.tzinfo is not None else None
            except (ValueError,TypeError,OverflowError):
                return utc(text)
        published = dated(item.get('published'))
        revised = dated(dict(item).get('updated'))
        if published is None:published=revised
        summary = _clean(item.get('summary', ''), 400)
        text = f'{title} {summary}'.lower()
        uncertain = any(w in text for w in ('rumor', 'rumour', 'unconfirmed', 'alleged', 'rumeur', 'reportedly'))
        correction = any(w in text for w in ('correction', 'corrected', 'revised', 'revision', 'rectificatif'))
        identity = str(item.get('id') or link or sha(title.encode()))
        out.append(('news', identity, {'title': title, 'excerpt': summary, 'url': link if link.startswith('https://') else '',
                    'published_at': published, 'revision_published_at': revised, 'scheduled_at': None,
                    'origin_verified': origin_verified, 'official': bool(source['official'] and origin_verified),
                    'dependency_group': source['dependency_group'], 'rumor': uncertain, 'correction': correction,
                    'assets': detect_assets(title+' '+summary, {'BTC', 'ETH', 'SOL'}),
                    'suspicious_instruction': bool(re.search(r'ignore.{0,40}instructions|system prompt|withdraw\s*\(|api.?key', text)),
                    'market_wide': event_type(title) != 'other', 'event_type': event_type(title),
                    'facts': ['Source reports the attached headline; numeric release values are not inferred from prose.'],
                    'hypotheses': [], 'crypto_direction': None, 'actual': None}))
    return out


def bls_series(raw):
    """Public BLS index observations, NOT CPI y/y or an FF actual. No release time invented."""
    data = json.loads(raw)
    if data.get('status') != 'REQUEST_SUCCEEDED':
        raise ValueError('BLS did not return successful data')
    out = []
    for series in data['Results']['series']:
        if series['seriesID'] != 'CUUR0000SA0':
            raise ValueError('Unexpected series')
        for item in series['data']:
            if not re.fullmatch(r'M(0[1-9]|1[0-2])', item['period']):
                continue  # annual averages are not monthly releases
            numeric = number(item['value'])
            missing = str(item['value']).strip() in ('-', '', 'N/A')
            if (not numeric and not missing) or (numeric and Decimal(numeric['value'])<=0):
                raise ValueError('Invalid BLS index')
            period = f"{item['year']}-{item['period']}"
            out.append(('release', 'CUUR0000SA0:'+period,
                        {'title': 'US CPI all urban consumers, unadjusted index', 'indicator': 'CUUR0000SA0',
                         'reference_period': period, 'unit': 'index_1982_84_100', 'actual': numeric['value'] if numeric else None, 'data_quality':'missing' if missing else 'reported',
                         'footnotes':[{'code':str(n.get('code',''))[:20],'text':_clean(str(n.get('text','')),200)} for n in item.get('footnotes',[])],
                         'forecast': None, 'previous': None, 'published_at': None, 'scheduled_at': None,
                         'currency': 'USD', 'event_type': 'inflation', 'official': True,
                         'publication_quality': 'release_time_unknown; available only from receipt',
                         'crypto_direction': None}))
    return out


def cluster_key(title):
    return tokens(title)


def dependent(a, b):
    return similarity(cluster_key(a), cluster_key(b)) >= .6


def surprise_at(calendar_event, official_release, as_of):
    """Only explicit, matched, dated official data can create a surprise. Never join by fuzzy title."""
    e,r=calendar_event,official_release
    published=r.get('published_at')
    if (not r.get('official') or published is None or r.get('available_at',float('inf'))>as_of
            or published>as_of or e.get('available_at',float('inf'))>published):
        return None
    if any(not e.get(k) or e.get(k)!=r.get(k) for k in ('indicator','reference_period')):
        return None
    value=difference(r.get('actual'),e.get('forecast'))
    if value is None:return None
    delta=Decimal(value['value'])
    names={'cpi_yoy':('inflation_above_consensus','inflation_below_consensus'),
           'unemployment_rate':('labor_weaker_than_consensus','labor_stronger_than_consensus'),
           'nonfarm_payrolls':('labor_stronger_than_consensus','labor_weaker_than_consensus'),
           'fed_rate':('tighter_than_consensus','looser_than_consensus')}
    interpretation=names.get(e['indicator'])
    meaning=interpretation[0 if delta>0 else 1] if interpretation and delta else 'unchanged' if not delta else 'indicator_definition_required'
    return {**value,'economic_interpretation':meaning,'indicator_direction':'higher' if delta>0 else 'lower' if delta<0 else 'unchanged',
            'crypto_direction':None,'official_source':r.get('source'),
            'forecast_version':e.get('version'),'release_version':r.get('version')}
