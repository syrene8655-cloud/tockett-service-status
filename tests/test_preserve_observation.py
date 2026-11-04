import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect
import preserve_observation as preserve


class PreserveObservation(unittest.TestCase):
    def result(self, code=0, text=''):
        return subprocess.CompletedProcess([], code, '', text)

    def test_remote_rejection_recovers_only_after_unchanged_ref_confirmation(self):
        failure = self.result(1, 'remote: fatal error in commit_refs\n[remote rejected] (failure)')
        with patch.object(preserve, 'validate'), \
             patch.object(preserve, 'remote_head', side_effect=['base','base','base','new']), \
             patch.object(preserve, 'git', side_effect=[failure,self.result()]) as git, \
             patch.object(preserve.time, 'sleep') as sleep:
            preserve.push(Path('.'), 'base', 'new')
        self.assertEqual(git.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_internal_server_error_retries_but_stops_at_four(self):
        with patch.object(preserve, 'validate'), patch.object(preserve, 'remote_head', return_value='base'), \
             patch.object(preserve, 'git', return_value=self.result(1, '[remote rejected] (Internal Server Error)')) as git, \
             patch.object(preserve.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'PUSH_REJECTED'):preserve.push(Path('.'), 'base', 'new')
        self.assertEqual(git.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2,5,10])

    def test_accepted_push_with_lost_ack_is_not_replayed(self):
        for result in [self.result(1, 'connection closed'), subprocess.TimeoutExpired(['git'],20)]:
            with self.subTest(result=result), patch.object(preserve, 'validate'), \
                 patch.object(preserve, 'remote_head', side_effect=['base','new']), \
                 patch.object(preserve, 'git', side_effect=[result]) as git, patch.object(preserve.time, 'sleep') as sleep:
                preserve.push(Path('.'), 'base', 'new')
            git.assert_called_once(); sleep.assert_not_called()

    def test_unknown_timeout_is_not_replayed(self):
        with patch.object(preserve, 'validate'), patch.object(preserve, 'remote_head', return_value='base'), \
             patch.object(preserve, 'git', side_effect=subprocess.TimeoutExpired(['git'],20)) as git:
            with self.assertRaisesRegex(RuntimeError, 'UNKNOWN_NO_REPLAY'):preserve.push(Path('.'), 'base', 'new')
        git.assert_called_once()

    def test_concurrent_writer_and_auth_or_policy_rejections_are_not_retried(self):
        cases = [(['other'], None, 'REMOTE_CHANGED'),
                 (['base','other'], self.result(1,'[remote rejected] Internal Server Error'), 'REMOTE_CHANGED'),
                 (['base','base'], self.result(1,'permission denied'), 'PUSH_REJECTED'),
                 (['base','base'], self.result(1,'[remote rejected] protected branch'), 'PUSH_REJECTED'),
                 (['base','base'], self.result(0), 'PUSH_REJECTED')]
        for refs, result, reason in cases:
            with self.subTest(refs=refs,result=result), patch.object(preserve, 'validate'), \
                 patch.object(preserve, 'remote_head', side_effect=refs), \
                 patch.object(preserve, 'git', return_value=result), patch.object(preserve.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, reason):preserve.push(Path('.'), 'base', 'new')
                sleep.assert_not_called()

    def test_expiry_during_retry_stops_before_another_push(self):
        with patch.object(preserve, 'validate', side_effect=[None,ValueError('expired')]), \
             patch.object(preserve, 'remote_head', return_value='base'), \
             patch.object(preserve, 'git', return_value=self.result(1,'[remote rejected] Internal Server Error')) as git, \
             patch.object(preserve.time, 'sleep'):
            with self.assertRaisesRegex(ValueError,'expired'):preserve.push(Path('.'), 'base', 'new')
        git.assert_called_once()

    def test_validation_preserves_source_times_and_rejects_mismatch_and_expiry(self):
        doc=collect.publish({}, {'schemaVersion':1,'status':'operational','checkedAt':collect.stamp(2000),'validUntil':collect.stamp(2900)},True,2000)['document']
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'public').mkdir()
            (root/'state.json').write_text(json.dumps({'document':doc,'history':[]}))
            (root/'public/status.json').write_text(json.dumps(doc));(root/'public/history.json').write_text('[]')
            before=(root/'state.json').read_bytes()
            with patch.object(preserve.time,'time',return_value=2200):preserve.validate(root)
            self.assertEqual(before,(root/'state.json').read_bytes())
            with patch.object(preserve.time,'time',return_value=3000):
                with self.assertRaisesRegex(ValueError,'EXPIRED'):preserve.validate(root)
            (root/'public/history.json').write_text('[{}]')
            with self.assertRaisesRegex(ValueError,'MISMATCH'):preserve.validate(root)

    def test_real_local_repository_push_is_idempotent_and_preserves_unrelated_commits(self):
        with tempfile.TemporaryDirectory() as d:
            top=Path(d);remote=top/'remote';root=top/'work'
            def git(*args,cwd=None):return subprocess.run(['git',*args],cwd=cwd,check=True,capture_output=True,text=True).stdout.strip()
            git('init','--bare',str(remote));git('init','-b','main',str(root))
            git('config','user.name','test',cwd=root);git('config','user.email','test@example.test',cwd=root)
            (root/'static.txt').write_text('keep')
            git('add','static.txt',cwd=root);git('commit','-m','baseline',cwd=root)
            git('remote','add','origin',str(remote),cwd=root);git('push','origin','HEAD:main',cwd=root)
            (root/'public').mkdir()
            now=collect.time.time()
            doc=collect.publish({}, {'schemaVersion':1,'status':'operational','checkedAt':collect.stamp(now),'validUntil':collect.stamp(now+900)},True,now)['document']
            state=json.dumps({'document':doc,'history':[]})
            (root/'state.json').write_text(state);(root/'public/status.json').write_text(json.dumps(doc));(root/'public/history.json').write_text('[]')
            preserve.preserve(root);head=git('rev-parse','HEAD',cwd=root)
            preserve.preserve(root)
            self.assertEqual(git('rev-parse','HEAD',cwd=root),head)
            self.assertEqual(preserve.remote_head(root),head)
            self.assertEqual(git('show','HEAD:static.txt',cwd=root),'keep')
            self.assertEqual((root/'state.json').read_text(),state)
