import asyncio
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from config import Config, RunConfig, atomic_write_text, is_valid_username, load_lines
from engine import Checker, classify_response, retry_delay
from lab import (DiscordClient, JobManager, Result, Store, export_rows, generate_candidates,
                 interpret, normalize, parse_candidates, valid_candidate)
from proxy import ProxyManager
from server import SERVICE, create_app, validate_options
import checker as legacy
from config import AppSettings
from rich.console import Console


class DataTests(unittest.TestCase):
    def test_valid_usernames_and_edge_periods(self):
        for name in ['.a', 'a.', '.a.b.', 'xyb', 'ab_123']:
            with self.subTest(name=name):
                self.assertTrue(is_valid_username(name))
        for name in ['a', 'a..b', 'Aaaa', 'abc#1234', 'a b', 'a' * 33]:
            with self.subTest(name=name):
                self.assertFalse(is_valid_username(name))

    def test_normalize_and_deduplicate(self):
        self.assertEqual(parse_candidates('\ufeff@ABC, abc; DEF\n@def', 'username'), (['abc','def'],2))

    def test_invites_preserve_case_and_extract_urls(self):
        names, duplicates = parse_candidates('https://discord.gg/AbCd?x=1\ndiscord.com/invite/AbCd\nAbcd', 'vanity')
        self.assertEqual(names, ['AbCd', 'Abcd'])
        self.assertEqual(duplicates, 1)

    def test_arbitrary_links_are_not_request_targets(self):
        value = normalize('https://example.com/invite/abc', 'vanity')
        self.assertFalse(valid_candidate(value, 'vanity'))
        self.assertFalse(valid_candidate('../secret', 'vanity'))

    def test_parse_rejects_wrong_types_modes_and_size(self):
        for raw, mode in [(None,'username'),(['ok',1],'username'),('abc','bad'),('a'*257,'username')]:
            with self.subTest(raw=str(raw)[:20]):
                with self.assertRaises(ValueError):
                    parse_candidates(raw,mode)

    def test_response_requires_actual_boolean(self):
        for payload in [None, {}, [], {'taken':'false'}, {'taken':0}, {'taken':None}]:
            with self.subTest(payload=payload):
                self.assertEqual(classify_response(200,payload),'ERROR')
        self.assertIs(classify_response(200,{'taken':True}),False)
        self.assertIs(classify_response(200,{'taken':False}),True)
        self.assertEqual(classify_response(400,{'taken':False}),'ERROR')
        self.assertEqual(classify_response(204,{}),'ERROR')

    def test_vanity_not_found_never_means_available(self):
        result=interpret('vanity','xyb',404,{'code':10006})
        self.assertEqual(result.status,'not_found')
        self.assertIn('NO confirma',result.detail)
        self.assertEqual(interpret('vanity','xyb',404,{}).status,'unverified')

    def test_invite_metadata_and_mismatches(self):
        result=interpret('vanity','xyb',200,{'code':'xyb','guild':{'name':'My server','id':'123'},'approximate_member_count':10})
        self.assertEqual((result.status,result.guild_name,result.members),('active','My server',10))
        self.assertEqual(interpret('vanity','xyb',200,{'code':'other'}).status,'unverified')

    def test_rate_limit_delays(self):
        self.assertEqual(retry_delay({'retry_after':3.2},{'Retry-After':'5'}),5)
        for value in ['nan','inf',-5,True,None,{},'abc']:
            with self.subTest(value=value):
                self.assertEqual(retry_delay({'retry_after':value},{}),5)

    def test_generator_unique_and_bounded_by_capacity(self):
        values=generate_candidates({'pattern':'##','count':200})
        self.assertEqual(len(values),100)
        self.assertEqual(len(set(values)),100)

    def test_generator_literal_and_pattern(self):
        self.assertEqual(generate_candidates({'pattern':'xyb','count':500}),['xyb'])
        values=generate_candidates({'pattern':'?#','count':20,'prefix':'x_'})
        self.assertEqual(len(values),20)
        self.assertTrue(all(is_valid_username(v) for v in values))

    def test_generator_impossible_input_fails_without_loop(self):
        for options in [{'pattern':'..'},{'pattern':'!?'},{'count':0},{'count':10001},{'count':2.5},{'length':True},{'count':float('inf')},{'length':32,'prefix':'x'},{'mode':'unknown'},{'mode':'vanity','prefix':'x_'}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    generate_candidates(options)

    def test_malformed_invite_counts_are_not_persisted(self):
        result=interpret('vanity','xyb',200,{'code':'xyb','approximate_member_count':{'wrong':1},'approximate_presence_count':True})
        self.assertIsNone(result.members)
        self.assertIsNone(result.online)

    def test_options_reject_nonfinite_invalid_and_zero(self):
        for options in [{'delay':0},{'delay':float('inf')},{'timeout':False},{'timeout':-1},{'use_proxy':'yes'}]:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    validate_options(options)

    def test_old_config_rejects_hanging_worker_values(self):
        for count in [0,-1,'50',True]:
            with self.subTest(count=count):
                with self.assertRaises(ValueError):
                    RunConfig([],False,['abc'],count,1)
        cfg=RunConfig([],False,['ABC','abc',' def '],50,1)
        self.assertEqual(cfg.concurrency,1)
        self.assertEqual(cfg.usernames,['abc','def'])

    def test_utf8_bom_and_config_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'data.txt'
            path.write_text('\ufeffabc\n def\n',encoding='utf-8')
            self.assertEqual(load_lines(path),['abc','def'])
            path.write_text('[1,2]',encoding='utf-8')
            with self.assertRaises(ValueError):
                Config(path)
            self.assertEqual(path.read_text(),'[1,2]')

    def test_atomic_config_write_failure_preserves_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json'
            cfg=Config(path)
            cfg.set('timeout',10)
            with patch('config.Path.replace',side_effect=OSError('blocked')):
                with self.assertRaises(OSError):
                    cfg.set('timeout',20)
            self.assertEqual(cfg.get('timeout'),10)
            self.assertEqual(json.loads(path.read_text())['timeout'],10)
            self.assertEqual(list(Path(tmp).glob('*.tmp')),[])

    def test_export_csv_formula_protection_and_unicode(self):
        content,mime=export_rows([{'candidate':'=1+1','guild_name':'@bad','mode':'vanity','status':'active'}],'csv')
        self.assertEqual(mime,'text/csv')
        self.assertIn("'=1+1",content)
        self.assertIn("'@bad",content)
        self.assertTrue(content.startswith('\ufeff'))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'lab.sqlite3')
    def tearDown(self):
        self.store.close()
        self.temp.cleanup()
    def test_modes_are_separate_and_history_not_overwritten(self):
        a=self.store.create('username',['xyb'],{})
        b=self.store.create('vanity',['xyb'],{})
        self.store.save(a,Result('xyb','username','candidate','test'))
        self.store.save(b,Result('xyb','vanity','not_found','test'))
        self.assertEqual(self.store.get_job(a)['counts'],{'candidate':1})
        self.assertEqual(self.store.get_job(b)['counts'],{'not_found':1})
    def test_duplicate_result_is_replaced_not_counted_twice(self):
        job=self.store.create('username',['xyb'],{})
        self.store.save(job,Result('xyb','username','unverified','test'))
        self.store.save(job,Result('xyb','username','taken','test'))
        self.assertEqual(self.store.get_job(job)['completed'],1)
        self.assertEqual(self.store.get_job(job)['counts'],{'taken':1})
    def test_search_literal_not_sql_wildcard(self):
        job=self.store.create('username',['a_b','axb'],{})
        for name in ['a_b','axb']:
            self.store.save(job,Result(name,'username','valid','test'))
        self.assertEqual(self.store.results(job,search='_')['total'],1)
    def test_favorites_are_independent_of_mode(self):
        self.store.favorite('username','xyb',True)
        self.store.favorite('vanity','xyb',True)
        self.store.favorite('username','xyb',False)
        self.assertEqual(len(self.store.favorites()),1)
        self.assertEqual(self.store.favorites()[0]['mode'],'vanity')
    def test_restart_marks_incomplete_jobs_interrupted(self):
        job=self.store.create('username',['xyb'],{})
        self.store.close()
        self.store=Store(Path(self.temp.name)/'lab.sqlite3')
        self.assertEqual(self.store.get_job(job)['state'],'interrupted')
    def test_second_instance_cannot_change_active_history(self):
        job=self.store.create('username',['xyb'],{})
        with self.assertRaises(ValueError):
            Store(Path(self.temp.name)/'lab.sqlite3')
        self.assertEqual(self.store.get_job(job)['state'],'running')


