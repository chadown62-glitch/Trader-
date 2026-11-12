import json
from decimal import Decimal

import pytest

from tools.collect_public import download
from tools.validation.archive import STEP_MS, canonical_rows, encoded, load_archive


def rows(start, n=2):
    return [[start+i*STEP_MS,'100.000','101','99','100.5','20',start+(i+1)*STEP_MS-1] for i in range(n)]


def test_resume_after_interruption_and_verify_every_byte(tmp_path):
    calls=[]
    def fetch(symbol, start, end):
        calls.append(start)
        if len(calls)==2:
            raise OSError('interrupted')
        return encoded(rows(start))
    with pytest.raises(OSError):
        download(tmp_path,'BTC/EUR',0,4*STEP_MS,fetch,observed_at_ms=5*STEP_MS)
    assert (tmp_path/'raw/0.json').exists()
    download(tmp_path,'BTC/EUR',0,4*STEP_MS,lambda s,a,b:encoded(rows(a)))
    frame,m=load_archive(tmp_path)
    assert len(frame)==4 and m['quality']['missing_count']==0
    assert frame.ts.tolist()==list(range(0,4*STEP_MS,STEP_MS))
    assert download(tmp_path,'BTC/EUR',0,4*STEP_MS,lambda *args:pytest.fail('must not refetch'))==m
    with pytest.raises(ValueError,match='parameters differ'):
        download(tmp_path,'BTC/EUR',0,6*STEP_MS,fetch)
    (tmp_path/'raw/0.json').write_bytes(b'[]')
    with pytest.raises(ValueError,match='Raw page integrity'):
        load_archive(tmp_path)


@pytest.mark.parametrize('kind', ['gap','duplicate','reverse','invalid','nonfinite'])
def test_bad_public_data_never_becomes_a_complete_archive(tmp_path, kind):
    batch=rows(0,4)
    if kind=='gap': del batch[1]
    elif kind=='duplicate':batch[1]=batch[0]
    elif kind=='reverse':batch.reverse()
    elif kind=='invalid':batch[0][2]='90'
    else:batch[0][4]='NaN'
    with pytest.raises(ValueError):
        download(tmp_path,'ETH/EUR',0,4*STEP_MS,lambda *a:encoded(batch),observed_at_ms=5*STEP_MS)
    assert not (tmp_path/'manifest.json').exists()
    assert (tmp_path/'raw/0.json').read_bytes()==encoded(batch)


def test_incomplete_requested_period_refused_and_decimal_format_canonical(tmp_path):
    with pytest.raises(ValueError, match='incomplete'):
        download(tmp_path,'SOL/EUR',0,4*STEP_MS,lambda *a:None,observed_at_ms=3*STEP_MS+1)
    a=rows(0)
    b=rows(0);b[0][1]='1e2'
    assert canonical_rows(a)==canonical_rows(b)
    assert Decimal(canonical_rows(a)[0][1])==Decimal('100')


@pytest.mark.parametrize('target', ['candles.jsonl','manifest.json'])
def test_tampered_derived_file_or_manifest_is_refused(tmp_path,target):
    download(tmp_path,'SOL/EUR',0,2*STEP_MS,lambda *a:encoded(rows(0)),observed_at_ms=3*STEP_MS)
    path=tmp_path/target
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='integrity'):
        load_archive(tmp_path)


def test_protocol_is_immutable_and_forbids_historical_promotion(tmp_path):
    from tools.validation.historical import register
    root=tmp_path/'archives'
    for asset in ('BTC','ETH','SOL'):
        download(root/f'{asset}-EUR',f'{asset}/EUR',0,2*STEP_MS,lambda *a:encoded(rows(0)),observed_at_ms=3*STEP_MS)
    path=tmp_path/'protocol.json'
    p=register(root,path)
    assert p['promotion_allowed'] is False and p['independent_final'] is False
    assert p['sizes']==[500,1500] and p['initial_eur']==30
    assert len(p['assets'])==3
    path.write_text('user data')
    with pytest.raises(ValueError,match='already exists'):
        register(root,path)
    assert path.read_text()=='user data'


def test_decimal_canonicalization_does_not_depend_on_decimal_context():
    from decimal import localcontext
    from tools.validation.archive import decimal_text
    with localcontext() as context:
        context.prec=3
        assert decimal_text('123456.789000')=='123456.789'
    with pytest.raises(ValueError,match='Nonfinite'):
        decimal_text('1e9999')
