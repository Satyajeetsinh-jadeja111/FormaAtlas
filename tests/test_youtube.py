import datetime,hashlib,json,secrets,time,unittest,urllib.parse
from unittest.mock import patch
import server,settings,youtube_store as s,youtube_runtime as rt,youtube_google as g,youtube_service as service,youtube_worker_api as wa,youtube_render as render
import test_server as accounts
CID='UC'+'a'*22;OTHER='UC'+'b'*22
class YouTube(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  self.admin=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'administrator-password-123'});self.member=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'member-password-123'})
  self.uid=self.admin[1]['user']['id'];self.cookie=self.admin[2]
  with server.db() as c:
   c.execute('INSERT INTO admins VALUES(?)',(self.uid,));c.execute('UPDATE managed_agents SET live=0 WHERE id="youtube"');c.execute('DELETE FROM yt_jobs');c.execute('DELETE FROM yt_tasks');c.execute('DELETE FROM yt_workers');c.execute('UPDATE yt_runtime SET mode="laptop",epoch=1');c.execute('DELETE FROM yt_config')
 def connect(self,uid=None):
  uid=uid or self.uid
  a={'channel':CID if uid==self.uid else OTHER,'title':'My channel','email':'google@example.com','sub':'google-user','token':'hidden-upload-token','refresh':'hidden-refresh','expires':int(time.time())+3600,'analytics_token':'hidden-analytics-token','analytics_expires':int(time.time())+3600,'analytics_connected':True}
  with server.db() as c:s.put_account(c,uid,a)
  return a
 def configure(self):
  data={'client_id':'client','client_secret':'client-secret-hidden','redirect_uri':'http://localhost:8000/api/youtube/callback'}
  self.assertEqual(self.call('admin/youtube/settings',data,self.cookie)[0],200)
 def enabled(self):
  self.connect()
  p=s.DEFAULTS|{'niche':'Software engineering','audience':'Developers','expertise':'I build web applications','assets_rights':True,'made_for_kids':False,'channel_confirmed':True,'publishing_consent':True,'enabled':True}
  with server.db() as c:s.save_profile(c,self.uid,p)
  return p
 def worker(self,mode='laptop'):
  with server.db() as c:w=rt.register(c,mode);rt.heartbeat(c,w['id'],{'ffmpeg':True,'tts':True})
  return w
 def claim(self,w):
  with server.db() as c:
   c.execute('BEGIN IMMEDIATE');c.execute('UPDATE yt_jobs SET state="queued",due=?',(int(time.time())-1,));return rt.claim(c,w['id'])
 def test_private_visibility_and_authentication(self):
  self.assertEqual(self.call('youtube')[0],401);self.assertEqual(self.call('youtube',cookie=self.member[2])[0],400);self.assertEqual(self.call('admin/youtube',cookie=self.member[2])[0],403)
  self.assertNotIn('youtube',[a['id'] for a in self.call('agents',cookie=self.member[2])[1]])
  self.assertIn('youtube',[a['id'] for a in self.call('agents',cookie=self.cookie)[1]])
  self.assertEqual(self.call('admin/agents/toggle',{'id':'youtube','live':True},self.cookie)[0],200)
  self.assertEqual(self.call('youtube',cookie=self.member[2])[0],200)
 def test_secrets_encrypted_and_never_returned(self):
  self.configure();self.connect();r=self.call('admin/youtube',cookie=self.cookie);self.assertTrue(r[1]['settings']['client_secret_saved']);self.assertNotIn('client-secret-hidden',json.dumps(r));self.assertNotIn('hidden-upload-token',json.dumps(self.call('youtube',cookie=self.cookie)))
  with server.db() as c:self.assertNotIn(b'client-secret-hidden',c.execute('SELECT encrypted FROM yt_config').fetchone()[0])
 def test_oauth_state_bound_to_session_and_scope_separate(self):
  self.configure();self.connect()
  with server.db() as c:url=g.start(c,self.uid,'bound-session','analytics')
  q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query);self.assertNotIn('youtube.upload',q['scope'][0])
  with patch('youtube_google.http') as request:
   with self.assertRaises(s.YouTubeError):g.callback(server.db,self.uid,'wrong-session',{'state':q['state'],'code':['code']})
   request.assert_not_called()
  with server.db() as c:self.assertIsNotNone(c.execute('SELECT 1 FROM yt_oauth WHERE user=?',(self.uid,)).fetchone())
 def test_oauth_decline_preserves_existing_account(self):
  self.configure();self.connect()
  with server.db() as c:url=g.start(c,self.uid,'bound','analytics')
  state=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['state']
  with self.assertRaises(s.YouTubeError):g.callback(server.db,self.uid,'bound',{'state':state,'error':['access_denied']})
  with server.db() as c:self.assertEqual(s.account(c,self.uid)['token'],'hidden-upload-token')
 def test_oauth_channel_identity_and_granted_scope(self):
  self.configure()
  with server.db() as c:url=g.start(c,self.uid,'bound','upload')
  state=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['state']
  token={'access_token':'hidden-token','expires_in':3600,'refresh_token':'refresh','scope':'openid https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'}
  with patch('youtube_google.http',side_effect=[token,{'sub':'google','email':'google@example.com','email_verified':True},{'items':[{'id':CID,'snippet':{'title':'My channel'}}]}]):g.callback(server.db,self.uid,'bound',{'state':state,'code':['code']})
  with server.db() as c:self.assertEqual(s.account(c,self.uid)['channel'],CID);self.assertFalse(s.profile(c,self.uid)['enabled'])
 def test_settings_limits_and_consent(self):
  p=self.enabled()
  for fields in ({'shorts_count':16},{'long_count':6},{'long_count':5,'long_seconds':3000},{'shorts_seconds':181},{'publishing_consent':False},{'made_for_kids':None}):
   self.assertEqual(self.call('youtube/settings',p|fields,self.cookie)[0],400)
  self.assertEqual(self.call('youtube/settings',p|{'long_count':5,'long_seconds':2880},self.cookie)[0],200)
 def test_free_plan_cannot_enable_daily_automation(self):
  self.enabled();self.connect(self.member[1]['user']['id'])
  with server.db() as c:c.execute('UPDATE managed_agents SET live=1 WHERE id="youtube"')
  p=s.DEFAULTS|{'niche':'Tech','audience':'People','expertise':'Web work','assets_rights':True,'made_for_kids':False,'channel_confirmed':True,'publishing_consent':True,'enabled':True}
  self.assertEqual(self.call('youtube/settings',p,self.member[2])[0],400)
 def test_missing_provider_blocks_without_network_or_upload(self):
  self.enabled()
  with patch('youtube_google.http') as google,patch('youtube_provider.call') as provider:
   result=self.call('youtube/preview',{'format':'shorts'},self.cookie);self.assertEqual(result[0],200)
   self.assertEqual(self.call('youtube',cookie=self.cookie)[1]['jobs'][0]['state'],'blocked');google.assert_not_called();provider.assert_not_called()
 def test_daily_caps_and_timezone_not_reset(self):
  p=self.enabled();p.update(shorts_count=15,long_count=5,long_seconds=2880,shorts_start='00:00',long_start='00:00',spacing_minutes=30)
  with server.db() as c:
   s.save_profile(c,self.uid,p)
   for i in range(5):s.reserve(c,self.uid,'long',i)
   with self.assertRaises(s.YouTubeError):s.reserve(c,self.uid,'long',5)
   with self.assertRaises(s.YouTubeError):s.save_profile(c,self.uid,p|{'timezone':'UTC'})
   for i in range(15):s.reserve(c,self.uid,'shorts',i)
   with self.assertRaises(s.YouTubeError):s.reserve(c,self.uid,'shorts',15)
 def test_no_historical_catch_up_burst(self):
  p=self.enabled();p.update(shorts_count=5,long_count=0,shorts_start='09:00')
  with server.db() as c:
   s.save_profile(c,self.uid,p);now=datetime.datetime(2026,10,21,8,0,tzinfo=datetime.timezone.utc);rt.schedule(c,now);rt.schedule(c,now)
   rows=c.execute('SELECT slot FROM yt_jobs WHERE user=? AND preview=0',(self.uid,)).fetchall();self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],4)
 def test_single_video_lease_fencing_and_mode_switch(self):
  self.enabled()
  with server.db() as c:s.reserve(c,self.uid,'shorts',preview=True)
  w=self.worker();ctx=self.claim(w);self.assertIsNotNone(ctx)
  with server.db() as c:
   self.assertIsNone(rt.claim(c,w['id']))
   with self.assertRaises(s.YouTubeError):rt.switch(c,'server')
   with self.assertRaises(s.YouTubeError):rt.guard(c,w['id'],{'id':ctx['id'],'type':'video','lease':'wrong'})
   c.execute('UPDATE yt_jobs SET lease_until=0');rt.schedule(c)
   self.assertEqual(c.execute('SELECT state FROM yt_jobs').fetchone()[0],'held')
 def test_mode_requires_online_destination(self):
  with server.db() as c:
   with self.assertRaises(s.YouTubeError):rt.switch(c,'server')
  w=self.worker('server')
  with server.db() as c:self.assertEqual(rt.switch(c,'server')['mode'],'server')
 def test_pause_stops_next_external_step(self):
  self.enabled()
  with server.db() as c:s.reserve(c,self.uid,'shorts',0)
  w=self.worker();ctx=self.claim(w);self.call('youtube/pause',{},self.cookie)
  with server.db() as c:
   with self.assertRaises(s.YouTubeError):rt.guard(c,w['id'],ctx)
 def test_worker_context_excludes_unrelated_secrets(self):
  self.enabled()
  with server.db() as c:settings.update(c,{'linkedin_client_secret':'must-not-leave-server','smtp_password':'also-private'});s.reserve(c,self.uid,'shorts',preview=True)
  ctx=self.claim(self.worker());self.assertNotIn('must-not-leave-server',json.dumps(ctx));self.assertNotIn('also-private',json.dumps(ctx));self.assertNotIn('hidden-analytics-token',json.dumps(ctx))
 def test_worker_asset_ownership_and_duration_checks(self):
  self.enabled()
  with server.db() as c:s.reserve(c,self.uid,'shorts',preview=True)
  w=self.worker();ctx=self.claim(w);identity={k:ctx[k] for k in ('id','lease','type')}
  with server.db() as c:
   with self.assertRaises(s.YouTubeError):rt.update(c,w['id'],identity|{'private':{'measured_seconds':181}})
   asset=wa.action(c,w['id'],'asset/start',identity|{'kind':'captions'})
   import base64
   wa.action(c,w['id'],'asset/chunk',identity|{'asset':asset['id'],'offset':0,'data':base64.b64encode(b'private captions').decode()});wa.action(c,w['id'],'asset/finish',identity|{'asset':asset['id']})
  self.assertEqual(self.call('youtube/assets/'+asset['id'],cookie=self.member[2])[0],400)
 def test_cache_isolation_throttle_and_report_readiness(self):
  self.connect();self.configure()
  with server.db() as c:c.execute('INSERT INTO yt_cache VALUES(?,?,?,?,?,?,?)',(self.uid,CID,service.report_key({}),'{}',int(time.time()),int(time.time()),''))
  self.assertEqual(self.call('youtube/refresh',{},self.cookie)[0],400)
  with server.db() as c:
   c.execute('UPDATE managed_agents SET live=1 WHERE id="youtube"')
  self.connect(self.member[1]['user']['id'])
  result=self.call('youtube/report',cookie=self.member[2]);self.assertEqual(result[0],200);self.assertIsNone(result[1]['data'])
 def test_private_report_cannot_query_another_channel_video(self):
  self.connect()
  with patch('youtube_google.get',return_value={'items':[{'snippet':{'channelId':OTHER}}]}),patch('youtube_google.http') as network:
   with self.assertRaises(s.YouTubeError):g.analytics(server.db,self.uid,{'video':'abcdefghijk'})
   network.assert_not_called()
 def test_competitor_resolution_and_limit(self):
  self.connect()
  with patch('youtube_google.get',return_value={'items':[{'id':OTHER,'snippet':{'title':'Public channel'}}]}):
   result=self.call('youtube/competitors/resolve',{'value':'https://www.youtube.com/@public'},self.cookie);self.assertEqual(result[0],200)
   self.assertEqual(self.call('youtube/competitors/add',{'id':OTHER},self.cookie)[0],200)
  with patch('youtube_google.get') as google:
   self.assertEqual(self.call('youtube/competitors/resolve',{'value':'https://localhost/@x'},self.cookie)[0],400);google.assert_not_called()
 def test_competitor_retention(self):
  self.connect()
  with server.db() as c:
   c.execute('INSERT INTO yt_cache VALUES(?,?,?,?,?,?,?)',(self.uid,CID,'competitor:'+OTHER,'{}',int(time.time())-31*86400,0,''));rt.schedule(c);self.assertIsNone(c.execute('SELECT * FROM yt_cache WHERE user=?',(self.uid,)).fetchone())
 def test_sender_secret_and_recipient_verification(self):
  self.connect();data={'smtp_host':'smtp.example.com','smtp_port':465,'smtp_security':'tls','smtp_username':'sender','smtp_from':'sender@example.com','smtp_password':'private-password'}
  self.assertEqual(self.call('me/mail',data,self.cookie)[0],200);self.assertNotIn('private-password',json.dumps(self.call('me/mail',cookie=self.cookie)))
  with server.db() as c:
   result=service.start_verification(c,self.uid,'google');self.assertEqual(result['email'],'google@example.com');code=s.decrypt(c.execute('SELECT detail FROM yt_outbox WHERE user=?',(self.uid,)).fetchone()[0].encode())['code']
   self.assertTrue(service.verify_email(c,self.uid,'google@example.com',code)['verified'])
   p=s.profile(c,self.uid);p['notify_google']=True;p['notify_primary']=True;s.save_profile(c,self.uid,p);s.queue_mail(c,self.uid,'published','video-job');s.queue_mail(c,self.uid,'published','video-job')
   self.assertEqual(c.execute('SELECT COUNT(*) FROM yt_outbox WHERE user=? AND event="published"',(self.uid,)).fetchone()[0],1)
 def test_local_data_deletion_does_not_delete_youtube_videos(self):
  self.connect()
  with patch('youtube_google.http') as network:
   self.assertEqual(self.call('youtube/delete-data',{'confirm':'wrong'},self.cookie)[0],400)
   self.assertEqual(self.call('youtube/delete-data',{'confirm':'DELETE MY FORMA YOUTUBE DATA'},self.cookie)[0],200);network.assert_not_called()
  with server.db() as c:self.assertIsNone(s.account(c,self.uid))
 def test_storyboard_validation(self):
  valid={'title':'An original engineering lesson','description':'Factual perspective','tags':['engineering'],'scenes':[{'seconds':30,'narration':'A short verified lesson.','prompt':'An original software illustration'}]}
  self.assertEqual(render.validate_storyboard(valid,30),valid)
  with self.assertRaises(s.YouTubeError):render.validate_storyboard(valid|{'scenes':[{'seconds':300,'narration':'x','prompt':'x'}]},30)
 def test_mode_switch_drains_before_changing_epoch(self):
  self.enabled()
  with server.db() as c:s.reserve(c,self.uid,'shorts',preview=True)
  laptop=self.worker();ctx=self.claim(laptop);remote=self.worker('server')
  with server.db() as c:
   before=c.execute('SELECT epoch FROM yt_runtime').fetchone()[0];pending=rt.switch(c,'server');self.assertEqual(pending['pending_mode'],'server');self.assertEqual(pending['mode'],'laptop');self.assertIsNone(rt.claim(c,laptop['id']))
   rt.update(c,laptop['id'],{k:ctx[k] for k in ('id','lease','type')}|{'state':'ready','detail':'Private fixture preview'})
   rt.schedule(c);current=c.execute('SELECT * FROM yt_runtime').fetchone();self.assertEqual(current['mode'],'server');self.assertEqual(current['epoch'],before+1)
 def test_revocation_clears_cached_owner_reports(self):
  self.enabled()
  with server.db() as c:
   s.reserve(c,self.uid,'shorts',preview=True);c.execute('INSERT INTO yt_cache VALUES(?,?,?,?,?,?,?)',(self.uid,CID,'videos','{}',int(time.time()),0,''))
  laptop=self.worker();ctx=self.claim(laptop)
  with server.db() as c:
   rt.update(c,laptop['id'],{k:ctx[k] for k in ('id','lease','type')}|{'state':'held','detail':'Google revoked access','revoked_kind':'upload'})
   self.assertIsNone(s.account(c,self.uid));self.assertFalse(s.profile(c,self.uid)['enabled']);self.assertIsNone(c.execute('SELECT 1 FROM yt_cache WHERE user=?',(self.uid,)).fetchone())
 def test_worker_routes_reject_unregistered_credentials(self):
  import http.client
  c=http.client.HTTPConnection('127.0.0.1',self.port)
  c.request('POST','/api/youtube-worker/claim',json.dumps({}),{'Content-Type':'application/json','X-Forma-Worker':'unknown','Authorization':'Bearer forged'})
  r=c.getresponse();self.assertEqual(r.status,403);r.read();c.close()
 def test_database_init_is_idempotent_after_runtime_migration(self):
  server.init();server.init()
  with server.db() as c:self.assertIn('pending_mode',{r[1] for r in c.execute('PRAGMA table_info(yt_runtime)')})
 def test_known_upload_session_recovered_without_new_upload_intent(self):
  self.enabled()
  with server.db() as c:s.reserve(c,self.uid,'shorts',preview=True)
  w=self.worker();ctx=self.claim(w);identity={k:ctx[k] for k in ('id','lease','type')}
  with server.db() as c:
   rt.update(c,w['id'],identity|{'state':'uploading','private':{'upload_session':'https://www.googleapis.com/upload/saved-session','upload_intent':True}})
   c.execute('UPDATE yt_jobs SET lease_until=0');rt.schedule(c)
   self.assertEqual(c.execute('SELECT state FROM yt_jobs').fetchone()[0],'queued')
 def test_remote_narration_readiness_uses_selected_worker(self):
  self.worker('server')
  with server.db() as c:
   rt.switch(c,'server')
   with patch('youtube_store.dependencies',return_value={'tts':False}):self.assertTrue(s.worker_dependencies(c)['tts'])