class Response:
    def __init__(self,status,data,headers=None):
        self.status,self.data,self.headers=status,data,headers or {}
    async def __aenter__(self):
        return self
    async def __aexit__(self,*args):
        return False
    async def json(self):
        if isinstance(self.data,Exception):
            raise self.data
        return self.data


class Session:
    def __init__(self,responses):
        self.responses=list(responses)
        self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        return self.responses.pop(0)
    def post(self,*args,**kwargs):
        return self.request('POST',*args,**kwargs)


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_candidate_makes_no_request(self):
        session=Session([])
        result=await DiscordClient(session).check('username','bad#1234')
        self.assertEqual(result.status,'invalid')
        self.assertEqual(session.calls,[])
    async def test_vanity_uses_get_invite_with_counts(self):
        session=Session([Response(200,{'code':'AbCd','guild':{'name':'Test'}})])
        result=await DiscordClient(session).check('vanity','AbCd')
        self.assertEqual(result.status,'active')
        self.assertEqual(session.calls[0][0],('GET','https://discord.com/api/v10/invites/AbCd'))
        self.assertEqual(session.calls[0][1]['params'],{'with_counts':'true'})
    async def test_username_post_and_strict_boolean(self):
        session=Session([Response(200,{'taken':'false'})])
        result=await DiscordClient(session).check('username','xyb')
        self.assertEqual(result.status,'unverified')
        self.assertEqual(session.calls[0][1]['json'],{'username':'xyb'})
    async def test_rate_limit_waits_before_retry_even_with_proxy(self):
        session=Session([Response(429,{'retry_after':2}),Response(200,{'taken':True})])
        client=DiscordClient(session,proxy='http://proxy.test:8080')
        with patch('lab.asyncio.sleep',new_callable=AsyncMock) as sleep:
            result=await client.check('username','xyb')
        self.assertEqual(result.status,'taken')
        self.assertEqual(client.rate_limits,1)
        self.assertGreater(sleep.call_args.args[0],1.9)
        self.assertEqual(session.calls[0][1]['proxy'],session.calls[1][1]['proxy'])
    async def test_malformed_json_remains_unverified(self):
        result=await DiscordClient(Session([Response(200,ValueError())])).check('username','xyb')
        self.assertEqual(result.status,'unverified')
    async def test_http_400_403_404_not_classified_taken(self):
        for status in [400,403,404]:
            result=await DiscordClient(Session([Response(status,{})])).check('username','xyb')
            self.assertEqual(result.status,'unverified')
    async def test_legacy_checker_empty_success_does_not_save_taken(self):
        checker=Checker(ProxyManager([]))
        result=await checker.check(Session([Response(204,ValueError())]),'xyb')
        self.assertEqual(result[0],'ERROR')
    async def test_legacy_checker_invalid_rejected_locally(self):
        checker=Checker(ProxyManager([]))
        session=Session([])
        result=await checker.check(session,'a..b')
        self.assertEqual(result[0],'ERROR')
        self.assertEqual(session.calls,[])


