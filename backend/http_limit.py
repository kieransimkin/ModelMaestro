"""Bound API POST bodies, including chunked requests without Content-Length."""
import asyncio

class BodyLimit:
    def __init__(self,app,limit=1_048_576):self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method') not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        chunks=[];size=0
        try:
            async with asyncio.timeout(30):
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect':return
                    size+=len(message.get('body',b''))
                    if size>self.limit:
                        await self.error(send,413,b'{"detail":"Request body too large"}');return
                    chunks.append(message)
                    if not message.get('more_body',False):break
        except TimeoutError:
            await self.error(send,408,b'{"detail":"Request body timeout"}');return
        async def replay():
            if chunks:return chunks.pop(0)
            return await receive()
        await self.app(scope,replay,send)
    @staticmethod
    async def error(send,status,body):
        await send({'type':'http.response.start','status':status,'headers':[(b'content-type',b'application/json')]})
        await send({'type':'http.response.body','body':body})
