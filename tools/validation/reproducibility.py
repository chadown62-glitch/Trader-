"""Lossless numeric input snapshots, independent of CSV formatting; no rounding."""
import gzip
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from .archive import COLUMNS, digest, encoded, immutable


def runtime():
    return {'python':sys.version,'platform':platform.platform(),'machine':platform.machine(),
            'packages':{name:importlib.metadata.version(name) for name in ('numpy','pandas','scikit-learn','scipy','ccxt')}}


def canonical_history(history):
    """IEEE float.hex preserves every bit, fixed column/symbol ordering; reject nonfinite."""
    import math
    rows=[]
    for symbol in sorted(history):
        frame=history[symbol]
        for row in frame[COLUMNS].itertuples(index=False,name=None):
            ts,*values=row
            if int(ts)!=ts or any(not math.isfinite(float(v)) for v in values):
                raise ValueError('Invalid numeric input')
            rows.append([symbol,int(ts),*[float(v).hex() for v in values]])
    return b''.join(encoded(r) for r in rows)


def save_snapshot(root, history, parameters):
    root=Path(root)
    payload=canonical_history(history)
    compressed=gzip.compress(payload,mtime=0)
    immutable(root/'inputs.jsonl.gz',compressed)
    m={'schema':1,'format':'sorted-symbol fixed-column IEEE-float-hex JSONL v1','columns':COLUMNS,
       'parameters':parameters,'input_sha256':digest(payload),'compressed_sha256':digest(compressed),'runtime':runtime()}
    immutable(root/'manifest.json',encoded(m))
    return m


def load_snapshot(root):
    import pandas as pd
    root=Path(root)
    m=json.loads((root/'manifest.json').read_bytes())
    raw=(root/'inputs.jsonl.gz').read_bytes()
    if digest(raw)!=m['compressed_sha256']:
        raise ValueError('Snapshot integrity failure')
    data=gzip.decompress(raw)
    if digest(data)!=m['input_sha256']:
        raise ValueError('Snapshot input integrity failure')
    grouped={}
    for line in data.splitlines():
        symbol,ts,*values=json.loads(line)
        grouped.setdefault(symbol,[]).append([ts,*map(float.fromhex,values)])
    hist={s:pd.DataFrame(rows,columns=COLUMNS) for s,rows in grouped.items()}
    if canonical_history(hist)!=data:
        raise ValueError('Noncanonical snapshot')
    return hist,m
