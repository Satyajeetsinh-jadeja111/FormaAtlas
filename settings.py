"""Encrypted administrator integrations and completion notifications."""
import json,os,re,secrets,sqlite3,smtplib,ssl,time
from pathlib import Path
from email.message import EmailMessage
from cryptography.fernet import Fernet
KEY_PATH=Path(os.environ.get('FORMA_SECRET_KEY_FILE','.forma/server.key'))
EVENTS={'draft_saved','review_saved','gemini_completed','linkedin_published','automatic_publish_failed','automatic_generation_failed'}
def cipher():
 KEY_PATH.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if KEY_PATH.is_symlink():raise RuntimeError('Secret key must not be a symlink')
 try:
  fd=os.open(KEY_PATH,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError:pass
 else:
  with os.fdopen(fd,'wb') as f:f.write(Fernet.generate_key())
 if KEY_PATH.stat().st_mode&0o077:raise RuntimeError('Secret key permissions must be 0600')
 return Fernet(KEY_PATH.read_bytes())
def init(c):
 cipher(); c.executescript('CREATE TABLE IF NOT EXISTS admins(user TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS integrations(id INTEGER PRIMARY KEY CHECK(id=1),encrypted BLOB); CREATE TABLE IF NOT EXISTS completion_events(id TEXT PRIMARY KEY,user TEXT,agent TEXT,event TEXT,summary TEXT,delivery TEXT,created INTEGER);')
def load(c):
 r=c.execute('SELECT encrypted FROM integrations WHERE id=1').fetchone()
 return json.loads(cipher().decrypt(r[0])) if r else {}
def public(s):
 return {k:v for k,v in s.items() if k not in ('gemini_key','smtp_password','linkedin_client_secret')}|{'gemini_key_saved':bool(s.get('gemini_key')),'smtp_password_saved':bool(s.get('smtp_password')),'linkedin_client_secret_saved':bool(s.get('linkedin_client_secret'))}
def update(c,data):
 old=load(c); allowed={'model','free_tier_confirmed','smtp_host','smtp_port','smtp_security','smtp_username','smtp_from','notify_to','notifications_enabled','events','gemini_key','smtp_password','remove_gemini_key','remove_smtp_password','linkedin_client_id','linkedin_client_secret','linkedin_redirect_uri','remove_linkedin_client_secret','nk_email_enabled','nk_gemini_enabled','nk_daily_limit','nk_ai_daily_limit'}
 if set(data)-allowed:raise ValueError('Unknown setting')
 for k,v in data.items():
  if k.startswith('remove_'):
   if v is True:old.pop(k[7:],None)
  elif k in ('gemini_key','smtp_password','linkedin_client_secret'):
   if v:
    if not isinstance(v,str) or len(v)>4096:raise ValueError('Invalid secret')
    old[k]=v
  else:old[k]=v
 if not isinstance(old.get('free_tier_confirmed',False),bool) or not isinstance(old.get('notifications_enabled',False),bool):raise ValueError('Confirmation must be boolean')
 for k in ('nk_email_enabled','nk_gemini_enabled'):
  if not isinstance(old.get(k,False),bool):raise ValueError('Confirmation must be boolean')
 for k,maximum,default in [('nk_daily_limit',50,50),('nk_ai_daily_limit',1000,50)]:
  if type(old.get(k,default)) is not int or not 1<=old.get(k,default)<=maximum:raise ValueError('Invalid Naukri limit')
 if old.get('linkedin_redirect_uri'):
  from urllib.parse import urlsplit
  uri=urlsplit(old['linkedin_redirect_uri'])
  if uri.path!='/api/linkedin/callback' or uri.query or uri.fragment or uri.username or not (uri.scheme=='https' or (uri.scheme=='http' and uri.hostname in ('localhost','127.0.0.1'))):raise ValueError('Invalid LinkedIn redirect URI')
 if old.get('model') and not re.fullmatch(r'gemini-[a-z0-9.-]+',old['model']):raise ValueError('Invalid Gemini model')
 if old.get('smtp_host') and not re.fullmatch(r'[a-zA-Z0-9.-]+',old['smtp_host']):raise ValueError('Invalid SMTP hostname')
 if old.get('smtp_security','tls') not in ('tls','starttls'):raise ValueError('TLS is required')
 if not isinstance(old.get('smtp_port',465),int) or not 1<=old.get('smtp_port',465)<=65535:raise ValueError('Invalid SMTP port')
 for k in ('smtp_username','smtp_from','notify_to'):
  if not isinstance(old.get(k,''),str) or any(x in old.get(k,'') for x in ('\r','\n')):raise ValueError('Invalid email field')
 for k in ('smtp_from','notify_to'):
  if old.get(k) and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',old[k]):raise ValueError('Invalid email address')
 if not isinstance(old.get('events',[]),list) or not set(old.get('events',[]))<=EVENTS:raise ValueError('Unknown completion event')
 c.execute('INSERT INTO integrations VALUES(1,?) ON CONFLICT(id) DO UPDATE SET encrypted=excluded.encrypted',(cipher().encrypt(json.dumps(old).encode()),))
 return public(old)
def send(s,summary):
 required=('smtp_host','smtp_username','smtp_password','smtp_from','notify_to')
 if not all(s.get(k) for k in required):return 'not_configured'
 msg=EmailMessage();msg['Subject']=s.get('mail_subject','Forma · LinkedIn Content completed work');msg['From']=s['smtp_from'];msg['To']=s['notify_to'];msg.set_content(summary+'\n\nOpen Forma to review the action status. This notification contains no resume or draft text.')
 cls=smtplib.SMTP_SSL if s.get('smtp_security','tls')=='tls' else smtplib.SMTP
 kwargs={'timeout':10}
 if cls is smtplib.SMTP_SSL:kwargs['context']=ssl.create_default_context()
 try:
  with cls(s['smtp_host'],s.get('smtp_port',465),**kwargs) as smtp:
   if s.get('smtp_security')=='starttls':smtp.starttls(context=ssl.create_default_context())
   smtp.login(s['smtp_username'],s['smtp_password']);smtp.send_message(msg)
  return 'sent'
 except (smtplib.SMTPException,OSError):return 'failed'
def complete(connect,user,event,summary):
 if event not in EVENTS:raise ValueError('Unknown event')
 with connect() as c:
  s=load(c);event_id=secrets.token_hex(16)
  c.execute('INSERT INTO completion_events VALUES(?,?,?,?,?,?,?)',(event_id,user,'linkedin',event,summary,'pending',int(time.time())))
 delivery=send(s,summary) if s.get('notifications_enabled') and event in s.get('events',[]) else 'disabled'
 with connect() as c:c.execute('UPDATE completion_events SET delivery=? WHERE id=?',(delivery,event_id))
 if delivery=='failed':
  import observability
  observability.report(connect,RuntimeError('Completion email delivery failed'),service='smtp',agent='linkedin',operation='completion_email')
