import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collect import publish, stamp, validate_observation
from export_observation import observation


def obs(at, status='operational'):
    return {'schemaVersion': 1, 'status': status, 'checkedAt': stamp(at), 'validUntil': stamp(at + 900), 'scope': 'all'}


class StatusTests(unittest.TestCase):
    def test_health_without_quote_observation_is_unknown(self):
        self.assertEqual(publish({}, None, True, 2000)['document']['status'], 'unknown')

    def test_two_external_failures_need_sixty_seconds(self):
        state = publish({}, None, False, 2000)
        self.assertEqual(state['document']['status'], 'degraded')
        state = publish(state, None, False, 2030)
        self.assertEqual(state['document']['status'], 'degraded')
        self.assertEqual(publish(state, None, False, 2060)['document']['status'], 'outage')

    def test_recovery_needs_three_fresh_samples_and_fifteen_minutes(self):
        state = publish(publish({}, None, False, 2000), None, False, 2060)
        for at in (2100, 2400, 2700):
            state = publish(state, obs(at), True, at)
            self.assertEqual(state['document']['status'], 'degraded')
        state = publish(state, obs(3000), True, 3000)
        self.assertEqual(state['document']['status'], 'operational')

    def test_replaying_sample_never_counts_as_recovery(self):
        state = publish(publish({}, None, False, 2000), None, False, 2060)
        for at in (2100, 2400, 2700):
            state = publish(state, obs(2100), True, at)
        self.assertEqual(state['successes'], 1)

    def test_manual_stop_is_not_overwritten_by_healthy_probe(self):
        decision = {'status': 'suspended', 'effectiveAt': stamp(1000),
                    'summary': {'zh': '暂停', 'en': 'Suspended'},
                    'detail': {'zh': '恢复时间未定', 'en': 'No recovery date'},
                    'scope': {'zh': '部分行情', 'en': 'Some quotes'}}
        state = publish({}, obs(2000), True, 2000, decision)
        self.assertEqual(state['document']['status'], 'suspended')
        self.assertEqual(state['document']['checkedAt'], stamp(1000))
        self.assertEqual(publish(state, obs(4000), True, 4000, decision)['document']['status'], 'suspended')

    def test_partial_outage_is_not_a_total_failure(self):
        value = observation({'lastFullCheckedAt': 2000, 'incidents': {'q': {'status': 'open', 'code': 'QUOTE_AAPL'}}}, 2010)
        self.assertEqual(value['status'], 'degraded')

    def test_stale_or_missing_monitor_never_means_green(self):
        self.assertEqual(observation({}, 2000)['status'], 'unknown')
        self.assertEqual(observation({'lastFullCheckedAt': 1000}, 2000)['status'], 'unknown')
        self.assertIsNone(validate_observation(obs(1000), 2000))

    def test_internal_probe_failure_is_not_proof_of_public_outage(self):
        value = observation({'lastFullCheckedAt': 2000, 'incidents': {'x': {'status': 'open', 'code': 'REMOTE_PROBE_FAILED'}}}, 2010)
        self.assertEqual(value['status'], 'unknown')

    def test_exporter_allowlist_excludes_private_diagnostics(self):
        value = observation({'lastFullCheckedAt': 2000, 'secret': 'never-export', 'incidents': {}}, 2010)
        self.assertEqual(set(value), {'schemaVersion', 'status', 'checkedAt', 'validUntil', 'scope'})
        self.assertNotIn('never-export', str(value))

    def test_future_times_and_stop_from_automatic_probe_are_rejected(self):
        for value in (obs(2500), obs(2000, 'discontinued')):
            with self.assertRaises(ValueError): validate_observation(value, 2000)

    def test_observation_age_is_not_extended_on_fetch(self):
        state = publish({}, obs(2000), True, 2300)
        self.assertEqual(state['document']['checkedAt'], stamp(2000))
        self.assertEqual(state['document']['validUntil'], stamp(2900))

    def test_revision_increases_history_only_tracks_meaningful_changes(self):
        state = publish({}, obs(2000), True, 2000)
        state = publish(state, obs(2300), True, 2300)
        self.assertEqual(state['document']['revision'], 2)
        self.assertEqual(len(state['history']), 1)

    def test_invalid_stop_decision_cannot_silently_resume_service(self):
        for decision in ({'status': 'suspened'}, {}, {'status': 'operational'}):
            with self.assertRaises(ValueError): publish({}, obs(2000), True, 2000, decision)

    def test_one_failed_connection_does_not_claim_quote_impact(self):
        state = publish({}, None, False, 2000)
        self.assertIn('尚不能确定', state['document']['detail']['zh'])


if __name__ == '__main__': unittest.main()
