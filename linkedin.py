"""LinkedIn OAuth and reviewed text publishing. No automatic publication."""
import hashlib,json,secrets,time,urllib.parse,urllib.request,urllib.error
import settings,media,linkedin_pages
class LinkedInError(Exception):pass
def init(c):
 linkedin_pages.init(c)
 c.executescript('CREATE TABLE IF NOT EXISTS linkedin_states(state TEXT PRIMARY KEY,user TEXT,session TEXT,expires INTEGER); CREATE TABLE IF NOT EXISTS linkedin_accounts(user TEXT PRIMARY KEY,encrypted BLOB); CREATE TABLE IF NOT EXISTS approvals(user TEXT,draft TEXT,digest TEXT,PRIMARY KEY(user,draft)); CREATE TABLE IF NOT EXISTS publications(user TEXT,draft TEXT,status TEXT,post TEXT,PRIMARY KEY(user,draft));')
def digest(text):return hashlib.sha256(text.encode()).hexdigest()
def request(url,data=None,headers=None,form=False):
 body=(urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()) if data is not None else None
 req=urllib.request.Request(url,data=body,headers=headers or {})
 try:
  with urllib.request.urlopen(req,timeout=25) as r:
   raw=r.read();return r.status,json.loads(raw) if raw else {},r.headers
 except urllib.error.HTTPError as e:
  raise LinkedInError('LinkedIn denied the request. Check product access or reconnect.' if e.code in (401,403) else 'LinkedIn request failed. Check the platform before retrying.') from None
 except (urllib.error.URLError,TimeoutError,ValueError):raise LinkedInError('LinkedIn response unavailable. Publication outcome may be unknown; check LinkedIn before trying another draft.') from None
def start(c,user,session,page_access=False):
 s=settings.load(c)
 if not all(s.get(k) for k in ('linkedin_client_id','linkedin_client_secret','linkedin_redirect_uri')):raise LinkedInError('Administrator must configure the LinkedIn application first.')
 state=secrets.token_urlsafe(32);
 if page_access:state='pages_'+state
 c.execute('DELETE FROM linkedin_states WHERE expires<?',(time.time(),));c.execute('INSERT INTO linkedin_states VALUES(?,?,?,?)',(digest(state),user,session,int(time.time())+600))
 return 'https://www.linkedin.com/oauth/v2/authorization?'+urllib.parse.urlencode({'response_type':'code','client_id':s['linkedin_client_id'],'redirect_uri':s['linkedin_redirect_uri'],'state':state,'scope':'openid profile w_member_social'+(' w_organization_social r_organization_admin' if page_access else '')})
def callback(connect,user,session,query):
 state=query.get('state',[''])[0]
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM linkedin_states WHERE state=?',(digest(state),)).fetchone()
  if not r or r['user']!=user or r['session']!=session or r['expires']<time.time():raise LinkedInError('Invalid or expired OAuth state. Start connection again.')
  c.execute('DELETE FROM linkedin_states WHERE state=?',(digest(state),));s=settings.load(c)
 if query.get('error'):raise LinkedInError('LinkedIn authorization was not granted.')
 code=query.get('code',[''])[0]
 if not code:raise LinkedInError('LinkedIn returned no authorization code.')
 _,token,_=request('https://www.linkedin.com/oauth/v2/accessToken',{'grant_type':'authorization_code','code':code,'redirect_uri':s['linkedin_redirect_uri'],'client_id':s['linkedin_client_id'],'client_secret':s['linkedin_client_secret']},{'Content-Type':'application/x-www-form-urlencoded'},True)
 access=token.get('access_token');expires=token.get('expires_in')
 if not access or not isinstance(expires,(int,float)) or expires<=0:raise LinkedInError('LinkedIn returned an invalid access token response.')
 _,profile,_=request('https://api.linkedin.com/v2/userinfo',headers={'Authorization':'Bearer '+access})
 sub=profile.get('sub')
 if not isinstance(sub,str) or not sub or len(sub)>200:raise LinkedInError('LinkedIn returned no member identifier.')
 record={'page_access':state.startswith('pages_'),'token':access,'expires':int(time.time()+expires),'sub':sub,'name':profile.get('name','LinkedIn member')}
 with connect() as c:c.execute('INSERT INTO linkedin_accounts VALUES(?,?) ON CONFLICT(user) DO UPDATE SET encrypted=excluded.encrypted',(user,settings.cipher().encrypt(json.dumps(record).encode())))
def account(c,user):
 r=c.execute('SELECT encrypted FROM linkedin_accounts WHERE user=?',(user,)).fetchone()
 return json.loads(settings.cipher().decrypt(r[0])) if r else None
def status(c,user):
 a=account(c,user);s=settings.load(c)
 return {'configured':all(s.get(k) for k in ('linkedin_client_id','linkedin_client_secret','linkedin_redirect_uri')),'connected':bool(a and a['expires']>time.time()),'name':a['name'] if a else None,'expires':a['expires'] if a else None,'destination':linkedin_pages.destination(c,user),'page_permissions_requested':bool(a and a.get('page_access')),'page_publications':[dict(r) for r in c.execute('SELECT draft,page,status,post FROM page_publications WHERE user=?',(user,))],'publications':[dict(r) for r in c.execute('SELECT draft,status,post FROM publications WHERE user=?',(user,))]}
