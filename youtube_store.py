"""YouTube Studio persistence, validation, quotas and worker fencing."""
import datetime,hashlib,json,re,secrets,shutil,time
from pathlib import Path
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
import billing,settings
ROOT=Path('.forma/youtube')
class YouTubeError(ValueError):pass
DEFAULTS={'niche':'','audience':'','language':'en','region':'IN','expertise':'','boundaries':'','brand':'','voice':'en','assets_rights':False,'made_for_kids':None,'synthetic_media':True,'visibility':'private','timezone':'Asia/Kolkata','shorts_count':1,'long_count':1,'shorts_seconds':30,'long_seconds':300,'shorts_topic':'','long_topic':'','shorts_start':'09:00','long_start':'12:00','spacing_minutes':60,'enabled':False,'publishing_consent':False,'channel_confirmed':False,'notify_google':False,'notify_primary':False}
SCOPES={'upload':['openid','email','https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly'],'analytics':['https://www.googleapis.com/auth/yt-analytics.readonly'],'revenue':['https://www.googleapis.com/auth/yt-analytics-monetary.readonly']}
ACTIVE=('claimed','scripting','footage','rendering','uploading','processing')
def init(c):
 c.executescript('''
 CREATE TABLE IF NOT EXISTS yt_config(id INTEGER PRIMARY KEY,encrypted BLOB);
 CREATE TABLE IF NOT EXISTS yt_accounts(user TEXT PRIMARY KEY,encrypted BLOB,validated INTEGER);
 CREATE TABLE IF NOT EXISTS yt_profiles(user TEXT PRIMARY KEY,data TEXT,revision INTEGER DEFAULT 1);
 CREATE TABLE IF NOT EXISTS yt_oauth(state TEXT PRIMARY KEY,user TEXT,session TEXT,kind TEXT,expires INTEGER);
 CREATE TABLE IF NOT EXISTS yt_jobs(id TEXT PRIMARY KEY,user TEXT,channel TEXT,day TEXT,timezone TEXT,format TEXT,slot INTEGER,seconds INTEGER,due INTEGER,state TEXT,detail TEXT,private BLOB,created INTEGER,updated INTEGER,worker TEXT,epoch INTEGER,lease TEXT,lease_until INTEGER,preview INTEGER,video TEXT,UNIQUE(user,channel,day,format,slot));
 CREATE TABLE IF NOT EXISTS yt_cache(user TEXT,channel TEXT,key TEXT,data TEXT,updated INTEGER,attempted INTEGER,error TEXT,PRIMARY KEY(user,channel,key));
 CREATE TABLE IF NOT EXISTS yt_competitors(user TEXT,channel TEXT,confirmed INTEGER,PRIMARY KEY(user,channel));
 CREATE TABLE IF NOT EXISTS yt_workers(id TEXT PRIMARY KEY,mode TEXT,secret_hash TEXT,heartbeat INTEGER,capabilities TEXT,enabled INTEGER DEFAULT 1);
 CREATE TABLE IF NOT EXISTS yt_runtime(id INTEGER PRIMARY KEY CHECK(id=1),mode TEXT,epoch INTEGER);
 INSERT OR IGNORE INTO yt_runtime(id,mode,epoch) VALUES(1,'laptop',1);
 CREATE TABLE IF NOT EXISTS yt_assets(id TEXT PRIMARY KEY,job TEXT,user TEXT,kind TEXT,size INTEGER,created INTEGER,complete INTEGER);
 CREATE TABLE IF NOT EXISTS yt_mail(user TEXT PRIMARY KEY,encrypted BLOB);
 CREATE TABLE IF NOT EXISTS yt_email_checks(user TEXT,email TEXT,hash TEXT,expires INTEGER,verified INTEGER,PRIMARY KEY(user,email));
 CREATE TABLE IF NOT EXISTS yt_outbox(id TEXT PRIMARY KEY,user TEXT,event TEXT,reference TEXT,recipient TEXT,state TEXT,attempts INTEGER,due INTEGER,detail TEXT,UNIQUE(user,event,reference,recipient));
 ''')

