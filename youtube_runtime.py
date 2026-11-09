import observability
"""Persistent selected-worker leases, scheduling, cache jobs and result commits."""
import datetime,hashlib,hmac,json,secrets,sqlite3,time
from zoneinfo import ZoneInfo
import youtube_store as s
from youtube_store import YouTubeError

def init(c):
 if 'pending_mode' not in {r[1] for r in c.execute('PRAGMA table_info(yt_runtime)')}:c.execute('ALTER TABLE yt_runtime ADD COLUMN pending_mode TEXT')
 c.execute('CREATE TABLE IF NOT EXISTS yt_tasks(id TEXT PRIMARY KEY,user TEXT,kind TEXT,reference TEXT,state TEXT,worker TEXT,epoch INTEGER,lease TEXT,lease_until INTEGER,updated INTEGER,UNIQUE(user,kind,reference))')

def register(c,mode):
 if mode not in ('laptop','server'):raise YouTubeError('Choose laptop or server worker.')
 wid=secrets.token_hex(12);key=secrets.token_urlsafe(40)
 c.execute('INSERT INTO yt_workers VALUES(?,?,?,?,?,1)',(wid,mode,hashlib.sha256(key.encode()).hexdigest(),0,'{}'))
 return {'id':wid,'token':key,'mode':mode,'notice':'Save this worker token securely; it is shown once.'}
def authenticate(c,wid,secret):
 r=c.execute('SELECT * FROM yt_workers WHERE id=? AND enabled=1',(wid,)).fetchone()
 if not r or not hmac.compare_digest(r['secret_hash'],hashlib.sha256(secret.encode()).hexdigest()):raise YouTubeError('Invalid worker authentication.')
 return r

def heartbeat(c,wid,capabilities):
 if not isinstance(capabilities,dict):raise YouTubeError('Invalid capabilities.')
 clean={k:capabilities.get(k) for k in ('ffmpeg','ffprobe','tts','free_disk_bytes')}
 c.execute('UPDATE yt_workers SET heartbeat=?,capabilities=? WHERE id=?',(int(time.time()),json.dumps(clean),wid))
 if c.execute("SELECT 1 FROM sqlite_master WHERE name='ops_health'").fetchone():observability.heartbeat(c,'youtube:'+wid,'youtube_worker')
 return dict(c.execute('SELECT mode,epoch FROM yt_runtime').fetchone())
def switch(c,mode):
 if mode not in ('laptop','server'):raise YouTubeError('Choose laptop or server.')
 current=c.execute('SELECT * FROM yt_runtime').fetchone()
 if current['mode']==mode:
  c.execute('UPDATE yt_runtime SET pending_mode=NULL');return dict(c.execute('SELECT * FROM yt_runtime').fetchone())
 ready=c.execute('SELECT 1 FROM yt_workers WHERE mode=? AND enabled=1 AND heartbeat>?',(mode,time.time()-90)).fetchone()
 if not ready:raise YouTubeError('Destination worker is offline. Deploy and connect it before switching.')
 active=c.execute('SELECT 1 FROM yt_jobs WHERE lease IS NOT NULL AND lease_until>?',(time.time(),)).fetchone() or c.execute('SELECT 1 FROM yt_tasks WHERE state="running" AND lease_until>?',(time.time(),)).fetchone()
 if active:
  c.execute('UPDATE yt_runtime SET pending_mode=?',(mode,));return dict(c.execute('SELECT * FROM yt_runtime').fetchone())
 c.execute('UPDATE yt_runtime SET mode=?,epoch=epoch+1,pending_mode=NULL WHERE id=1',(mode,));return dict(c.execute('SELECT * FROM yt_runtime').fetchone())

