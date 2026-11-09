"""Explicit Page destinations and independent, duplicate-safe Page publication."""
import json,re,time,urllib.parse,urllib.request,urllib.error
import media,settings
VERSION='202607'
def init(c):
 c.executescript('CREATE TABLE IF NOT EXISTS linkedin_destinations(user TEXT PRIMARY KEY,mode TEXT,page TEXT,member TEXT); CREATE TABLE IF NOT EXISTS page_publications(user TEXT,draft TEXT,page TEXT,status TEXT,post TEXT,PRIMARY KEY(user,draft,page));')
def destination(c,user):
 r=c.execute('SELECT mode,page,member FROM linkedin_destinations WHERE user=?',(user,)).fetchone()
 return dict(r) if r else {'mode':'personal','page':'','member':''}
def headers(a):return {'Authorization':'Bearer '+a['token'],'Content-Type':'application/json','X-Restli-Protocol-Version':'2.0.0','LinkedIn-Version':VERSION}
def pages(a):
 import linkedin
 if not a or a['expires']<=time.time():raise linkedin.LinkedInError('Reconnect LinkedIn first.')
 if not a.get('page_access'):raise linkedin.LinkedInError('Connect with Page permissions after LinkedIn approves Community Management access.')
 results=[]
 # Bounded pagination, never follow response-supplied URLs.
 for start in range(0,500,100):
  _,data,_=linkedin.request('https://api.linkedin.com/rest/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED&count=100&start='+str(start),headers=headers(a))
  rows=data.get('elements',[])
  for r in rows:
   urn=r.get('organizationTarget','')
   if r.get('state')=='APPROVED' and r.get('role')=='ADMINISTRATOR' and re.fullmatch(r'urn:li:organization:[0-9]+',urn) and r.get('roleAssignee')=='urn:li:person:'+a['sub']:results.append({'id':urn,'label':'LinkedIn Page '+urn.rsplit(':',1)[1]})
  if len(rows)<100:return results
 raise linkedin.LinkedInError('Too many Pages to verify safely. Use a dedicated Page administrator account.')
def save(connect,user,data):
 import linkedin
 mode=data.get('mode')
 if mode not in ('personal','both'):raise linkedin.LinkedInError('Choose personal profile or both destinations.')
 if mode=='both':
  with connect() as c:a=linkedin.account(c,user)
  page=data.get('page','')
  if page not in {p['id'] for p in pages(a)}:raise linkedin.LinkedInError('Select a Page where LinkedIn verified your administrator role.')
  member=a['sub']
 else:page='';member=''
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  current=linkedin.account(c,user)
  if mode=='both' and (not current or current['sub']!=member):raise linkedin.LinkedInError('Connection changed. Select your Page again.')
  old=destination(c,user)
  c.execute('INSERT INTO linkedin_destinations VALUES(?,?,?,?) ON CONFLICT(user) DO UPDATE SET mode=excluded.mode,page=excluded.page,member=excluded.member',(user,mode,page,member))
  if old!={'mode':mode,'page':page,'member':member}:c.execute('DELETE FROM approvals WHERE user=?',(user,))
 return {'mode':mode,'page':page,'review_required':True}
def verify(a,target):
 import linkedin
 if a.get('sub')!=target['member'] or target['page'] not in {p['id'] for p in pages(a)}:raise linkedin.LinkedInError('Page access changed. Reconnect and verify the selected Page before publishing.')
def image(a,attachment,page):
 import linkedin
 _,data,_=linkedin.request('https://api.linkedin.com/rest/images?action=initializeUpload',{'initializeUploadRequest':{'owner':page}},headers(a))
 try:
  v=data['value'];url=v['uploadUrl'];urn=v['image'];p=urllib.parse.urlsplit(url)
  if p.scheme!='https' or not p.hostname or not (p.hostname=='linkedin.com' or p.hostname.endswith('.linkedin.com')) or p.username or p.password or p.port not in (None,443) or not urn.startswith('urn:li:image:'):raise ValueError()
 except (KeyError,TypeError,ValueError):raise linkedin.LinkedInError('Invalid Page image upload response.') from None
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):return None
 try:
  req=urllib.request.Request(url,data=attachment['data'],headers={'Authorization':'Bearer '+a['token'],'Content-Type':'image/png'},method='PUT')
  with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as r:
   if r.status not in (200,201):raise linkedin.LinkedInError('Page image upload was not accepted.')
 except (urllib.error.URLError,TimeoutError):raise linkedin.LinkedInError('Page image upload failed. No Page post was confirmed.') from None
 _,ready,_=linkedin.request('https://api.linkedin.com/rest/images/'+urllib.parse.quote(urn,safe=''),headers=headers(a))
 if ready.get('status')!='AVAILABLE':raise linkedin.LinkedInError('Page image is not ready. Page publication is held; no automatic retry will occur.')
 return urn

def publish(connect,user,draft_id,text,expected_media):
 import linkedin
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');a=linkedin.account(c,user);target=destination(c,user)
  if not a or a['expires']<=time.time() or target['mode']!='both':raise linkedin.LinkedInError('Page destination is not connected.')
  w=json.loads(c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone()[0]);d=next((d for d in w.get('drafts',[]) if d['id']==draft_id),None)
  approved=c.execute('SELECT digest FROM approvals WHERE user=? AND draft=?',(user,draft_id)).fetchone();attachment=media.get(c,user,draft_id)
  if not d or d.get('status')!='approved' or not approved or approved[0]!=media.approval_digest(c,user,d) or d['text']!=text or (attachment['id'] if attachment else None)!=expected_media:raise linkedin.LinkedInError('Review and approve the exact text, image and destinations again.')
  if c.execute('SELECT 1 FROM page_publications WHERE user=? AND draft=? AND page=?',(user,draft_id,target['page'])).fetchone():raise linkedin.LinkedInError('Page publication already attempted. Check its status; do not duplicate it.')
  c.execute('INSERT INTO page_publications VALUES(?,?,?,?,?)',(user,draft_id,target['page'],'pending',None))
 try:
  verify(a,target)
  payload={'author':target['page'],'commentary':text,'visibility':'PUBLIC','distribution':{'feedDistribution':'MAIN_FEED','targetEntities':[],'thirdPartyDistributionChannels':[]},'lifecycleState':'PUBLISHED','isReshareDisabledByAuthor':False}
  if attachment:payload['content']={'media':{'id':image(a,attachment,target['page']),'altText':attachment['alt']}}
  code,body,h=linkedin.request('https://api.linkedin.com/rest/posts',payload,headers(a));post=h.get('x-restli-id') or h.get('X-RestLi-Id') or body.get('id')
  if code!=201 or not post:raise linkedin.LinkedInError('Page publication outcome is unknown. Check LinkedIn before posting again.')
 except linkedin.LinkedInError:
  with connect() as c:c.execute('UPDATE page_publications SET status=? WHERE user=? AND draft=? AND page=?',('unknown',user,draft_id,target['page']))
  raise
 with connect() as c:c.execute('UPDATE page_publications SET status=?,post=? WHERE user=? AND draft=? AND page=?',('published',str(post),user,draft_id,target['page']))
 settings.complete(connect,user,'linkedin_published','LinkedIn confirmed publication of one approved post to the selected company Page.')
 return str(post)