def encrypt(data):return settings.cipher().encrypt(json.dumps(data).encode())
def decrypt(value):return json.loads(settings.cipher().decrypt(value))
def config(c):
 r=c.execute('SELECT encrypted FROM yt_config WHERE id=1').fetchone()
 return decrypt(r[0]) if r else {'client_id':'','redirect_uri':'http://localhost:8000/api/youtube/callback','provider':'none','free_confirmed':False,'tts_license_confirmed':False}
def public_config(c):
 s=config(c);return {k:v for k,v in s.items() if k not in ('client_secret','provider_token')}|{'client_secret_saved':bool(s.get('client_secret')),'provider_token_saved':bool(s.get('provider_token')),'readiness':dependencies()}
def save_config(c,d):
 allowed={'client_id','client_secret','redirect_uri','provider','provider_token','free_confirmed','tts_license_confirmed','tts_voice','provider_endpoint','provider_model'}
 if set(d)-allowed:raise YouTubeError('Unknown YouTube integration setting.')
 s=config(c)
 for k,v in d.items():
  if k in ('client_secret','provider_token') and not v:continue
  if not isinstance(v,(str,bool)) or isinstance(v,str) and len(v)>4096:raise YouTubeError('Invalid integration setting.')
  s[k]=v
 from urllib.parse import urlsplit
 uri=urlsplit(s.get('redirect_uri',''))
 if uri.path!='/api/youtube/callback' or uri.query or uri.fragment or uri.username or uri.password or not (uri.scheme=='https' or uri.scheme=='http' and uri.hostname in ('localhost','127.0.0.1')):raise YouTubeError('Use the exact YouTube callback URL on HTTPS or localhost.')
 if s.get('provider') not in ('none','forma-video-v1'):raise YouTubeError('Unsupported video provider adapter.')
 if s.get('provider')!='none':
  from youtube_provider import validate_endpoint
  validate_endpoint(s.get('provider_endpoint',''))
 if type(s.get('free_confirmed',False)) is not bool or type(s.get('tts_license_confirmed',False)) is not bool:raise YouTubeError('Confirmations must be boolean.')
 c.execute('INSERT INTO yt_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET encrypted=excluded.encrypted',(encrypt(s),))
 return public_config(c)
def dependencies():
 return {'ffmpeg':bool(shutil.which('ffmpeg')),'ffprobe':bool(shutil.which('ffprobe')),'tts':bool(shutil.which('espeak-ng')),'free_disk_bytes':shutil.disk_usage('.').free}
def worker_dependencies(c):
 runtime=c.execute('SELECT mode FROM yt_runtime').fetchone()
 mode=runtime['mode'] if runtime else 'laptop'
 rows=c.execute('SELECT capabilities FROM yt_workers WHERE mode=? AND enabled=1 AND heartbeat>?',(mode,time.time()-90)).fetchall()
 if rows:
  capabilities=[json.loads(r[0]) for r in rows]
  return {k:any(x.get(k) is True for x in capabilities) for k in ('ffmpeg','ffprobe','tts')}
 return dependencies() if mode=='laptop' else {'ffmpeg':False,'ffprobe':False,'tts':False}
def account(c,user):
 r=c.execute('SELECT encrypted FROM yt_accounts WHERE user=?',(user,)).fetchone();return decrypt(r[0]) if r else None
def put_account(c,user,a):c.execute('INSERT INTO yt_accounts VALUES(?,?,?) ON CONFLICT(user) DO UPDATE SET encrypted=excluded.encrypted,validated=excluded.validated',(user,encrypt(a),int(time.time())))
def profile(c,user):
 r=c.execute('SELECT data FROM yt_profiles WHERE user=?',(user,)).fetchone();return DEFAULTS|json.loads(r[0]) if r else dict(DEFAULTS)