def schedule(c,now=None):
 now=now or datetime.datetime.now(datetime.timezone.utc)
 runtime=c.execute('SELECT * FROM yt_runtime').fetchone()
 if runtime['pending_mode']:
  active=c.execute('SELECT 1 FROM yt_jobs WHERE lease IS NOT NULL AND lease_until>?',(time.time(),)).fetchone() or c.execute('SELECT 1 FROM yt_tasks WHERE state="running" AND lease_until>?',(time.time(),)).fetchone()
  destination=c.execute('SELECT 1 FROM yt_workers WHERE mode=? AND enabled=1 AND heartbeat>?',(runtime['pending_mode'],time.time()-90)).fetchone()
  if not active and destination:c.execute('UPDATE yt_runtime SET mode=pending_mode,epoch=epoch+1,pending_mode=NULL')
 for r in c.execute('SELECT user FROM yt_profiles').fetchall():
  user=r[0];p=s.profile(c,user);a=s.account(c,user)
  if not a:continue
  try:s.access(c,user)
  except YouTubeError:continue
  can_schedule=True
  try:s.access(c,user,True)
  except YouTubeError:can_schedule=False
  if p['enabled'] and can_schedule:
   local=now.astimezone(ZoneInfo(p['timezone']));day=local.date().isoformat()
   for fmt in ('shorts','long'):
    t=datetime.time.fromisoformat(p[fmt+'_start']);start=local.replace(hour=t.hour,minute=t.minute,second=0,microsecond=0)
    # No catch-up burst: reserve only the latest eligible slot. Older missed slots are skipped.
    eligible=min(p[fmt+'_count']-1,int((local-start).total_seconds()//(p['spacing_minutes']*60)))
    if eligible>=0 and not c.execute('SELECT 1 FROM yt_jobs WHERE user=? AND channel=? AND day=? AND format=? AND slot=?',(user,a['channel'],day,fmt,eligible)).fetchone():
     try:s.reserve(c,user,fmt,eligible,now=now)
     except (YouTubeError,sqlite3.IntegrityError):pass
  for kind,interval in (('analytics',21600),('competitors',86400)):
   if kind=='analytics' and not a.get('analytics_token'):continue
   if kind=='competitors' and not c.execute('SELECT 1 FROM yt_competitors WHERE user=?',(user,)).fetchone():continue
   bucket=str(int(now.timestamp())//interval)
   c.execute('INSERT OR IGNORE INTO yt_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(secrets.token_hex(16),user,kind,bucket,'queued',None,None,None,0,int(now.timestamp())))
 for r in c.execute('SELECT id,user FROM yt_outbox WHERE state="pending" AND due<=? AND attempts<3',(now.timestamp(),)).fetchall():
  c.execute('INSERT OR IGNORE INTO yt_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(secrets.token_hex(16),r['user'],'email',r['id'],'queued',None,None,None,0,int(now.timestamp())))
 cfg=s.config(c);deps=s.worker_dependencies(c)
 if cfg.get('provider')=='forma-video-v1' and cfg.get('free_confirmed') and cfg.get('tts_license_confirmed') and deps['tts']:
  c.execute('UPDATE yt_jobs SET state="queued",detail="Checking free-provider availability again.",updated=? WHERE state="blocked" AND updated<?',(int(now.timestamp()),now.timestamp()-3600))
 # Stale actions are not replayed; a provider/upload may have completed before the process died.
 for stale in c.execute('SELECT * FROM yt_jobs WHERE lease IS NOT NULL AND lease_until<?',(time.time(),)).fetchall():
  progress=s.decrypt(stale['private']);known=bool(stale['video'] or progress.get('upload_session'))
  c.execute('UPDATE yt_jobs SET state=?,detail=?,lease=NULL,updated=? WHERE id=?',('queued' if known else 'held','Worker lost; checking the persisted upload session before any further bytes.' if known else 'Worker lease expired. Inspect provider state before resuming.',int(time.time()),stale['id']))
 for task in c.execute('SELECT reference FROM yt_tasks WHERE state="running" AND kind="email" AND lease_until<?',(time.time(),)).fetchall():
  c.execute('UPDATE yt_outbox SET state="unknown",detail="Worker lost during email delivery. No automatic resend." WHERE id=?',(task['reference'],))
 c.execute('UPDATE yt_tasks SET state="held",lease=NULL WHERE state="running" AND lease_until<?',(time.time(),))
 c.execute('DELETE FROM yt_cache WHERE key LIKE "competitor:%" AND updated<?',(time.time()-30*86400,))
 c.execute('DELETE FROM yt_oauth WHERE expires<?',(time.time(),))
 for r in c.execute('SELECT user FROM yt_accounts WHERE validated<?',(time.time()-30*86400,)).fetchall():
  c.execute('DELETE FROM yt_cache WHERE user=?',(r[0],))
 # Owner caches are refreshed or removed within 30 days as well.
 c.execute('DELETE FROM yt_cache WHERE updated<?',(time.time()-30*86400,))
 for asset in c.execute('SELECT a.id,j.state,a.created FROM yt_assets a JOIN yt_jobs j ON j.id=a.job').fetchall():
  age=7*86400 if asset['state'] in ('published','ready') else 3*86400 if asset['state'] in ('failed','held','blocked','cancelled') else None
  if age and asset['created']<time.time()-age:
   path=s.ROOT/'assets'/asset['id']
   try:path.unlink(missing_ok=True)
   except OSError:continue
   c.execute('DELETE FROM yt_assets WHERE id=?',(asset['id'],))

def guard(c,wid,data):
 table='yt_jobs' if data.get('type')=='video' else 'yt_tasks'
 r=c.execute('SELECT * FROM '+table+' WHERE id=?',(data.get('id'),)).fetchone();rt=c.execute('SELECT * FROM yt_runtime').fetchone();w=c.execute('SELECT * FROM yt_workers WHERE id=?',(wid,)).fetchone()
 if not r or r['worker']!=wid or not r['lease'] or not hmac.compare_digest(r['lease'],str(data.get('lease',''))) or r['epoch']!=rt['epoch'] or w['mode']!=rt['mode'] or r['lease_until']<time.time():raise YouTubeError('Worker lease is no longer valid.')
 if not (table=='yt_tasks' and r['kind']=='email'):s.access(c,r['user'],table=='yt_jobs' and not r['preview'])
 c.execute('UPDATE yt_workers SET heartbeat=? WHERE id=?',(int(time.time()),wid))
 if table=='yt_jobs':
  p=s.profile(c,r['user']);a=s.account(c,r['user'])
  if not a or a['channel']!=r['channel']:raise YouTubeError('Channel connection changed.')
  if not r['preview'] and not r['video'] and datetime.datetime.now(ZoneInfo(r['timezone'])).date().isoformat()!=r['day']:raise YouTubeError('This job’s reserved quota day ended. No late upload is allowed.')
  if not r['preview'] and (not p['enabled'] or not p['publishing_consent']):raise YouTubeError('Automation was paused.')
 c.execute('UPDATE '+table+' SET lease_until=? WHERE id=?',(int(time.time())+120,r['id']))
 return r

def claim(c,wid,maintenance=False):
 rt=c.execute('SELECT * FROM yt_runtime').fetchone();worker=c.execute('SELECT * FROM yt_workers WHERE id=? AND enabled=1',(wid,)).fetchone()
 if not worker or worker['mode']!=rt['mode'] or rt['pending_mode']:return None
 table='yt_tasks' if maintenance else 'yt_jobs'
 if not maintenance and c.execute('SELECT 1 FROM yt_jobs WHERE lease IS NOT NULL AND lease_until>?',(time.time(),)).fetchone():return None
 query='SELECT * FROM '+table+' WHERE state="queued"'+(' AND due<=?' if not maintenance else '')+' ORDER BY updated LIMIT 20'
 rows=c.execute(query,(time.time(),) if not maintenance else ()).fetchall()
 for r in rows:
  try:
   if not (maintenance and r['kind']=='email'):s.access(c,r['user'],not maintenance and not r['preview'])
  except YouTubeError:
   c.execute('UPDATE '+table+' SET state="held" WHERE id=?',(r['id'],));continue
  if not maintenance:
   a=s.account(c,r['user']);p=s.profile(c,r['user'])
   if not a or a['channel']!=r['channel'] or not r['preview'] and not p['enabled']:continue
   if not r['preview'] and not r['video'] and datetime.datetime.now(ZoneInfo(r['timezone'])).date().isoformat()!=r['day']:
    c.execute('UPDATE yt_jobs SET state="held",detail="Reserved quota day expired; late upload blocked." WHERE id=?',(r['id'],));continue
  if maintenance and r['kind']=='email':
   c.execute('UPDATE yt_outbox SET state="sending" WHERE id=? AND state="pending"',(r['reference'],))
  lease=secrets.token_urlsafe(24);state='running' if maintenance else 'claimed'
  c.execute('UPDATE '+table+' SET state=?,worker=?,epoch=?,lease=?,lease_until=?,updated=? WHERE id=?',(state,wid,rt['epoch'],lease,int(time.time())+120,int(time.time()),r['id']))
  a=s.account(c,r['user']);cfg=s.config(c)
  result={'id':r['id'],'lease':lease,'type':'maintenance' if maintenance else 'video','user':r['user'],'kind':r['kind'] if maintenance else 'video','reference':r['reference'] if maintenance else '', 'account':a,'config':cfg,'profile':s.profile(c,r['user']),'job':s.job_public(r) if not maintenance else None}
  if not maintenance:
   import settings
   result['private']=s.decrypt(r['private']);result['gemini']={k:v for k,v in settings.load(c).items() if k in ('gemini_key','model','free_tier_confirmed')}
  elif r['kind']=='email':
   mail=c.execute('SELECT * FROM yt_outbox WHERE id=?',(r['reference'],)).fetchone();result['email']=dict(mail) if mail else None
   if mail and mail['event']=='verify':result['email']['verification_code']=s.decrypt(mail['detail'].encode())['code']
   result['smtp']=s.mail_config(c,r['user'])
  elif r['kind']=='manual_report':result['report_request']=json.loads(r['reference'])
  elif r['kind']=='competitors':result['competitors']=[x[0] for x in c.execute('SELECT channel FROM yt_competitors WHERE user=?',(r['user'],))]
  if not maintenance:
   for key in list(result.get('account') or {}):
    if key.startswith(('analytics_','revenue_')):result['account'].pop(key)
  elif r['kind']=='email':result['account']=None;result['config']={}
  return result
 return None

def update(c,wid,data):
 r=guard(c,wid,data);now=int(time.time())
 if data['type']=='video':
  private=s.decrypt(r['private']);patch=data.get('private',{})
  if not isinstance(patch,dict) or set(patch)-{'story','provider_tasks','upload_session','upload_offset','metadata','measured_seconds','ready_notified','upload_intent'}:raise YouTubeError('Invalid worker progress.')
  if patch.get('story') and not r['preview'] and not private.get('story'):
   def signature(story):return hashlib.sha256((' '.join(story.get('title','').lower().split())+'|'+''.join(' '.join(x.get('narration','').lower().split()) for x in story.get('scenes',[]))).encode()).hexdigest()
   digest=signature(patch['story'])
   for prior in c.execute('SELECT private FROM yt_jobs WHERE user=? AND id!=? AND preview=0 ORDER BY created DESC LIMIT 100',(r['user'],r['id'])):
    previous=s.decrypt(prior[0]).get('story')
    if previous and signature(previous)==digest:raise YouTubeError('This script duplicates an earlier scheduled video. Generation is held before footage is requested.')
  private.update(patch)
  if 'measured_seconds' in private:
   n=private['measured_seconds']
   if type(n) is not int or not 1<=n<=r['seconds']:raise YouTubeError('Actual video duration exceeds the reserved budget.')
  state=data.get('state',r['state'])
  if state not in ('queued','claimed','scripting','footage','rendering','ready','uploading','processing','published','blocked','held','failed','cancelled'):raise YouTubeError('Invalid job state.')
  video=data.get('video',r['video'])
  if video and not __import__('re').fullmatch('[A-Za-z0-9_-]{11}',video):raise YouTubeError('Invalid YouTube video identifier.')
  if state=='published' and (not video or not data.get('publication_confirmed')):raise YouTubeError('Publication must be confirmed by Google.')
  final=state in ('queued','ready','published','blocked','held','failed','cancelled')
  detail=str(data.get('detail',''))[:1000]
  if patch.get('ready_notified') and not s.decrypt(r['private']).get('ready_notified'):s.queue_mail(c,r['user'],'video_ready',r['id'])
  if state=='queued':c.execute('UPDATE yt_jobs SET due=? WHERE id=?',(now+30,r['id']))
  c.execute('UPDATE yt_jobs SET private=?,state=?,detail=?,video=?,updated=?,lease=?,lease_until=? WHERE id=?',(s.encrypt(private),state,detail,video,now,None if final else r['lease'],0 if final else now+120,r['id']))
  if state in ('ready','published','blocked','held','failed'):s.queue_mail(c,r['user'],'video_ready' if state=='ready' else 'published' if state=='published' else 'action_needed',r['id'])
 else:
  state=data.get('state','completed')
  if state not in ('completed','failed','held'):raise YouTubeError('Invalid maintenance state.')
  c.execute('UPDATE yt_tasks SET state=?,lease=NULL,updated=? WHERE id=?',(state,now,r['id']))
  if r['kind']=='email':
   sent=data.get('sent') is True
   c.execute('UPDATE yt_outbox SET state=?,attempts=attempts+1,due=?,detail=? WHERE id=?',('sent' if sent else 'unknown' if data.get('uncertain') else 'pending',now+3600,'Delivered to SMTP server' if sent else 'Delivery outcome unknown; no automatic retry' if data.get('uncertain') else 'Delivery failed before submission; bounded retry pending',r['reference']))
   # Permit later bounded retry, retaining the outbox attempt count.
   if not sent and not data.get('uncertain'):c.execute('DELETE FROM yt_tasks WHERE id=?',(r['id'],))
  elif state=='failed' and r['kind']=='manual_report':
   ref=json.loads(r['reference']);a=s.account(c,r['user'])
   if a:c.execute('UPDATE yt_cache SET error=? WHERE user=? AND channel=? AND key=?',(str(data.get('detail','Report unavailable. Check permissions.'))[:500],r['user'],a['channel'],ref['key']))
  elif data.get('cache'):
   for entry in data['cache']:
    key=entry.get('key','')
    if r['kind'] in ('analytics','manual_report') and not (key.startswith('report:') or key=='videos'):raise YouTubeError('Invalid analytics cache.')
    if r['kind']=='competitors' and not key.startswith('competitor:'):raise YouTubeError('Invalid competitor cache.')
    c.execute('INSERT INTO yt_cache VALUES(?,?,?,?,?,?,?) ON CONFLICT(user,channel,key) DO UPDATE SET data=excluded.data,updated=excluded.updated,attempted=excluded.attempted,error=excluded.error',(r['user'],data['account']['channel'],key,json.dumps(entry['data']),now,now,''))
 if data.get('account'):
  current=s.account(c,r['user']);new=data['account']
  if current and new.get('channel')==current['channel'] and new.get('sub')==current.get('sub'):
   # Merge refreshed tokens only; worker cannot change channel or account identity.
   for prefix in ('','analytics_','revenue_'):
    for name in ('token','expires'):
     if prefix+name in new:current[prefix+name]=new[prefix+name]
   s.put_account(c,r['user'],current)
 if data.get('revoked_kind') in ('upload','analytics','revenue'):
  kind=data['revoked_kind'];a=s.account(c,r['user'])
  if kind=='upload':
   c.execute('DELETE FROM yt_accounts WHERE user=?',(r['user'],));p=s.profile(c,r['user']);p['enabled']=False;p['channel_confirmed']=False;s.save_profile(c,r['user'],p)
  elif a:
   for key in list(a):
    if key.startswith(kind+'_'):a.pop(key)
   s.put_account(c,r['user'],a)
  c.execute('DELETE FROM yt_cache WHERE user=?',(r['user'],))
 return {'saved':True}


def start(connect):
 import threading
 stop=threading.Event()
 def loop():
  while not stop.is_set():
   try:
    with connect() as c:c.execute('BEGIN IMMEDIATE');schedule(c)
   except Exception as exc:observability.report(connect,exc,service='scheduler',agent='youtube',operation='scheduled_tick')
   stop.wait(30)
 threading.Thread(target=loop,daemon=True).start();return stop
