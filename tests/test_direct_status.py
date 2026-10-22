import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import collect
spec = importlib.util.spec_from_file_location('direct_monitor', ROOT/'deployment/shanghai_status_monitor.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def sample(at, status='operational'):
    return {'schemaVersion': 1, 'status': status,
            'checkedAt': collect.stamp(at), 'validUntil': collect.stamp(at+900)}


class DirectStatusTests(unittest.TestCase):
    def test_direct_observer_never_reads_status_website(self):
        requested = []
        def fetch(url):
            requested.append(url)
            return {'ok': True} if url.endswith('/health') else sample(2000)
        with patch.object(collect, 'fetch', fetch):
            state = {}
            self.assertEqual(monitor.direct_status(state, 2000, {'status': None})[0], 'operational')
        self.assertEqual(requested, ['https://api.tockett.work/health',
                                     'https://api.tockett.work/status-observation.json'])

    def test_publication_checks_live_delivery_as_well_as_old_app_copy(self):
        document = collect.publish({}, sample(2000), True, 2000)['document']
        with patch.object(collect, 'fetch', return_value=document) as fetch:
            self.assertEqual(monitor.check_publication(2000)[0], 'operational')
            self.assertEqual([c.args[0].split('?')[0] for c in fetch.call_args_list],
                             [monitor.DIRECT_URL, monitor.STATUS_URL])
        with patch.object(collect, 'fetch', side_effect=OSError('live delivery down')):
            self.assertEqual(monitor.check_publication(2000)[0], 'unavailable')

    def test_website_failure_does_not_turn_stock_service_red(self):
        sent = []
        with tempfile.TemporaryDirectory() as directory:
            for n in range(4):
                result = monitor.run({}, Path(directory), now=2000+n*60,
                    observer=lambda now: (True, sample(now)), decision={'status': None},
                    publication=('unavailable', 'expired_mirror'),
                    sender=lambda config, subject, body: sent.append((config, subject, body)))
                self.assertEqual(result['observed'], 'operational')
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0][0]['subject_prefix'], '[Tockett Status Page] ')
            self.assertNotIn('股票服务故障', sent[0][1])

    def test_direct_fault_confirmation_and_recovery_keep_original_rules(self):
        state = {}
        for now, expected in ((2000, 'degraded'), (2030, 'degraded'), (2060, 'outage')):
            self.assertEqual(monitor.direct_status(state, now, {'status': None}, lambda _: (False, None))[0], expected)
        for now in (2100, 2400, 2700):
            self.assertEqual(monitor.direct_status(state, now, {'status': None}, lambda at: (True, sample(at)))[0], 'degraded')
        self.assertEqual(monitor.direct_status(state, 3000, {'status': None}, lambda at: (True, sample(at)))[0], 'operational')

    def test_health_only_cannot_turn_green_and_source_expiry_is_not_extended(self):
        with patch.object(collect, 'fetch', side_effect=[{'ok': True}, sample(1000)]):
            reachable, observation = collect.observe(2000)
        self.assertTrue(reachable)
        self.assertIsNone(observation)
        self.assertEqual(monitor.direct_status({}, 2000, {'status': None}, lambda _: (reachable, observation))[0], 'unknown')
        state = {}
        monitor.direct_status(state, 2300, {'status': None}, lambda _: (True, sample(2000)))
        self.assertEqual(state['collector']['document']['validUntil'], collect.stamp(2900))

    def test_smtp_failure_does_not_hide_observation_from_grok_or_web(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def fail(*args):
                raise OSError('smtp failed')
            result = monitor.run({}, root/'private', now=2000,
                observer=lambda _: (True, sample(2000, 'outage')), decision={'status': None},
                public_dir=root/'public', sender=fail)
            self.assertTrue(result['mail_error'])
            state = json.loads((root/'private/state.json').read_text())
            public = json.loads((root/'public/status.json').read_text())
            self.assertEqual(state['last_observed'], 'outage')
            self.assertIsNone(state.get('notified'))
            self.assertEqual(public, state['collector']['document'])
            self.assertNotIn('notified', public)
            self.assertEqual((root/'public/status.json').stat().st_mode & 0o777, 0o644)

    def test_publication_failure_does_not_hide_service_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(monitor, 'write_public', side_effect=OSError('disk')):
                result = monitor.run({}, Path(directory), now=2000,
                    observer=lambda _: (True, sample(2000)), decision={'status': None},
                    public_dir=Path(directory)/'public', publication=('operational', 'fresh'))
            self.assertTrue(result['publication_error'])
            state = json.loads((Path(directory)/'state.json').read_text())
            self.assertEqual(state['last_observed'], 'operational')
            self.assertEqual(state['publication']['last_observed'], 'unavailable')

    def test_owner_stop_cannot_be_automatically_cleared(self):
        decision = {'status': 'suspended', 'effectiveAt': collect.stamp(1000),
                    'summary': {'zh': '暂停', 'en': 'Suspended'},
                    'detail': {'zh': '时间未定', 'en': 'No date'},
                    'scope': {'zh': '行情', 'en': 'Quotes'}}
        state = {}
        self.assertEqual(monitor.direct_status(state, 2000, decision, lambda _: (True, sample(2000)))[0], 'suspended')
        with self.assertRaises(ValueError):
            monitor.direct_status(state, 2060, {'status': 'typo'}, lambda _: (True, sample(2060)))

    def test_mirror_rejects_stale_or_malformed_document(self):
        state = collect.publish({}, sample(2000), True, 2000)
        value = state['document']
        self.assertEqual(collect.validate_public_document(value, 2000), value)
        for changed in ({'revision': True}, {'validUntil': collect.stamp(4000)},
                        {'summary': {}}, {'summary': 'invalid'}, {'checkedAt': None}):
            with self.assertRaises(ValueError):
                collect.validate_public_document(dict(value, **changed), 2000)
        with self.assertRaises(ValueError):
            collect.validate_public_document(value, 3000)


if __name__ == '__main__':
    unittest.main()
