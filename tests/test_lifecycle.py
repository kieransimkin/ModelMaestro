import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock
from backend.http_limit import BodyLimit
from backend.processes import spawn,terminate,configure_registry,recover_processes
from backend.store import Store

class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_body_limit_before_route_execution(self):
        app=AsyncMock();send=AsyncMock()
        receive=AsyncMock(side_effect=[{'type':'http.request','body':b'a'*6,'more_body':True},{'type':'http.request','body':b'b'*6,'more_body':False}])
        await BodyLimit(app,10)({'type':'http','method':'POST'},receive,send)
        app.assert_not_called();self.assertEqual(send.await_args_list[0].args[0]['status'],413)
    async def test_body_under_limit_replays_unchanged(self):
        bodies=[]
        async def app(scope,receive,send):
            while True:
                part=await receive();bodies.append(part['body'])
                if not part.get('more_body'):break
        receive=AsyncMock(side_effect=[{'type':'http.request','body':b'ab','more_body':True},{'type':'http.request','body':b'cd','more_body':False}])
        await BodyLimit(app,10)({'type':'http','method':'POST'},receive,AsyncMock())
        self.assertEqual(bodies,[b'ab',b'cd'])
    async def test_owned_child_registration_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'db');configure_registry(store)
            proc=None
            try:
                proc=await spawn([sys.executable,'-c','import time; time.sleep(60)'])
                self.assertEqual(store.objects('processes')[0]['pid'],proc.pid)
                await terminate(proc)
                self.assertFalse(store.objects('processes'))
            finally:
                if proc:await terminate(proc)
                configure_registry(None);store.close()
    async def test_recovery_does_not_kill_reused_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'db');proc=await spawn([sys.executable,'-c','import time; time.sleep(60)'])
            try:
                store.put('processes',str(proc.pid),{'pid':proc.pid,'created':0})
                await recover_processes(store)
                self.assertIsNone(proc.returncode)
            finally:await terminate(proc);store.close()
