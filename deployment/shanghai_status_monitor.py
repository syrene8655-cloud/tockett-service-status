#!/usr/bin/env python3
"""Shanghai directly observes Hong Kong; publication is a separate check."""
import argparse
import datetime as dt
import fcntl
import http.client
import ipaddress
import json
import os
from pathlib import Path
import smtplib
import ssl
import socket
import subprocess
import time
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from urllib.parse import urlsplit
import collect

STATUS_URL = 'https://status.tockett.work/status.json'
DIRECT_URL = 'https://shanghai-tencent.tail5ba9ea.ts.net/service-status/status.json'
PUBLIC_DIR = Path('/var/www/tockett-status')
DECISION = Path('/etc/tockett-status-alert/decision.json')
CONFIG = Path('/etc/website-security/monitor.json')
STATE_DIR = Path('/var/lib/tockett-status-alert')
MAX_BYTES = 65_536
STATES = {'operational', 'degraded', 'outage', 'unknown', 'suspended', 'discontinued'}
LABELS = {
    'operational': '股票状态恢复正常',
    'degraded': '股票服务有波动',
    'outage': '股票服务故障（红灯）',
    'unknown': '股票状态无法确认',
    'unavailable': '状态页不可用或数据已过期',
    'suspended': '股票服务已暂停',
    'discontinued': '股票服务已停止',
}