def validate_profile(p):
 if set(p)-set(DEFAULTS):raise YouTubeError('Unknown channel setting.')
 p=DEFAULTS|p
 for k in ('enabled','publishing_consent','channel_confirmed','assets_rights','synthetic_media','notify_google','notify_primary'):
  if type(p[k]) is not bool:raise YouTubeError('Use boolean confirmation values.')
 if p['made_for_kids'] is not None and type(p['made_for_kids']) is not bool:raise YouTubeError('Select the intended audience designation.')
 for k,lo,hi in [('shorts_count',0,15),('long_count',0,5),('shorts_seconds',1,180),('long_seconds',1,14400),('spacing_minutes',30,720)]:
  if type(p[k]) is not int or not lo<=p[k]<=hi:raise YouTubeError('Invalid video count, duration or spacing.')
 if p['long_count']*p['long_seconds']>14400:raise YouTubeError('Combined daily long-video duration cannot exceed four hours.')
 for k in ('niche','audience','language','region','expertise','boundaries','brand','voice','shorts_topic','long_topic','timezone','shorts_start','long_start'):
  if not isinstance(p[k],str) or len(p[k])>4000:raise YouTubeError('Invalid text setting.')
 if not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',p['voice']):raise YouTubeError('Use a supported standard voice identifier.')
 if not re.fullmatch(r'[A-Z]{2}',p['region']) or not re.fullmatch(r'[a-zA-Z-]{2,20}',p['language']):raise YouTubeError('Use a two-letter region and a language code.')
 try:
  ZoneInfo(p['timezone'])
  for kind in ('shorts','long'):
   t=datetime.time.fromisoformat(p[kind+'_start'])
   if t.second or t.microsecond or t.hour*60+t.minute+max(0,p[kind+'_count']-1)*p['spacing_minutes']>=1440:raise ValueError()
 except (ValueError,TypeError,ZoneInfoNotFoundError):raise YouTubeError('Use a valid timezone and a schedule fitting within one local day.') from None
 if p['visibility'] not in ('private','unlisted','public'):raise YouTubeError('Choose private, unlisted or public visibility.')
 return p
def access(c,user,automation=False):
 r=c.execute('SELECT live FROM managed_agents WHERE id="youtube"').fetchone();admin=c.execute('SELECT 1 FROM admins WHERE user=?',(user,)).fetchone()
 if not r or not (r[0] or admin):raise YouTubeError('YouTube Studio is not available.')
 if automation and not billing.entitlement(c,user)['automation']:raise YouTubeError('Daily YouTube automation requires Pro or Premium.')
def save_profile(c,user,d):
 p=validate_profile(d);a=account(c,user)
 if p['enabled']:
  access(c,user,True)
  if not a or not p['channel_confirmed'] or not p['publishing_consent'] or not p['assets_rights'] or p['made_for_kids'] is None or not all(p[k].strip() for k in ('niche','audience','expertise')):raise YouTubeError('Connect and confirm your channel, complete the brief, rights/audience settings and publishing authorization.')
 old=profile(c,user)
 # Freeze timezone until existing quota days have passed; changing it cannot reset counts.
 if p['timezone']!=old['timezone'] and c.execute('SELECT 1 FROM yt_jobs WHERE user=? AND preview=0 AND created>?',(user,time.time()-86400)).fetchone():raise YouTubeError('Timezone can change after 24 hours without new reserved jobs.')
 c.execute('INSERT INTO yt_profiles VALUES(?,?,1) ON CONFLICT(user) DO UPDATE SET data=excluded.data,revision=revision+1',(user,json.dumps(p)))
 return p

