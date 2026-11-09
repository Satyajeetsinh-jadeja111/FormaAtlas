"""Versioned plan offers and server-verified Razorpay subscriptions."""
import base64,hashlib,hmac,json,re,secrets,time,urllib.request,urllib.error
import settings
class BillingError(ValueError):pass
DEFAULTS=[
 {'id':'free','name':'Free','amount':0,'posts_per_day':1,'agent_runs':10,'automation':False,'features':['LinkedIn content studio','One draft per day','Manual publishing and local graphics','10 agent runs per month']},
 {'id':'pro','name':'Pro','amount':29900,'posts_per_day':3,'agent_runs':100,'automation':True,'features':['Everything in Free','Up to 3 scheduled posts per day','Automatic publishing with your authorization','100 agent runs per month']},
 {'id':'premium','name':'Premium','amount':79900,'posts_per_day':10,'agent_runs':500,'automation':True,'features':['Everything in Pro','Up to 10 scheduled posts per day','500 agent runs per month','Highest available scheduling allowance']}]

def init(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS billing_config(id INTEGER PRIMARY KEY,encrypted BLOB);
 CREATE TABLE IF NOT EXISTS plan_offers(version TEXT PRIMARY KEY,plans TEXT,approved INTEGER,created INTEGER);
 CREATE TABLE IF NOT EXISTS billing_subscriptions(id TEXT PRIMARY KEY,user TEXT,plan TEXT,version TEXT,mode TEXT,state TEXT,valid_until INTEGER,created INTEGER);
 CREATE TABLE IF NOT EXISTS billing_events(id TEXT PRIMARY KEY,created INTEGER);
 CREATE TABLE IF NOT EXISTS billing_drafts(user TEXT,draft TEXT,day TEXT,PRIMARY KEY(user,draft));
 CREATE TABLE IF NOT EXISTS billing_attempts(user TEXT PRIMARY KEY,created INTEGER);
 ''')
 if not c.execute('SELECT 1 FROM plan_offers').fetchone():c.execute('INSERT INTO plan_offers VALUES(?,?,0,?)',(secrets.token_hex(8),json.dumps(DEFAULTS),int(time.time())))
def config(c):
 r=c.execute('SELECT encrypted FROM billing_config WHERE id=1').fetchone()
 return json.loads(settings.cipher().decrypt(r[0])) if r else {'mode':'test','enabled':False}
def public_config(s):
 return {k:v for k,v in s.items() if k not in ('secret','webhook_secret')}|{'secret_saved':bool(s.get('secret')),'webhook_secret_saved':bool(s.get('webhook_secret'))}
def configure(c,data):
 if set(data)-{'key_id','secret','webhook_secret','mode','enabled','remove_secret','remove_webhook_secret','live_confirmed'}:raise BillingError('Unknown payment setting.')
 s=config(c)
 for k,v in data.items():
  if k.startswith('remove_'):
   if v is True:s.pop(k[7:],None)
  elif k in ('secret','webhook_secret'):
   if v:
    if not isinstance(v,str) or len(v)>4096:raise BillingError('Invalid credential.')
    s[k]=v
  else:s[k]=v
 if s.get('mode') not in ('test','live') or type(s.get('enabled',False)) is not bool:raise BillingError('Choose test or live mode.')
 if s.get('key_id') and not re.fullmatch('rzp_'+s['mode']+'_[A-Za-z0-9]+',s['key_id']):raise BillingError('Key ID must match the selected mode.')
 if s.get('enabled'):
  if not all(s.get(k) for k in ('key_id','secret','webhook_secret')):raise BillingError('Add all Razorpay credentials before enabling checkout.')
  if s['mode']=='live' and s.get('live_confirmed') is not True:raise BillingError('Confirm live payment activation explicitly.')
 # Never reuse an old key secret or webhook secret for a changed account/environment.
 old=config(c)
 if (old.get('mode')!=s.get('mode') or old.get('key_id')!=s.get('key_id')) and s.get('enabled') and not all(data.get(k) for k in ('secret','webhook_secret')):raise BillingError('Supply fresh secrets when changing mode or key ID.')
 if old.get('mode')!=s.get('mode') or old.get('key_id')!=s.get('key_id'):
  for k in ('secret','webhook_secret'):
   if not data.get(k):s.pop(k,None)
 c.execute('INSERT INTO billing_config VALUES(1,?) ON CONFLICT(id) DO UPDATE SET encrypted=excluded.encrypted',(settings.cipher().encrypt(json.dumps(s).encode()),))
 return public_config(s)
def offers(c,admin=False):
 r=c.execute('SELECT * FROM plan_offers WHERE approved=1 ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
 result={'version':r['version'],'approved':True,'plans':json.loads(r['plans'])} if r else {'version':None,'approved':False,'plans':DEFAULTS}
 if admin:
  d=c.execute('SELECT * FROM plan_offers ORDER BY created DESC,rowid DESC LIMIT 1').fetchone()
  result['draft']={'version':d['version'],'approved':bool(d['approved']),'plans':json.loads(d['plans'])}
 return result
def save_offer(c,plans):
 if not isinstance(plans,list) or len(plans)!=3:raise BillingError('Provide Free, Pro and Premium plans.')
 clean=[]
 for p,identity in zip(plans,('free','pro','premium')):
  if not isinstance(p,dict):raise BillingError('Invalid plan entry.')
  if p.get('id')!=identity:raise BillingError('Plan identifiers cannot change.')
  for key,lo,hi in [('amount',0 if identity=='free' else 100,0 if identity=='free' else 10000000),('posts_per_day',1,10),('agent_runs',0,10000)]:
   if type(p.get(key)) is not int or not lo<=p[key]<=hi:raise BillingError('Invalid plan price or usage limit.')
  if type(p.get('automation')) is not bool:raise BillingError('Automation must be true or false.')
  if not isinstance(p.get('name'),str) or not 1<=len(p['name'])<=40:raise BillingError('Use a plan name of 1–40 characters.')
  if not isinstance(p.get('features'),list) or not 1<=len(p['features'])<=12 or any(not isinstance(f,str) or not 1<=len(f)<=200 for f in p['features']):raise BillingError('Add 1–12 short feature descriptions.')
  clean.append({k:p[k] for k in ('id','name','amount','posts_per_day','agent_runs','automation','features')})
 version=secrets.token_hex(8);c.execute('INSERT INTO plan_offers VALUES(?,?,0,?)',(version,json.dumps(clean),int(time.time())))
 return offers(c,True)
def approve(c,version):
 latest=offers(c,True)['draft']
 if latest['version']!=version:raise BillingError('Plans changed. Reload and review the latest draft.')
 c.execute('UPDATE plan_offers SET approved=1 WHERE version=?',(version,));return offers(c,True)
def entitlement(c,user):
 # Admins retain existing owner workflows while customer packaging is introduced.
 admin=c.execute('SELECT 1 FROM admins WHERE user=?',(user,)).fetchone()
 if admin:return dict(DEFAULTS[-1],name='Administrator',admin=True)
 now=int(time.time());r=c.execute("SELECT s.*,o.plans FROM billing_subscriptions s JOIN plan_offers o ON o.version=s.version WHERE s.user=? AND s.mode='live' AND s.valid_until>? AND s.state IN ('active','cancelled') ORDER BY s.valid_until DESC LIMIT 1",(user,now)).fetchone()
 if r:return next(p for p in json.loads(r['plans']) if p['id']==r['plan'])
 return offers(c)['plans'][0]
def check_profile(c,user,p):
 e=entitlement(c,user)
 if int(p.get('posts_per_day',1))>e['posts_per_day']:raise BillingError('Your plan supports up to '+str(e['posts_per_day'])+' posts per day.')
 if (p.get('daily_enabled') or p.get('auto_publish') or p.get('auto_ai')) and not e['automation']:raise BillingError('Scheduled automation requires Pro or Premium. Manual drafting remains available.')
def check_agent(c,user):
 e=entitlement(c,user)
 if e.get('admin'):return
 import datetime
 start=int(datetime.datetime.now(datetime.timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0).timestamp())
 count=c.execute('SELECT COUNT(*) FROM agent_runs WHERE user=? AND created>=?',(user,start)).fetchone()[0]
 if count>=e['agent_runs']:raise BillingError('Monthly agent-run allowance reached. Review Plans & billing.')
def request(s,path,data=None):
 auth=base64.b64encode((s['key_id']+':'+s['secret']).encode()).decode()
 req=urllib.request.Request('https://api.razorpay.com/v1/'+path,data=json.dumps(data).encode() if data is not None else None,headers={'Authorization':'Basic '+auth,'Content-Type':'application/json'})
 try:
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):return None
  with urllib.request.build_opener(NoRedirect).open(req,timeout=15) as response:return json.loads(response.read(300000))
 except (OSError,ValueError):raise BillingError('Razorpay request could not be confirmed. Check credentials and payment status before retrying.') from None

def subscribe(connect,user,plan_id):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');s=config(c);o=offers(c)
  if not s.get('enabled') or not o['approved']:raise BillingError('Checkout is not enabled yet.')
  p=next((p for p in o['plans'] if p['id']==plan_id and p['amount']>0),None)
  if not p:raise BillingError('Choose a paid plan.')
  prior=c.execute('SELECT created FROM billing_attempts WHERE user=?',(user,)).fetchone()
  if prior and prior[0]>time.time()-60:raise BillingError('Wait a minute before starting another checkout.')
  existing=c.execute("SELECT * FROM billing_subscriptions WHERE user=? AND mode=? AND (state IN ('active','authenticated','pending','halted') OR (state='created' AND created>?))",(user,s['mode'],time.time()-86400)).fetchone()
  if existing:
   if existing['state']=='created' and existing['plan']==p['id'] and existing['version']==o['version']:
    return {'key':s['key_id'],'subscription_id':existing['id'],'name':'Forma','description':p['name']+' · monthly recurring subscription, up to 120 months','mode':s['mode']}
   raise BillingError('An existing subscription needs attention. Refresh billing status before starting another.')
  c.execute('INSERT INTO billing_attempts VALUES(?,?) ON CONFLICT(user) DO UPDATE SET created=excluded.created',(user,int(time.time())))
 # Razorpay plans are immutable: each checkout snapshots the approved price/features.
 rp=request(s,'plans',{'period':'monthly','interval':1,'item':{'name':'Forma '+p['name'],'amount':p['amount'],'currency':'INR'}})
 sub=request(s,'subscriptions',{'plan_id':rp['id'],'total_count':120,'quantity':1,'customer_notify':True})
 with connect() as c:c.execute('INSERT INTO billing_subscriptions VALUES(?,?,?,?,?,?,?,?)',(sub['id'],user,p['id'],o['version'],s['mode'],'created',0,int(time.time())))
 return {'key':s['key_id'],'subscription_id':sub['id'],'name':'Forma','description':p['name']+' · monthly recurring subscription, up to 120 months','mode':s['mode']}
def sync(connect,user,sid):
 if not re.fullmatch(r'sub_[A-Za-z0-9]+',sid):raise BillingError('Invalid subscription.')
 with connect() as c:
  row=c.execute('SELECT * FROM billing_subscriptions WHERE id=? AND user=?',(sid,user)).fetchone();s=config(c)
 if not row or row['mode']!=s.get('mode'):raise BillingError('Subscription is not available in this environment.')
 remote=request(s,'subscriptions/'+sid)
 if remote.get('id')!=sid:raise BillingError('Subscription mismatch.')
 # Fetch current provider state rather than trusting browser or out-of-order webhook bodies.
 state=remote.get('status','created');end=int(remote.get('current_end') or 0)
 with connect() as c:c.execute('UPDATE billing_subscriptions SET state=?,valid_until=? WHERE id=?',(state,end,sid))
 return {'state':state,'valid_until':end}
def verify(connect,user,data):
 sid=str(data.get('razorpay_subscription_id',''));pid=str(data.get('razorpay_payment_id',''));signature=str(data.get('razorpay_signature',''))
 with connect() as c:
  s=config(c);row=c.execute('SELECT 1 FROM billing_subscriptions WHERE id=? AND user=?',(sid,user)).fetchone()
 if not row:raise BillingError('Unknown subscription.')
 digest=hmac.new(s.get('secret','').encode(),(pid+'|'+sid).encode(),hashlib.sha256).hexdigest()
 if not s.get('secret') or not hmac.compare_digest(signature,digest):raise BillingError('Payment signature verification failed.')
 return sync(connect,user,sid)
def webhook(connect,raw,signature,event_id):
 with connect() as c:s=config(c)
 if not s.get('webhook_secret') or not hmac.compare_digest(hmac.new(s['webhook_secret'].encode(),raw,hashlib.sha256).hexdigest(),signature or ''):raise BillingError('Invalid webhook signature.')
 try:event=json.loads(raw);sid=event['payload']['subscription']['entity']['id']
 except (ValueError,KeyError,TypeError):return {'ignored':True}
 event_id=event_id or hashlib.sha256(raw).hexdigest()
 with connect() as c:
  if c.execute('SELECT 1 FROM billing_events WHERE id=?',(event_id,)).fetchone():return {'duplicate':True}
  row=c.execute('SELECT user FROM billing_subscriptions WHERE id=?',(sid,)).fetchone()
 if row:sync(connect,row['user'],sid)
 with connect() as c:c.execute('INSERT OR IGNORE INTO billing_events VALUES(?,?)',(event_id,int(time.time())))
 return {'received':True}
def summary(c,user):
 s=config(c);o=offers(c)
 return o|{'mode':s.get('mode','test'),'checkout_enabled':bool(s.get('enabled') and o['approved']),'current':entitlement(c,user) if user else None,'subscriptions':[dict(r) for r in c.execute('SELECT id,plan,mode,state,valid_until FROM billing_subscriptions WHERE user=? ORDER BY created DESC LIMIT 10',(user,))] if user else []}
def cancel(connect,user,sid):
 with connect() as c:row=c.execute('SELECT * FROM billing_subscriptions WHERE id=? AND user=?',(sid,user)).fetchone();s=config(c)
 if not row or row['mode']!=s.get('mode'):raise BillingError('Subscription unavailable.')
 request(s,'subscriptions/'+sid+'/cancel',{'cancel_at_cycle_end':True})
 return sync(connect,user,sid)
