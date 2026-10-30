import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from engine import retry_delay
from lab import DiscordClient, JobManager, Result, Store
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

    async def test_wait_extends_if_another_request_updates_deadline(self):
        session = Session([Response(200, {'taken': True})])
        client = DiscordClient(session)
        client.next_request = time.monotonic() + 10
        sleeps = []
        async def fake_sleep(wait):
            sleeps.append(wait)
            if len(sleeps) == 1:
                client.next_request += 20
        with patch('lab.asyncio.sleep', side_effect=fake_sleep):
            result = await client.check('username', 'xyb')
        self.assertEqual(result.status, 'taken')
        self.assertEqual(len(sleeps), 2)
        self.assertGreater(sleeps[1], sleeps[0])

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

    async def test_rejected_access_stops_before_further_requests(self):
        class RejectClient:
            calls = []
            def __init__(self, *args, **kwargs):
                self.next_request = self.requests = self.rate_limits = 0
            async def check(self, mode, name):
                self.calls.append(name)
                return Result(name, mode, 'unverified', 'test', 403)
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / 'history.sqlite')
            manager = JobManager(store, None, client_factory=RejectClient)
            try:
                job_id = manager.start('username', ['alpha', 'beta'], {'delay': 1, 'timeout': 3})
                await manager.task
                job = store.get_job(job_id)
                self.assertEqual(RejectClient.calls, ['alpha'])
                self.assertEqual(job['state'], 'failed')
                self.assertEqual(job['completed'], 1)
                self.assertIn('403', job['error'])
            finally:
                await manager.close()
                store.close()

    async def test_discord_cooldown_survives_cancellation_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'history.sqlite'
            store = Store(path)
            responses = Session([Response(429, {'retry_after': 30})])
            manager = JobManager(store, responses)
            try:
                manager.start('username', ['alpha'], {'delay': 1, 'timeout': 3})
                for _ in range(10):
                    await asyncio.sleep(0)
                    if store.cooldown_deadline() > time.time():
                        break
                self.assertGreater(store.cooldown_deadline() - time.time(), 28)
                await manager.close()
                store.close()
                store = Store(path)
                manager = JobManager(store, Session([]))
                self.assertGreater(manager.next_request - time.monotonic(), 28)
                job = manager.start('username', ['beta'], {'delay': 1, 'timeout': 3})
                await asyncio.sleep(0)
                self.assertGreater(manager.live()['cooldown'], 28)
                self.assertEqual(manager.session.calls, [])
                await manager.close()
                self.assertEqual(store.get_job(job)['state'], 'stopped')
            finally:
                await manager.close()
                store.close()
