"""User-owned business projects, immutable artifacts and permissioned handoffs."""
import copy,hashlib,json,re,secrets,time
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import settings,agent_platform
AGENTS={
 'software-qa':('Software QA','Run reproducible website tests and share evidence-backed bug reports.','Quality & reliability'),
 'quotation':('Quotation Assistant','Turn approved services and requirements into accurate, versioned quotations.','Sales & operations'),
 'onboarding':('Client Onboarding','Collect requirements, coordinate checklists and track project readiness.','Sales & operations'),
 'revenue-watchdog':('Revenue Watchdog','Monitor approved customer journeys and investigate failures.','Quality & reliability'),
 'sales-journey':('Sales Journey Orchestrator','Coordinate enquiries, quotations, follow-ups and client onboarding.','Sales & operations')}
EXISTING={'linkedin','youtube','naukri'}
class BusinessError(ValueError):pass

def encode(data):return settings.cipher().encrypt(json.dumps(data).encode())
def decode(data):return json.loads(settings.cipher().decrypt(data))
def uid():return secrets.token_hex(16)
def now():return int(time.time())
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def init(c):
 c.executescript('''
 CREATE TABLE IF NOT EXISTS biz_projects(id TEXT PRIMARY KEY,user TEXT,name TEXT,revision INTEGER,paused INTEGER,config BLOB,created INTEGER);
 CREATE TABLE IF NOT EXISTS biz_workflows(id TEXT PRIMARY KEY,user TEXT,project TEXT,created INTEGER);
 CREATE TABLE IF NOT EXISTS biz_tasks(id TEXT PRIMARY KEY,user TEXT,project TEXT,workflow TEXT,agent TEXT,operation TEXT,input BLOB,state TEXT,depth INTEGER,dedupe TEXT,worker TEXT,lease TEXT,lease_until INTEGER,created INTEGER,updated INTEGER,error TEXT,UNIQUE(user,project,dedupe));
 CREATE TABLE IF NOT EXISTS biz_artifacts(id TEXT PRIMARY KEY,user TEXT,project TEXT,task TEXT,kind TEXT,revision INTEGER,data BLOB,created INTEGER);
 CREATE TABLE IF NOT EXISTS biz_evidence(id TEXT PRIMARY KEY,user TEXT,project TEXT,task TEXT,kind TEXT,data BLOB,created INTEGER);
 CREATE TABLE IF NOT EXISTS biz_events(id TEXT PRIMARY KEY,user TEXT,project TEXT,task TEXT,kind TEXT,data BLOB,created INTEGER);
 CREATE TABLE IF NOT EXISTS biz_connections(id TEXT PRIMARY KEY,user TEXT,kind TEXT,name TEXT,private BLOB,created INTEGER);
 CREATE TABLE IF NOT EXISTS biz_oauth(state TEXT PRIMARY KEY,user TEXT,session TEXT,expires INTEGER,private BLOB);
 CREATE TABLE IF NOT EXISTS biz_admin(id INTEGER PRIMARY KEY CHECK(id=1),private BLOB);
 CREATE TABLE IF NOT EXISTS biz_workers(id TEXT PRIMARY KEY,user TEXT,hash TEXT,browser INTEGER,seen INTEGER,enabled INTEGER);
 CREATE TABLE IF NOT EXISTS biz_actions(id TEXT PRIMARY KEY,user TEXT,project TEXT,artifact TEXT,kind TEXT,destination TEXT,template TEXT,digest TEXT,payload BLOB,state TEXT,approved TEXT,created INTEGER,updated INTEGER,detail TEXT,UNIQUE(user,digest));
 CREATE TABLE IF NOT EXISTS biz_watch(project TEXT,journey TEXT,state TEXT,artifact TEXT,updated INTEGER,PRIMARY KEY(project,journey));
 CREATE TABLE IF NOT EXISTS biz_mail_seen(user TEXT,connection TEXT,message TEXT,PRIMARY KEY(user,connection,message));
 CREATE TABLE IF NOT EXISTS biz_ai_calls(day TEXT PRIMARY KEY,count INTEGER);
 CREATE TABLE IF NOT EXISTS biz_task_revisions(task TEXT PRIMARY KEY,revision INTEGER);
 CREATE TABLE IF NOT EXISTS biz_ai_cache(user TEXT,key TEXT,data BLOB,PRIMARY KEY(user,key));
 ''')
 for aid,(name,desc,category) in AGENTS.items():
  c.execute('INSERT OR IGNORE INTO managed_agents(id,name,description,category,kind,live,test_status) VALUES(?,?,?,?,?,?,?)',(aid,name,desc,category,'builtin',1,'Setup required · isolated business worker'))

