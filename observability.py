"""Sanitized operational incidents, durable mail alerts and process health."""
import contextvars,hashlib,json,logging,logging.handlers,os,re,secrets,smtplib,ssl,time,traceback
from pathlib import Path
from email.message import EmailMessage
import settings
ROOT=Path(__file__).resolve().parent
LOG_ROOT=Path(os.environ.get('FORMA_LOG_ROOT','.forma/observability'))
request_id=contextvars.ContextVar('forma_request_id',default='')
SAFE=re.compile(r'^[a-zA-Z0-9_./:@ ,;()-]{0,200}$')

def init(c):
 c.executescript('''
 CREATE TABLE IF NOT EXISTS ops_incidents(id TEXT PRIMARY KEY,fingerprint TEXT UNIQUE,state TEXT,data TEXT,first INTEGER,last INTEGER,count INTEGER,resolved INTEGER);
 CREATE TABLE IF NOT EXISTS ops_errors(id TEXT PRIMARY KEY,incident TEXT,created INTEGER,data TEXT);
 CREATE TABLE IF NOT EXISTS ops_alerts(id TEXT PRIMARY KEY,incident TEXT,kind TEXT,state TEXT,due INTEGER,attempts INTEGER,data TEXT,detail TEXT);
 CREATE TABLE IF NOT EXISTS ops_health(id TEXT PRIMARY KEY,service TEXT,owner TEXT,seen INTEGER,state TEXT,expected INTEGER);
 CREATE TABLE IF NOT EXISTS ops_validation(id TEXT PRIMARY KEY,created INTEGER,user TEXT,operation TEXT,status INTEGER,request TEXT);
 CREATE TABLE IF NOT EXISTS ops_config(id INTEGER PRIMARY KEY CHECK(id=1),data TEXT);
 ''')

def config(c):
 r=c.execute('SELECT data FROM ops_config WHERE id=1').fetchone();return {'enabled':False,'recipient':''}|(json.loads(r[0]) if r else {})

def save_config(c,d):
 if set(d)-{'enabled','recipient'} or type(d.get('enabled')) is not bool or not re.fullmatch(r'[^\s@<>,;:]+@[^\s@<>,;:]+\.[^\s@<>,;:]+',d.get('recipient','')):raise ValueError('Enter a valid admin recipient and enable setting.')
 c.execute('INSERT OR REPLACE INTO ops_config VALUES(1,?)',(json.dumps(d),));return config(c)

def safe(value):
 value=str(value)
 if value in ('<module>','<lambda>','<listcomp>','<dictcomp>','<setcomp>','<genexpr>'):return value
 return value if SAFE.fullmatch(value) and not re.search(r'(?i)(password|bearer|token=|secret=|api.key=)',value) else '[redacted]'

def fallback(record):
 try:
  LOG_ROOT.mkdir(mode=0o700,parents=True,exist_ok=True)
  logger=logging.getLogger('forma.sanitized.errors');logger.setLevel(logging.ERROR)
  if not logger.handlers:
   handler=logging.handlers.RotatingFileHandler(LOG_ROOT/'errors.jsonl',maxBytes=2_000_000,backupCount=3);handler.setFormatter(logging.Formatter('%(message)s'));logger.addHandler(handler);logger.propagate=False
  logger.error(json.dumps(record))
  # Fixed number of bounded, independently replayable sanitized records.
  spool=LOG_ROOT/'spool';spool.mkdir(mode=0o700,exist_ok=True)
  if len(list(spool.glob('*.json')))<1000:
   p=spool/(record['error_id']+'.json');p.write_text(json.dumps(record));p.chmod(0o600)
 except Exception:pass

