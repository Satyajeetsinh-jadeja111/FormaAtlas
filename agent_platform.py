"""Admin-managed agents, tenant isolation, hosted adapters and container-only Python."""
import selectors
import base64,hashlib,http.client,io,ipaddress,json,os,secrets,socket,ssl,sqlite3,stat,subprocess,tempfile,time,urllib.parse,zipfile
from pathlib import Path
import billing,settings
MAX_PACKAGE=256000
class AgentError(Exception):pass

def init(c):
 c.executescript('CREATE TABLE IF NOT EXISTS managed_agents(id TEXT PRIMARY KEY,name TEXT,description TEXT,category TEXT,kind TEXT,live INTEGER DEFAULT 0,config BLOB,package BLOB,tested TEXT,test_status TEXT DEFAULT "Not tested",revision INTEGER DEFAULT 1); CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,user TEXT,agent TEXT,output TEXT,status TEXT,created INTEGER);')
 c.execute('INSERT OR IGNORE INTO managed_agents(id,name,description,category,kind,live,test_status) VALUES(?,?,?,?,?,?,?)',('linkedin','LinkedIn Content','Create professional posts from verified facts and recent technology sources.','Career & growth','builtin',1,'Built-in workflow'))
 c.execute('INSERT OR IGNORE INTO managed_agents(id,name,description,category,kind,live,test_status) VALUES(?,?,?,?,?,?,?)',('naukri','Naukri Job Assistant','Resume matching, local browser applications and morning resume refresh.','Career & growth','builtin',1,'Local worker · live verification required'))
 # YouTube remains private until an administrator explicitly releases it.
 c.execute('INSERT OR IGNORE INTO managed_agents(id,name,description,category,kind,live,test_status) VALUES(?,?,?,?,?,?,?)',('youtube','YouTube Studio','Plan original videos, schedule publishing and view channel insights.','Video & audience','builtin',0,'Built-in workflow · generation requires a verified free provider'))
def fingerprint(row):return hashlib.sha256((str(row['revision'])+str(row['config'])+str(row['package'])).encode()).hexdigest()
def visible(c,agent,user):
 row=c.execute('SELECT * FROM managed_agents WHERE id=?',(agent,)).fetchone()
 if not row or not (row['live'] or user['is_admin']):raise AgentError('Agent not found.')
 return row
def public(row,admin=False):
 item={'id':row['id'],'name':row['name'],'description':row['description'],'category':row['category'],'kind':row['kind'],'live':bool(row['live']),'status':'available','icon':'in' if row['id']=='linkedin' else '▶' if row['id']=='youtube' else '◈','color':'blue' if row['id']=='linkedin' else 'green','capabilities':['profile','drafts','review'] if row['kind']=='builtin' else ['run']}
 if row['id'] in ('software-qa','quotation','onboarding','revenue-watchdog','sales-journey'):item['capabilities']=['projects','runs','automation','evidence']
 if admin:
  cfg=json.loads(settings.cipher().decrypt(row['config'])) if row['config'] else {}
  item.update(test_status=row['test_status'],revision=row['revision'],package_saved=bool(row['package']),endpoint=cfg.get('endpoint',''),token_saved=bool(cfg.get('token')),no_charge_confirmed=bool(cfg.get('no_charge_confirmed')),reviewed=bool(cfg.get('reviewed')))
 return item
def catalog(c,user):return [public(row,bool(user and user['is_admin'])) for row in c.execute('SELECT * FROM managed_agents ORDER BY kind,id') if row['live'] or (user and user['is_admin'])]
def unpack(raw):
 if not raw or len(raw)>MAX_PACKAGE:raise AgentError('Upload a ZIP smaller than 256 KB.')
 try:
  files={}
  with zipfile.ZipFile(io.BytesIO(raw)) as z:
   entries=z.infolist()
   if len(entries)>30:raise AgentError('Packages may contain at most 30 files.')
   for item in entries:
    path=Path(item.filename)
    if path.is_absolute() or '..' in path.parts or '\\' in item.filename or len(path.parts)>3 or stat.S_ISLNK(item.external_attr>>16):raise AgentError('Unsafe package path.')
    if item.is_dir():continue
    if item.filename in files or not (item.filename.endswith('.py') or item.filename in ('manifest.json','README.md')) or item.file_size>MAX_PACKAGE or item.compress_size and item.file_size/item.compress_size>100:raise AgentError('Unsupported package file.')
    files[item.filename]=z.read(item)
   if sum(map(len,files.values()))>MAX_PACKAGE:raise AgentError('Expanded package is too large.')
  if 'main.py' not in files or 'manifest.json' not in files:raise AgentError('Include manifest.json and main.py in the ZIP root.')
  manifest=json.loads(files['manifest.json'])
  if manifest.get('protocol')!='forma-agent-v1' or manifest.get('entrypoint')!='main.py':raise AgentError('Use forma-agent-v1 with entrypoint main.py.')
  return files
 except (zipfile.BadZipFile,ValueError,KeyError,RuntimeError):raise AgentError('Invalid agent package.') from None

