"""Local diagnostic evidence. This is a provenance seal, not remote attestation."""
from __future__ import annotations
import hashlib
import hmac
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
import xml.etree.ElementTree as ET

from tools.validation.archive import digest, encoded

PACKAGES = ('fastapi', 'uvicorn', 'websockets', 'httpx', 'ccxt', 'numpy', 'pandas', 'scikit-learn', 'joblib', 'feedparser', 'telethon')


def runtime():
    return {'python': platform.python_version(), 'implementation': platform.python_implementation(),
            'machine': platform.machine(), 'host': digest(platform.node().encode()),
            'system': platform.system(), 'release': platform.release(),
            'interpreter_sha256': digest(Path(sys.executable).read_bytes()),
            'dependencies': {p: version(p) for p in PACKAGES}}


def junit_summary(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    result = {'tests': len(cases), 'failures': len(list(root.iter('failure'))),
              'errors': len(list(root.iter('error'))), 'skipped': len(list(root.iter('skipped')))}
    if not cases or any(result[k] for k in ('failures', 'errors', 'skipped')):
        raise ValueError('Windows tests missing, failed or skipped')
    return result


def validate_scope(path):
    cases = list(ET.parse(path).getroot().iter('testcase'))
    required = {'test_failed_tick_keeps_failure_not_stopped', 'test_second_cli_cannot_change_owner_metadata',
                'test_rehashed_derived_book_cannot_disagree_with_raw_http',
                'test_full_get_archive_model_paper_journal_report_and_repeat'}
    if len(cases) < 519 or not required <= {c.get('name') for c in cases}:
        raise ValueError('Incomplete Windows suite; full repository tests required')


def _key(*, create=False):
    path = Path.home() / '.sentinelle' / 'diagnostic.key'
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as f:
                f.write(os.urandom(32))
    return path.read_bytes()


def seal(report_path, junit_path):
    from tools.diagnose_windows import system_info
    if not system_info()['windows_actually_tested']:
        raise ValueError('Real Windows 11 required to seal diagnostic')
    path, junit = Path(report_path), Path(junit_path).resolve()
    report = json.loads(path.read_bytes())
    validate_scope(junit)
    report['validation'] = {'runtime': runtime(), 'junit': str(junit),
                            'junit_sha256': digest(junit.read_bytes()), 'summary': junit_summary(junit)}
    report.pop('local_seal', None)
    report['local_seal'] = hmac.new(_key(create=True), encoded(report), hashlib.sha256).hexdigest()
    path.write_bytes(encoded(report))
    return report


def verify(report):
    clean = dict(report)
    signature = clean.pop('local_seal', '')
    try:
        expected = hmac.new(_key(), encoded(clean), hashlib.sha256).hexdigest()
        evidence = report['validation']
        validate_scope(evidence['junit'])
        junit = Path(evidence['junit'])
        ok = (hmac.compare_digest(signature, expected) and evidence['runtime'] == runtime()
              and evidence['junit_sha256'] == digest(junit.read_bytes())
              and evidence['summary'] == junit_summary(junit))
    except (OSError, KeyError, ValueError):
        ok = False
    if not ok:
        raise ValueError('Windows evidence missing or runtime/tests changed; rerun diagnostic on this PC')
