import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import backup_trigger
import render_cached


class PublicationSplitTests(unittest.TestCase):
    def test_existing_static_or_mirror_run_prevents_duplicate_dispatch(self):
        for name in backup_trigger.WORKFLOWS:
            for status in ('queued','in_progress','waiting','pending'):
                result=subprocess.CompletedProcess([],0,json.dumps([{'workflowName':name,'status':status}]),'')
                with patch.object(backup_trigger.subprocess,'run',return_value=result):
                    self.assertTrue(backup_trigger.publication_pending())
        with patch.object(backup_trigger.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'[{"workflowName":"unrelated","status":"in_progress"}]','')):
            self.assertFalse(backup_trigger.publication_pending())

    def test_unavailable_run_list_does_not_blindly_dispatch(self):
        with patch.object(backup_trigger.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','private error')):
            with self.assertRaises(RuntimeError):backup_trigger.publication_pending()

    def test_static_deployment_preserves_expired_observation_without_a_live_fetch(self):
        source=Path(__file__).resolve().parents[1]/'state.json'
        state=json.loads(source.read_text())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'state.json').write_text(json.dumps(state))
            with patch('collect.fetch',side_effect=AssertionError('static publication cannot fetch')):
                render_cached.render(root)
            self.assertEqual(json.loads((root/'public/status.json').read_text()),state['document'])
            self.assertEqual(json.loads((root/'public/history.json').read_text()),state['history'])
