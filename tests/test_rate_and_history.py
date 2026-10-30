import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from engine import retry_delay
from lab import DiscordClient, Result, Store
from test_lab import Response, Session


class RateAndHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_429_honors_longest_reset_header(self):
        session = Session([Response(429, {'retry_after': 2}, {'Retry-After': '3', 'X-RateLimit-Reset-After': '7'}),
                           Response(200, {'taken': True})])
        client = DiscordClient(session)
        with patch('lab.asyncio.sleep', new_callable=AsyncMock) as sleep:
            result = await client.check('username', 'xyb')
        self.assertEqual(result.status, 'taken')
        self.assertGreater(sleep.call_args.args[0], 6.9)

    async def test_exhausted_quota_reports_wait_and_keeps_next_request(self):
        waits = []
        session = Session([Response(200, {'taken': True}, {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset-After': '12'})])
        client = DiscordClient(session, on_wait=waits.append)
        result = await client.check('username', 'xyb')
        self.assertEqual(result.status, 'taken')
        self.assertEqual(waits[-1], 12)
        self.assertGreater(client.wait_until, 0)

    def test_history_matches_full_job_without_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'history.sqlite')
            try:
                job_id = store.create('username', ['alpha', 'beta'], {'offline': True})
                store.save(job_id, Result('alpha', 'username', 'valid', 'ok'))
                full = store.get_job(job_id)
                del full['candidates']
                self.assertEqual(store.get_job(job_id, include_candidates=False), full)
                self.assertEqual(store.jobs(), [full])
            finally:
                store.close()

    def test_server_reset_header_is_included(self):
        self.assertEqual(retry_delay({'retry_after': 3}, {'X-RateLimit-Reset-After': '8'}), 8)