def classification(exc):
 import sqlite3,socket
 if isinstance(exc,smtplib.SMTPAuthenticationError):return ('smtp_auth','SMTP authentication failed. Verify the sender account and provider-issued app password.')
 if isinstance(exc,socket.gaierror):return ('dns','Hostname resolution failed. Check network connectivity and configured provider hostname.')
 if isinstance(exc,(TimeoutError,ConnectionError)):return ('network','A connection timed out or failed. Check provider availability and network access before retrying.')
 if isinstance(exc,sqlite3.OperationalError):return ('database','Database operation failed. Check storage availability, permissions and competing database writers.')
 if isinstance(exc,PermissionError):return ('permissions','An operation was denied. Check file permissions and the scoped connection grant.')
 if type(exc).__name__ in ('ProviderUnavailable','GooglePermissionError'):return ('provider_unavailable','Provider access, configuration or quota prevented completion. Check integration readiness and reconnect if required.')
 if type(exc).__name__=='BusinessError':
  allowed={
   'Configure at least one authorized test journey.':'Configure an authorized test journey in Project setup.',
   'Worker is disabled.':'Worker access was revoked. Check worker enrollment and enable state.',
   'Project changed during execution. Prepare a new run.':'The project changed during a run. The stale result was rejected; start a new run.',
   'Project paused or capability disabled.':'The project was paused or a capability was disabled before completion.',
   'A test-only form value is missing.':'A journey fill step references a missing test credential value.',
   'Business Gemini daily budget reached. No paid fallback.':'The configured free Gemini request budget was exhausted. Wait for the next UTC day.',
   'Website resolves to an unapproved private or metadata address.':'Website destination was rejected by network isolation. Inspect the approved origins.',
   'Google session expired. Sign in again.':'Google authorization expired. Sign in again under Project connections.'}
  import re
  missing=re.fullmatch(r'Connect the required (email|google|github|browser) integration\.',str(exc))
  if missing:return ('missing_connection','The required '+missing[1]+' connection is missing. Configure it under Project connections.')
  return ('business_blocked',allowed.get(str(exc),'Business task could not proceed. Check project requirements, connection readiness and operation permissions.'))
 return ('operation_failed','An operational failure occurred. Inspect the source location, stack frames and correlated run to identify it.')

def report(connect,exc,*,service='server',agent='',operation='operation',severity='error',impact='Operation stopped; inspect its run before retrying.',**context):
 error=secrets.token_hex(16);frames=[];code,explanation=classification(exc)
 for f in traceback.extract_tb(exc.__traceback__)[-16:]:
  try:path=str(Path(f.filename).resolve().relative_to(ROOT))
  except ValueError:path=Path(f.filename).name
  frames.append({'file':safe(path),'function':safe(f.name),'line':f.lineno})
 location=frames[-1] if frames else {'file':'unknown','function':'unknown','line':0}
 data={'error_id':error,'created':int(time.time()),'service':safe(service),'agent':safe(agent),'operation':safe(operation),'severity':severity if severity in ('warning','error','critical') else 'error','exception':safe(type(exc).__name__),'message':explanation,'code':code,'location':location,'trace':frames,'release':safe(os.environ.get('FORMA_RELEASE','local')),'impact':safe(impact),'diagnostics':['Find this error ID in Administration > Errors & Incidents.','Inspect the referenced operation and sanitized stack frames.','Check connection status, worker heartbeat and provider quotas.','Check remote action history before retrying any uncertain write.'],'request':safe(request_id.get()),'context':{k:safe(v) for k,v in context.items() if k in ('project','workflow','task','worker','retry_status')}}
 try:persist(connect,data)
 except Exception:fallback(data)
 return error

def persist(connect,data):
 fingerprint=hashlib.sha256(json.dumps([data['service'],data['agent'],data['operation'],data['exception'],data['location']]).encode()).hexdigest();now=data['created']
 with connect() as c:
  if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
  if c.execute('SELECT 1 FROM ops_errors WHERE id=?',(data['error_id'],)).fetchone():return
  r=c.execute('SELECT * FROM ops_incidents WHERE fingerprint=?',(fingerprint,)).fetchone()
  iid=r['id'] if r else secrets.token_hex(16);data=data|{'incident_id':iid}
  c.execute('INSERT INTO ops_errors VALUES(?,?,?,?)',(data['error_id'],iid,now,json.dumps(data)))
  first=not r or r['state']=='resolved'
  if not r:c.execute('INSERT INTO ops_incidents VALUES(?,?,?,?,?,?,?,NULL)',(iid,fingerprint,'open',json.dumps(data),now,now,1))
  else:c.execute('UPDATE ops_incidents SET state=?,data=?,last=?,count=count+1,resolved=NULL WHERE id=?',('open' if first else r['state'],json.dumps(data),now,iid))
  if first:
   c.execute('INSERT INTO ops_alerts VALUES(?,?,?,?,?,?,?,?)',(secrets.token_hex(16),iid,'first','pending',now,0,json.dumps(data),''))
  elif not c.execute('SELECT 1 FROM ops_alerts WHERE incident=? AND kind="recurrence" AND state="pending"',(iid,)).fetchone():
   c.execute('INSERT INTO ops_alerts VALUES(?,?,?,?,?,?,?,?)',(secrets.token_hex(16),iid,'recurrence','pending',now+900,0,json.dumps(data),''))

