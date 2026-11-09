import business_store,business_service,business_runtime,observability,threading,signal
import naukri,naukri_login
import youtube_service,youtube_store,youtube_runtime,youtube_worker_api,linkedin_pages,billing,settings,linkedin,scheduler,media,base64,binascii,agent_platform
import hashlib,hmac,json,os,secrets,sqlite3,time
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path
from providers import refine,ProviderUnavailable
DB=os.environ.get('FORMA_DB','forma.sqlite3')
def db():
 c=sqlite3.connect(DB,timeout=15); c.row_factory=sqlite3.Row; c.execute('PRAGMA busy_timeout=15000'); return c
def init():
 with db() as c:
  naukri.init(c);naukri_login.init(c);settings.init(c);linkedin.init(c);scheduler.init(c);media.init(c);agent_platform.init(c);billing.init(c);youtube_service.init(c);business_store.init(c);observability.init(c)
  c.executescript('CREATE TABLE IF NOT EXISTS ai_usage(user TEXT,day TEXT,PRIMARY KEY(user,day)); CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,password TEXT,salt TEXT); CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user TEXT,expires INTEGER); CREATE TABLE IF NOT EXISTS workspaces(user TEXT PRIMARY KEY,data TEXT);')
class Handler(SimpleHTTPRequestHandler):
 def log_message(self,*args): pass
 def operational(self,exc):
  if not getattr(self,'ops_error_id',None):
   from urllib.parse import urlsplit
   parts=urlsplit(self.path).path.strip('/').split('/')
   component=parts[1] if len(parts)>1 and parts[0]=='api' else 'request'
   agent=next((a for a in ('linkedin','youtube','naukri') if component.startswith(a)),parts[2] if component=='business-agents' and len(parts)>2 and parts[2] in business_store.AGENTS else '')
   self.ops_error_id=observability.report(db,exc,service='server',agent=agent,operation=getattr(self,'command','request').lower()+'/'+component)
  return self.ops_error_id
 def handle_request(self,method):
  self.ops_error_id=None;self.validation_status=None
  token=observability.request_id.set(secrets.token_hex(16))
  try:return method()
  except (BrokenPipeError,ConnectionResetError):return
  except Exception as exc:
   error=self.operational(exc)
   try:return self.reply(500,{'error':'An operational error occurred. Contact the administrator with this reference.','error_id':error})
   except (BrokenPipeError,ConnectionResetError):pass
  finally:
   if self.validation_status is not None:
    from urllib.parse import urlsplit
    component=urlsplit(self.path).path.strip('/').split('/')
    observability.validation(db,'',getattr(self,'command','request').lower()+'/'+(component[1] if len(component)>1 else 'request'),self.validation_status,observability.request_id.get())
   observability.request_id.reset(token)
 def do_GET(self):return self.handle_request(self.dispatch_GET)
 def do_POST(self):return self.handle_request(self.dispatch_POST)
 def business_get(self):
  u=self.user()
  if not u:return self.reply(401,{'error':'Sign in required.'})
  try:result=business_service.get(db,u,self.path,self.session_digest())
  except PermissionError:return self.reply(403,{'error':'Administrator access required.'})
  except (business_store.BusinessError,ValueError) as exc:return self.reply(400,{'error':str(exc)})
  if '_redirect' in result:
   self.send_response(303);self.send_header('Location',result['_redirect']);self.end_headers();return
  if '_binary' in result:
   self.send_response(200);self.send_header('Content-Type',result['type']);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Disposition','inline' if result['type']=='image/png' else 'attachment; filename="forma-report.json"');self.end_headers();self.wfile.write(result['_binary']);return
  return self.reply(200,result)

 def reply(self,status,data,cookie=None):
  if status>=500:
   import sys
   self.operational(sys.exc_info()[1] or RuntimeError('Operational HTTP failure'))
  if status in (400,401,403,404,409,413,422,429) and not getattr(self,'ops_error_id',None):self.validation_status=status
  if getattr(self,'ops_error_id',None) and status>=400:data=data|{'error_id':self.ops_error_id}
  body=json.dumps(data).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('X-Request-ID',observability.request_id.get())
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers(); self.wfile.write(body)
 def user(self):
  try:
   cookies=SimpleCookie(self.headers.get('Cookie','')); token=cookies['session'].value
   with db() as c: return c.execute('SELECT users.id,users.email,EXISTS(SELECT 1 FROM admins WHERE admins.user=users.id) AS is_admin FROM sessions JOIN users ON users.id=sessions.user WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
  except (KeyError,ValueError): return None
 def session_digest(self):
  cookies=SimpleCookie(self.headers.get('Cookie',''));return hashlib.sha256(cookies['session'].value.encode()).hexdigest()
 def linkedin_access(self):
  user=self.user()
  if not user:return True
  with db() as c:
   try:agent_platform.visible(c,'linkedin',user);return True
   except agent_platform.AgentError:return False
 def dispatch_GET(self):
  if self.path.startswith(business_service.PREFIXES):return self.business_get()
  if self.path=='/api/naukri':
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   with db() as c:
    try:agent_platform.visible(c,'naukri',u)
    except agent_platform.AgentError:return self.reply(404,{'error':'Agent not found.'})
    result=naukri.state(c,u['id'])|{'connection':naukri_login.status(c,u['id'])}
   return self.reply(200,result|{'user_id':u['id']})
  if self.path.startswith(('/api/youtube','/api/admin/youtube','/api/me/mail')):
   from urllib.parse import urlsplit,parse_qs
   parsed=urlsplit(self.path);path=parsed.path;u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   try:
    if path=='/api/admin/youtube':
     if not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
     with db() as c:result=youtube_service.admin_get(c)
    elif path=='/api/youtube/callback':
     with db() as c:youtube_store.access(c,u['id'])
     youtube_service.g.callback(db,u['id'],self.session_digest(),parse_qs(parsed.query))
     self.send_response(303);self.send_header('Location','/#youtube');self.send_header('Cache-Control','no-store');self.end_headers();return
    elif path=='/api/me/mail':
     with db() as c:result=youtube_store.mail_public(c,u['id'])
    elif path.startswith('/api/youtube/assets/'):
     with db() as c:
      youtube_store.access(c,u['id']);asset=c.execute('SELECT * FROM yt_assets WHERE id=? AND user=? AND complete=1',(path.rsplit('/',1)[1],u['id'])).fetchone()
     if not asset:return self.reply(404,{'error':'Private asset not found.'})
     fpath=youtube_store.ROOT/'assets'/asset['id']
     if not fpath.is_file():return self.reply(404,{'error':'Media retention period expired.'})
     size=fpath.stat().st_size;start=0;end=size-1;partial=False
     if self.headers.get('Range'):
      import re
      match=re.fullmatch(r'bytes=(\d+)-(\d*)',self.headers['Range'])
      if not match:return self.reply(416,{'error':'Unsupported range.'})
      start=int(match[1]);end=min(int(match[2]) if match[2] else end,end);partial=True
      if not 0<=start<=end:return self.reply(416,{'error':'Range outside media.'})
     self.send_response(206 if partial else 200);self.send_header('Content-Type',{'video':'video/mp4','thumbnail':'image/jpeg','captions':'text/plain; charset=utf-8','segment':'video/mp4'}[asset['kind']]);self.send_header('Content-Length',str(end-start+1));self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
     if partial:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
     self.end_headers()
     with fpath.open('rb') as f:
      f.seek(start);remaining=end-start+1
      while remaining:
       chunk=f.read(min(65536,remaining))
       if not chunk:break
       self.wfile.write(chunk);remaining-=len(chunk)
     return
    else:result=youtube_service.get(db,u['id'],path.removeprefix('/api/youtube').strip('/'),{k:v[0] for k,v in parse_qs(parsed.query).items()})
   except youtube_store.YouTubeError as e:self.operational(e);return self.reply(400,{'error':str(e)})
   return self.reply(200,result)
  if self.path=='/api/billing':
   u=self.user()
   with db() as c:result=billing.summary(c,u['id'] if u else None)
   return self.reply(200,result)
  if self.path=='/api/admin/billing':
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   if not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
   with db() as c:result=billing.offers(c,True)|{'settings':billing.public_config(billing.config(c))}
   return self.reply(200,result)
  if self.path=='/api/agents':
   with db() as c:result=agent_platform.catalog(c,self.user())
   return self.reply(200,result)
  if self.path=='/api/admin/agents':
   user=self.user()
   if not user:return self.reply(401,{'error':'Sign in required.'})
   if not user['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
   with db() as c:result=agent_platform.catalog(c,user)
   return self.reply(200,result)
  if self.path.startswith('/api/agent-runs/'):
   user=self.user()
   if not user:return self.reply(401,{'error':'Sign in required.'})
   aid=self.path.removeprefix('/api/agent-runs/')
   with db() as c:
    try:agent_platform.visible(c,aid,user)
    except agent_platform.AgentError:return self.reply(404,{'error':'Agent not found.'})
    result=[dict(r) for r in c.execute('SELECT output,status,created FROM agent_runs WHERE agent=? AND user=? ORDER BY created DESC LIMIT 20',(aid,user['id']))]
   return self.reply(200,result)
  if self.path.startswith(('/api/linkedin/','/api/media/','/api/automation/')) or self.path in ('/api/workspace','/api/refine'):
   if not self.linkedin_access():return self.reply(404,{'error':'Agent not found.'})
  if self.path.startswith('/api/media/'):
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   with db() as c:row=c.execute('SELECT data FROM draft_media WHERE id=? AND user=?',(self.path.removeprefix('/api/media/'),u['id'])).fetchone()
   if not row:return self.reply(404,{'error':'Image not found.'})
   self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(row[0])));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(row[0]);return
  if self.path.startswith('/api/linkedin/callback?'):
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in again and restart LinkedIn connection.'})
   from urllib.parse import parse_qs,urlsplit
   try:linkedin.callback(db,u['id'],self.session_digest(),parse_qs(urlsplit(self.path).query))
   except linkedin.LinkedInError as e:self.operational(e);return self.reply(400,{'error':str(e)})
   self.send_response(303);self.send_header('Location','/#linkedin');self.send_header('Cache-Control','no-store');self.end_headers();return
  if self.path=='/api/automation/status':
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   with db() as c:result=[dict(r) for r in c.execute('SELECT day,slot,status FROM scheduled_slots WHERE user=? ORDER BY created DESC LIMIT 30',(u['id'],))]
   return self.reply(200,result)
  if self.path=='/api/linkedin/status':
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   with db() as c:result=linkedin.status(c,u['id'])
   return self.reply(200,result)
  if self.path=='/api/session':
   u=self.user(); return self.reply(200,{'user':dict(u) if u else None})
  if self.path in ('/api/admin/settings','/api/admin/events'):
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   if not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
   with db() as c:
    result=settings.public(settings.load(c)) if self.path.endswith('settings') else [dict(r) for r in c.execute('SELECT agent,event,summary,delivery,created FROM completion_events ORDER BY created DESC LIMIT 50')]
   return self.reply(200,result)
  if self.path=='/api/workspace':
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in to access your workspace.'})
   with db() as c:
    r=c.execute('SELECT data FROM workspaces WHERE user=?',(u['id'],)).fetchone();w=media.enrich(c,u['id'],json.loads(r['data']) if r else {})
   return self.reply(200,w)
  if self.path.startswith('/api/'):return self.reply(404,{'error':'Unknown endpoint'})
  from urllib.parse import urlsplit
  asset='index.html' if urlsplit(self.path).path=='/' else urlsplit(self.path).path.lstrip('/')
  if asset not in {'index.html','style.css','icons.js','app.js','agents.js','admin.js','agent-manager.js','billing.js','linkedin-pages.js','youtube.js','naukri.js','business.js','errors.js','brand/software-qa.svg','brand/quotation.svg','brand/onboarding.svg','brand/revenue-watchdog.svg','brand/sales-journey.svg','brand/atlas.svg','brand/atlas.png','brand/atlas-hero.png','brand/linkedin.svg','brand/youtube.svg','brand/naukri.svg','brand/workflow.svg'}:return self.reply(404,{'error':'Not found'})
  self.path='/public/'+asset
  return super().do_GET()
 def dispatch_POST(self):
  if self.path=='/api/billing/webhook':
   try:
    size=int(self.headers.get('Content-Length',0))
    if not 0<size<=200000:return self.reply(413,{'error':'Invalid webhook size.'})
    result=billing.webhook(db,self.rfile.read(size),self.headers.get('X-Razorpay-Signature'),self.headers.get('X-Razorpay-Event-Id'))
   except (billing.BillingError,ValueError):return self.reply(400,{'error':'Webhook could not be verified or processed.'})
   return self.reply(200,result)
  if self.headers.get('Origin') and self.headers['Origin']!=os.environ.get('FORMA_PUBLIC_ORIGIN','http://'+self.headers.get('Host','')):return self.reply(403,{'error':'Origin rejected'})
  try:
   size=int(self.headers.get('Content-Length',0))
   if size<0:return self.reply(400,{'error':'Invalid request length.'})
   if size>(12*1024*1024 if self.path in ('/api/media/attach','/api/naukri/save','/api/business-workers/dispatch') else 900000 if self.path.startswith('/api/youtube-worker/') else 500000 if self.path=='/api/admin/agents/save' else 200000):return self.reply(413,{'error':'Request too large'})
   data=json.loads(self.rfile.read(size))
   if not isinstance(data,dict):raise ValueError()
  except (ValueError,TypeError):return self.reply(400,{'error':'Invalid JSON'})
  if self.path.startswith(business_service.PREFIXES):
   if self.path=='/api/business-workers/dispatch':
    try:result=business_service.worker_post(db,self.headers.get('X-Forma-Worker',''),self.headers.get('Authorization','').removeprefix('Bearer '),data)
    except business_store.BusinessError as exc:return self.reply(403,{'error':str(exc)})
    return self.reply(200,result)
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   try:result=business_service.post(db,u,self.path,data,self.session_digest())
   except PermissionError:return self.reply(403,{'error':'Administrator access required.'})
   except (business_store.BusinessError,ValueError) as exc:return self.reply(400,{'error':str(exc)})
   return self.reply(200,result)
  if self.path in ('/api/naukri/save','/api/naukri/login'):
   u=self.user()
   if not u:return self.reply(401,{'error':'Sign in required.'})
   try:
    with db() as c:
     try:agent_platform.visible(c,'naukri',u)
     except agent_platform.AgentError:return self.reply(404,{'error':'Agent not found.'})
     result=naukri_login.start(c,u['id'],DB) if self.path.endswith('/login') else naukri.save(c,u['id'],data)
   except naukri.NaukriError as e:return self.reply(400,{'error':str(e)})
   return self.reply(200,result)
  if self.path.startswith('/api/youtube-worker/'):

   try:
    wid=self.headers.get('X-Forma-Worker','');secret=self.headers.get('Authorization','').removeprefix('Bearer ')
    with db() as c:
     c.execute('BEGIN IMMEDIATE');youtube_runtime.authenticate(c,wid,secret);result=youtube_worker_api.action(c,wid,self.path.removeprefix('/api/youtube-worker/'),data)
   except youtube_store.YouTubeError as e:self.operational(e);return self.reply(403,{'error':str(e)})
   return self.reply(200,result)
  if self.path in ['/api/signup','/api/login']:
   email=str(data.get('email','')).strip().lower(); password=str(data.get('password',''))
   if '@' not in email or len(email)>254 or len(password)<12 or len(password)>1024:return self.reply(400,{'error':'Use a valid email and a password of 12–1024 characters.'})
   with db() as c:
    u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
    if self.path=='/api/signup':
     if u:return self.reply(409,{'error':'Account already exists. Sign in instead.'})
     salt=secrets.token_hex(16); uid=secrets.token_hex(16); hashed=hashlib.scrypt(password.encode(),salt=salt.encode(),n=16384,r=8,p=1).hex()
     c.execute('INSERT INTO users VALUES(?,?,?,?)',(uid,email,hashed,salt))
    else:
     if not u or not hmac.compare_digest(hashlib.scrypt(password.encode(),salt=u['salt'].encode(),n=16384,r=8,p=1).hex(),u['password']):return self.reply(401,{'error':'Email or password incorrect.'})
     uid=u['id']
    token=secrets.token_urlsafe(32); c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,int(time.time())+86400))
   return self.reply(200,{'user':{'id':uid,'email':email}},'session='+token+'; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400'+('; Secure' if os.environ.get('FORMA_PUBLIC_ORIGIN','').startswith('https://') else ''))
  u=self.user()
  if not u:return self.reply(401,{'error':'Sign in required.'})
  if self.path.startswith(('/api/youtube/','/api/admin/youtube/','/api/me/mail')):
   try:
    if self.path.startswith('/api/admin/'):
     if not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
     with db() as c:c.execute('BEGIN IMMEDIATE');result=youtube_service.admin_post(c,self.path.removeprefix('/api/admin/youtube/'),data)
    elif self.path.startswith('/api/me/mail'):
     with db() as c:
      action=self.path.removeprefix('/api/me/mail').strip('/')
      if not action:result=youtube_store.save_mail(c,u['id'],data)
      elif action=='verify/start':result=youtube_service.start_verification(c,u['id'],data.get('recipient'))
      elif action=='verify/complete':result=youtube_service.verify_email(c,u['id'],str(data.get('email','')),str(data.get('code','')))
      else:raise youtube_store.YouTubeError('Unknown email action.')
    else:result=youtube_service.post(db,u['id'],self.path.removeprefix('/api/youtube/'),data,self.session_digest())
   except (youtube_store.YouTubeError,TypeError,ValueError) as e:return self.reply(400,{'error':str(e) if isinstance(e,youtube_store.YouTubeError) else 'Invalid YouTube request.'})
   return self.reply(200,result)
  if self.path.startswith('/api/admin/billing/') or self.path in ('/api/billing/subscribe','/api/billing/verify','/api/billing/sync','/api/billing/cancel'):
   if self.path.startswith('/api/admin/') and not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
   try:
    if self.path=='/api/admin/billing/settings':
     with db() as c:result=billing.configure(c,data)
    elif self.path=='/api/admin/billing/plans':
     with db() as c:result=billing.save_offer(c,data.get('plans'))
    elif self.path=='/api/admin/billing/approve':
     with db() as c:result=billing.approve(c,data.get('version'))
    elif self.path=='/api/billing/subscribe':result=billing.subscribe(db,u['id'],data.get('plan'))
    elif self.path=='/api/billing/verify':result=billing.verify(db,u['id'],data)
    elif self.path=='/api/billing/sync':result=billing.sync(db,u['id'],str(data.get('id','')))
    elif self.path=='/api/billing/cancel':result=billing.cancel(db,u['id'],str(data.get('id','')))
    else:return self.reply(404,{'error':'Unknown billing action.'})
   except (billing.BillingError,KeyError,TypeError,ValueError) as e:return self.reply(400,{'error':str(e) if isinstance(e,billing.BillingError) else 'Invalid billing request.'})
   return self.reply(200,result)
  if self.path in ('/api/admin/agents/save','/api/admin/agents/toggle','/api/admin/agents/test','/api/agents/run'):
   if self.path.startswith('/api/admin/') and not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
   try:
    if self.path.endswith('/save'):
     with db() as c:result=agent_platform.save(c,data)
    elif self.path.endswith('/toggle'):
     if not isinstance(data.get('live'),bool):raise agent_platform.AgentError('Live must be true or false.')
     with db() as c:result=agent_platform.toggle(c,str(data.get('id','')),data['live'])
    else:result=agent_platform.run(db,u,str(data.get('id','')),data.get('input',''),test=self.path.endswith('/test'))
   except agent_platform.AgentError as e:self.operational(e);return self.reply(400,{'error':str(e)})
   return self.reply(200,result)
  if self.path.startswith(('/api/linkedin/','/api/media/','/api/automation/')) or self.path in ('/api/workspace','/api/refine'):
   if not self.linkedin_access():return self.reply(404,{'error':'Agent not found.'})
  if self.path=='/api/automation/pause':
   with db() as c:
    c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT data FROM workspaces WHERE user=?',(u['id'],)).fetchone();w=json.loads(r[0]) if r else {};p=w.setdefault('profile',{});p['daily_enabled']=False;p['auto_publish']=False;c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),u['id']))
   return self.reply(200,{'paused':True})
  if self.path in ('/api/media/attach','/api/media/graphic','/api/media/remove'):
   try:
    if self.path.endswith('remove'):w=media.remove(db,u['id'],str(data.get('id','')))
    else:
     raw=media.graphic(data.get('headline',''),data.get('brand','SR Infotech')) if self.path.endswith('graphic') else base64.b64decode(data.get('image',''),validate=True)
     w=media.attach(db,u['id'],str(data.get('id','')),raw,data.get('alt',''))
   except (media.MediaError,ValueError,TypeError,binascii.Error) as e:return self.reply(400,{'error':str(e) if isinstance(e,media.MediaError) else 'Invalid image upload.'})
   return self.reply(200,w)
  if self.path=='/api/linkedin/pages':
   try:
    with db() as c:a=linkedin.account(c,u['id'])
    result=linkedin_pages.pages(a)
   except linkedin.LinkedInError as e:self.operational(e);return self.reply(400,{'error':str(e)})
   return self.reply(200,result)
  if self.path=='/api/linkedin/destinations':
   try:result=linkedin_pages.save(db,u['id'],data)
   except linkedin.LinkedInError as e:self.operational(e);return self.reply(400,{'error':str(e)})
   return self.reply(200,result)
  if self.path=='/api/linkedin/connect':
   try:
    with db() as c:url=linkedin.start(c,u['id'],self.session_digest(),page_access=data.get('page_access') is True)
   except linkedin.LinkedInError as e:self.operational(e);return self.reply(503,{'error':str(e)})
   return self.reply(200,{'url':url})
  if self.path=='/api/linkedin/disconnect':
   with db() as c:
    c.execute('DELETE FROM linkedin_accounts WHERE user=?',(u['id'],));c.execute('DELETE FROM linkedin_states WHERE user=?',(u['id'],))
   return self.reply(200,{'disconnected':True})
  if self.path=='/api/linkedin/publish':
   try:post=linkedin.publish(db,u['id'],str(data.get('id','')),str(data.get('text','')),data.get('image'))
   except linkedin.LinkedInError as e:self.operational(e);return self.reply(409,{'error':str(e)})
   return self.reply(200,{'published':True,'post':post})
  if self.path=='/api/admin/settings':
   if not u['is_admin']:return self.reply(403,{'error':'Administrator access required.'})
   try:
    with db() as c:result=settings.update(c,data)
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid settings. Check model, SMTP fields and selected events.'})
   return self.reply(200,result)
  if self.path=='/api/refine':
   with db() as c:provider_settings=settings.load(c)
   if not provider_settings.get('free_tier_confirmed'):return self.reply(503,{'error':'Gemini is disabled. An administrator must confirm free-tier setup.'})
   with db() as c:
    r=c.execute('SELECT data FROM workspaces WHERE user=?',(u['id'],)).fetchone()
    w=json.loads(r['data']) if r else {}; draft=next((d for d in w.get('drafts',[]) if d.get('id')==data.get('id')),None)
    if not draft:return self.reply(404,{'error':'Draft not found'})
    try:c.execute('INSERT INTO ai_usage VALUES(?,?)',(u['id'],time.strftime('%Y-%m-%d',time.gmtime())))
    except sqlite3.IntegrityError:return self.reply(429,{'error':'Local safety limit: one Gemini attempt per user per UTC day.'})
   try:text=refine(w.get('profile',{}),draft['text'],provider_settings)
   except ProviderUnavailable as e:self.operational(e);return self.reply(503,{'error':str(e)})
   draft.update(text=text,status='review',mode='gemini')
   with db() as c:c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),u['id']))
   settings.complete(db,u['id'],'gemini_completed','LinkedIn Content refined one draft with Gemini and returned it to the review queue.')
   with db() as c:media.enrich(c,u['id'],w)
   return self.reply(200,w)
  if self.path=='/api/logout':
   with db() as c:c.execute('DELETE FROM sessions WHERE user=?',(u['id'],))
   return self.reply(200,{},'session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0')
  if self.path=='/api/workspace':
   try:
    scheduler.validate(data.get('profile',{}))
    with db() as c:billing.check_profile(c,u['id'],data.get('profile',{}))
   except ValueError as e:return self.reply(400,{'error':str(e)})
   with db() as c:
    c.execute('BEGIN IMMEDIATE')
    previous=c.execute('SELECT data FROM workspaces WHERE user=?',(u['id'],)).fetchone()
    old=json.loads(previous['data']) if previous else {}; old_drafts={d.get('id'):d for d in old.get('drafts',[]) if isinstance(d,dict)}
    incoming=data.get('drafts',[])
    if not isinstance(incoming,list) or any(not isinstance(d,dict) or not isinstance(d.get('id'),str) or not isinstance(d.get('text'),str) for d in incoming):return self.reply(400,{'error':'Invalid drafts'})
    if len({d['id'] for d in incoming})!=len(incoming):return self.reply(400,{'error':'Duplicate draft identifiers.'})
    ent=billing.entitlement(c,u['id']);today=time.strftime('%Y-%m-%d',time.gmtime())
    new=[d for d in incoming if d['id'] not in old_drafts]
    used=c.execute('SELECT COUNT(*) FROM billing_drafts WHERE user=? AND day=?',(u['id'],today)).fetchone()[0]
    if not ent.get('admin') and used+len(new)>ent['posts_per_day']:return self.reply(403,{'error':'Daily draft allowance reached for your plan.'})
    for d in new:
     d.setdefault('day',today)
     c.execute('INSERT OR IGNORE INTO billing_drafts VALUES(?,?,?)',(u['id'],d['id'],today))
    incoming_ids={d['id'] for d in incoming}
    data['drafts']=incoming+[d for id,d in old_drafts.items() if id not in incoming_ids]
    for index,d in enumerate(incoming):
     if d['id'] not in old_drafts:
      if media.automatic(c,u['id'],d,data.get('profile',{}),len(old_drafts)+index):d['status']='review'
     attached=media.get(c,u['id'],d['id'])
     if d.get('status')=='approved' and (d.get('image') or {}).get('id')!=(attached['id'] if attached else None):return self.reply(409,{'error':'The image changed. Reload and review the saved image before approval.'})
     if d.get('status')=='approved':c.execute('INSERT INTO approvals VALUES(?,?,?) ON CONFLICT(user,draft) DO UPDATE SET digest=excluded.digest',(u['id'],d['id'],media.approval_digest(c,u['id'],d)))
     else:c.execute('DELETE FROM approvals WHERE user=? AND draft=?',(u['id'],d['id']))
    c.execute('INSERT INTO workspaces VALUES(?,?) ON CONFLICT(user) DO UPDATE SET data=excluded.data',(u['id'],json.dumps(data)))
   new=[d for d in data.get('drafts',[]) if isinstance(d,dict) and d.get('id') not in old_drafts]
   approvals=[d for d in data.get('drafts',[]) if isinstance(d,dict) and d.get('status')=='approved' and old_drafts.get(d.get('id'),{}).get('status')!='approved']
   if new:settings.complete(db,u['id'],'draft_saved',f'LinkedIn Content saved {len(new)} new draft(s) to a private workspace for review.')
   if approvals:settings.complete(db,u['id'],'review_saved',f'LinkedIn Content saved approval for {len(approvals)} draft(s). Approval does not publish.')
   return self.reply(200,{'saved':True})
  return self.reply(404,{'error':'Unknown endpoint'})