def save(c,data):
 aid=data.get('id') or 'agent-'+secrets.token_hex(8);old=c.execute('SELECT * FROM managed_agents WHERE id=?',(aid,)).fetchone()
 if data.get('id') and not old:raise AgentError('Unknown agent ID. Omit ID to create a new agent.')
 if old and old['kind']=='builtin':raise AgentError('Built-in agent metadata cannot be replaced.')
 name=data.get('name','');desc=data.get('description','');category=data.get('category','General');kind=data.get('kind','')
 if not isinstance(name,str) or not 1<=len(name.strip())<=80 or not isinstance(desc,str) or not 1<=len(desc)<=500 or not isinstance(category,str) or not 1<=len(category)<=60 or kind not in ('http','python'):raise AgentError('Enter a name, description, category and supported adapter.')
 cfg=json.loads(settings.cipher().decrypt(old['config'])) if old and old['config'] else {}
 cfg.update(endpoint=str(data.get('endpoint','')),no_charge_confirmed=data.get('no_charge_confirmed') is True,reviewed=data.get('reviewed') is True)
 if data.get('remove_token'):cfg.pop('token',None)
 if data.get('token'):
  if not isinstance(data['token'],str) or len(data['token'])>4096 or '\n' in data['token'] or '\r' in data['token']:raise AgentError('Invalid token.')
  cfg['token']=data['token']
 package=old['package'] if old and old['kind']==kind else None
 if kind=='http':
  url=urllib.parse.urlsplit(cfg['endpoint'])
  if url.scheme!='https' or not url.hostname or url.username or url.password or url.fragment or url.port not in (None,443):raise AgentError('Use a public HTTPS endpoint on port 443 without URL credentials.')
 if data.get('package'):
  try:package=base64.b64decode(data['package'],validate=True)
  except (ValueError,TypeError):raise AgentError('Invalid package encoding.') from None
  unpack(package)
 if kind=='python' and not package:raise AgentError('Upload a compatible Python ZIP package.')
 revision=(old['revision']+1) if old else 1
 c.execute('INSERT INTO managed_agents VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,category=excluded.category,kind=excluded.kind,live=0,config=excluded.config,package=excluded.package,tested=NULL,test_status=excluded.test_status,revision=excluded.revision',(aid,name.strip(),desc,category,kind,0,settings.cipher().encrypt(json.dumps(cfg).encode()),package,None,'Not tested',revision))
 return public(c.execute('SELECT * FROM managed_agents WHERE id=?',(aid,)).fetchone(),True)
def toggle(c,aid,live):
 row=c.execute('SELECT * FROM managed_agents WHERE id=?',(aid,)).fetchone()
 if not row:raise AgentError('Agent not found.')
 if live and row['kind']!='builtin' and row['tested']!=fingerprint(row):raise AgentError('Save and successfully test this exact version before making it Live.')
 c.execute('UPDATE managed_agents SET live=? WHERE id=?',(int(live),aid));return {'live':live}
def parse_output(raw):
 if len(raw)>256000:raise AgentError('Agent output exceeds the limit.')
 try:result=json.loads(raw);output=result['output']
 except (ValueError,KeyError,TypeError):raise AgentError('Agent must return JSON with an output string.') from None
 if not isinstance(output,str) or not output.strip() or len(output)>20000:raise AgentError('Agent output must be a nonempty string up to 20,000 characters.')
 return output

def hosted(cfg,payload):
 url=urllib.parse.urlsplit(cfg['endpoint']);addresses=socket.getaddrinfo(url.hostname,443,type=socket.SOCK_STREAM)
 if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):raise AgentError('Private, loopback and nonpublic endpoints are blocked.')
 # Pin the validated address and retain HTTPS certificate/hostname verification.
 connection=http.client.HTTPSConnection(url.hostname,443,timeout=25)
 sock=socket.create_connection((addresses[0][4][0],addresses[0][4][1]),timeout=25);connection.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=url.hostname)
 headers={'Content-Type':'application/json','Accept':'application/json'}
 if cfg.get('token'):headers['Authorization']='Bearer '+cfg['token']
 try:
  connection.request('POST',url.path or '/',body=json.dumps(payload).encode(),headers=headers) if not url.query else connection.request('POST',url.path+'?'+url.query,body=json.dumps(payload).encode(),headers=headers)
  response=connection.getresponse()
  if response.status!=200:raise AgentError('Hosted agent failed. Redirects are not followed.')
  return parse_output(response.read(256001))
 finally:connection.close()
