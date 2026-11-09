import base64,io,json,math,os,secrets,sqlite3,struct,tempfile,time,unittest,wave
from pathlib import Path
from unittest.mock import patch
import server,youtube_store as s,youtube_runtime as rt,youtube_worker_api as wa,youtube_worker as worker,youtube_render as render
import test_server as accounts
class Worker(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  self.user=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'youtube-worker-password-123'});self.uid=self.user[1]['user']['id']
  with server.db() as c:
   c.execute('INSERT INTO admins VALUES(?)',(self.uid,));c.execute('DELETE FROM yt_jobs');c.execute('DELETE FROM yt_tasks');c.execute('DELETE FROM yt_workers');c.execute('UPDATE yt_runtime SET mode="laptop",epoch=1')
   s.put_account(c,self.uid,{'channel':'UC'+'x'*22,'sub':'google','token':'secret','expires':int(time.time())+3600})
   self.w=rt.register(c,'laptop');rt.heartbeat(c,self.w['id'],{'ffmpeg':True,'ffprobe':True,'tts':True})
   s.save_config(c,{'provider':'forma-video-v1','provider_endpoint':'https://example.com/video','free_confirmed':True,'tts_license_confirmed':True})
  self.story={'title':'An original lesson','description':'A factual short lesson','tags':['lesson'],'scenes':[{'seconds':1,'narration':'Hello.','prompt':'An original animation'}]}
 def job(self,preview):
  with server.db() as c:
   p=s.profile(c,self.uid);p.update(shorts_seconds=1,niche='Engineering',audience='People',expertise='Original expertise',assets_rights=True,made_for_kids=False,channel_confirmed=True,publishing_consent=True,enabled=True);s.save_profile(c,self.uid,p)
   with patch('youtube_store.dependencies',return_value={'tts':True}):return s.reserve(c,self.uid,'shorts',preview=preview)
 def control(self):
  wid=self.w['id']
  class Direct:
   def call(self,action,data):
    with server.db() as c:c.execute('BEGIN IMMEDIATE');return wa.action(c,wid,action,data)
  return Direct()
 def context(self):
  with server.db() as c:
   c.execute('UPDATE yt_jobs SET due=? WHERE state="queued"',(time.time()-1,));return rt.claim(c,self.w['id'])
 def test_complete_preview_never_uploads(self):
  jid=self.job(True);control=self.control()
  def fetch(cfg,url,path):Path(path).write_bytes(b'footage')
  def render_fixture(folder,*args):
   paths={}
   for kind in ('video','thumbnail','captions'):paths[kind]=Path(folder)/kind;paths[kind].write_bytes(b'fixture '+kind.encode())
   return paths|{'seconds':1}
  with patch('youtube_provider.readiness'),patch('youtube_worker.script',return_value=self.story),patch('youtube_provider.call',side_effect=[{'id':'task1'},{'state':'completed','url':'https://example.com/artifact'}]),patch('youtube_provider.fetch',side_effect=fetch),patch('youtube_render.probe',return_value=(1,[{'codec_type':'video'}])),patch('youtube_worker.s.dependencies',return_value={'tts':True,'ffmpeg':True,'ffprobe':True}),patch('youtube_render.render',side_effect=render_fixture),patch('youtube_worker.publish') as upload:
   for _ in range(4):
    ctx=self.context();self.assertIsNotNone(ctx);worker.execute(control,ctx)
   upload.assert_not_called()
  with server.db() as c:
   r=c.execute('SELECT state,preview FROM yt_jobs WHERE id=?',(jid,)).fetchone();self.assertEqual(r['state'],'ready');self.assertEqual(r['preview'],1)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM yt_assets WHERE job=? AND complete=1',(jid,)).fetchone()[0],4)
 def test_free_quota_hold_does_not_call_script(self):
  jid=self.job(True)
  with patch('youtube_provider.readiness',side_effect=s.YouTubeError('Free allowance exhausted')),patch('youtube_worker.script') as script:
   worker.execute(self.control(),self.context());script.assert_not_called()
  with server.db() as c:self.assertEqual(c.execute('SELECT state FROM yt_jobs WHERE id=?',(jid,)).fetchone()[0],'blocked')
 def test_resume_existing_upload_without_creating_new_session(self):
  class FakeTask:
   ctx={'user':'u','private':{'profile':{'visibility':'private'}}}
   def guard(self):pass
   def connect(self):pass
   def update(self,**kw):pass
  with tempfile.NamedTemporaryFile() as f:
   f.write(b'video');f.flush()
   with patch('youtube_google.token',return_value='token'),patch('youtube_worker.raw_request',return_value=(200,{},b'{"id":"abcdefghijk"}')) as request:
    result=worker.publish(FakeTask(),f.name,{}, {'upload_session':'https://www.googleapis.com/upload/session'});self.assertEqual(result,'abcdefghijk');self.assertEqual(request.call_count,1);self.assertEqual(request.call_args.args[1],'PUT')
 def test_ambiguous_upload_initialization_never_restarts(self):
  class FakeTask:
   ctx={'user':'u','private':{'profile':{}}}
   def guard(self):pass
   def connect(self):pass
  with tempfile.NamedTemporaryFile() as f:
   with patch('youtube_google.token',return_value='token'),patch('youtube_worker.raw_request') as request:
    with self.assertRaises(s.YouTubeError):worker.publish(FakeTask(),f.name,{}, {'upload_intent':True})
    request.assert_not_called()
 def test_corrupt_media_and_invalid_story_are_held(self):
  with tempfile.NamedTemporaryFile() as f:
   f.write(b'not a video');f.flush()
   with self.assertRaises(s.YouTubeError):render.probe(f.name)
  with self.assertRaises(s.YouTubeError):render.validate_storyboard({'title':'x','description':'x','tags':[],'scenes':[]},30)
 def test_real_ffmpeg_composition_with_fixture_narration(self):
  # The audio below is a QA tone, not production narration or a fallback video generator.
  import shutil
  if not shutil.which('ffmpeg'):self.skipTest('ffmpeg unavailable')
  with tempfile.TemporaryDirectory(prefix='forma-render-test-') as folder:
   folder=Path(folder);clip=folder/'clip.mp4';real=render.run
   real(['ffmpeg','-nostdin','-y','-f','lavfi','-i','color=c=blue:s=320x240:r=15','-t','1','-c:v','libx264','-threads','1',str(clip)])
   def fake_speech(args,timeout=1800):
    if args[0]=='espeak-ng':
     wav=Path(args[args.index('-w')+1])
     with wave.open(str(wav),'wb') as out:
      out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*4000)
     return b''
    return real(args,timeout)
   with patch('youtube_render.shutil.which',return_value='/usr/bin/runtime'),patch('youtube_render.run',side_effect=fake_speech):result=render.render(folder,[clip],self.story,'shorts','en')
   duration,streams=render.probe(result['video']);self.assertLessEqual(result['seconds'],1);self.assertTrue(result['captions'].is_file());self.assertTrue(result['thumbnail'].is_file());self.assertTrue(any(x.get('codec_type')=='audio' for x in streams))