def epoch(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        raise ValueError('invalid_time')
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def classify(document, now):
    if not isinstance(document, dict) or document.get('schemaVersion') != 1:
        return 'unavailable', 'invalid_document'
    status = document.get('status')
    if status not in STATES:
        return 'unavailable', 'invalid_status'
    try:
        checked, until = epoch(document['checkedAt']), epoch(document['validUntil'])
    except (KeyError, TypeError, ValueError):
        return 'unavailable', 'invalid_time'
    if checked > now + 300 or until < checked or until > checked + 900:
        return 'unavailable', 'invalid_time_range'
    if status not in ('suspended', 'discontinued') and now >= until:
        return 'unavailable', 'expired'
    return status, 'public_status'


def direct_status(state, now, decision, observer=collect.observe):
    reachable, observation = observer(now)
    collector = collect.publish(state.get('collector', {}), observation, reachable, now, decision)
    state.update(collector=collector, observer='shanghai-direct-v1')
    return classify(collector['document'], now)[0], 'direct_hong_kong'


def write_public(state, directory):
    """Only consumer-safe documents go to nginx; private state stays private."""
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    for name, value in (('status.json', state['collector']['document']),
                        ('history.json', state['collector'].get('history', []))):
        path = directory / name
        temporary = path.with_suffix('.tmp')
        with temporary.open('w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


def fetch_live_delivery(now):
    """Use public Funnel ingress, not this node's split-DNS private address.

    Keep the original hostname for both SNI and certificate verification.
    No system DNS, routing, TLS policy or other site's nginx is changed.
    """
    url = urlsplit(DIRECT_URL)
    host = url.hostname
    result = subprocess.run(['/bin/dig', '+short', '+time=2', '+tries=1',
                             '@119.29.29.29', host, 'A'],
                            capture_output=True, text=True, timeout=3)
    if result.returncode or len(result.stdout) > 4096:
        raise ValueError('PUBLIC_DNS_FAILED')
    addresses = []
    for line in result.stdout.splitlines():
        try:
            address = ipaddress.ip_address(line)
        except ValueError:
            continue
        if address.version == 4 and address.is_global:
            addresses.append(str(address))
    if not addresses:
        raise ValueError('PUBLIC_INGRESS_REQUIRED')
    context = ssl.create_default_context()
    connection = http.client.HTTPSConnection(host, timeout=5, context=context)
    try:
        raw = socket.create_connection((addresses[int(now // 60) % len(addresses)], 443), timeout=5)
        try:
            connection.sock = context.wrap_socket(raw, server_hostname=host)
        except Exception:
            raw.close()
            raise
        connection.request('GET', url.path + '?publication=' + str(int(now)),
                           headers={'Accept': 'application/json', 'User-Agent': 'Tockett-Status-Alert/2'})
        response = connection.getresponse()
        body = response.read(MAX_BYTES + 1)
        if response.status != 200 or len(body) > MAX_BYTES:
            raise ValueError('INVALID_PUBLIC_DELIVERY')
        return json.loads(body)
    finally:
        connection.close()


def check_publication(now):
    """Check live delivery and the compatibility copy, never stock health."""
    try:
        collect.validate_public_document(fetch_live_delivery(now), now)
    except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException, subprocess.SubprocessError):
        return 'unavailable', 'live_delivery_unavailable_or_expired'
    try:
        document = collect.fetch(STATUS_URL + '?publication=' + str(int(now)))
        collect.validate_public_document(document, now)
    except (OSError, ValueError, KeyError, TypeError):
        return 'unavailable', 'compatibility_copy_unavailable_or_expired'
    return 'operational', 'live_and_compatibility_copy_fresh'


def next_notice(state, observed):
    """Red/explicit unknown are immediate; failures, yellow and recovery need 3 checks."""
    if state.get('candidate') == observed:
        state['count'] = state.get('count', 0) + 1
    else:
        state['candidate'], state['count'] = observed, 1
    if state.get('notified') is None and observed == 'operational':
        state['notified'] = observed
        return None
    if observed == state.get('notified'):
        return None
    required = 1 if observed in ('outage', 'unknown', 'suspended', 'discontinued') else 3
    return observed if state['count'] >= required else None


def send_mail(config, subject, body):
    settings = json.loads(Path(config['smtp_file']).read_text())
    message = EmailMessage()
    message['Subject'] = config.get('subject_prefix', '[Tockett Status] ') + subject
    message['From'] = formataddr(('Tockett 状态监控', parseaddr(settings['SNIPPOP_SMTP_FROM'])[1]))
    message['To'] = config['recipient']
    message.set_content(body)
    with smtplib.SMTP_SSL(settings['SNIPPOP_SMTP_HOST'], int(settings.get('SNIPPOP_SMTP_PORT', '465')),
                         timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(settings['SNIPPOP_SMTP_USER'], settings['SNIPPOP_SMTP_PASSWORD'])
        if smtp.send_message(message):
            raise RuntimeError('recipient_refused')


def atomic_state(path, state):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump(state, output, sort_keys=True)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def run(config, state_dir=STATE_DIR, *, observation=None, now=None, sender=send_mail,
        decision=None, public_dir=None, observer=collect.observe, publication=None):
    now = time.time() if now is None else now
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_dir / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path = state_dir / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if observation is None:
            observed, reason = direct_status(state, now, decision, observer)
        else:
            observed, reason = observation
        notice = next_notice(state, observed)
        state.update(last_observed=observed, last_reason=reason,
                     last_check_at=dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat())
        # Persist direct observations even when SMTP or public delivery fails.
        # Grok reads this state independently of email delivery.
        atomic_state(state_path, state)
        publication_error = False
        if public_dir is not None:
            try:
                write_public(state, public_dir)
            except OSError:
                publication_error = True
        mail_error = False
        if notice:
            subject = LABELS[notice]
            body = (subject + '\n\n公开状态：' + STATUS_URL + '\n上海监控时间（UTC）：' +
                    state['last_check_at'] + '\n检查结果：' + reason +
                    '\n\n报警依据：上海直接检查香港健康接口和新鲜行情巡检结果；不依赖网页发布。')
            try:
                sender(config, subject, body)
                state['notified'] = notice
                state['last_notice_at'] = state['last_check_at']
            except (OSError, smtplib.SMTPException, RuntimeError):
                mail_error = True
        if publication is not None:
            if callable(publication):
                publication = publication(now)
            page = state.setdefault('publication', {})
            page_observed, page_reason = ('unavailable', 'local_publication_failed') if publication_error else publication
            page_notice = next_notice(page, page_observed)
            page.update(last_observed=page_observed, last_reason=page_reason,
                        last_check_at=state['last_check_at'])
            if page_notice:
                subject = ('状态发布恢复' if page_notice == 'operational' else
                           '旧版App状态副本更新延迟' if page_reason.startswith('compatibility_copy') else
                           '状态网页数据读取异常')
                # Separate subject: this is not a stock outage or repair request.
                try:
                    page_config = dict(config, subject_prefix='[Tockett Status Page] ')
                    sender(page_config, subject, subject + '\n\n' + STATUS_URL +
                           '\n检查结果：' + page_reason + '\n股票检查结果：' + observed +
                           '\n网页发布异常不代表股票服务故障。')
                    page['notified'] = page_notice
                except (OSError, smtplib.SMTPException, RuntimeError):
                    mail_error = True
        atomic_state(state_path, state)
        return {'observed': observed, 'reason': reason, 'notice': notice,
                'notified': state.get('notified'), 'mail_error': mail_error,
                'publication_error': publication_error}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-mail', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(CONFIG.read_text())
    if args.test_mail:
        send_mail(config, '监控测试通知', '这是上海服务器状态监控的投递测试；没有发生模拟或真实故障。\n' + STATUS_URL)
        print('test_mail_accepted')
        return
    decision = json.loads(DECISION.read_text())
    # A malformed owner decision must never silently resume a suspended service.
    if not isinstance(decision, dict) or decision.get('status', 'missing') not in (None, 'suspended', 'discontinued'):
        raise ValueError('INVALID_OWNER_DECISION')
    print(json.dumps(run(config, decision=decision, public_dir=PUBLIC_DIR,
                         publication=check_publication), sort_keys=True))


if __name__ == '__main__':
    main()
