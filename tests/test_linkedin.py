import datetime,json,unittest,urllib.parse
from unittest.mock import patch
import server,settings,linkedin,scheduler,test_server as accounts
class LinkedIn(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  import secrets
  self.account=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'linked-in-password-123'});self.uid=self.account[1]['user']['id'];self.cookie=self.account[2]
  with server.db() as c:
   c.execute('DELETE FROM workspaces')
   c.execute('INSERT INTO admins VALUES(?)',(self.uid,))
   settings.update(c,{'linkedin_client_id':'test-client','linkedin_client_secret':'private-client-secret','linkedin_redirect_uri':'http://localhost:8000/api/linkedin/callback','notifications_enabled':False})
 def connect(self):
  with server.db() as c:url=linkedin.start(c,self.uid,'session-bound')
  state=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['state'][0]
  with patch('linkedin.request',side_effect=[(200,{'access_token':'private-member-token','expires_in':3600},{}),(200,{'sub':'member-id','name':'Test Member'},{})]) as request:
   with self.assertRaises(linkedin.LinkedInError):linkedin.callback(server.db,'someone-else','session-bound',{'state':[state],'code':['test']})
   linkedin.callback(server.db,self.uid,'session-bound',{'state':[state],'code':['test']});self.assertEqual(request.call_count,2)
   with self.assertRaises(linkedin.LinkedInError):linkedin.callback(server.db,self.uid,'session-bound',{'state':[state],'code':['test']})
  with server.db() as c:
   status=linkedin.status(c,self.uid);self.assertTrue(status['connected']);self.assertNotIn('private-member-token',json.dumps(status));raw=c.execute('SELECT encrypted FROM linkedin_accounts WHERE user=?',(self.uid,)).fetchone()[0];self.assertNotIn(b'private-member-token',raw)
 def test_oauth_publish_requires_exact_approval_and_no_duplicates(self):
  self.connect();w={'drafts':[{'id':'draft','text':'Verified experience. Open to freelance and full-time work.','status':'review'}]};self.call('workspace',w,self.cookie)
  with patch('linkedin.request',return_value=(201,{}, {'X-RestLi-Id':'urn:li:share:123'})) as request:
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft',w['drafts'][0]['text'])
   request.assert_not_called();w['drafts'][0]['status']='approved';self.call('workspace',w,self.cookie)
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft','unsaved changes')
   self.assertEqual(linkedin.publish(server.db,self.uid,'draft',w['drafts'][0]['text']),'urn:li:share:123')
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft',w['drafts'][0]['text'])
   self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.args[1]['author'],'urn:li:person:member-id')
 def test_ambiguous_publication_is_not_retried(self):
  self.connect();self.call('workspace',{'drafts':[{'id':'uncertain','text':'Approved fact','status':'approved'}]},self.cookie)
  with patch('linkedin.request',side_effect=linkedin.LinkedInError('timeout')) as request:
   for _ in range(2):
    with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'uncertain','Approved fact')
   self.assertEqual(request.call_count,1)
  with server.db() as c:self.assertEqual(linkedin.status(c,self.uid)['publications'][0]['status'],'unknown')
 def test_daily_deduplication_timezone_and_preserve_scheduled_drafts(self):
  self.call('workspace',{'profile':{'daily_enabled':True,'name':'Person','resume':'Verified skill','timezone':'Asia/Kolkata','time':'09:00'},'drafts':[]},self.cookie)
  now=datetime.datetime(2026,10,17,4,0,tzinfo=datetime.timezone.utc)
  self.assertEqual(scheduler.tick(server.db,now),1);self.assertEqual(scheduler.tick(server.db,now),0)
  w=self.call('workspace',cookie=self.cookie)[1];self.assertEqual(w['drafts'][0]['day'],'2026-10-17');self.assertIsNotNone(w['drafts'][0]['image']);self.assertEqual(w['drafts'][0]['status'],'review');self.assertIn('freelance',w['drafts'][0]['text']);self.assertIn('full-time',w['drafts'][0]['text'])
  self.call('workspace',{'profile':w['profile'],'drafts':[]},self.cookie);self.assertEqual(len(self.call('workspace',cookie=self.cookie)[1]['drafts']),1)
 def test_automatic_schedule_count_spacing_pause_and_consent(self):
  p={'daily_enabled':True,'auto_publish':True,'automation_consent':True,'name':'Test Person','resume':'Verified software project','timezone':'UTC','time':'09:00','posts_per_day':3,'post_interval_minutes':30}
  self.assertEqual(self.call('workspace',{'profile':p,'drafts':[]},self.cookie)[0],200)
  now=datetime.datetime(2026,10,20,9,0,tzinfo=datetime.timezone.utc)
  with patch('linkedin.publish',return_value='mock-published') as publish,patch('settings.complete'):
   scheduler.tick(server.db,now);self.assertEqual(publish.call_count,1)
   scheduler.tick(server.db,now+datetime.timedelta(minutes=10));self.assertEqual(publish.call_count,1)
   scheduler.tick(server.db,now+datetime.timedelta(minutes=30));self.assertEqual(publish.call_count,2)
   self.call('automation/pause',{},self.cookie);scheduler.tick(server.db,now+datetime.timedelta(hours=1));self.assertEqual(publish.call_count,2)
  p['automation_consent']=False;self.assertEqual(self.call('workspace',{'profile':p,'drafts':[]},self.cookie)[0],400)
  p['auto_publish']=False;p['posts_per_day']=11;self.assertEqual(self.call('workspace',{'profile':p,'drafts':[]},self.cookie)[0],400)
 def test_automatic_failure_is_recorded_without_retry(self):
  p={'daily_enabled':True,'auto_publish':True,'automation_consent':True,'name':'Test Person','resume':'Verified facts','timezone':'UTC','time':'09:00','posts_per_day':1,'post_interval_minutes':60}
  self.call('workspace',{'profile':p,'drafts':[]},self.cookie);now=datetime.datetime(2026,10,21,9,0,tzinfo=datetime.timezone.utc)
  with patch('linkedin.publish',side_effect=linkedin.LinkedInError('Unavailable')) as publish,patch('settings.complete'):
   scheduler.tick(server.db,now);scheduler.tick(server.db,now+datetime.timedelta(hours=2));self.assertEqual(publish.call_count,1)
  with server.db() as c:self.assertEqual(c.execute('SELECT status FROM scheduled_slots WHERE user=?',(self.uid,)).fetchone()[0],'failed_or_unknown')
 def test_scheduled_gemini_news_uses_source_and_publishes_after_generation(self):
  p={'daily_enabled':True,'auto_publish':True,'automation_consent':True,'auto_ai':True,'ai_consent':True,'content_mix':'news','name':'Test Person','resume':'Verified facts','timezone':'UTC','time':'09:00'}
  self.call('workspace',{'profile':p,'drafts':[]},self.cookie)
  with server.db() as c:settings.update(c,{'gemini_key':'test-key','model':'gemini-test','free_tier_confirmed':True})
  now=datetime.datetime(2026,10,23,9,0,tzinfo=datetime.timezone.utc);source={'title':'Official technology release','summary':'Published release details.','publisher':'GitHub Engineering','published':'2026-10-22T10:00:00+00:00','url':'https://github.blog/release'}
  with patch('scheduler.news.select',return_value=source),patch('scheduler.refine',return_value='According to GitHub: release details.\nSource: https://github.blog/release') as refine,patch('linkedin.publish',return_value='mock') as publish,patch('settings.complete'):
   scheduler.tick(server.db,now);refine.assert_called_once();self.assertEqual(refine.call_args.kwargs['source'],source);publish.assert_called_once();self.assertIn('release details',publish.call_args.args[3])
  w=self.call('workspace',cookie=self.cookie)[1];self.assertEqual(w['drafts'][0]['mode'],'scheduled-gemini');self.assertEqual(w['drafts'][0]['source'],source['url']);self.assertIsNotNone(w['drafts'][0]['image'])
 def test_scheduled_gemini_failure_holds_post(self):
  from providers import ProviderUnavailable
  p={'daily_enabled':True,'auto_publish':True,'automation_consent':True,'auto_ai':True,'ai_consent':True,'content_mix':'projects','name':'Test Person','resume':'Verified facts','timezone':'UTC','time':'09:00'}
  self.call('workspace',{'profile':p,'drafts':[]},self.cookie)
  with server.db() as c:settings.update(c,{'gemini_key':'test-key','model':'gemini-test','free_tier_confirmed':True})
  now=datetime.datetime(2026,10,24,9,0,tzinfo=datetime.timezone.utc)
  with patch('scheduler.refine',side_effect=ProviderUnavailable('Quota')),patch('linkedin.publish') as publish,patch('settings.complete'):
   scheduler.tick(server.db,now);scheduler.tick(server.db,now);publish.assert_not_called()
  with server.db() as c:self.assertEqual(c.execute('SELECT status FROM scheduled_slots WHERE user=?',(self.uid,)).fetchone()[0],'generation_blocked')