def incidents(c,filters=None):
 filters=filters or {};result=[]
 for r in c.execute('SELECT * FROM ops_incidents ORDER BY last DESC LIMIT 500'):
  d=dict(r);d['data']=json.loads(d['data']);q=filters.get('q','').lower()
  if q and q not in json.dumps(d).lower():
   if not c.execute('SELECT 1 FROM ops_errors WHERE incident=? AND (id=? OR data LIKE ?)',(r['id'],q,'%'+q+'%')).fetchone():continue
  if any(filters.get(k) and filters[k]!=d['data'].get(k) for k in ('agent','severity','service')):continue
  if filters.get('state') and filters['state']!=d['state']:continue
  if filters.get('since') and d['last']<int(filters['since']):continue
  d['alerts']=[dict(a) for a in c.execute('SELECT id,kind,state,due,attempts,detail FROM ops_alerts WHERE incident=? ORDER BY due DESC',(r['id'],))]
  if filters.get('delivery') and not any(a['state']==filters['delivery'] for a in d['alerts']):continue
  result.append(d)
 return {'validation_recent':[dict(r) for r in c.execute('SELECT created,user,operation,status,request FROM ops_validation ORDER BY created DESC LIMIT 100')],'incidents':result,'health':[dict(r) for r in c.execute('SELECT * FROM ops_health')],'config':config(c)}

def detail(c,iid):
 r=c.execute('SELECT * FROM ops_incidents WHERE id=?',(iid,)).fetchone()
 if not r:raise ValueError('Incident not found.')
 return {'incident':dict(r)|{'data':json.loads(r['data'])},'occurrences':[json.loads(r[0]) for r in c.execute('SELECT data FROM ops_errors WHERE incident=? ORDER BY created DESC LIMIT 200',(iid,))]}

def set_state(c,iid,state):
 if state not in ('acknowledged','resolved','open'):raise ValueError('Invalid incident state.')
 if not c.execute('UPDATE ops_incidents SET state=?,resolved=? WHERE id=?',(state,int(time.time()) if state=='resolved' else None,iid)).rowcount:raise ValueError('Incident not found.')
 return {'saved':True}

def heartbeat(c,identifier,service,owner='',state='running'):
 c.execute('INSERT OR REPLACE INTO ops_health VALUES(?,?,?,?,?,?)',(identifier,service,owner,int(time.time()),state,int(state=='running')))

def mail(s,recipient,subject,body):
 """Only failures known to precede SMTP DATA are retried."""
 if not all(s.get(k) for k in ('smtp_host','smtp_username','smtp_password','smtp_from')):return 'not_configured'
 started=False
 try:
  cls=smtplib.SMTP_SSL if s.get('smtp_security','tls')=='tls' else smtplib.SMTP
  kw={'timeout':10}
  if cls is smtplib.SMTP_SSL:kw['context']=ssl.create_default_context()
  with cls(s['smtp_host'],s.get('smtp_port',465),**kw) as smtp:
   if cls is smtplib.SMTP:smtp.starttls(context=ssl.create_default_context())
   smtp.login(s['smtp_username'],s['smtp_password']);m=EmailMessage();m['From']=s['smtp_from'];m['To']=recipient;m['Subject']=subject;m.set_content(body);started=True;smtp.send_message(m)
  return 'sent'
 except smtplib.SMTPRecipientsRefused:return 'failed'
 except Exception:return 'unknown' if started else 'retry'

