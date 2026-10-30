import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from server import SERVICE, create_app


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    loop_factory = staticmethod(asyncio.SelectorEventLoop)

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = create_app(self.root)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        boot = await (await self.client.get('/api/bootstrap')).json()
        self.headers = {'X-XYB-Token': boot['token']}

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def post(self, path, body):
        return await self.client.post(path, json=body, headers=self.headers)

    async def test_secrets_are_not_returned_and_original_proxy_is_backed_up(self):
        old = 'old:secret@host.test:80\nother:80\n'
        data = self.root / 'data'
        data.mkdir(exist_ok=True)
        (data / 'proxies.txt').write_text(old)
        proxy = 'new:private@host.test:8080'
        webhook = 'https://discord.com/api/webhooks/123456/SecretToken'
        r = await self.post('/api/connections', {'proxy': proxy, 'webhook': webhook})
        self.assertEqual(r.status, 200)
        self.assertEqual(await r.json(), {'proxy_configured': True, 'webhook_configured': True})
        boot = await (await self.client.get('/api/bootstrap')).text()
        self.assertNotIn('private', boot)
        self.assertNotIn('SecretToken', boot)
        backups = list(data.glob('proxies-backup-*.txt'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), old)
        self.assertEqual(self.app[SERVICE].proxy({'use_proxy': True}), 'http://' + proxy)
        r = await self.post('/api/connections', {'clear_proxy': True, 'clear_webhook': True})
        self.assertEqual(await r.json(), {'proxy_configured': False, 'webhook_configured': False})

    async def test_rejects_invalid_secrets_without_writes(self):
        for payload in ({'proxy': 'http://host.test:80/path'},
                        {'webhook': 'https://discord.com.evil.test/api/webhooks/1/x'},
                        {'webhook': 'http://discord.com/api/webhooks/1/x'},
                        {'webhook': 'https://discord.com/api/webhooks/1/x?foo=bar'},
                        {'clear_proxy': 'true'}):
            self.assertEqual((await self.post('/api/connections', payload)).status, 400)
        self.assertFalse((self.root / 'data' / 'proxies.txt').exists())
        self.assertFalse((self.root / 'data' / 'lab_webhook.txt').exists())

    async def test_webhook_test_and_completed_job_are_opt_in(self):
        await self.post('/api/connections', {'webhook': 'https://discord.com/api/webhooks/1/test'})
        with patch.object(self.app[SERVICE], 'send_webhook', new_callable=AsyncMock) as send:
            self.assertEqual((await self.post('/api/webhook/test', {})).status, 200)
            self.assertEqual(send.await_count, 1)
            send.reset_mock()
            response = await self.post('/api/jobs', {'mode': 'username', 'candidates': 'abc', 'offline': True})
            self.assertEqual(response.status, 201)
            await self.app[SERVICE].manager.task
            send.assert_not_awaited()
            await self.post('/api/settings', {'webhook_enabled': True})
            response = await self.post('/api/jobs', {'mode': 'username', 'candidates': 'abc', 'offline': True})
            self.assertEqual(response.status, 201)
            await self.app[SERVICE].manager.task
            send.assert_awaited_once()

    async def test_completed_session_stays_completed_if_notification_fails_or_is_cancelled(self):
        await self.post('/api/settings', {'webhook_enabled': True})
        with patch.object(self.app[SERVICE], 'send_webhook', new_callable=AsyncMock, side_effect=RuntimeError('unexpected')):
            response = await self.post('/api/jobs', {'candidates': 'abc', 'offline': True})
            job = (await response.json())['id']
            await self.app[SERVICE].manager.task
            self.assertEqual(self.app[SERVICE].store.get_job(job)['state'], 'completed')
        entered = asyncio.Event()
        async def blocking(*args):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.app[SERVICE], 'send_webhook', side_effect=blocking):
            response = await self.post('/api/jobs', {'candidates': 'abc', 'offline': True})
            job = (await response.json())['id']
            await entered.wait()
            await self.app[SERVICE].manager.close()
            self.assertEqual(self.app[SERVICE].store.get_job(job)['state'], 'completed')

    async def test_webhook_429_prevents_immediate_second_attempt(self):
        await self.post('/api/connections', {'webhook': 'https://discord.com/api/webhooks/1/test'})
        class Response:
            status = 429
            headers = {}
            async def json(self): return {'retry_after': 20}
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        with patch.object(self.app[SERVICE].session, 'post', return_value=Response()) as send:
            response = await self.post('/api/webhook/test', {})
            self.assertEqual(response.status, 400)
            self.assertEqual((await self.post('/api/webhook/test', {})).status, 400)
            send.assert_called_once()
            self.assertFalse(send.call_args.kwargs['allow_redirects'])
            self.assertEqual(send.call_args.kwargs['json']['allowed_mentions'], {'parse': []})


if __name__ == '__main__':
    unittest.main()