def text(value,limit=4000):
 if not isinstance(value,str) or len(value)>limit:raise BusinessError('Invalid text field.')
 return value.strip()

def number(value,lo=0,hi=100000000):
 try:d=Decimal(str(value))
 except (InvalidOperation,ValueError):raise BusinessError('Enter a valid decimal amount.') from None
 if not d.is_finite() or d<lo or d>hi:raise BusinessError('Amount outside the allowed range.')
 return d

def _validate_config(d):
 if not isinstance(d,dict):raise BusinessError('Project configuration must be an object.')
 allowed={'agents','currency','catalogue','tax_rate','discount_rate','customer','requirements','checklist','sites','journeys','test_values','allow_local','timezone','watch_enabled','watch_hours','rules','connections','pipeline','followup_days','selected_files','adapters','ai_consent','ai_brief','mail_folder'}
 if set(d)-allowed:raise BusinessError('Unknown project configuration field.')
 cfg={'agents':list(AGENTS),'currency':'','catalogue':[],'tax_rate':'0','discount_rate':'0','customer':{},'requirements':{},'checklist':[],'sites':[],'journeys':[],'test_values':{},'allow_local':False,'timezone':'Asia/Kolkata','watch_enabled':False,'watch_hours':6,'rules':[],'connections':{},'pipeline':[],'followup_days':3,'selected_files':[],'adapters':[],'ai_consent':False,'ai_brief':'','mail_folder':'INBOX'}|d
 if not isinstance(cfg['agents'],list) or not set(cfg['agents'])<=set(AGENTS):raise BusinessError('Select supported business agents.')
 try:ZoneInfo(cfg['timezone'])
 except (KeyError,ValueError,TypeError):raise BusinessError('Invalid timezone.') from None
 for k in ('watch_enabled','allow_local','ai_consent'):
  if type(cfg[k]) is not bool:raise BusinessError('Use boolean project controls.')
 if type(cfg['watch_hours']) is not int or not 1<=cfg['watch_hours']<=168:raise BusinessError('Watch interval must be 1–168 hours.')
 if type(cfg['followup_days']) is not int or not 1<=cfg['followup_days']<=90:raise BusinessError('Follow-up interval must be 1–90 days.')
 if cfg['currency'] and not re.fullmatch('[A-Z]{3}',cfg['currency']):raise BusinessError('Use a three-letter currency code.')
 for k in ('tax_rate','discount_rate'):cfg[k]=str(number(cfg[k],0,100))
 if cfg['test_values']:raise BusinessError('Store test credentials in an encrypted Test account connection, not project configuration.')
 for k in ('customer','requirements','test_values','connections'):
  if not isinstance(cfg[k],dict):raise BusinessError('Invalid '+k+' configuration.')
 for k,limit in [('catalogue',100),('checklist',100),('sites',3),('journeys',15),('rules',30),('pipeline',100),('selected_files',50),('adapters',20)]:
  if not isinstance(cfg[k],list) or len(cfg[k])>limit:raise BusinessError('Too many or invalid '+k+' entries.')
 ids=set()
 for item in cfg['catalogue']:
  if not isinstance(item,dict) or not text(item.get('id',''),80) or item['id'] in ids or not text(item.get('name',''),200):raise BusinessError('Catalogue items require unique IDs and names.')
  ids.add(item['id']);item['price']=str(number(item.get('price')))
 for site in cfg['sites']:
  u=urlsplit(site)
  if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.fragment or u.query:raise BusinessError('Use website origins without credentials, query strings or fragments.')
  if u.path not in ('','/'):raise BusinessError('Sites must be origins; put page paths in journeys.')
 if cfg['watch_enabled'] and 'revenue-watchdog' not in cfg['agents']:raise BusinessError('Enable the Watchdog agent for scheduled checks.')
 for journey in cfg['journeys']:
  if not isinstance(journey,dict) or not re.fullmatch('[a-zA-Z0-9_-]{1,64}',journey.get('id','')) or journey.get('site') not in cfg['sites']:raise BusinessError('Each journey needs an ID and approved site.')
  if journey.get('authorized') is not True:raise BusinessError('Confirm website ownership and permission for every journey.')
  steps=journey.get('steps',[])
  if not isinstance(steps,list) or not 1<=len(steps)<=25:raise BusinessError('Journeys require 1–25 steps.')
  for step in steps:
   if not isinstance(step,dict) or step.get('action') not in ('goto','click','fill','expect_text','expect_url'):raise BusinessError('Unsupported browser step.')
   if step['action']=='click' and (step.get('safe') is not True or re.search(r'(?i)pay|purchase|delete|remove|transfer|subscribe|place order|confirm order|submit order',str(step.get('label','')))):raise BusinessError('Only explicitly safe test clicks are permitted.')
   if step['action']=='goto' and (not isinstance(step.get('path'),str) or not step['path'].startswith('/') or step['path'].startswith('//')):raise BusinessError('Navigation must use an approved-site relative path.')
 if len({j['id'] for j in cfg['journeys']})!=len(cfg['journeys']):raise BusinessError('Journey IDs must be unique.')
 for site in cfg['sites']:
  if sum(j['site']==site for j in cfg['journeys'])>5:raise BusinessError('At most five journeys per site.')
 for rule in cfg['rules']:
  if not isinstance(rule,dict) or rule.get('kind') not in ('email','github_issue','drive_document','drive_folder','sheet_export') or not text(rule.get('destination',''),300) or rule.get('template') not in ('quote_v1','onboarding_v1','qa_v1','followup_v1','incident_v1'):raise BusinessError('Rules require a supported action, exact destination and approved template.')
  if type(rule.get('enabled',False)) is not bool or type(rule.get('daily_limit',5)) is not int or not 1<=rule.get('daily_limit',5)<=50:raise BusinessError('Invalid automation rule limit.')
 if any(not isinstance(fid,str) or not re.fullmatch('[A-Za-z0-9_-]{1,150}',fid) for fid in cfg['selected_files']):raise BusinessError('Invalid selected Google file ID.')
 for adapter in cfg['adapters']:
  if not isinstance(adapter,dict) or not adapter.get('agent') or adapter.get('enabled') is not True or adapter.get('capability') not in ('project_draft','selected_summary','custom_text'):raise BusinessError('Invalid opt-in adapter capability.')
 text(cfg['ai_brief'],12000);text(cfg['mail_folder'],100)
 if len(json.dumps(cfg))>100000:raise BusinessError('Project configuration is too large.')
 return cfg