if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser();parser.add_argument('--grant-admin',metavar='EXISTING_EMAIL');args=parser.parse_args();init()
 if args.grant_admin:
  with db() as c:
   account=c.execute('SELECT id FROM users WHERE email=?',(args.grant_admin.strip().lower(),)).fetchone()
   if not account:parser.error('Create the account first; no admin was granted.')
   c.execute('INSERT OR IGNORE INTO admins VALUES(?)',(account['id'],))
  print('Admin access granted to the existing account.');raise SystemExit(0)
 stop=scheduler.start(db)
 youtube_stop=youtube_runtime.start(db)
 health_stop=threading.Event()
 def health_loop():
  while not health_stop.is_set():
   try:
    with db() as c:observability.heartbeat(c,'control','server')
   except Exception:pass
   health_stop.wait(15)
 health_thread=threading.Thread(target=health_loop,daemon=True);health_thread.start()
 def terminate(*args):raise KeyboardInterrupt()
 signal.signal(signal.SIGTERM,terminate)
 print('Forma: http://localhost:8000',flush=True)
 try:ThreadingHTTPServer(('127.0.0.1',8000),Handler).serve_forever()
 finally:
  stop.set();youtube_stop.set();health_stop.set();health_thread.join(timeout=5)
  with db() as c:observability.heartbeat(c,'control','server',state='stopped')
