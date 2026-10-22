"""Independent public status publisher. Uses no server or phone credentials."""
import datetime as dt
import json
import ssl
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
STATES = {'operational', 'degraded', 'outage', 'suspended', 'discontinued', 'unknown'}
TITLES = {
    'operational': ('当前运行正常', 'Currently operating normally'),
    'degraded': ('服务有波动', 'Service disruptions'),
    'outage': ('服务故障', 'Service outage'),
    'suspended': ('服务已暂停', 'Service suspended'),
    'discontinued': ('服务已停止', 'Service discontinued'),
    'unknown': ('无法确认最新状态', 'Latest status unavailable'),
}
DETAILS = {
    'operational': ('最近有效检查未发现股票行情服务异常。检查覆盖代表性样本，不保证每只股票始终准确或持续更新。', 'Recent valid checks found no stock quote service issues. Checks cover representative samples and do not guarantee that every quote is always accurate or continuously updated.'),
    'degraded': ('部分行情或服务连接存在波动，或正在观察恢复情况。没有有效报价时显示横线；目前没有确定的恢复时间。', 'Some quotes or service connections are disrupted, or recovery is being monitored. Dashes indicate no valid quote. There is no confirmed recovery time.'),
    'outage': ('已确认股票行情服务存在故障，报价可能无法获取。当前没有确定的恢复时间；处理进展会在此页更新。', 'A stock quote service fault has been confirmed, and quotes may be unavailable. There is no confirmed recovery time; progress will be posted here.'),
    'unknown': ('当前未取得足够新鲜的有效检查结果。无法确认不等于正常，也不等于停止服务。请稍后刷新或联系反馈。', 'There are not enough fresh, valid observations to confirm the current status. This does not mean the service is operating normally or has stopped. Refresh later or contact support.'),
}


def text(zh, en):
    return {'zh': zh, 'en': en}


