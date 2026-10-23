import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backup_trigger import should_dispatch


class BackupTriggerTests(unittest.TestCase):
    def test_fresh_record_waits_and_near_expiry_dispatches(self):
        document = {'schemaVersion': 1, 'status': 'operational',
                    'checkedAt': '2026-10-21T10:00:00Z', 'validUntil': '2026-10-21T10:15:00Z'}
        self.assertFalse(should_dispatch(document, 1792576800))
        self.assertTrue(should_dispatch(document, 1792577220))

    def test_owner_decision_is_never_replaced_by_backup_trigger(self):
        document = {'schemaVersion': 1, 'status': 'suspended',
                    'checkedAt': '2026-10-21T10:00:00Z', 'validUntil': '2026-10-21T10:15:00Z'}
        self.assertFalse(should_dispatch(document, 1792584000))

    def test_invalid_or_missing_record_dispatches(self):
        self.assertTrue(should_dispatch(None, 1792576800))
        self.assertTrue(should_dispatch({'schemaVersion': 1, 'status': 'operational'}, 1792576800))


if __name__ == '__main__':
    unittest.main()