class FakeClient:
    block=None
    seen=[]
    def __init__(self,*args,**kwargs):
        self.requests=self.rate_limits=0
        self.next_request=0
    async def check(self,mode,name):
        self.requests+=1
        type(self).seen.append(name)
        if type(self).block:
            await type(self).block.wait()
        return Result(name,mode,'candidate' if mode=='username' else 'not_found','mock')


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'lab.sqlite3')
        FakeClient.block=None
        FakeClient.seen=[]
        self.manager=JobManager(self.store,None,client_factory=FakeClient)
        self.options={'delay':1.5,'timeout':15,'offline':False,'use_proxy':False}
    async def asyncTearDown(self):
        await self.manager.close()
        self.store.close()
        self.temp.cleanup()
    async def test_stop_and_resume_preserves_unprocessed_candidate(self):
        FakeClient.block=asyncio.Event()
        job=self.manager.start('username',['xyb','abc'],self.options)
        await asyncio.sleep(0)
        await self.manager.action(job,'stop')
        self.assertEqual(self.store.get_job(job)['completed'],0)
        FakeClient.block=None
        await self.manager.action(job,'resume')
        await self.manager.task
        self.assertEqual(self.store.get_job(job)['completed'],2)
        self.assertEqual(self.store.get_job(job)['state'],'completed')
    async def test_resume_skips_already_persisted_results(self):
        job=self.store.create('username',['xyb','abc'],self.options)
        self.store.save(job,Result('xyb','username','taken','old'))
        await self.manager.action(job,'resume')
        await self.manager.task
        self.assertEqual(FakeClient.seen,['abc'])
    async def test_one_job_at_a_time(self):
        FakeClient.block=asyncio.Event()
        self.manager.start('username',['xyb'],self.options)
        with self.assertRaises(ValueError):
            self.manager.start('vanity',['xyb'],self.options)
    async def test_offline_does_not_call_network_and_counts_invalid(self):
        job=self.manager.start('username',['xyb','a..b'],{**self.options,'offline':True})
        await self.manager.task
        self.assertEqual(FakeClient.seen,[])
        self.assertEqual(self.store.get_job(job)['counts'],{'invalid':1,'valid':1})
    async def test_finished_elapsed_stays_fixed(self):
        self.manager.start('username',['xyb'],self.options)
        await self.manager.task
        with patch('lab.time.monotonic',return_value=10**12):
            self.assertLess(self.manager.live()['elapsed'],5)
    async def test_pause_blocks_next_candidate(self):
        FakeClient.block=asyncio.Event()
        job=self.manager.start('username',['xyb','abc'],self.options)
        await asyncio.sleep(0)
        await self.manager.action(job,'pause')
        FakeClient.block.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(FakeClient.seen,['xyb'])
        await self.manager.action(job,'resume')
        await self.manager.task
        self.assertEqual(FakeClient.seen,['xyb','abc'])


