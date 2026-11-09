import json,secrets,unittest
from unittest.mock import patch
import test_server,server,business_store as s

class BusinessHTTP(test_server.Accounts):
 def test_project_connections_and_admin_errors_isolation(self):
  a=self.call('signup',{'email':secrets.token_hex(5)+'@example.com','password':'business-password-123'});b=self.call('signup',{'email':secrets.token_hex(5)+'@example.com','password':'business-password-456'});cookie=a[2]
  self.assertEqual(self.call('projects')[0],401)
  p=self.call('projects',{'name':'Pilot','config':{}},cookie);self.assertEqual(p[0],200);pid=p[1]['id']
  self.assertEqual(self.call('projects/'+pid,cookie=b[2])[0],400)
  self.assertEqual(self.call('admin/errors',cookie=cookie)[0],403)
  self.assertEqual(self.call('admin/errors/settings',{'enabled':True,'recipient':'admin@example.com'},cookie)[0],403)
  with server.db() as c:c.execute('INSERT INTO admins VALUES(?)',(a[1]['user']['id'],))
  self.assertEqual(self.call('admin/errors',cookie=cookie)[0],200)
  self.assertEqual(self.call('admin/business',{'google_client_id':'client','google_client_secret':'PRIVATE','google_redirect_uri':'http://localhost:8000/api/connections/google/callback','ai_daily_limit':10},cookie)[0],200)
  self.assertNotIn('PRIVATE',json.dumps(self.call('admin/business',cookie=cookie)))
  r=self.call('orchestration',{'project':pid,'agent':'quotation','input':{}},cookie);self.assertEqual(r[0],200)
  self.assertEqual(self.call('orchestration',{'project':pid,'agent':'quotation','input':{}},b[2])[0],400)
  worker=self.call('business-workers',{'action':'register','browser':True},cookie);self.assertEqual(worker[0],200)
  self.assertNotIn('token',self.call('business-workers',cookie=cookie)[1]['workers'][0])
 def test_google_state_bound_to_user_and_session(self):
  a=self.call('signup',{'email':secrets.token_hex(5)+'@example.com','password':'business-password-123'});b=self.call('signup',{'email':secrets.token_hex(5)+'@example.com','password':'business-password-456'})
  with server.db() as c:
   import business_connectors
   business_connectors.admin_save(c,{'google_client_id':'id','google_client_secret':'secret','google_redirect_uri':'http://localhost:8000/api/connections/google/callback'})
  start=self.call('connections/google/start',{},a[2]);self.assertEqual(start[0],200)
  from urllib.parse import urlsplit,parse_qs
  state=parse_qs(urlsplit(start[1]['url']).query)['state'][0]
  with patch('business_connectors.http',side_effect=AssertionError('network')):
   self.assertEqual(self.call('connections/google/callback?state='+state+'&code=x',cookie=b[2])[0],400)
 def test_unhandled_exception_sanitized_and_admin_searchable(self):
  a=self.call('signup',{'email':secrets.token_hex(5)+'@example.com','password':'business-password-123'})
  with server.db() as c:c.execute('INSERT INTO admins VALUES(?)',(a[1]['user']['id'],))
  with patch('business_service.get',side_effect=RuntimeError('token=TOPSECRET private resume text')):
   r=self.call('projects',cookie=a[2]);self.assertEqual(r[0],500);self.assertNotIn('TOPSECRET',json.dumps(r));self.assertIn('error_id',r[1])
  data=self.call('admin/errors?q='+r[1]['error_id'],cookie=a[2]);self.assertEqual(data[0],200);self.assertTrue(data[1]['incidents']);self.assertNotIn('TOPSECRET',json.dumps(data))
 def test_all_agent_logos_are_served(self):
  import http.client
  for aid in s.AGENTS:
   c=http.client.HTTPConnection('127.0.0.1',self.port);c.request('GET','/brand/'+aid+'.svg');r=c.getresponse();self.assertEqual(r.status,200);self.assertIn(b'<svg',r.read());c.close()
