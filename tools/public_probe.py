"""Three finite public GET probes. HTTP success, freshness and latency are separate facts."""
import argparse
import json
from pathlib import Path
import time
import httpx
from tools.public_capture import BASE
from tools.validation.archive import encoded, immutable, digest


def run(output):
    output = Path(output)
    rows=[]
    with httpx.Client(timeout=10,follow_redirects=False) as client:
        for index in range(3):
            start=time.monotonic();requested=time.time()
            row={'index':index,'requested_at':requested,'endpoint':BASE+'/api/v3/time'}
            try:
                with client.stream('GET',row['endpoint']) as response:
                    raw=b''
                    for chunk in response.iter_bytes():
                        raw+=chunk
                        if len(raw)>1_000_000 or time.monotonic()-start>12:
                            raise TimeoutError('Public probe budget exceeded')
                    row.update(status=response.status_code,received_at=time.time(),latency_s=time.monotonic()-start,
                               raw_sha256=digest(raw),raw_file=f'time-{index}.json')
                    immutable(output/row['raw_file'],raw)
                    payload=json.loads(raw) if response.status_code==200 else {}
                    row['clock_skew_s']=abs(payload['serverTime']/1000-row['received_at']) if 'serverTime' in payload else None
                    row['admissible']=(response.status_code==200 and row['latency_s']<=5
                                       and row['clock_skew_s'] is not None and row['clock_skew_s']<=5)
            except Exception as exc:
                row.update(error=type(exc).__name__,latency_s=time.monotonic()-start,admissible=False)
            rows.append(row)
    result={'schema':'sentinelle.public-probe/v1','samples':rows,'p99_s':None,
            'note':'Three samples cannot establish P99, persistent availability or prospective performance.'}
    immutable(output/'probes.json',encoded(result))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(run(parser.parse_args().output)))