def stamp(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def epoch(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return parsed.timestamp()


def fetch(url):
    request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Tockett-Public-Status/1'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise ValueError('REDIRECT_REJECTED')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    with opener.open(request, timeout=8) as response:
        data = response.read(65_537)
        if response.status != 200 or len(data) > 65_536:
            raise ValueError('INVALID_RESPONSE')
        return json.loads(data)


def validate_observation(value, now):
    if not isinstance(value, dict):
        raise ValueError('INVALID_OBSERVATION')
    checked, valid = epoch(value['checkedAt']), epoch(value['validUntil'])
    if value.get('schemaVersion') != 1 or value.get('status') not in STATES - {'suspended', 'discontinued'}:
        raise ValueError('INVALID_OBSERVATION')
    if not checked <= now + 300 or not checked <= valid <= checked + 900:
        raise ValueError('INVALID_OBSERVATION_TIME')
    return value if now < valid else None


def publish(state, obs, reachable, now, decision=None):
    """Pure transition function, including confirmation and recovery hysteresis."""
    if decision is not None and (not isinstance(decision, dict) or 'status' not in decision or decision['status'] not in (None, 'suspended', 'discontinued')):
        raise ValueError('INVALID_OWNER_DECISION')
    old = state.get('document', {})
    if not reachable:
        if now - state.get('lastFailureAt', 0) >= 60:
            state['failures'] = state.get('failures', 0) + 1
            state['lastFailureAt'] = now
        state.pop('healthySince', None)
        state['successes'] = 0
        if state['failures'] >= 2:
            state['externalOutage'] = True
        current = 'outage' if state.get('externalOutage') else 'degraded'
        checked, until = now, now + 900
    elif obs is None:
        current, checked, until = 'unknown', now, now + 900
        state['successes'] = 0
        state.pop('healthySince', None)
    else:
        state['failures'] = 0
        current, checked, until = obs['status'], epoch(obs['checkedAt']), epoch(obs['validUntil'])
        if state.get('externalOutage') and current == 'operational':
            # A delayed runner must not count an old observation twice.
            if checked > state.get('lastSuccessObservation', 0):
                if now - state.get('lastSuccessAt', now) > 600:
                    state.pop('healthySince', None)
                    state['successes'] = 0
                state.setdefault('healthySince', now)
                state['successes'] = state.get('successes', 0) + 1
                state['lastSuccessAt'] = now
                state['lastSuccessObservation'] = checked
            if state.get('successes', 0) >= 3 and now - state.get('healthySince', now) >= 900:
                state['externalOutage'] = False
            else:
                current = 'degraded'
        elif current != 'operational':
            state['successes'] = 0
            state.pop('healthySince', None)
    summary, detail = text(*TITLES[current]), text(*DETAILS[current])
    scope = text('部分股票行情及相关更新' if current == 'degraded' else '股票行情及相关更新', 'Some stock quotes and related updates' if current == 'degraded' else 'Stock quotes and related updates')
    if not reachable and current == 'degraded':
        detail = text('外部连接检查暂未成功，正在等待复核。目前尚不能确定行情是否受影响。', 'An external connection check failed and is awaiting confirmation. The impact on quotes has not yet been confirmed.')
        scope = text('股票服务连接观测；影响尚未确认', 'Stock service connection checks; impact not yet confirmed')
    if decision and decision.get('status') in ('suspended', 'discontinued'):
        for key in ('summary', 'detail', 'scope'):
            if not all(isinstance(decision.get(key, {}).get(lang), str) and decision[key][lang].strip() and len(decision[key][lang]) <= 8000 for lang in ('zh', 'en')):
                raise ValueError('DECISION_REQUIRES_BILINGUAL_COPY')
        current, summary, detail, scope = decision['status'], decision['summary'], decision['detail'], decision['scope']
        checked, until = epoch(decision['effectiveAt']), epoch(decision['effectiveAt']) + 900
        if checked > now + 300:
            raise ValueError('FUTURE_DECISION')
    changed = any(old.get(k) != v for k, v in {'status': current, 'summary': summary, 'detail': detail, 'scope': scope}.items())
    updated = stamp(now) if changed else old.get('updatedAt', stamp(now))
    document = {'schemaVersion': 1, 'revision': old.get('revision', 0) + 1, 'status': current,
                'checkedAt': stamp(checked), 'validUntil': stamp(until), 'updatedAt': updated,
                'summary': summary, 'detail': detail, 'scope': scope}
    if len(json.dumps(document, ensure_ascii=False).encode()) > 65_536:
        raise ValueError('ANNOUNCEMENT_TOO_LARGE')
    if changed:
        state['history'] = (state.get('history', []) + [document])[-30:]
    state['document'] = document
    return state


def observe(now):
    """Direct Hong Kong checks, independent of any website publisher."""
    reachable, obs = False, None
    try:
        # Health alone never proves working quotes. The observation contains
        # only exported maintenance verdicts, not credentials or diagnostics.
        health = fetch('https://api.tockett.work/health')
        reachable = isinstance(health, dict) and health.get('ok') is True
    except (OSError, ValueError, KeyError):
        pass
    if reachable:
        try:
            obs = validate_observation(fetch('https://api.tockett.work/status-observation.json'), now)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return reachable, obs


def main():
    # GitHub only mirrors Shanghai. It never produces an independent verdict.
    state_path = ROOT / 'state.json'
    document = fetch('https://shanghai-tencent.tail5ba9ea.ts.net/service-status/status.json')
    validate_public_document(document, time.time())
    history = fetch('https://shanghai-tencent.tail5ba9ea.ts.net/service-status/history.json')
    if not isinstance(history, list) or len(history) > 30:
        raise ValueError('INVALID_HISTORY')
    for item in history:
        validate_public_document(item, time.time(), allow_expired=True)
    state_path.write_text(json.dumps({'document': document, 'history': history}, ensure_ascii=False, indent=2) + '\n')
    public = ROOT / 'public'
    public.mkdir(exist_ok=True)
    (public / 'status.json').write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    (public / 'history.json').write_text(json.dumps(history, ensure_ascii=False, indent=2) + '\n')
    print('Mirrored Shanghai status:', document['status'])


def validate_public_document(value, now, allow_expired=False):
    if not isinstance(value, dict) or value.get('schemaVersion') != 1 or value.get('status') not in STATES:
        raise ValueError('INVALID_PUBLIC_DOCUMENT')
    revision = value.get('revision')
    if type(revision) is not int or not 0 < revision <= 9007199254740991:
        raise ValueError('INVALID_REVISION')
    checked, until, updated = (epoch(value[k]) for k in ('checkedAt', 'validUntil', 'updatedAt'))
    if checked > now + 300 or updated > now + 300 or not checked <= until <= checked + 900:
        raise ValueError('INVALID_PUBLIC_TIME')
    if not allow_expired and value['status'] not in ('suspended', 'discontinued') and now >= until:
        raise ValueError('EXPIRED_PUBLIC_DOCUMENT')
    for key in ('summary', 'detail', 'scope'):
        if not all(isinstance(value.get(key, {}).get(lang), str) and 0 < len(value[key][lang]) <= 8000 for lang in ('zh', 'en')):
            raise ValueError('INVALID_PUBLIC_COPY')
    return value


if __name__ == '__main__':
    main()