def validate_config(d):
 """Reject malformed nested data at the boundary, before it reaches a worker."""
 try:
  if not isinstance(d,dict):raise BusinessError('Project settings must be an object.')
  json.dumps(d,allow_nan=False)
  cfg=copy.deepcopy(d)
  if 'agents' in cfg and (not isinstance(cfg['agents'],list) or any(not isinstance(a,str) for a in cfg['agents'])):raise BusinessError('Select agents by their names.')
  if 'currency' in cfg and not isinstance(cfg['currency'],str):raise BusinessError('Currency must be a three-letter code.')
  for key in ('customer','requirements','connections','test_values'):
   if key in cfg and not isinstance(cfg[key],dict):raise BusinessError(key+' must be an object.')
  for key in ('name','email'):
   if key in cfg.get('customer',{}) and not isinstance(cfg['customer'][key],str):raise BusinessError('Customer '+key+' must be text.')
  for key in ('scope','timeline'):
   if key in cfg.get('requirements',{}) and not isinstance(cfg['requirements'][key],str):raise BusinessError('Requirements '+key+' must be text.')
  for kind,cid in cfg.get('connections',{}).items():
   if kind not in ('email','google','github','browser') or not isinstance(cid,str):raise BusinessError('Select a valid saved connection for each service.')
  if 'checklist' in cfg:
   if not isinstance(cfg['checklist'],list):raise BusinessError('Checklist must be a list.')
   for item in cfg['checklist']:
    if not isinstance(item,dict) or not isinstance(item.get('title'),str) or not item['title'].strip():raise BusinessError('Every checklist item needs a title.')
    for key in ('required','done'):
     if key in item and type(item[key]) is not bool:raise BusinessError('Checklist completion and required settings must be true or false.')
    for key in ('id','deadline','owner','document'):
     if key in item and not isinstance(item[key],str):raise BusinessError('Checklist '+key+' must be text.')
  for site in cfg.get('sites',[]):
   if not isinstance(site,str) or any(ch.isspace() for ch in site):raise BusinessError('Enter a valid website origin without spaces.')
   u=urlsplit(site)
   if u.port is not None and not 1<=u.port<=65535:raise BusinessError('Website port is invalid.')
  for journey in cfg.get('journeys',[]):
   if not isinstance(journey,dict) or not isinstance(journey.get('id'),str):raise BusinessError('Each test journey needs a text ID.')
   for step in journey.get('steps',[]):
    if not isinstance(step,dict) or not isinstance(step.get('action'),str):raise BusinessError('Each test step needs an action.')
    action=step['action']
    for key in ({'click':('label',),'fill':('label','value_key'),'expect_text':('text',),'expect_url':('path',),'goto':('path',)}.get(action,())):
     if not isinstance(step.get(key),str) or not step[key].strip() or len(step[key])>2000:raise BusinessError('Test step '+key+' needs non-empty text.')
    if 'role' in step and step['role'] not in ('button','link','checkbox','radio','menuitem'):raise BusinessError('Use a supported accessible role for test clicks.')
  for rule in cfg.get('rules',[]):
   if not isinstance(rule,dict) or not isinstance(rule.get('kind'),str):raise BusinessError('Select a valid action type for each rule.')
  result=_validate_config(cfg)
  # Test evidence, quantities and imported content have independent bounded budgets.
  for item in result['requirements'].get('items',[]):
   if not isinstance(item,dict) or not isinstance(item.get('id'),str):raise BusinessError('Quotation items need a service ID.')
   number(item.get('quantity',1),Decimal('0.01'),10000)
  return result
 except BusinessError:raise
 except (TypeError,ValueError,KeyError,AttributeError,OverflowError,RecursionError):raise BusinessError('Check the format of your project settings and test steps.') from None

