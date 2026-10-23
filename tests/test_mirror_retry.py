import contextlib
import http.client
import io
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import collect


def document():
    observation = {'schemaVersion': 1, 'status': 'operational',
                   'checkedAt': collect.stamp(2000), 'validUntil': collect.stamp(2900)}
    return collect.publish({}, observation, True, 2000)['document']


class MirrorRetryTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_tls_eof_then_success_retries_and_reports_recovery(self):
        value = document()
        error = urllib.error.URLError(ssl.SSLEOFError('unexpected EOF'))
        with patch.object(collect, 'fetch', side_effect=[error, value]) as fetch, \
                patch.object(collect.time, 'sleep') as sleep:
            self.assertIs(collect.fetch_mirror('https://example.test/status.json'), value)
        self.assertEqual(fetch.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertIn('error=SSLEOFError', self.output.getvalue())
        self.assertIn('recovered: attempt=2/4', self.output.getvalue())

    def test_repeated_transient_errors_stop_after_four_attempts(self):
        error = urllib.error.URLError(TimeoutError('timeout'))
        with patch.object(collect, 'fetch', side_effect=error) as fetch, \
                patch.object(collect.time, 'sleep') as sleep:
            with self.assertRaises(urllib.error.URLError) as caught:
                collect.fetch_mirror('https://example.test/status.json')
        self.assertIs(caught.exception, error)
        self.assertEqual(fetch.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(2), call(5), call(10)])
        self.assertIn('failed: attempt=4/4', self.output.getvalue())

    def test_only_transient_transport_and_http_errors_retry(self):
        errors = [ConnectionResetError('reset'), http.client.IncompleteRead(b''),
                  http.client.RemoteDisconnected('closed'),
                  socket.gaierror(socket.EAI_AGAIN, 'temporary DNS failure')]
        errors.extend(urllib.error.HTTPError('https://example.test', code, 'error', {}, None)
                      for code in (408, 429, 500, 502, 503, 504))
        for error in errors:
            with self.subTest(error=repr(error)), \
                    patch.object(collect, 'fetch', side_effect=[error, {}]) as fetch, \
                    patch.object(collect.time, 'sleep') as sleep:
                self.assertEqual(collect.fetch_mirror('https://example.test/status.json'), {})
                self.assertEqual(fetch.call_count, 2)
                sleep.assert_called_once_with(2)

    def test_certificate_protocol_redirect_and_invalid_data_fail_without_retry(self):
        certificate = ssl.SSLCertVerificationError('invalid certificate')
        errors = [certificate, urllib.error.URLError(certificate),
                  ssl.SSLError('unsupported protocol'),
                  urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, 'unknown host')),
                  ValueError('REDIRECT_REJECTED'), ValueError('INVALID_RESPONSE'),
                  json.JSONDecodeError('invalid JSON', '', 0)]
        errors.extend(urllib.error.HTTPError('https://example.test', code, 'error', {}, None)
                      for code in (301, 400, 401, 403, 404))
        for error in errors:
            with self.subTest(error=repr(error)), \
                    patch.object(collect, 'fetch', side_effect=error) as fetch, \
                    patch.object(collect.time, 'sleep') as sleep:
                with self.assertRaises(type(error)):
                    collect.fetch_mirror('https://example.test/status.json')
                fetch.assert_called_once()
                sleep.assert_not_called()

    def test_both_downloads_can_recover_and_keep_source_timestamps(self):
        value = document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collect, 'ROOT', root), patch.object(collect.time, 'time', return_value=2200), \
                    patch.object(collect.time, 'sleep'), \
                    patch.object(collect, 'fetch', side_effect=[
                        urllib.error.URLError(ssl.SSLEOFError('EOF')), value,
                        ConnectionResetError('reset'), [value]]):
                collect.main()
            self.assertEqual(json.loads((root/'state.json').read_text()),
                             {'document': value, 'history': [value]})
            self.assertEqual(json.loads((root/'public/status.json').read_text()), value)
            self.assertEqual(json.loads((root/'public/history.json').read_text()), [value])

    def test_network_and_validation_failures_preserve_existing_files(self):
        value = document()
        cases = [([TimeoutError('timeout')] * 4, 2200),
                 ([value] + [ConnectionResetError('reset')] * 4, 2200),
                 ([value, {'invalid': 'history'}], 2200),
                 ([value, [dict(value, summary={})]], 2200),
                 ([dict(value, summary={})], 2200),
                 ([value], 3000)]
        for responses, now in cases:
            with self.subTest(responses=repr(responses)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'public').mkdir()
                paths = [root/'state.json', root/'public/status.json', root/'public/history.json']
                for path in paths:
                    path.write_text('previous valid file')
                with patch.object(collect, 'ROOT', root), \
                        patch.object(collect.time, 'time', return_value=now), \
                        patch.object(collect.time, 'sleep'), \
                        patch.object(collect, 'fetch', side_effect=responses):
                    with self.assertRaises((OSError, ValueError)):
                        collect.main()
                self.assertEqual([p.read_text() for p in paths], ['previous valid file'] * 3)

    def test_expiry_during_history_retry_cannot_publish_old_green(self):
        value = document()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(collect, 'ROOT', root), \
                    patch.object(collect.time, 'time', side_effect=[2899, 2901]), \
                    patch.object(collect.time, 'sleep'), \
                    patch.object(collect, 'fetch', side_effect=[value, TimeoutError('timeout'), [value]]):
                with self.assertRaisesRegex(ValueError, 'EXPIRED_PUBLIC_DOCUMENT'):
                    collect.main()
            self.assertEqual(list(root.iterdir()), [])

    def test_stock_observer_does_not_use_mirror_retries(self):
        with patch.object(collect, 'fetch', side_effect=TimeoutError('timeout')) as fetch, \
                patch.object(collect.time, 'sleep') as sleep:
            self.assertEqual(collect.observe(2200), (False, None))
            fetch.assert_called_once_with('https://api.tockett.work/health')
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
