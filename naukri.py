"""Private Naukri configuration and conservative matching; no paid services."""
import base64, json, re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

class NaukriError(ValueError): pass

def init(c):
 c.executescript('CREATE TABLE IF NOT EXISTS nk_profiles(user TEXT PRIMARY KEY,data TEXT,resume BLOB); CREATE TABLE IF NOT EXISTS nk_jobs(user TEXT,id TEXT,status TEXT,detail TEXT,PRIMARY KEY(user,id));')
 migrate(c)

def state(c,user):
 r=c.execute('SELECT data FROM nk_profiles WHERE user=?',(user,)).fetchone()
 return {'profile':json.loads(r[0]) if r else {},'jobs':[dict(r) for r in c.execute('SELECT id,status,detail FROM nk_jobs WHERE user=? ORDER BY rowid DESC LIMIT 100',(user,))], 'summaries':[dict(r) for r in c.execute('SELECT day,reason,applied,delivery FROM nk_days WHERE user=? ORDER BY day DESC LIMIT 30',(user,))], 'worker':'Run the local worker; live Naukri selectors are not verified.'}

def save(c,user,d):
 try:
  p={k:d.get(k,'') for k in ('resume_text','skills','search','model','time','timezone','answers')}
  if any(not isinstance(v,str) for v in p.values()):raise ValueError()
  if not p['resume_text'].strip() or not p['skills'].strip() or not p['search'].strip():raise NaukriError('Enter resume text, matching skills and a job search.')
  if not re.fullmatch(r'\d{2}:\d{2}',p['time']):raise ValueError()
  datetime.strptime(p['time'],'%H:%M');ZoneInfo(p['timezone'])
  answers=json.loads(p['answers'] or '{}')
  if not isinstance(answers,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in answers.items()):raise ValueError()
  p['gemini_consent']=d.get('gemini_consent') is True;p['enabled']=d.get('enabled') is True;p['auto_apply']=d.get('auto_apply') is True
  old=c.execute('SELECT resume FROM nk_profiles WHERE user=?',(user,)).fetchone();raw=old[0] if old else None
  if d.get('resume'):
   raw=base64.b64decode(d['resume'],validate=True)
   if len(raw)>5*1024*1024 or not raw.startswith(b'%PDF-'):raise NaukriError('Upload a PDF resume up to 5 MB.')
  if not raw:raise NaukriError('Upload your PDF resume.')
  c.execute('INSERT INTO nk_profiles VALUES(?,?,?) ON CONFLICT(user) DO UPDATE SET data=excluded.data,resume=excluded.resume',(user,json.dumps(p),raw))
  return state(c,user)
 except (ValueError,KeyError,ZoneInfoNotFoundError) as e:
  if isinstance(e,NaukriError):raise
  raise NaukriError('Check time, timezone, resume encoding and answers JSON.') from None

def job_id(url):
 try:
  u=urlsplit(url)
  if u.scheme!='https' or u.hostname!='www.naukri.com' or u.username or u.password or u.port not in (None,443):return None
  m=re.search(r'-(\d{8,})(?:/)?$',u.path)
  return m[1] if m and u.path.startswith('/job-listings-') else None
 except ValueError:return None

def matches(text,skills):
 required=[s.strip().lower() for s in skills.split(',') if s.strip()]
 return bool(required) and all(re.search(r'(?<!\w)'+re.escape(s)+r'(?!\w)',text.lower()) for s in required)

def claim(c,user,jid):
 import time
 return c.execute('INSERT OR IGNORE INTO nk_jobs(user,id,status,detail,created) VALUES(?,?,?,?,?)',(user,jid,'pending','Claimed before application; uncertain outcomes are never retried automatically.',int(time.time()))).rowcount==1

# Rolling 24-hour attempt cap is conservative when Naukri's reset window is unknown.
def migrate(c):
 columns={r[1] for r in c.execute('PRAGMA table_info(nk_jobs)')}
 if 'created' not in columns:c.execute('ALTER TABLE nk_jobs ADD COLUMN created INTEGER DEFAULT 0')
 c.executescript('CREATE TABLE IF NOT EXISTS nk_days(user TEXT,day TEXT,reason TEXT,applied INTEGER,delivery TEXT,PRIMARY KEY(user,day)); CREATE TABLE IF NOT EXISTS nk_ai_cache(user TEXT,key TEXT,answer TEXT,PRIMARY KEY(user,key)); CREATE TABLE IF NOT EXISTS nk_ai_calls(day TEXT PRIMARY KEY,calls INTEGER);')

def allowance(c,user,cap,now=None):
 import time
 return max(0,cap-c.execute("SELECT count(*) FROM nk_jobs WHERE user=? AND id NOT LIKE 'refresh:%' AND created>?",(user,(now or time.time())-86400)).fetchone()[0])

def finish(connect,user,p,reason):
 import settings
 day=datetime.now(ZoneInfo(p['timezone'])).date().isoformat()
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  start=datetime.combine(datetime.now(ZoneInfo(p['timezone'])).date(),datetime.min.time(),ZoneInfo(p['timezone'])).timestamp()
  count=c.execute("SELECT count(*) FROM nk_jobs WHERE user=? AND status='applied' AND created>=?",(user,start)).fetchone()[0]
  if not c.execute('INSERT OR IGNORE INTO nk_days VALUES(?,?,?,?,?)',(user,day,reason,count,'pending')).rowcount:return
  s=settings.load(c);r=c.execute('SELECT email FROM users WHERE id=?',(user,)).fetchone()
  email=r[0] if r else None
 summary=f'Naukri daily summary for {day} ({p["timezone"]}).\nConfirmed applications: {count}.\nResult: {reason}.\nOnly applications confirmed by Naukri are counted. Manual applications are not included.'
 delivery=settings.send(s|{'notify_to':email,'mail_subject':'Forma · Naukri daily application summary'},summary) if s.get('nk_email_enabled') and email else 'disabled'
 with connect() as c:c.execute('UPDATE nk_days SET delivery=? WHERE user=? AND day=?',(delivery,user,day))
 if delivery=='failed':
  import observability
  observability.report(connect,RuntimeError('Naukri summary delivery failed'),service='smtp',agent='naukri',operation='daily_summary')