class ApiTests(unittest.IsolatedAsyncioTestCase):
    loop_factory = staticmethod(asyncio.SelectorEventLoop)
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        FakeClient.block=None
        FakeClient.seen=[]
        self.app=create_app(Path(self.temp.name),client_factory=FakeClient)
        self.client=TestClient(TestServer(self.app))
        self.addAsyncCleanup(self.client.close)
        await self.client.start_server()
        boot=await (await self.client.get('/api/bootstrap')).json()
        self.headers={'X-XYB-Token':boot['token']}
    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()
    async def post(self,path,data,headers=None):
        return await self.client.post(path,json=data,headers=headers or self.headers)
    async def test_root_assets_and_bootstrap(self):
        r=await self.client.get('/')
        self.assertEqual(r.status,200)
        self.assertIn('Identity Lab',await r.text())
        self.assertIn('frame-ancestors',r.headers['Content-Security-Policy'])
        self.assertEqual((await self.client.get('/assets/app.js')).status,200)
    async def test_csrf_and_external_origin_rejected(self):
        self.assertEqual((await self.client.post('/api/jobs',json={})).status,403)
        self.assertEqual((await self.post('/api/jobs',{},headers={**self.headers,'Origin':'https://evil.test'})).status,403)
        self.assertEqual((await self.client.get('/api/state',headers={'Host':'evil.test'})).status,403)
    async def test_full_offline_workflow_export_and_favorite(self):
        r=await self.post('/api/jobs',{'mode':'username','candidates':'@XYB,xyb,a..b','offline':True})
        self.assertEqual(r.status,201)
        job=(await r.json())['id']
        await self.app[SERVICE].manager.task
        rows=await (await self.client.get('/api/results?job='+job)).json()
        self.assertEqual(rows['total'],2)
        self.assertEqual({x['status'] for x in rows['items']},{'valid','invalid'})
        export=await self.client.get('/api/export?job='+job+'&format=csv&status=valid')
        self.assertEqual(export.status,200)
        text=await export.text()
        self.assertIn('xyb',text)
        self.assertNotIn('a..b',text)
        r=await self.post('/api/favorites',{'mode':'username','candidate':'xyb','enabled':True})
        self.assertEqual(r.status,200)
        summary=await (await self.client.get('/api/state')).json()
        self.assertEqual(len(summary['favorites']),1)
    async def test_generator_api_and_invalid_input(self):
        r=await self.post('/api/generate',{'pattern':'??##','count':12})
        self.assertEqual(len((await r.json())['candidates']),12)
        self.assertEqual((await self.post('/api/generate',{'pattern':'..'})).status,400)
    async def test_settings_persist_and_reject_invalid_numbers(self):
        r=await self.post('/api/settings',{'delay':2,'timeout':20,'use_proxy':False})
        self.assertEqual(r.status,200)
        self.assertTrue((Path(self.temp.name)/'data/lab_settings.json').exists())
        self.assertEqual((await self.post('/api/settings',{'delay':0})).status,400)
    async def test_secret_files_cannot_be_loaded(self):
        self.assertEqual((await self.client.get('/api/input/proxies')).status,400)
        self.assertEqual((await self.client.get('/api/input/config')).status,400)
    async def test_vanity_workflow(self):
        r=await self.post('/api/jobs',{'mode':'vanity','candidates':'https://discord.gg/AbCd,AbCd'})
        body=await r.json()
        self.assertEqual(body['duplicates'],1)
        await self.app[SERVICE].manager.task
        rows=await (await self.client.get('/api/results?job='+body['id'])).json()
        self.assertEqual(rows['items'][0]['status'],'not_found')
    async def test_invalid_export_and_empty_job(self):
        self.assertEqual((await self.client.get('/api/export')).status,400)
        self.assertEqual((await self.post('/api/jobs',{'candidates':''})).status,400)


class LegacyRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.patches=ExitStack()
        self.patches.enter_context(patch.object(legacy,'RESULTS_DIR',self.root))
        self.patches.enter_context(patch.object(legacy,'console',Console(file=io.StringIO())))
        self.summary=self.patches.enter_context(patch.object(legacy,'final_summary'))
    async def asyncTearDown(self):
        self.patches.close()
        self.tmp.cleanup()
    def cfg(self,names):
        return RunConfig(['proxy.test:8080'],False,names,1,1)
    async def test_uncertain_and_exceptions_stay_retryable(self):
        check=self.patches.enter_context(patch.object(legacy.Checker,'check',new_callable=AsyncMock))
        check.side_effect=[('ERROR',None,204),RuntimeError('test failure')]
        await legacy._run_checker(self.cfg(['abc','def']),AppSettings())
        self.assertEqual(set(load_lines(self.root/'unverified.txt')),{'abc','def'})
        self.assertEqual(load_lines(self.root/'taken.txt'),[])
        self.assertEqual(self.summary.call_args.kwargs['unverified'],2)
    async def test_resolved_uncertain_removed_atomically(self):
        (self.root/'unverified.txt').write_text('abc\ndef\ndef\n',encoding='utf-8')
        self.patches.enter_context(patch.object(legacy.Checker,'check',new_callable=AsyncMock,return_value=(False,{'taken':True},200)))
        await legacy._run_checker(self.cfg(['abc']),AppSettings())
        self.assertEqual(load_lines(self.root/'unverified.txt'),['def'])
        self.assertEqual(load_lines(self.root/'taken.txt'),['abc'])
    async def test_cancellation_persists_inflight_and_reports_stopped(self):
        entered=asyncio.Event()
        async def blocking(*args):
            entered.set()
            await asyncio.Event().wait()
        self.patches.enter_context(patch.object(legacy.Checker,'check',side_effect=blocking))
        task=asyncio.create_task(legacy._run_checker(self.cfg(['abc','def']),AppSettings()))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(load_lines(self.root/'unverified.txt'),['abc'])
        self.assertTrue(self.summary.call_args.kwargs['interrupted'])
        self.assertEqual(self.summary.call_args.kwargs['remaining'],1)
    async def test_legacy_attempt_file_is_not_proof_of_taken(self):
        (self.root/'used_attempts.txt').write_text('abc\n',encoding='utf-8')
        check=self.patches.enter_context(patch.object(legacy.Checker,'check',new_callable=AsyncMock,return_value=('ERROR',None,None)))
        await legacy._run_checker(self.cfg(['abc']),AppSettings())
        check.assert_awaited_once()


if __name__=='__main__':
    unittest.main()
