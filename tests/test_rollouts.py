import json
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

import zstandard

from codex_usage.rollouts import iter_usage_events, resolve_rollout_path
from codex_usage.tasks import build_task_rows


class RolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.session = self.home / 'sessions' / '2026' / '01' / '01' / 'rollout.jsonl'
        self.session.parent.mkdir(parents=True)
        self.event = {'timestamp': '2026-01-01T00:00:00Z', 'type': 'token_usage_record',
                      'payload': {'usage': {'input_tokens': 10, 'cached_input_tokens': 4,
                                            'output_tokens': 2, 'total_tokens': 12}}}

    def write(self, path, compressed=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.dumps(self.event) + '\n').encode()
        path.write_bytes(zstandard.ZstdCompressor().compress(data) if compressed else data)

    def test_plain_and_compressed_are_equivalent(self):
        self.write(self.session)
        plain = list(iter_usage_events(self.session))
        compressed = Path(str(self.session) + '.zst')
        self.write(compressed, True)
        self.assertEqual(plain, list(iter_usage_events(compressed)))
        self.assertEqual(resolve_rollout_path(self.session), self.session)

    def test_missing_recorded_path_uses_compressed_sibling(self):
        self.write(Path(str(self.session) + '.zst'), True)
        self.assertEqual(list(iter_usage_events(self.session))[0].total_tokens, 12)

    def test_archived_compressed_path_and_task_stats(self):
        archive = self.home / 'archived_sessions' / (self.session.name + '.zst')
        self.write(archive, True)
        self.assertEqual(resolve_rollout_path(self.session), archive)
        thread = {'id': 'root', 'rollout_path': str(self.session), 'cwd': 'project',
                  'model': 'unknown', 'reasoning_effort': 'unknown', 'title': 'task',
                  'created_at': 1767225600, 'updated_at': 1767225600}
        rows = build_task_rows([thread], [], {'project': 'project'}, lambda x: x, 80, 'UTC')
        self.assertEqual(rows[0]['direct_tokens'], 12)

    def test_legacy_compressed_deduplicates_repeated_counts(self):
        usage = {'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}
        event = {'timestamp': '2026-01-01T00:00:00Z', 'type': 'event_msg',
                 'payload': {'type': 'token_count', 'info': {
                     'last_token_usage': usage, 'total_token_usage': usage}}}
        path = Path(str(self.session) + '.zst')
        raw = (json.dumps(event) + '\n') * 2
        path.write_bytes(zstandard.ZstdCompressor().compress(raw.encode()))
        self.assertEqual(sum(e.total_tokens for e in iter_usage_events(self.session)), 12)

    def test_missing_and_ambiguous_paths(self):
        self.assertIsNone(resolve_rollout_path(self.session))
        self.assertEqual(list(iter_usage_events(self.session)), [])
        self.write(Path(str(self.session) + '.zst'), True)
        self.write(self.home / 'archived_sessions' / (self.session.name + '.zst'), True)
        with self.assertRaises(RuntimeError):
            resolve_rollout_path(self.session)

    def test_timezone_database(self):
        self.assertEqual(ZoneInfo('Europe/Saratov').key, 'Europe/Saratov')


if __name__ == '__main__':
    unittest.main()