def project(c,user,pid):
 if not isinstance(pid,str):raise BusinessError('Select a valid project.')
 r=c.execute('SELECT * FROM biz_projects WHERE id=? AND user=?',(pid,user)).fetchone()
 if not r:raise BusinessError('Project not found.')
 return dict(r)|{'config':decode(r['config'])}

def save_project(c,user,d):
 if type(d.get('paused',False)) is not bool:raise BusinessError('Pause must be true or false.')
 cfg=validate_config(d.get('config',{}));pid=d.get('id') or uid();old=project(c,user,pid) if d.get('id') else None
 name=text(d.get('name',''),100)
 if not name or any(x in name for x in ('\r','\n')):raise BusinessError('Enter a single-line project name.')
 if old and d.get('revision')!=old['revision']:raise BusinessError('Project changed. Reload before saving.')
 for cid in cfg['connections'].values():
  if cid and not c.execute('SELECT 1 FROM biz_connections WHERE id=? AND user=?',(cid,user)).fetchone():raise BusinessError('Connection not found.')
 for aid in cfg['agents']:
  row=c.execute('SELECT live FROM managed_agents WHERE id=?',(aid,)).fetchone()
  if not row or not row[0]:raise BusinessError('Selected agent is unavailable.')
 revision=old['revision']+1 if old else 1;paused=int(d.get('paused',False))
 c.execute('INSERT INTO biz_projects VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,revision=excluded.revision,paused=excluded.paused,config=excluded.config',(pid,user,name,revision,paused,encode(cfg),now()))
 event(c,user,pid,'','project_saved',{'revision':revision})
 return project(c,user,pid)

