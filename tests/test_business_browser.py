import json,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from business_browser import run

class Fixture(BaseHTTPRequestHandler):
 broken=True
 def log_message(self,*args):pass
 def do_GET(self):
  body=('<html><body><h1>Test store</h1><label>Email<input name="email"></label>'+('<script>throw Error("Fixture intentional failure")</script>' if self.broken else '')+'</body></html>').encode();self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)

class BrowserFixture(unittest.TestCase):
 def test_real_browser_failure_recovery_and_private_evidence(self):
  http=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=http.serve_forever,daemon=True).start();site='http://127.0.0.1:'+str(http.server_port)
  config={'sites':[site],'allow_local':True,'journeys':[{'id':'signup','site':site,'authorized':True,'steps':[{'action':'expect_text','text':'Test store'},{'action':'fill','label':'Email','value_key':'test_email'}]}]}
  try:
   Fixture.broken=True;failed=run(config,{'test_email':'PRIVATE_TEST_VALUE'});self.assertEqual(failed['state'],'failed');self.assertTrue(failed['evidence']);self.assertNotIn('PRIVATE_TEST_VALUE',json.dumps(failed))
   Fixture.broken=False;passed=run(config,{'test_email':'PRIVATE_TEST_VALUE'});self.assertEqual(passed['state'],'passed');self.assertEqual(len(passed['journeys']),2)
  finally:http.shutdown();http.server_close()

 def test_watchdog_to_qa_to_authorized_github_issue(self):
  import test_business,business_store as s,business_runtime as rt,business_connectors as connectors
  from unittest.mock import patch
  helper=test_business.Business('test_quote_decimal_and_missing_requirements');helper.setUp()
  http=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=http.serve_forever,daemon=True).start();site='http://127.0.0.1:'+str(http.server_port);Fixture.broken=True
  try:
   with helper.connect() as c:
    connection=connectors.save(c,helper.user,{'kind':'github','name':'Fixture repository','config':{'token':'fixture-token','repositories':['owner/repo'],'issues_enabled':True}})
    p=s.project(c,helper.user,helper.project['id']);cfg=p['config']|{'sites':[site],'allow_local':True,'journeys':[{'id':'homepage','site':site,'authorized':True,'steps':[{'action':'expect_text','text':'Test store'}]}],'connections':{'github':connection['id']},'rules':[{'enabled':True,'kind':'github_issue','destination':'owner/repo','template':'qa_v1','daily_limit':3}]}
    s.save_project(c,helper.user,{'id':p['id'],'revision':p['revision'],'name':p['name'],'config':cfg})
   helper.task('revenue-watchdog');rt.tick(helper.connect,helper.worker);rt.tick(helper.connect,helper.worker)
   with patch('business_connectors.http',return_value={'number':1,'html_url':'https://github.com/owner/repo/issues/1'}) as github:
    rt.tick(helper.connect,helper.worker);self.assertEqual(github.call_count,1);self.assertEqual(github.call_args.args[0],'https://api.github.com/repos/owner/repo/issues')
   result=helper.snapshot();self.assertTrue(any(e['kind']=='watch_failed' for e in result['events']));self.assertTrue(any(a['kind']=='qa_report' for a in result['artifacts']));self.assertEqual(result['actions'][0]['state'],'succeeded');self.assertTrue(result['evidence'])
  finally:http.shutdown();http.server_close();helper.tearDown()
