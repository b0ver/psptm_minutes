import json,threading,unittest
from http.server import BaseHTTPRequestHandler,HTTPServer
from smoke_restored_http import smoke
class Tests(unittest.TestCase):
 def test_external_denied(self):
  for u in ('https://example.com','http://localhost:8080','http://127.0.0.1@evil.test','http://127.0.0.1:8080/private'):
   with self.subTest(u=u),self.assertRaises(ValueError):smoke(u)
 def test_local_get_only(self):
  paths=[]
  class Handler(BaseHTTPRequestHandler):
   def do_GET(self):
    paths.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok"}' if self.path=='/healthz' else b'synthetic')
   def log_message(self,*a):pass
  server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:self.assertEqual(smoke('http://127.0.0.1:'+str(server.server_port))['external_requests'],0);self.assertEqual(paths,['/healthz','/'])
  finally:server.shutdown();thread.join();server.server_close()
