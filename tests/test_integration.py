"""Real local Socket.IO / ASGI integration; no GPU or model downloads.

Requires python-socketio and aiohttp. Skipped explicitly when unavailable.
The CI workflow installs these and runs this test against a real Uvicorn process.
"""
from __future__ import annotations
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import httpx

AVAILABLE=bool(importlib.util.find_spec('socketio') and importlib.util.find_spec('aiohttp'))

@unittest.skipUnless(AVAILABLE,'python-socketio/aiohttp unavailable: real Socket.IO integration not run')
class SocketIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import socketio
        self.temp=tempfile.TemporaryDirectory();self.token='integration-token-no-real-secrets-123456'
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1]
        self.url=f'http://127.0.0.1:{self.port}'
        self.env={**os.environ,'APP_STATE_DIR':self.temp.name,'APP_TOKEN':self.token,'AUTO_INSTALL':'0','DEMO_MODE':'1',
                  'APP_ORIGIN':self.url,'BACKEND_PORT':str(self.port)}
        self.log=open(Path(self.temp.name)/'server.log','w+')
        self.proc=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app:app','--host','127.0.0.1','--port',str(self.port)],
            cwd=Path(__file__).resolve().parents[1],env=self.env,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
        self.client=httpx.AsyncClient(base_url=self.url,headers={'Authorization':'Bearer '+self.token})
        for _ in range(100):
            if self.proc.poll() is not None:raise AssertionError('Uvicorn exited; inspect integration log')
            try:
                response=await self.client.get('/api/health')
                if response.status_code==200:break
            except httpx.HTTPError:pass
            await asyncio.sleep(.05)
        else:raise AssertionError('Uvicorn failed to become ready')
        self.io=socketio.AsyncClient(reconnection=False);self.events=[]
        self.io.on('event',lambda event:self.events.append(event))
        await self.io.connect(self.url,auth={'token':self.token},headers={'Origin':self.url},transports=['websocket'])
    async def asyncTearDown(self):
        if hasattr(self,'io') and self.io.connected:await self.io.disconnect()
        if hasattr(self,'client'):await self.client.aclose()
        if hasattr(self,'proc') and self.proc.poll() is None:
            self.proc.terminate()
            try:await asyncio.to_thread(self.proc.wait,timeout=15)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        if hasattr(self,'log'):self.log.close()
        self.temp.cleanup()
    async def wait_job(self,ident):
        for _ in range(200):
            job=(await self.client.get('/api/jobs/'+ident)).json()
            if job['status'] in ('completed','failed','cancelled','interrupted'):return job
            await asyncio.sleep(.03)
        raise AssertionError('Job timed out')
    async def test_websocket_submit_stream_disconnect_replay(self):
        reply=await self.io.call('job:submit',{'kind':'model.load','payload':{'model_id':'demo:echo'}},timeout=5)
        self.assertTrue(reply['ok']);job=await self.wait_job(reply['job']['id']);self.assertEqual(job['status'],'completed')
        reply=await self.io.call('job:submit',{'kind':'chat','payload':{'chat_id':'integration','model_id':'demo:echo','prompt':'a b c d e f g h i j k l m n o p'}},timeout=5)
        ident=reply['job']['id']
        for _ in range(100):
            if any(e['type']=='chat.delta' and e['job_id']==ident for e in self.events):break
            await asyncio.sleep(.02)
        self.assertTrue(any(e['type']=='chat.delta' and e['job_id']==ident for e in self.events))
        cursor=self.events[-1]['seq'];await self.io.disconnect()
        done=await self.wait_job(ident);self.assertEqual(done['status'],'completed');self.assertIn('[DEMO',done['result']['content'])
        await self.io.connect(self.url,auth={'token':self.token},headers={'Origin':self.url},transports=['websocket'])
        sync=await self.io.call('sync',{'after':cursor},timeout=5)
        self.assertTrue(sync['ok']);self.assertTrue(any(e['seq']>cursor and e['job_id']==ident for e in sync['events']))
    async def test_http_auth_cookie_and_origin(self):
        async with httpx.AsyncClient(base_url=self.url) as client:
            self.assertEqual((await client.get('/api/state')).status_code,401)
            response=await client.post('/api/login',json={'token':self.token},headers={'Origin':self.url,'X-Console-Request':'1'})
            self.assertEqual(response.status_code,200);self.assertIn('HttpOnly',response.headers['set-cookie'])
            self.assertEqual((await client.get('/api/state')).status_code,200)
            wrong=await client.post('/api/jobs',json={'kind':'model.load','payload':{'model_id':'demo:echo'}},headers={'Origin':'https://evil.invalid','X-Console-Request':'1'})
            self.assertEqual(wrong.status_code,403)
    async def test_invalid_socket_input_and_denied_auth(self):
        import socketio
        reply=await self.io.call('job:submit',{'kind':'not-a-job','payload':{}},timeout=5);self.assertFalse(reply['ok'])
        reply=await self.io.call('sync',{'after':'invalid'},timeout=5);self.assertFalse(reply['ok'])
        bad=socketio.AsyncClient(reconnection=False)
        try:
            with self.assertRaises(socketio.exceptions.ConnectionError):
                await bad.connect(self.url,auth={'token':'wrong'},headers={'Origin':self.url},transports=['websocket'])
        finally:
            if bad.connected:await bad.disconnect()
