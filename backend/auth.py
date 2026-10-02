from __future__ import annotations
import hashlib
import hmac
from http.cookies import SimpleCookie
import time

class Auth:
    def __init__(self,settings): self.settings=settings; self.failures={}
    def valid_token(self,value):
        return isinstance(value,str) and hmac.compare_digest(value,self.settings.token)
    def session(self):
        expiry=str(int(time.time())+7*86400)
        signature=hmac.new(self.settings.token.encode(),('session:'+expiry).encode(),hashlib.sha256).hexdigest()
        return expiry+'.'+signature
    def valid_session(self,value):
        try:
            expiry,signature=value.split('.',1)
            if int(expiry)<time.time(): return False
            expected=hmac.new(self.settings.token.encode(),('session:'+expiry).encode(),hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature,expected)
        except (ValueError,TypeError,AttributeError): return False
    def authorised(self,headers):
        bearer=headers.get('authorization','')
        if bearer.startswith('Bearer ') and self.valid_token(bearer[7:]): return True
        cookies=SimpleCookie()
        try: cookies.load(headers.get('cookie',''))
        except Exception: return False
        return 'console_session' in cookies and self.valid_session(cookies['console_session'].value)
    def csrf_ok(self,headers):
        origin=headers.get('origin')
        if origin and origin not in self.settings.origins: return False
        if headers.get('authorization','').startswith('Bearer '): return True
        return origin in self.settings.origins and headers.get('x-console-request')=='1'
    def rate_limit(self,peer):
        now=time.monotonic(); failures=[t for t in self.failures.get(peer,[]) if now-t<60]
        self.failures[peer]=failures
        return len(failures)<10
    def failed(self,peer): self.failures.setdefault(peer,[]).append(time.monotonic())
