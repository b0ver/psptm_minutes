"""Read-only local app smoke, inside isolated old-API restore environment only."""
import json,urllib.request,urllib.parse

def smoke(base='http://127.0.0.1:8080'):
 u=urllib.parse.urlsplit(base)
 if u.scheme!='http' or u.hostname!='127.0.0.1' or u.username or u.password or u.path not in ('','/') or u.query or u.fragment:raise ValueError('local_only')
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):raise ValueError('redirect_denied')
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 with opener.open(base.rstrip('/')+'/healthz',timeout=5) as r:
  assert r.status==200;health=json.loads(r.read(65537));assert health.get('status')=='ok'
 with opener.open(base.rstrip('/')+'/',timeout=5) as r:
  assert r.status==200 and r.read(1)
 return {'local_healthz':'ok','local_homepage':'ok','external_requests':0,'mutating_requests':0}
if __name__=='__main__':print(json.dumps(smoke()))
