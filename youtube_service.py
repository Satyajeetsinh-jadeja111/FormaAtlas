"""Authenticated Studio operations and private analytics cache."""
import datetime,hashlib,hmac,json,re,secrets,time
import youtube_store as s,youtube_google as g,youtube_runtime as runtime
from youtube_store import YouTubeError

def init(c):s.init(c);runtime.init(c)
def report_key(params):
 start,end=g.dates(params)
 return 'report:'+json.dumps({'report':params.get('report','overview'),'start':start,'end':end,'format':params.get('format','all'),'video':params.get('video','')},sort_keys=True,separators=(',',':'))
def cached(c,user,key):
 a=s.account(c,user)
 if not a:raise YouTubeError('Connect your YouTube channel first.')
 r=c.execute('SELECT * FROM yt_cache WHERE user=? AND channel=? AND key=?',(user,a['channel'],key)).fetchone()
 return {'data':json.loads(r['data']) if r and r['data'] else None,'updated':r['updated'] if r else None,'stale':not r or r['updated']<time.time()-21600,'error':r['error'] if r else '', 'status':'available' if r and r['data'] else 'not_synced','reporting_timezone':'America/Los_Angeles'}
def refresh(connect,user,params):
 key=report_key(params) if params.get('type','report')=='report' else 'videos'
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');s.access(c,user);a=s.account(c,user)
  if not a:raise YouTubeError('Connect YouTube first.')
  if c.execute('SELECT COUNT(*) FROM yt_tasks WHERE user=? AND kind="manual_report" AND updated>?',(user,time.time()-3600)).fetchone()[0]>=20:raise YouTubeError('Maximum twenty manual report refreshes per hour.')
  prior=c.execute('SELECT attempted FROM yt_cache WHERE user=? AND channel=? AND key=?',(user,a['channel'],key)).fetchone()
  if prior and prior[0]>time.time()-900:raise YouTubeError('Please wait fifteen minutes before refreshing this report again.')
  c.execute('INSERT INTO yt_cache VALUES(?,?,?,?,?,?,?) ON CONFLICT(user,channel,key) DO UPDATE SET attempted=excluded.attempted',(user,a['channel'],key,'',0,int(time.time()),''))
 # Manual refresh enqueues selected-worker work, rather than performing remote calls in the web request.
 with connect() as c:
  ref=json.dumps({'key':key,'params':params},sort_keys=True)
  c.execute('INSERT OR IGNORE INTO yt_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(secrets.token_hex(16),user,'manual_report',ref,'queued',None,None,None,0,int(time.time())))
 return {'queued':True,'message':'Refresh queued for the selected worker. Cached data remains visible.'}

def start_verification(c,user,which):
 smtp=s.mail_config(c,user)
 if not all(smtp.get(k) for k in ('smtp_host','smtp_username','smtp_password','smtp_from')):raise YouTubeError('Configure your sender account first.')
 a=s.account(c,user) or {};r=c.execute('SELECT email FROM users WHERE id=?',(user,)).fetchone()
 email=r[0] if which=='primary' and r else a.get('email') if which=='google' else None
 if not email:raise YouTubeError('The requested recipient is not available.')
 if c.execute('SELECT COUNT(*) FROM yt_outbox WHERE user=? AND event="verify" AND due>?',(user,time.time()-3600)).fetchone()[0]>=3:raise YouTubeError('Maximum three verification emails per hour.')
 code=str(secrets.randbelow(900000)+100000);salt=secrets.token_hex(8);digest=salt+':'+hashlib.sha256((salt+code).encode()).hexdigest()
 c.execute('INSERT INTO yt_email_checks VALUES(?,?,?,?,0) ON CONFLICT(user,email) DO UPDATE SET hash=excluded.hash,expires=excluded.expires,verified=0',(user,email,digest,int(time.time())+900))
 c.execute('INSERT INTO yt_outbox VALUES(?,?,?,?,?,?,?,?,?)',(secrets.token_hex(16),user,'verify',secrets.token_hex(8),email,'pending',0,int(time.time()),s.encrypt({'code':code}).decode()))
 return {'queued':True,'email':email}
def verify_email(c,user,email,code):
 row=c.execute('SELECT * FROM yt_email_checks WHERE user=? AND email=?',(user,email)).fetchone()
 if not row or row['expires']<time.time() or row['verified']:raise YouTubeError('Verification is expired or unavailable.')
 salt,digest=row['hash'].split(':');valid=hmac.compare_digest(digest,hashlib.sha256((salt+str(code)).encode()).hexdigest())
 # One code attempt per request; failed attempts consume the challenge to prevent guessing.
 if not valid:
  c.execute('DELETE FROM yt_email_checks WHERE user=? AND email=?',(user,email));return {'verified':False,'message':'Invalid code. Request another verification email.'}
 c.execute('UPDATE yt_email_checks SET verified=1,hash="" WHERE user=? AND email=?',(user,email));return {'verified':True}
def delete_data(c,user):
 # Local deletion never deletes videos stored by YouTube.
 for r in c.execute('SELECT id FROM yt_assets WHERE user=?',(user,)).fetchall():
  try:(s.ROOT/'assets'/r[0]).unlink(missing_ok=True)
  except OSError:pass
 for table in ('yt_assets','yt_jobs','yt_profiles','yt_accounts','yt_cache','yt_competitors','yt_oauth','yt_mail','yt_email_checks','yt_outbox','yt_tasks'):c.execute('DELETE FROM '+table+' WHERE user=?',(user,))
 return {'deleted':True,'youtube_videos_unchanged':True}
def get(connect,user,path,params):
 with connect() as c:
  s.access(c,user)
  if path=='':return s.dashboard(c,user)
  if path=='report':return cached(c,user,report_key(params))
  if path=='videos':return cached(c,user,'videos')
  if path=='competitors':return [{'channel':r[0],'snapshot':cached(c,user,'competitor:'+r[0])} for r in c.execute('SELECT channel FROM yt_competitors WHERE user=?',(user,))]
 raise YouTubeError('Unknown Studio resource.')
def post(connect,user,path,data,session):
 with connect() as c:s.access(c,user)
 if path=='connect':
  with connect() as c:return {'url':g.start(c,user,session,data.get('kind','upload'))}
 if path=='settings':
  with connect() as c:return s.save_profile(c,user,data)
 if path=='pause':
  with connect() as c:
   p=s.profile(c,user);p['enabled']=False;return s.save_profile(c,user,p)
 if path=='preview':
  with connect() as c:c.execute('BEGIN IMMEDIATE');jid=s.reserve(c,user,data.get('format','shorts'),preview=True)
  return {'id':jid,'message':'Preview reserved. No upload is authorized for preview jobs.'}
 if path=='refresh':return refresh(connect,user,data)
 if path=='disconnect':
  with connect() as c:
   p=s.profile(c,user);p['enabled']=False;p['channel_confirmed']=False;s.save_profile(c,user,p)
   c.execute('DELETE FROM yt_accounts WHERE user=?',(user,));c.execute('DELETE FROM yt_oauth WHERE user=?',(user,));c.execute('DELETE FROM yt_cache WHERE user=?',(user,))
  return {'disconnected':True,'notice':'Local access removed. You can also revoke Forma in Google Account permissions.'}
 if path=='competitors/resolve':return g.resolve_competitor(connect,user,data.get('value',''))
 if path=='competitors/add':
  cid=data.get('id','')
  if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',cid):raise YouTubeError('Select a resolved YouTube channel.')
  resolved=g.resolve_competitor(connect,user,cid)
  with connect() as c:
   c.execute('BEGIN IMMEDIATE')
   if c.execute('SELECT COUNT(*) FROM yt_competitors WHERE user=?',(user,)).fetchone()[0]>=5:raise YouTubeError('Maximum five competitor channels.')
   c.execute('INSERT OR IGNORE INTO yt_competitors VALUES(?,?,?)',(user,resolved['id'],int(time.time())))
  return {'added':True}
 if path=='competitors/remove':
  with connect() as c:c.execute('DELETE FROM yt_competitors WHERE user=? AND channel=?',(user,data.get('id')));c.execute('DELETE FROM yt_cache WHERE user=? AND key=?',(user,'competitor:'+str(data.get('id',''))))
  return {'removed':True}
 if path=='mail':
  with connect() as c:return s.save_mail(c,user,data)
 if path=='mail/verify/start':
  with connect() as c:return start_verification(c,user,data.get('recipient'))
 if path=='mail/verify/complete':
  with connect() as c:return verify_email(c,user,str(data.get('email','')),str(data.get('code','')))
 if path=='delete-data':
  if data.get('confirm')!='DELETE MY FORMA YOUTUBE DATA':raise YouTubeError('Explicit local-data deletion confirmation is required.')
  with connect() as c:return delete_data(c,user)
 raise YouTubeError('Unknown Studio action.')

def admin_get(c):
 return {'settings':s.public_config(c),'runtime':dict(c.execute('SELECT * FROM yt_runtime').fetchone()),'workers':[{'id':r['id'],'mode':r['mode'],'online':r['heartbeat']>time.time()-90,'enabled':bool(r['enabled']),'capabilities':json.loads(r['capabilities'])} for r in c.execute('SELECT * FROM yt_workers')]}
def admin_post(c,path,data):
 if path=='settings':return s.save_config(c,data)
 if path=='workers/register':return runtime.register(c,data.get('mode'))
 if path=='workers/revoke':
  c.execute('UPDATE yt_workers SET enabled=0 WHERE id=?',(data.get('id'),));return {'revoked':True}
 if path=='mode':return runtime.switch(c,data.get('mode'))
 raise YouTubeError('Unknown administrator YouTube action.')
