"""Explicit user connections; no paid providers and no implicit data sharing."""
import base64,email,hashlib,imaplib,json,re,secrets,ssl,time,urllib.parse,urllib.request,urllib.error
from email.header import decode_header,make_header
from email.message import EmailMessage
import settings,observability
import business_store as s

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args):return None

def http(url,data=None,headers=None,method=None,form=False):
 body=(urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
 req=urllib.request.Request(url,body,headers or {},method=method)
 try:
  with urllib.request.build_opener(NoRedirect()).open(req,timeout=25) as r:return json.loads(r.read(2_000_000)) if r.status!=204 else {}
 except urllib.error.HTTPError as e:
  if e.code in (401,403):raise s.BusinessError('Connection permission or quota unavailable. Reconnect or check the provider.') from None
  if e.code==429:raise s.BusinessError('Provider quota reached. No paid fallback was attempted.') from None
  raise s.BusinessError('Provider request failed. Inspect remote history before retrying a write.') from None
 except (OSError,ValueError):raise s.BusinessError('Provider response is uncertain. Inspect remote history before retrying a write.') from None

def public(c,user):
 return [{'id':r['id'],'kind':r['kind'],'name':r['name'],'created':r['created'],'status':'saved_unverified','selected_repositories':s.decode(r['private']).get('repositories',[]),'selected_files':s.decode(r['private']).get('selected_files',[])} for r in c.execute('SELECT * FROM biz_connections WHERE user=?',(user,))]

def get(c,user,cid,kind=None):
 r=c.execute('SELECT * FROM biz_connections WHERE id=? AND user=?',(cid,user)).fetchone()
 if not r or kind and r['kind']!=kind:raise s.BusinessError('Connect the required '+(kind or 'provider')+' integration.')
 return s.decode(r['private'])

def save(c,user,d):
 kind=d.get('kind');cid=d.get('id') or s.uid();old=get(c,user,cid,kind) if d.get('id') else {}
 if kind not in ('email','github','browser'):raise s.BusinessError('Use Google sign-in for Google connections.')
 cfg=old.copy();values=d.get('config',{})
 if not isinstance(values,dict):raise s.BusinessError('Invalid connection configuration.')
 allowed={'email':{'smtp_host','smtp_port','smtp_security','smtp_username','smtp_from','smtp_password','imap_host','imap_port'},'github':{'token','repositories','issues_enabled'},'browser':{'values'}}[kind]
 if set(values)-allowed:raise s.BusinessError('Unknown connection field.')
 for k,v in values.items():
  if k in ('token','smtp_password') and not v:continue
  cfg[k]=v
 if kind=='email':
  for k in ('smtp_host','imap_host'):
   if not re.fullmatch('[A-Za-z0-9.-]{1,253}',str(cfg.get(k,''))):raise s.BusinessError('Enter SMTP and IMAP hostnames.')
  for k in ('smtp_username','smtp_from'):
   if not re.fullmatch(r'[^\s@<>,;:]+@[^\s@<>,;:]+\.[^\s@<>,;:]+',str(cfg.get(k,''))):raise s.BusinessError('Enter your email address.')
  if cfg.get('smtp_security','tls') not in ('tls','starttls') or type(cfg.get('smtp_port')) is not int or not 1<=cfg['smtp_port']<=65535 or type(cfg.get('imap_port')) is not int or not 1<=cfg['imap_port']<=65535:raise s.BusinessError('Use valid TLS email ports.')
  if not isinstance(cfg.get('smtp_password'),str) or not cfg['smtp_password'] or len(cfg['smtp_password'])>4096:raise s.BusinessError('Enter a provider-issued app password.')
 if kind=='github':
  if not isinstance(cfg.get('token'),str) or not 1<=len(cfg['token'])<=4096:raise s.BusinessError('Enter your fine-grained GitHub token.')
  if not isinstance(cfg.get('repositories'),list) or not cfg['repositories'] or any(not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',r) for r in cfg['repositories']):raise s.BusinessError('Select repositories as owner/name.')
 if kind=='browser' and (not isinstance(cfg.get('values'),dict) or len(json.dumps(cfg['values']))>16000):raise s.BusinessError('Enter a JSON object of test-only credential values.')
 name=s.text(d.get('name',''),100)
 if not name:raise s.BusinessError('Name this connection.')
 c.execute('INSERT INTO biz_connections VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,private=excluded.private',(cid,user,kind,name,s.encode(cfg),s.now()));return {'id':cid,'saved':True}

def admin(c):
 r=c.execute('SELECT private FROM biz_admin WHERE id=1').fetchone();return s.decode(r[0]) if r else {}

def admin_public(c):
 d=admin(c);return {k:v for k,v in d.items() if k!='google_client_secret'}|{'google_client_secret_saved':bool(d.get('google_client_secret'))}

def admin_save(c,d):
 cfg=admin(c)
 if set(d)-{'google_client_id','google_client_secret','google_redirect_uri','google_picker_key','google_project_number','ai_daily_limit'}:raise s.BusinessError('Unknown business integration setting.')
 for k,v in d.items():
  if k=='google_client_secret' and not v:continue
  cfg[k]=v
 for field in ('google_client_id','google_client_secret','google_redirect_uri','google_picker_key','google_project_number'):
  if field in cfg and (not isinstance(cfg[field],str) or len(cfg[field])>4096):raise s.BusinessError('Invalid Google integration field.')
 if cfg.get('google_redirect_uri'):
  u=urllib.parse.urlsplit(cfg['google_redirect_uri'])
  if u.path!='/api/connections/google/callback' or u.query or u.fragment or u.username or u.password or not (u.scheme=='https' or u.scheme=='http' and u.hostname in ('localhost','127.0.0.1')):raise s.BusinessError('Use the exact Google callback URI on HTTPS or localhost.')
 if cfg.get('google_project_number') and not re.fullmatch(r'\d{1,30}',str(cfg['google_project_number'])):raise s.BusinessError('Use the numeric Google Cloud project number for Picker.')
 if type(cfg.get('ai_daily_limit',50)) is not int or not 1<=cfg.get('ai_daily_limit',50)<=1000:raise s.BusinessError('AI daily budget must be 1–1000.')
 c.execute('INSERT OR REPLACE INTO biz_admin VALUES(1,?)',(s.encode(cfg),));return admin_public(c)

def google_start(c,user,session):
 cfg=admin(c)
 if not all(cfg.get(k) for k in ('google_client_id','google_client_secret','google_redirect_uri')):raise s.BusinessError('Ask the administrator to configure Business Google OAuth.')
 state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
 c.execute('INSERT INTO biz_oauth VALUES(?,?,?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),user,session,s.now()+600,s.encode({'verifier':verifier})))
 return {'url':'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode({'client_id':cfg['google_client_id'],'redirect_uri':cfg['google_redirect_uri'],'response_type':'code','scope':'openid email https://www.googleapis.com/auth/drive.file','access_type':'offline','prompt':'consent','state':state,'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),'code_challenge_method':'S256'})}

def google_callback(connect,user,session,query):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM biz_oauth WHERE state=?',(hashlib.sha256(query.get('state',[''])[0].encode()).hexdigest(),)).fetchone()
  if not r or r['user']!=user or r['session']!=session or r['expires']<time.time():raise s.BusinessError('Google authorization expired or belongs to another session.')
  c.execute('DELETE FROM biz_oauth WHERE state=?',(r['state'],));cfg=admin(c);private=s.decode(r['private'])
 if query.get('error') or not query.get('code'):raise s.BusinessError('Google permission was not granted.')
 token=http('https://oauth2.googleapis.com/token',{'client_id':cfg['google_client_id'],'client_secret':cfg['google_client_secret'],'redirect_uri':cfg['google_redirect_uri'],'code':query['code'][0],'code_verifier':private['verifier'],'grant_type':'authorization_code'},{'Content-Type':'application/x-www-form-urlencoded'},form=True)
 if 'https://www.googleapis.com/auth/drive.file' not in token.get('scope','').split():raise s.BusinessError('Selected-file Google Drive permission was not granted.')
 identity=http('https://openidconnect.googleapis.com/v1/userinfo',headers={'Authorization':'Bearer '+token['access_token']})
 if not identity.get('sub'):raise s.BusinessError('Google identity could not be confirmed.')
 with connect() as c:
  cid=s.uid();cfg={'access_token':token['access_token'],'refresh_token':token.get('refresh_token',''),'expires':s.now()+token.get('expires_in',3600),'selected_files':[],'sub':identity['sub']}
  c.execute('INSERT INTO biz_connections VALUES(?,?,?,?,?,?)',(cid,user,'google',identity.get('email','Google Drive'),s.encode(cfg),s.now()))
 return cid

def google_token(connect,user,cid):
 with connect() as c:d=get(c,user,cid,'google');cfg=admin(c)
 if d.get('expires',0)>time.time()+60:return d['access_token']
 if not d.get('refresh_token'):raise s.BusinessError('Google session expired. Sign in again.')
 token=http('https://oauth2.googleapis.com/token',{'client_id':cfg['google_client_id'],'client_secret':cfg['google_client_secret'],'refresh_token':d['refresh_token'],'grant_type':'refresh_token'},{'Content-Type':'application/x-www-form-urlencoded'},form=True)
 with connect() as c:
  current=get(c,user,cid,'google')
  if current['refresh_token']!=d['refresh_token']:raise s.BusinessError('Google connection changed. Try again.')
  current.update(access_token=token['access_token'],expires=s.now()+token.get('expires_in',3600));c.execute('UPDATE biz_connections SET private=? WHERE id=? AND user=?',(s.encode(current),cid,user))
 return token['access_token']

def google_files(connect,user,cid):
 token=google_token(connect,user,cid)
 result=http('https://www.googleapis.com/drive/v3/files?'+urllib.parse.urlencode({'pageSize':100,'fields':'files(id,name,mimeType)','q':'trashed = false'}),headers={'Authorization':'Bearer '+token})
 return {'files':result.get('files',[]),'notice':'Only files authorized for this application are listed. Create documents here or explicitly open/share files with this app in Google.'}

def execute_action(connect,user,p,payload):
 kind=payload['kind'];cid=p['config']['connections'].get('email' if kind=='email' else 'github' if kind=='github_issue' else 'google','')
 with connect() as c:cfg=get(c,user,cid)
 if kind=='email':
  result=observability.mail(cfg,payload['destination'],payload['subject'],payload['body'])
  if result!='sent':raise s.BusinessError('Email '+result+'; inspect recipient and delivery before retrying.')
  return {'status':'sent'}
 if kind=='github_issue':
  repo=payload['destination']
  if repo not in cfg.get('repositories',[]) or cfg.get('issues_enabled') is not True:raise s.BusinessError('GitHub issue creation is not enabled for this repository.')
  r=http('https://api.github.com/repos/'+repo+'/issues',{'title':payload['subject'][:200],'body':payload['body']},{'Authorization':'Bearer '+cfg['token'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
  return {'url':r.get('html_url'),'number':r.get('number')}
 token=google_token(connect,user,cid);head={'Authorization':'Bearer '+token};parent=payload['destination']
 if parent!='root' and parent not in cfg.get('selected_files',[]):raise s.BusinessError('Select this Google parent folder in the connection first.')
 if kind in ('drive_document','drive_folder'):
  mime='application/vnd.google-apps.document' if kind=='drive_document' else 'application/vnd.google-apps.folder'
  r=http('https://www.googleapis.com/drive/v3/files',{'name':payload['subject'][:150],'mimeType':mime,'parents':[parent]},head)
  if kind=='drive_document':http('https://docs.googleapis.com/v1/documents/'+r['id']+':batchUpdate',{'requests':[{'insertText':{'location':{'index':1},'text':payload['body']}}]},head)
  with connect() as c:
   cfg=get(c,user,cid,'google');cfg['selected_files']=list(set(cfg.get('selected_files',[])+[r['id']]));c.execute('UPDATE biz_connections SET private=? WHERE id=? AND user=?',(s.encode(cfg),cid,user))
  return {'id':r['id'],'url':'https://drive.google.com/open?id='+r['id']}
 if kind=='sheet_export':
  r=http('https://sheets.googleapis.com/v4/spreadsheets',{'properties':{'title':payload['subject'][:150]}},head);sid=r['spreadsheetId']
  http('https://sheets.googleapis.com/v4/spreadsheets/'+sid+'/values/A1?valueInputOption=RAW',{'values':[['Forma report'],[payload['body']]]},head,method='PUT')
  if parent!='root':http('https://www.googleapis.com/drive/v3/files/'+sid+'?'+urllib.parse.urlencode({'addParents':parent}),{},head,method='PATCH')
  return {'id':sid,'url':r.get('spreadsheetUrl')}
 raise s.BusinessError('Unsupported external action.')

def import_email(connect,user,p):
 cid=p['config']['connections'].get('email','')
 with connect() as c:cfg=get(c,user,cid,'email')
 imported=[]
 try:
  with imaplib.IMAP4_SSL(cfg['imap_host'],cfg['imap_port'],ssl_context=ssl.create_default_context(),timeout=20) as box:
   box.login(cfg['smtp_username'],cfg['smtp_password']);status,_=box.select(p['config']['mail_folder'],readonly=True)
   if status!='OK':raise s.BusinessError('Selected email folder is unavailable.')
   validity=box.response('UIDVALIDITY')[1][0].decode();status,ids=box.uid('search',None,'ALL')
   if status!='OK':raise s.BusinessError('Email folder could not be searched.')
   for mid in ids[0].split()[-20:]:
    message=validity+':'+mid.decode()
    with connect() as c:
     if c.execute('SELECT 1 FROM biz_mail_seen WHERE user=? AND connection=? AND message=?',(user,cid,message)).fetchone():continue
    status,parts=box.uid('fetch',mid,'(RFC822.SIZE)')
    if status!='OK' or not parts or int(re.search(rb'RFC822.SIZE (\d+)',parts[0])[1])>200000:continue
    status,parts=box.uid('fetch',mid,'(BODY.PEEK[])')
    if status!='OK':continue
    raw=next((x[1] for x in parts if isinstance(x,tuple)),b'');msg=email.message_from_bytes(raw)
    plain=[]
    for part in msg.walk():
     if part.get_content_type()=='text/plain' and part.get_content_disposition()!='attachment':plain.append((part.get_payload(decode=True) or b'').decode(part.get_content_charset() or 'utf-8',errors='replace')[:12000])
    item={'subject':str(make_header(decode_header(msg.get('Subject',''))))[:300],'from':email.utils.parseaddr(msg.get('From',''))[1],'body':'\n'.join(plain)[:12000],'source':'private_email','message':message}
    with connect() as c:
     c.execute('BEGIN IMMEDIATE')
     if c.execute('INSERT OR IGNORE INTO biz_mail_seen VALUES(?,?,?)',(user,cid,message)).rowcount:
      aid=s.artifact(c,user,p['id'],'','enquiry',item);s.event(c,user,p['id'],'','email_imported',{'artifact':aid});imported.append(aid)
 except s.BusinessError:raise
 except Exception:raise s.BusinessError('Email import failed. Check IMAP connection, folder and app password.') from None
 return {'imported':imported}

def read_selected(connect,user,p):
 cid=p['config']['connections'].get('google','');selected=p['config'].get('selected_files',[])
 if not selected:return []
 with connect() as c:cfg=get(c,user,cid,'google')
 if not set(selected)<=set(cfg.get('selected_files',[])):raise s.BusinessError('Select and authorize these Google files before importing them.')
 token=google_token(connect,user,cid);head={'Authorization':'Bearer '+token};documents=[]
 for fid in selected:
  meta=http('https://www.googleapis.com/drive/v3/files/'+fid+'?fields=id,name,mimeType',headers=head);content=''
  if meta.get('mimeType')=='application/vnd.google-apps.document':
   doc=http('https://docs.googleapis.com/v1/documents/'+fid,headers=head)
   content=''.join(e.get('textRun',{}).get('content','') for block in doc.get('body',{}).get('content',[]) for e in block.get('paragraph',{}).get('elements',[]))[:16000]
  elif meta.get('mimeType')=='application/vnd.google-apps.spreadsheet':
   sheet=http('https://sheets.googleapis.com/v4/spreadsheets/'+fid+'/values/A1:Z200',headers=head);content=json.dumps(sheet.get('values',[]))[:16000]
  documents.append({'id':fid,'name':meta.get('name'),'mime_type':meta.get('mimeType'),'text':content,'source':'private_google_file'})
 return documents
