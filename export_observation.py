"""Export only consumer-safe facts; never copy maintenance diagnostics publicly."""
import datetime as dt
import json
import time
from pathlib import Path


def stamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def observation(state, now):
    checked = state.get('lastFullCheckedAt', 0)
    if not isinstance(checked, (int, float)) or not 0 < checked <= now + 300:
        checked = 0
    incidents = [i for i in state.get('incidents', {}).values() if i.get('status') != 'resolved']
    codes = {i.get('code', '') for i in incidents}
    pending = {code for code, value in state.get('streaks', {}).items() if value.get('bad', 0) > 0}
    major = {'PUBLIC_UNAVAILABLE', 'AUTH_BOUNDARY', 'ORIGIN_HEALTH', 'ORIGIN_AUTH', 'SERVICE_CLOCK_STOCK'}
    if not checked or now - checked >= 900:
        status = 'unknown'
    elif codes & major:
        status = 'outage'
    elif codes == {'REMOTE_PROBE_FAILED'}:
        status = 'unknown'
    elif codes or pending:
        status = 'degraded'
    else:
        status = 'operational'
    return {'schemaVersion': 1, 'status': status, 'checkedAt': stamp(checked),
            'validUntil': stamp(checked + 900), 'scope': 'partial' if status == 'degraded' else 'all'}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = observation(json.loads(args.state.read_text()), time.time())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix('.tmp')
    temporary.write_text(json.dumps(result) + '\n')
    temporary.chmod(0o644)
    temporary.replace(args.out)
