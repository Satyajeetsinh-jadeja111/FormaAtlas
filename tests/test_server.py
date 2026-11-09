import http.client,json,os,tempfile,threading,unittest
import server
class Accounts(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();server.DB=cls.tmp.name+'/test.sqlite3';server.init();cls.http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=cls.http.serve_forever,daemon=True).start();cls.port=cls.http.server_port
 @classmethod
 def tearDownClass(cls):cls.http.shutdown();cls.http.server_close();cls.tmp.cleanup()
 def call(self,path,data=None,cookie=None,origin=None):
  c=http.client.HTTPConnection('127.0.0.1',self.port);headers={'Content-Type':'application/json'}
  if cookie:headers['Cookie']=cookie
  if origin:headers['Origin']=origin
  c.request('POST' if data is not None else 'GET','/api/'+path,json.dumps(data) if data is not None else None,headers);r=c.getresponse();result=(r.status,json.loads(r.read()),r.getheader('Set-Cookie'));c.close();return result
 def test_accounts_are_isolated_and_logout_revokes(self):
  a=self.call('signup',{'email':'one@example.com','password':'long-password-123'});b=self.call('signup',{'email':'two@example.com','password':'long-password-456'});self.assertEqual(a[0],200);self.assertIn('HttpOnly',a[2]);self.assertEqual(self.call('login',{'email':'one@example.com','password':'long-password-123'})[0],200);self.assertEqual(self.call('workspace',{'profile':{'resume':'private facts'}},a[2])[0],200);self.assertEqual(self.call('workspace',cookie=b[2])[1],{});self.assertEqual(self.call('workspace')[0],401);self.assertEqual(self.call('login',{'email':'one@example.com','password':'wrong-password'})[0],401);self.assertEqual(self.call('logout',{},a[2])[0],200);self.assertEqual(self.call('workspace',cookie=a[2])[0],401)
 def test_origin_and_validation(self):
  self.assertEqual(self.call('signup',{'email':'bad','password':'short'})[0],400);self.assertEqual(self.call('signup',{},origin='https://evil.example')[0],403)
if __name__=='__main__':unittest.main()
