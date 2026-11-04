"""Preserve a mirror with bounded, reconciled retries for GitHub rejections."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import collect

DELAYS = (2, 5, 10)


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, capture_output=True,
                          text=True, timeout=20, check=False)


def checked(root, *args):
    result = git(root, *args)
    if result.returncode:
        raise RuntimeError('GIT_COMMAND_FAILED')
    return result.stdout.strip()


def validate(root):
    state = json.loads((root / 'state.json').read_text())
    document = json.loads((root / 'public/status.json').read_text())
    history = json.loads((root / 'public/history.json').read_text())
    if state != {'document': document, 'history': history}:
        raise ValueError('MIRROR_ARTIFACT_MISMATCH')
    now = time.time()
    collect.validate_public_document(document, now)
    if not isinstance(history, list) or len(history) > 30:
        raise ValueError('INVALID_HISTORY')
    for item in history:
        collect.validate_public_document(item, now, allow_expired=True)


def remote_head(root):
    lines = checked(root, 'ls-remote', '--exit-code', 'origin', 'refs/heads/main').splitlines()
    if len(lines) != 1:
        raise RuntimeError('REMOTE_HEAD_UNKNOWN')
    fields = lines[0].split()
    if len(fields) != 2 or fields[1] != 'refs/heads/main':
        raise RuntimeError('REMOTE_HEAD_UNKNOWN')
    return fields[0]


def push(root, base, wanted):
    for attempt in range(len(DELAYS) + 1):
        validate(root)
        actual = remote_head(root)
        if actual == wanted:
            print('Observation commit confirmed on remote; no replay.')
            return
        if actual != base:
            raise RuntimeError('REMOTE_CHANGED_NO_OVERWRITE')
        try:
            result = git(root, 'push', '--porcelain', 'origin', wanted + ':refs/heads/main')
        except subprocess.TimeoutExpired:
            if remote_head(root) == wanted:
                validate(root)
                print('Timed-out push reconciled as accepted; no replay.')
                return
            raise RuntimeError('PUSH_OUTCOME_UNKNOWN_NO_REPLAY') from None
        # Acknowledgement and failure can both follow a remote update. Read it.
        actual = remote_head(root)
        if actual == wanted:
            validate(root)
            return
        if actual != base:
            raise RuntimeError('REMOTE_CHANGED_NO_OVERWRITE')
        message = (result.stdout + result.stderr).lower()
        retryable = (result.returncode != 0 and 'remote rejected' in message and
                     ('fatal error in commit_refs' in message or
                      'internal server error' in message))
        if not retryable or attempt == len(DELAYS):
            raise RuntimeError('PUSH_REJECTED_OR_UNCONFIRMED')
        print(f'GitHub explicitly rejected observation; retry {attempt + 1}/3 after reconciliation.')
        time.sleep(DELAYS[attempt])


def preserve(root):
    validate(root)
    base = checked(root, 'rev-parse', 'HEAD')
    checked(root, 'config', 'user.name', 'Tockett status publisher')
    checked(root, 'config', 'user.email', 'status-publisher@users.noreply.github.com')
    checked(root, 'add', '--', 'state.json')
    staged = checked(root, 'diff', '--cached', '--name-only').splitlines()
    if not staged:
        return
    if staged != ['state.json']:
        raise RuntimeError('UNEXPECTED_STAGED_FILES')
    checked(root, 'commit', '-m', 'Update service status observation')
    push(root, base, checked(root, 'rev-parse', 'HEAD'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).parent
    validate(root) if args.validate_only else preserve(root)
