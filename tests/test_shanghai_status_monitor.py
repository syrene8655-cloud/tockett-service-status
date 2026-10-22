import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'deployment' / 'shanghai_status_monitor.py'
sys.path.insert(0, str(SCRIPT.parents[1]))
spec = importlib.util.spec_from_file_location('shanghai_status_monitor', SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class ShanghaiStatusMonitorTests(unittest.TestCase):
    def test_freshness_and_owner_decision(self):
        now = 1_792_560_000
        doc = {'schemaVersion': 1, 'status': 'operational',
               'checkedAt': monitor.dt.datetime.fromtimestamp(now - 60, monitor.dt.timezone.utc).isoformat().replace('+00:00', 'Z'),
               'validUntil': monitor.dt.datetime.fromtimestamp(now + 840, monitor.dt.timezone.utc).isoformat().replace('+00:00', 'Z')}
        self.assertEqual(monitor.classify(doc, now)[0], 'operational')
        self.assertEqual(monitor.classify(doc, now + 841), ('unavailable', 'expired'))
        doc['status'] = 'suspended'
        self.assertEqual(monitor.classify(doc, now + 841)[0], 'suspended')

    def test_red_unknown_failure_and_recovery_notices(self):
        sent = []
        with tempfile.TemporaryDirectory() as directory:
            def check(status, minute):
                return monitor.run({'recipient': 'test@example.invalid'}, Path(directory),
                                   observation=(status, 'test'), now=minute * 60,
                                   sender=lambda _config, subject, _body: sent.append(subject))
            self.assertIsNone(check('operational', 1)['notice'])
            self.assertEqual(check('outage', 2)['notice'], 'outage')
            self.assertEqual(check('unknown', 3)['notice'], 'unknown')
            self.assertIsNone(check('unavailable', 4)['notice'])
            self.assertIsNone(check('unavailable', 5)['notice'])
            self.assertEqual(check('unavailable', 6)['notice'], 'unavailable')
            self.assertIsNone(check('operational', 7)['notice'])
            self.assertIsNone(check('operational', 8)['notice'])
            self.assertEqual(check('operational', 9)['notice'], 'operational')
        self.assertEqual(len(sent), 4)

    def test_failed_mail_retries_without_claiming_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            monitor.run({}, path, observation=('operational', 'test'), now=60,
                        sender=lambda *_: None)
            def fail(*_):
                raise OSError('mail unavailable')
            failed = monitor.run({}, path, observation=('outage', 'test'), now=120, sender=fail)
            self.assertTrue(failed['mail_error'])
            self.assertEqual(__import__('json').loads((path/'state.json').read_text())['last_observed'], 'outage')
            self.assertEqual(monitor.run({}, path, observation=('outage', 'test'), now=180,
                                         sender=lambda *_: None)['notice'], 'outage')


if __name__ == '__main__':
    unittest.main()
