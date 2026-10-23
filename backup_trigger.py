"""Mac backup trigger for the independent GitHub Pages status publisher.

It does not decide or publish a service state. GitHub Actions still performs
the external check and deploys the public status document.
"""
import datetime as dt
import fcntl
import json
import subprocess
import time
import urllib.request
from pathlib import Path

STATUS_URL = 'https://status.tockett.work/status.json'
GH = '/Users/syrene/.local/bin/gh'
STATE_DIR = Path('/Users/syrene/Library/Application Support/TockettStatusTrigger')
MAX_BYTES = 65_536
TRIGGER_BEFORE_EXPIRY = 8 * 60
MIN_DISPATCH_GAP = 4 * 60
WORKFLOWS = {'Publish service status', 'Refresh service status compatibility data'}


def publication_pending():
    result=subprocess.run([GH,'run','list','--repo','syrene8655-cloud/tockett-service-status',
                           '--limit','30','--json','workflowName,status'],
                          capture_output=True,text=True,timeout=20)
    if result.returncode:raise RuntimeError('Cannot confirm existing publication runs')
    runs=json.loads(result.stdout)
    if not isinstance(runs,list):raise ValueError('Invalid workflow list')
    return any(row.get('workflowName') in WORKFLOWS and row.get('status')!='completed' for row in runs)


def epoch(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('invalid time')
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def should_dispatch(document, now):
    if not isinstance(document, dict) or document.get('schemaVersion') != 1:
        return True
    if document.get('status') in ('suspended', 'discontinued'):
        return False
    if document.get('status') not in ('operational', 'degraded', 'outage', 'unknown'):
        return True
    try:
        checked, until = epoch(document['checkedAt']), epoch(document['validUntil'])
    except (KeyError, TypeError, ValueError):
        return True
    if checked > now + 300 or until < checked or until - checked > 900:
        return True
    return until - now <= TRIGGER_BEFORE_EXPIRY


def public_document(now):
    request = urllib.request.Request(STATUS_URL + '?backup=' + str(int(now)),
                                     headers={'Accept': 'application/json', 'User-Agent': 'Tockett-Status-Backup/1'})
    with urllib.request.urlopen(request, timeout=8) as response:
        data = response.read(MAX_BYTES + 1)
        if response.status != 200 or len(data) > MAX_BYTES:
            raise ValueError('invalid status response')
    return json.loads(data)


def main():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE_DIR / 'trigger.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = time.time()
        try:
            needed = should_dispatch(public_document(now), now)
        except (OSError, ValueError, json.JSONDecodeError):
            needed = True
        if not needed:
            return
        last_path = STATE_DIR / 'last-dispatch.json'
        try:
            last = json.loads(last_path.read_text()).get('at', 0)
        except (OSError, ValueError):
            last = 0
        if now - last < MIN_DISPATCH_GAP:
            return
        if publication_pending():
            print('Existing status publication is pending; no duplicate dispatch',flush=True)
            return
        result = subprocess.run([GH, 'workflow', 'run', 'mirror.yml', '--repo',
                                 'syrene8655-cloud/tockett-service-status', '--ref', 'main'],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('GitHub workflow dispatch failed: ' + result.stderr.strip()[:300])
        last_path.write_text(json.dumps({'at': now}) + '\n')
        last_path.chmod(0o600)
        print('Requested independent status publication at', dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(), flush=True)


if __name__ == '__main__':
    main()