def reserve(c,user,fmt,slot=0,preview=False,now=None):
 now=now or datetime.datetime.now(datetime.timezone.utc);access(c,user,not preview);p=profile(c,user);a=account(c,user)
 if fmt not in ('shorts','long'):raise YouTubeError('Choose Shorts or long video.')
 if not a:raise YouTubeError('Connect a YouTube channel first.')
 if not p['assets_rights'] or p['made_for_kids'] is None or not all(p[k].strip() for k in ('niche','audience','expertise')):raise YouTubeError('Complete the content brief, rights confirmation and audience designation before generating.')
 if not preview and (not p['enabled'] or not p['publishing_consent']):raise YouTubeError('Daily automation is paused.')
 recent=c.execute('SELECT timezone FROM yt_jobs WHERE channel=? AND preview=0 AND created>? ORDER BY created DESC LIMIT 1',(a['channel'],now.timestamp()-86400)).fetchone()
 if not preview and recent and recent['timezone']!=p['timezone']:raise YouTubeError('This channel has an active quota timezone. Wait 24 hours before changing it.')
 local=now.astimezone(ZoneInfo(p['timezone']));day=local.date().isoformat();seconds=p[fmt+'_seconds']
 if preview:
  if c.execute('SELECT COUNT(*) FROM yt_jobs WHERE user=? AND preview=1 AND created>?',(user,time.time()-86400)).fetchone()[0]>=3:raise YouTubeError('Maximum three preview attempts per 24 hours.')
  slot=-int(time.time()*1000000)
 else:
  if slot>=p[fmt+'_count']:raise YouTubeError('Selected daily target reached.')
  rows=c.execute('SELECT format,seconds FROM yt_jobs WHERE channel=? AND day=? AND preview=0',(a['channel'],day)).fetchall()
  if sum(r['format']==fmt for r in rows)>=(15 if fmt=='shorts' else 5):raise YouTubeError('Daily upload count ceiling reached.')
  if fmt=='long' and sum(r['seconds'] for r in rows if r['format']=='long')+seconds>14400:raise YouTubeError('Daily long-video duration ceiling reached.')
 job=secrets.token_hex(16);t=datetime.time.fromisoformat(p[fmt+'_start']);due=local.replace(hour=t.hour,minute=t.minute,second=0,microsecond=0).timestamp()+slot*p['spacing_minutes']*60 if not preview else now.timestamp()
 cfg=config(c);deps=worker_dependencies(c);detail='Awaiting worker';state='queued'
 if cfg.get('provider')=='none' or not cfg.get('free_confirmed'):state='blocked';detail='No verified free AI-footage provider configured. No generation or upload attempted.'
 elif not deps['tts'] or not cfg.get('tts_license_confirmed'):state='blocked';detail='Standard local narration runtime and commercial-use voice confirmation are required.'
 c.execute('INSERT INTO yt_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(job,user,a['channel'],day,p['timezone'],fmt,slot,seconds,int(due),state,detail,encrypt({'profile':p,'channel':a['channel']}),int(now.timestamp()),int(now.timestamp()),None,None,None,0,int(preview),None))
 return job

def job_public(r):return {k:r[k] for k in ('id','channel','day','timezone','format','seconds','due','state','detail','created','updated','preview','video')}
def job_data(c,jid):
 r=c.execute('SELECT * FROM yt_jobs WHERE id=?',(jid,)).fetchone()
 if not r:raise YouTubeError('Video job not found.')
 return r,decrypt(r['private'])
def readiness(c,user):
 s=config(c);a=account(c,user);p=profile(c,user);deps=worker_dependencies(c);runtime=dict(c.execute('SELECT mode,epoch FROM yt_runtime').fetchone())
 workers=[{'mode':r['mode'],'online':r['heartbeat']>time.time()-90,'capabilities':json.loads(r['capabilities'])} for r in c.execute('SELECT * FROM yt_workers WHERE enabled=1')]
 return {'google_configured':all(s.get(k) for k in ('client_id','client_secret','redirect_uri')),'provider_ready':s.get('provider')!='none' and s.get('free_confirmed') is True,'narration_ready':deps['tts'] and s.get('tts_license_confirmed') is True,'dependencies':deps,'runtime':runtime,'workers':workers,'automation_saved':p['enabled'],'channel_connected':bool(a),'analytics_connected':bool(a and a.get('analytics_token')),'revenue_connected':bool(a and a.get('revenue_token')),'limitation':'Free video generation is blocked until a compatible free provider is configured. Targets are ceilings, not guaranteed output.'}
def dashboard(c,user):
 a=account(c,user);p=profile(c,user)
 return {'profile':p,'account':{k:a.get(k) for k in ('channel','title','email','analytics_connected','revenue_connected')} if a else None,'readiness':readiness(c,user),'jobs':[job_public(r) for r in c.execute('SELECT * FROM yt_jobs WHERE user=? ORDER BY created DESC LIMIT 100',(user,))],'assets':[dict(r) for r in c.execute('SELECT id,job,kind,size FROM yt_assets WHERE user=? AND complete=1',(user,))],'competitors':[dict(r) for r in c.execute('SELECT channel FROM yt_competitors WHERE user=?',(user,))],'notifications':mail_public(c,user),'outbox':[dict(r)|{'detail':'Verification email status' if r['event']=='verify' else r['detail']} for r in c.execute('SELECT event,recipient,state,attempts,detail FROM yt_outbox WHERE user=? ORDER BY due DESC LIMIT 30',(user,))]}
def mail_config(c,user):
 r=c.execute('SELECT encrypted FROM yt_mail WHERE user=?',(user,)).fetchone();return decrypt(r[0]) if r else {}
def mail_public(c,user):
 s=mail_config(c,user);return {k:v for k,v in s.items() if k!='smtp_password'}|{'password_saved':bool(s.get('smtp_password')),'verified':[r[0] for r in c.execute('SELECT email FROM yt_email_checks WHERE user=? AND verified=1',(user,))]}
def save_mail(c,user,d):
 s=mail_config(c,user)
 allowed={'smtp_host','smtp_port','smtp_security','smtp_username','smtp_password','smtp_from'}
 if set(d)-allowed:raise YouTubeError('Unknown email setting.')
 for k,v in d.items():
  if k!='smtp_port' and not isinstance(v,str):raise YouTubeError('SMTP credential fields must be text.')
  if k=='smtp_password' and not v:continue
  if not isinstance(v,(str,int)) or isinstance(v,str) and (len(v)>4096 or '\r' in v or '\n' in v):raise YouTubeError('Invalid mail credential.')
  s[k]=v
 if not re.fullmatch(r'[a-zA-Z0-9.-]+',s.get('smtp_host','')) or type(s.get('smtp_port')) is not int or not 1<=s['smtp_port']<=65535 or s.get('smtp_security') not in ('tls','starttls'):raise YouTubeError('Use a valid SMTP host, port and TLS transport.')
 if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',s.get('smtp_from','')):raise YouTubeError('Invalid sender address.')
 c.execute('INSERT INTO yt_mail VALUES(?,?) ON CONFLICT(user) DO UPDATE SET encrypted=excluded.encrypted',(user,encrypt(s)))
 return mail_public(c,user)
def queue_mail(c,user,event,reference):
 p=profile(c,user);a=account(c,user) or {};primary=c.execute('SELECT email FROM users WHERE id=?',(user,)).fetchone()
 recipients=set()
 if p['notify_primary'] and primary:recipients.add(primary[0].lower())
 if p['notify_google'] and a.get('email'):recipients.add(a['email'].lower())
 verified={r[0] for r in c.execute('SELECT email FROM yt_email_checks WHERE user=? AND verified=1',(user,))}
 for email in recipients&verified:c.execute('INSERT OR IGNORE INTO yt_outbox VALUES(?,?,?,?,?,?,?,?,?)',(secrets.token_hex(16),user,event,reference,email,'pending',0,int(time.time()),''))