def local_python(package,payload):
 files=unpack(package);image=os.environ.get('FORMA_AGENT_IMAGE','python:3.12-slim')
 try:
  check=subprocess.run(['docker','image','inspect',image,'--format','{{.Id}}'],capture_output=True,timeout=5)
  image_id=check.stdout.decode().strip()
  if check.returncode or not image_id.startswith('sha256:'):raise AgentError('Isolated Python runtime unavailable. No image is downloaded automatically.')
 except (OSError,subprocess.TimeoutExpired):raise AgentError('Isolated Python runtime unavailable.') from None
 root=Path('.forma/agent-runs');root.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(dir=root) as folder:
  folder=Path(folder).resolve();folder.chmod(0o755)
  for name,body in files.items():
   dest=folder/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.parent.chmod(0o755);dest.write_bytes(body);dest.chmod(0o444)
  container='forma-agent-'+secrets.token_hex(10)
  command=['docker','run','--rm','--name',container,'--pull=never','--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=32','--memory=256m','--memory-swap=256m','--cpus=1','--user=65534:65534','--log-driver=none','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--mount',f'type=bind,src={folder},dst=/agent,readonly','-i',image_id,'python','-I','/agent/main.py']
  request_file=folder/'request.json';request_file.write_text(json.dumps(payload));request_file.chmod(0o444)
  command[-3:]=['python','-I','-c',"import sys,runpy;sys.stdin=open('/agent/request.json');runpy.run_path('/agent/main.py',run_name='__main__')"]
  proc=None
  try:
   proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
   output=bytearray();deadline=time.monotonic()+25
   with selectors.DefaultSelector() as selector:
    selector.register(proc.stdout,selectors.EVENT_READ)
    while True:
     if time.monotonic()>deadline:raise AgentError('Agent exceeded its 25-second runtime limit.')
     if not selector.select(timeout=.2):continue
     chunk=os.read(proc.stdout.fileno(),65536)
     if not chunk:break
     output.extend(chunk)
     if len(output)>256000:raise AgentError('Agent output exceeds the limit.')
   proc.wait(timeout=max(.1,deadline-time.monotonic()))
   if proc.returncode:raise AgentError('Python agent failed in the isolated runtime.')
   return parse_output(output)
  except (OSError,subprocess.SubprocessError):raise AgentError('Isolated Python execution failed.') from None
  finally:
   if proc is not None:
    if proc.poll() is None:
     try:subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
     except (OSError,subprocess.SubprocessError):pass
     proc.kill();proc.wait()
    proc.stdout.close()

def run(connect,user,aid,input_text,test=False):
 if not isinstance(input_text,str) or not 1<=len(input_text)<=16000:raise AgentError('Provide input text of 1–16,000 characters.')
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  row=visible(c,aid,user)
  try:billing.check_agent(c,user['id'])
  except billing.BillingError as e:raise AgentError(str(e)) from None
  if test and not user['is_admin']:raise AgentError('Administrator access required.')
  if row['kind']=='builtin':raise AgentError('Open the built-in Content studio to use LinkedIn.')
  cfg=json.loads(settings.cipher().decrypt(row['config']))
  if not cfg.get('no_charge_confirmed') or not cfg.get('reviewed'):raise AgentError('Administrator must confirm this adapter is reviewed and has no usage fees.')
  if not test and row['tested']!=fingerprint(row):raise AgentError('This agent version has not passed its administrator test.')
  if c.execute('SELECT COUNT(*) FROM agent_runs WHERE status=? AND created>?',('running',time.time()-35)).fetchone()[0]>=3:raise AgentError('Agent runtime is busy. Try again shortly.')
  prior=c.execute('SELECT COUNT(*) FROM agent_runs WHERE user=? AND created>?',(user['id'],time.time()-60)).fetchone()[0]
  if prior>=3:raise AgentError('Please wait: maximum three runs per minute.')
  run_id=secrets.token_hex(16);c.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?)',(run_id,user['id'],aid,'','running',int(time.time())))
 payload={'protocol':'forma-agent-v1','input':input_text,'context':{'run_id':run_id}}
 try:
  output=hosted(cfg,payload) if row['kind']=='http' else local_python(row['package'],payload);status='completed'
 except AgentError:
  with connect() as c:
   c.execute('UPDATE agent_runs SET status=? WHERE id=?',('failed',run_id))
   if test:c.execute('UPDATE managed_agents SET tested=NULL,test_status=? WHERE id=? AND revision=?',('Test failed',aid,row['revision']))
  raise
 except (OSError,ValueError,subprocess.SubprocessError):
  with connect() as c:c.execute('UPDATE agent_runs SET status=? WHERE id=?',('failed',run_id))
  raise AgentError('Agent execution failed. No private error details were exposed.') from None
 with connect() as c:
  c.execute('UPDATE agent_runs SET output=?,status=? WHERE id=?',(output,status,run_id))
  if test:c.execute('UPDATE managed_agents SET tested=?,test_status=? WHERE id=? AND revision=?',(fingerprint(row),'Test passed',aid,row['revision']))
 return {'output':output,'status':status}