def dispatch(connect):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');cfg=config(c)
  # A killed dispatcher might have sent the mail already.
  c.execute('UPDATE ops_alerts SET state="unknown",detail="Dispatcher stopped during delivery; inspect mailbox before resending." WHERE state="sending" AND due<?',(time.time()-120,))
  r=c.execute('SELECT * FROM ops_alerts WHERE state="pending" AND due<=? ORDER BY due LIMIT 1',(time.time(),)).fetchone()
  if not r:return False
  s=settings.load(c)
  if not cfg['enabled'] or not cfg['recipient']:
   c.execute('UPDATE ops_alerts SET state="disabled" WHERE id=?',(r['id'],));return True
  c.execute('UPDATE ops_alerts SET state="sending",due=?,attempts=attempts+1 WHERE id=?',(int(time.time()),r['id']))
  incident=c.execute('SELECT * FROM ops_incidents WHERE id=?',(r['incident'],)).fetchone();d=json.loads(incident['data'])
 from datetime import datetime,timezone
 stamp=datetime.fromtimestamp(d['created'],timezone.utc).isoformat()
 location=d['location'];origin=os.environ.get('FORMA_PUBLIC_ORIGIN','http://localhost:8000').rstrip('/')
 body=f"Forma operational incident\nIncident: {r['incident']}\nError: {d['error_id']}\nUTC timestamp: {stamp}\nAgent/component: {d['agent']} / {d['service']}\nOperation: {d['operation']}\nSource: {location['file']}:{location['line']} ({location['function']})\nException: {d['exception']}\nExplanation: {d['message']}\nImpact: {d['impact']}\nReferences: {json.dumps(d['context'])}\nRequest: {d['request']}\nOccurrences: {incident['count']}\n"+'\n'.join(d['diagnostics'])+f"\nDashboard: {origin}/#errors\nSearch incident ID: {r['incident']}\nNo credentials or private document contents are included."
 status=mail(s,cfg['recipient'],'Forma error · '+d['agent']+' / '+d['operation'],body);attempt=r['attempts']+1
 with connect() as c:
  retry=status=='retry' and attempt<=3;delay=(60,300,900)[min(attempt-1,2)]
  c.execute('UPDATE ops_alerts SET state=?,due=?,detail=? WHERE id=?',('pending' if retry else 'failed' if status=='retry' else status,int(time.time())+delay,'Pre-delivery failure; retry scheduled.' if retry else status,r['id']))
 return True

def maintenance(connect):
 if LOG_ROOT.exists():
  for p in (LOG_ROOT/'spool').glob('*.json'):
   try:persist(connect,json.loads(p.read_text()));p.unlink()
   except Exception:break
 with connect() as c:
  stale=[dict(r) for r in c.execute('SELECT * FROM ops_health WHERE expected=1 AND seen<?',(time.time()-120,))]
  for r in stale:c.execute('UPDATE ops_health SET expected=0 WHERE id=?',(r['id'],))
  cutoff=time.time()-30*86400
  c.execute('DELETE FROM ops_validation WHERE created<?',(cutoff,))
  c.execute('DELETE FROM ops_errors WHERE created<? AND incident IN (SELECT id FROM ops_incidents WHERE state="resolved" AND resolved<?)',(cutoff,cutoff))
  c.execute('DELETE FROM ops_alerts WHERE incident IN (SELECT id FROM ops_incidents WHERE state="resolved" AND resolved<?)',(cutoff,))
  c.execute('DELETE FROM ops_incidents WHERE state="resolved" AND resolved<?',(cutoff,))
 for r in stale:report(connect,RuntimeError('heartbeat lost'),service=r['service'],operation='heartbeat_lost',worker=r['id'],impact='Service heartbeat lost. Check the service process and host.')

class _Existing:
 def __init__(self,c):self.c=c
 def __enter__(self):return self.c
 def __exit__(self,*args):return False

def record(c,exc,**context):
 """Record inside the caller's transaction; never starts a second DB writer."""
 return report(lambda:_Existing(c),exc,**context)

def remote_record(c,frames,*,service,agent,operation,task='',worker=''):
 # Worker-supplied traces contain location fields only; discard all other content.
 error=secrets.token_hex(16);clean=[]
 if isinstance(frames,list):
  for f in frames[-16:]:
   if isinstance(f,dict) and type(f.get('line')) is int and 0<=f['line']<=100000:
    clean.append({'file':safe(Path(str(f.get('file','unknown'))).name),'function':safe(f.get('function','unknown')),'line':f['line']})
 data={'error_id':error,'created':int(time.time()),'service':safe(service),'agent':safe(agent),'operation':safe(operation),'severity':'error','exception':'RemoteWorkerError','message':'Remote operation failed. Raw error text is withheld.','location':clean[-1] if clean else {'file':'remote_worker','function':'operation','line':0},'trace':clean,'release':safe(os.environ.get('FORMA_RELEASE','local')),'impact':'Task stopped. Check remote outcomes before retrying.','diagnostics':['Find this error ID in Administration > Errors & Incidents.','Inspect source location and the related task.','Check worker status and provider readiness.'],'request':request_id.get(),'context':{'task':safe(task),'worker':safe(worker)}}
 persist(lambda:_Existing(c),data);return error


def validation(connect,user,operation,status,correlation):
 try:
  with connect() as c:c.execute('INSERT INTO ops_validation VALUES(?,?,?,?,?,?)',(secrets.token_hex(16),int(time.time()),safe(user),safe(operation),int(status),safe(correlation)))
 except Exception:pass