def event(c,user,pid,task,kind,data):
 c.execute('INSERT INTO biz_events VALUES(?,?,?,?,?,?,?)',(uid(),user,pid,task,kind,encode(data),now()))

def artifact(c,user,pid,task,kind,data):
 project(c,user,pid);aid=uid();revision=c.execute('SELECT count(*) FROM biz_artifacts WHERE user=? AND project=? AND kind=?',(user,pid,kind)).fetchone()[0]+1
 c.execute('INSERT INTO biz_artifacts VALUES(?,?,?,?,?,?,?,?)',(aid,user,pid,task,kind,revision,encode(data),now()));event(c,user,pid,task,'artifact_created',{'id':aid,'kind':kind,'revision':revision});return aid

def get_artifact(c,user,aid):
 if not isinstance(aid,str):raise BusinessError('Select a valid report.')
 r=c.execute('SELECT * FROM biz_artifacts WHERE id=? AND user=?',(aid,user)).fetchone()
 if not r:raise BusinessError('Artifact not found.')
 return dict(r)|{'data':decode(r['data'])}

def enqueue(c,user,pid,agent,operation='run',data=None,*,workflow=None,depth=0,dedupe=None):
 p=project(c,user,pid)
 if p['paused']:raise BusinessError('Project is paused.')
 if agent not in p['config']['agents']:raise BusinessError('Agent is not selected for this project.')
 r=c.execute('SELECT live FROM managed_agents WHERE id=?',(agent,)).fetchone()
 if not r or not r[0]:raise BusinessError('Agent is unavailable.')
 if depth>5:raise BusinessError('Maximum five handoff levels.')
 if workflow and not c.execute('SELECT 1 FROM biz_workflows WHERE id=? AND user=? AND project=?',(workflow,user,pid)).fetchone():raise BusinessError('Workflow not found.')
 dedupe=dedupe or uid();existing=c.execute('SELECT id FROM biz_tasks WHERE user=? AND project=? AND dedupe=?',(user,pid,dedupe)).fetchone()
 if existing:return existing[0]
 if not workflow:
  workflow=uid();c.execute('INSERT INTO biz_workflows VALUES(?,?,?,?)',(workflow,user,pid,now()))
 if c.execute('SELECT count(*) FROM biz_tasks WHERE workflow=?',(workflow,)).fetchone()[0]>=20:raise BusinessError('Maximum twenty tasks per workflow.')
 tid=uid();c.execute('INSERT INTO biz_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(tid,user,pid,workflow,agent,operation,encode(data or {}),'queued',depth,dedupe,None,None,0,now(),now(),''));event(c,user,pid,tid,'task_queued',{'agent':agent,'operation':operation,'workflow':workflow});return tid

def snapshot(c,user,pid):
 p=project(c,user,pid)
 p['tasks']=[dict(r) for r in c.execute('SELECT id,workflow,agent,operation,state,depth,created,updated,error FROM biz_tasks WHERE user=? AND project=? ORDER BY created DESC LIMIT 100',(user,pid))]
 p['artifacts']=[dict(r)|{'data':decode(r['data'])} for r in c.execute('SELECT * FROM biz_artifacts WHERE user=? AND project=? ORDER BY rowid DESC LIMIT 100',(user,pid))]
 p['events']=[dict(r)|{'data':decode(r['data'])} for r in c.execute('SELECT * FROM biz_events WHERE user=? AND project=? ORDER BY rowid DESC LIMIT 200',(user,pid))]
 p['actions']=[dict(r)|{'payload':decode(r['payload'])} for r in c.execute('SELECT * FROM biz_actions WHERE user=? AND project=? ORDER BY created DESC LIMIT 100',(user,pid))]
 p['evidence']=[dict(r) for r in c.execute('SELECT id,task,kind,created FROM biz_evidence WHERE user=? AND project=? ORDER BY created DESC LIMIT 100',(user,pid))]
 return p