def _publish_personal(connect,user,draft_id,expected_text,expected_media=None):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');a=account(c,user)
  if not a or a['expires']<=time.time():raise LinkedInError('LinkedIn is disconnected or expired. Reconnect first.')
  r=c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone();w=json.loads(r[0]) if r else {};d=next((x for x in w.get('drafts',[]) if x.get('id')==draft_id),None)
  approval=c.execute('SELECT digest FROM approvals WHERE user=? AND draft=?',(user,draft_id)).fetchone()
  if not d or d.get('status')!='approved' or not approval or approval[0]!=media.approval_digest(c,user,d) or expected_text!=d['text']:raise LinkedInError('Save and approve the exact draft text before publishing.')
  attachment=media.get(c,user,draft_id)
  if (attachment['id'] if attachment else None)!=expected_media:raise LinkedInError('The image changed. Review the saved image before publishing.')
  if not 1<=len(d['text'])<=3000:raise LinkedInError('LinkedIn text must contain 1–3000 characters.')
  if c.execute('SELECT 1 FROM publications WHERE user=? AND draft=?',(user,draft_id)).fetchone():raise LinkedInError('Publication already attempted. Check its status and LinkedIn; do not duplicate it.')
  c.execute('INSERT INTO publications VALUES(?,?,?,?)',(user,draft_id,'pending',None));text=d['text']
 payload={'author':'urn:li:person:'+a['sub'],'lifecycleState':'PUBLISHED','specificContent':{'com.linkedin.ugc.ShareContent':{'shareCommentary':{'text':text},'shareMediaCategory':'NONE'}},'visibility':{'com.linkedin.ugc.MemberNetworkVisibility':'PUBLIC'}}
 try:
  if attachment:
   asset=upload_image(a,attachment)
   content=payload['specificContent']['com.linkedin.ugc.ShareContent'];content['shareMediaCategory']='IMAGE';content['media']=[{'status':'READY','media':asset,'description':{'text':attachment['alt']}}]
  code,body,headers=request('https://api.linkedin.com/v2/ugcPosts',payload,{'Authorization':'Bearer '+a['token'],'Content-Type':'application/json','X-Restli-Protocol-Version':'2.0.0'})
  post=headers.get('X-RestLi-Id') or body.get('id')
  if code!=201 or not post:raise LinkedInError('Publication response was ambiguous. Check LinkedIn before taking further action.')
 except LinkedInError:
  with connect() as c:c.execute('UPDATE publications SET status=? WHERE user=? AND draft=?',('unknown',user,draft_id))
  raise
 with connect() as c:c.execute('UPDATE publications SET status=?,post=? WHERE user=? AND draft=?',('published',str(post),user,draft_id))
 settings.complete(connect,user,'linkedin_published','LinkedIn Content published one approved post'+(' with an image.' if attachment else '.')+' LinkedIn returned a successful creation response.')
 return str(post)


def upload_image(account,attachment):
 headers={'Authorization':'Bearer '+account['token'],'Content-Type':'application/json','X-Restli-Protocol-Version':'2.0.0'}
 payload={'registerUploadRequest':{'recipes':['urn:li:digitalmediaRecipe:feedshare-image'],'owner':'urn:li:person:'+account['sub'],'serviceRelationships':[{'relationshipType':'OWNER','identifier':'urn:li:userGeneratedContent'}],'supportedUploadMechanism':['SYNCHRONOUS_UPLOAD']}}
 _,result,_=request('https://api.linkedin.com/v2/assets?action=registerUpload',payload,headers)
 try:
  value=result['value'];url=value['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl'];asset=value['asset'];parsed=urllib.parse.urlsplit(url)
  if parsed.scheme!='https' or not (parsed.hostname=='linkedin.com' or parsed.hostname.endswith('.linkedin.com')) or parsed.username or parsed.password or parsed.port not in (None,443) or not asset.startswith('urn:li:digitalmediaAsset:'):raise ValueError()
 except (KeyError,TypeError,ValueError,AttributeError):raise LinkedInError('LinkedIn returned an invalid image upload destination.') from None
 req=urllib.request.Request(url,data=attachment['data'],headers={'Authorization':'Bearer '+account['token'],'Content-Type':'image/png'},method='PUT')
 # Do not follow redirects carrying private image bytes or credentials to another host.
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):return None
 try:
  with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as response:
   if response.status not in (200,201):raise LinkedInError('LinkedIn did not accept the image.')
 except (urllib.error.URLError,TimeoutError):raise LinkedInError('LinkedIn image upload failed. No post creation was confirmed.') from None
 return asset


def publish(connect,user,draft_id,expected_text,expected_media=None):
 with connect() as c:target=linkedin_pages.destination(c,user);a=account(c,user)
 if target['mode']=='personal':return _publish_personal(connect,user,draft_id,expected_text,expected_media)
 if not a or a['expires']<=time.time():raise LinkedInError('Reconnect LinkedIn first.')
 # Preflight before either post; no silent fallback to personal-only.
 linkedin_pages.verify(a,target)
 with connect() as c:
  prior=c.execute('SELECT status,post FROM publications WHERE user=? AND draft=?',(user,draft_id)).fetchone()
  page_prior=c.execute('SELECT status FROM page_publications WHERE user=? AND draft=? AND page=?',(user,draft_id,target['page'])).fetchone()
 if page_prior:raise LinkedInError('Page publication already attempted. Check both destination statuses before retrying.')
 if prior and prior['status']!='published':raise LinkedInError('Personal publication outcome is unconfirmed. Check LinkedIn before proceeding.')
 personal=prior['post'] if prior else _publish_personal(connect,user,draft_id,expected_text,expected_media)
 try:page=linkedin_pages.publish(connect,user,draft_id,expected_text,expected_media)
 except LinkedInError as e:raise LinkedInError('Personal profile published; Page not confirmed. '+str(e)) from None
 return {'personal':personal,'page':page}
