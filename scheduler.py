import observability
"""Local, bounded daily scheduling. Automatic publication requires explicit opt-in."""
import datetime,json,secrets,sqlite3,threading
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
import billing,settings,media,linkedin,news
from providers import refine,ProviderUnavailable

def init(c):
 c.executescript('CREATE TABLE IF NOT EXISTS scheduled_ai_attempts(user TEXT,day TEXT,draft TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS daily_runs(user TEXT,day TEXT,PRIMARY KEY(user,day)); CREATE TABLE IF NOT EXISTS scheduled_slots(user TEXT,day TEXT,slot INTEGER,draft TEXT,created INTEGER,status TEXT,PRIMARY KEY(user,day,slot));')
def validate(profile):
 try:
  count=int(profile.get('posts_per_day',1));interval=int(profile.get('post_interval_minutes',60));hour=datetime.time.fromisoformat(profile.get('time','09:00'));ZoneInfo(profile.get('timezone','Asia/Kolkata'))
  if not 1<=count<=10 or not 30<=interval<=720 or hour.hour*60+hour.minute+(count-1)*interval>=1440:raise ValueError()
  if profile.get('content_mix','mixed') not in ('mixed','projects','technology','news'):raise ValueError()
  if profile.get('auto_ai') and profile.get('ai_consent') is not True:raise ValueError()
  if profile.get('auto_publish') and profile.get('automation_consent') is not True:raise ValueError()
 except (ValueError,TypeError,ZoneInfoNotFoundError):raise ValueError('Use 1–10 posts, at least 30 minutes apart, within the same local day, a valid timezone, and confirm automatic publication if enabled.') from None
 return count,interval,hour

def tick(connect,now=None):
 now=now or datetime.datetime.now(datetime.timezone.utc);completed=[]
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  for row in c.execute('SELECT user,data FROM workspaces').fetchall():
   available=c.execute('SELECT live FROM managed_agents WHERE id=?',('linkedin',)).fetchone()
   admin=c.execute('SELECT 1 FROM admins WHERE user=?',(row['user'],)).fetchone()
   if available and not available[0] and not admin:continue
   w=json.loads(row['data']);p=w.get('profile',{})
   try:billing.check_profile(c,row['user'],p)
   except billing.BillingError:continue
   if not p.get('daily_enabled') or not p.get('name') or not p.get('resume'):continue
   try:count,interval,due=validate(p);local=now.astimezone(ZoneInfo(p.get('timezone','Asia/Kolkata')))
   except ValueError:continue
   start=local.replace(hour=due.hour,minute=due.minute,second=0,microsecond=0);day=local.date().isoformat();drafts=w.setdefault('drafts',[])
   today=[d for d in drafts if d.get('day')==day]
   # Existing manual or old daily drafts count toward the chosen daily total.
   slot=len(today)
   if slot>=count or local<start+datetime.timedelta(minutes=slot*interval):continue
   previous=c.execute('SELECT MAX(created) FROM scheduled_slots WHERE user=? AND day=?',(row['user'],day)).fetchone()[0]
   if previous and now.timestamp()-previous<interval*60:continue
   draft_id=secrets.token_hex(16)
   utc_day=now.astimezone(datetime.timezone.utc).date().isoformat()
   ent=billing.entitlement(c,row['user'])
   used=c.execute('SELECT COUNT(*) FROM billing_drafts WHERE user=? AND day=?',(row['user'],utc_day)).fetchone()[0]
   if not ent.get('admin') and used>=ent['posts_per_day']:continue
   try:c.execute('INSERT INTO scheduled_slots VALUES(?,?,?,?,?,?)',(row['user'],day,slot,draft_id,int(now.timestamp()),'drafting'))
   except sqlite3.IntegrityError:continue
   c.execute('INSERT OR IGNORE INTO billing_drafts VALUES(?,?,?)',(row['user'],draft_id,utc_day))
   facts=[]
   if p.get('rustico'):facts.append(('Rasiko',p['rustico']))
   if p.get('outreach'):facts.append(('Outreach Studio',p['outreach']))
   project,fact=facts[len(drafts)%len(facts)] if facts else ('My professional work',p['resume'])
   openings=['A closer look at '+project+':','From my project portfolio — '+project+':','Sharing the practical details behind '+project+':','A project spotlight: '+project+'.','What I’m working on: '+project+'.']
   closing=['I’m open to both freelance collaborations and full-time opportunities where I can contribute these skills.','I welcome conversations with potential freelance clients and hiring teams about practical technology work.','I’m exploring freelance projects and full-time roles with equal interest.']
   text=openings[(now.date().toordinal()+slot)%len(openings)]+'\n\n'+fact[:2100]+'\n\n'+closing[slot%len(closing)]
   d={'id':draft_id,'day':day,'text':text,'status':'review','mode':'scheduled-template','source':None,'topic':'A closer look at '+project,'scheduled_slot':slot+1};drafts.append(d)
   ai=p.get('auto_ai') is True and p.get('ai_consent') is True
   mix=p.get('content_mix','mixed');kind=('project','technology','news')[len(drafts)%3] if mix=='mixed' else {'projects':'project','technology':'technology','news':'news'}[mix]
   d['content_kind']=kind;d['ai_requested']=ai
   if not ai:media.automatic(c,row['user'],d,p,len(drafts)-1)
   automatic=p.get('auto_publish') is True and p.get('automation_consent') is True
   if automatic and not ai:
    d['status']='approved';c.execute('INSERT INTO approvals VALUES(?,?,?)',(row['user'],draft_id,media.approval_digest(c,row['user'],d)))
   c.execute('UPDATE scheduled_slots SET status=? WHERE user=? AND day=? AND slot=?',('ready' if automatic else 'review',row['user'],day,slot));c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),row['user']))
   image=media.get(c,row['user'],draft_id);completed.append((row['user'],d,automatic,image['id'] if image else None,day,slot,ai,p))
 for user,d,automatic,image,day,slot,ai,profile in completed:
  if ai:
   try:
    d,image=generate(connect,user,d,profile,day,automatic,now)
   except (ProviderUnavailable,news.NewsUnavailable) as exc:
    observability.report(connect,exc,service="scheduler",agent="linkedin",operation="generate",task=d["id"])
    with connect() as c:
     c.execute('UPDATE scheduled_slots SET status=? WHERE user=? AND day=? AND slot=?',('generation_blocked',user,day,slot))
     row=c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone();w=json.loads(row[0]);target=next(x for x in w['drafts'] if x['id']==d['id']);target['status']='review';target['generation_error']='AI generation is held. Check Gemini configuration/quota or official-source availability. No automatic publication occurred.';c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),user))
    settings.complete(connect,user,'automatic_generation_failed','Scheduled AI text generation was held because configuration, quota, source availability or a concurrent edit prevented completion. Nothing was automatically published.')
    continue
  settings.complete(connect,user,'draft_saved','LinkedIn Content prepared one scheduled facts-based draft with a local graphic when enabled.')
  if automatic:
   # Recheck pause immediately before external work. Never publish pre-existing manual drafts.
   with connect() as c:
    current=json.loads(c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone()[0]).get('profile',{})
   state='paused'
   with connect() as c:
    try:billing.check_profile(c,user,current)
    except billing.BillingError:continue
   with connect() as c:
    available=c.execute('SELECT live FROM managed_agents WHERE id=?',('linkedin',)).fetchone();admin=c.execute('SELECT 1 FROM admins WHERE user=?',(user,)).fetchone()
   if (not available or available[0] or admin) and current.get('daily_enabled') and current.get('auto_publish') and current.get('automation_consent'):
    try:linkedin.publish(connect,user,d['id'],d['text'],image);state='published'
    except linkedin.LinkedInError as exc:
     observability.report(connect,exc,service='scheduler',agent='linkedin',operation='publish',task=d['id'])
     state='failed_or_unknown';settings.complete(connect,user,'automatic_publish_failed','A scheduled LinkedIn post could not be confirmed. Check the connection and publication status; no automatic retry was attempted.')
   with connect() as c:c.execute('UPDATE scheduled_slots SET status=? WHERE user=? AND day=? AND slot=?',(state,user,day,slot))
 return len(completed)

def start(connect):
 stop=threading.Event()
 def loop():
  while not stop.is_set():
   try:tick(connect)
   except Exception as exc:observability.report(connect,exc,service='scheduler',agent='linkedin',operation='scheduled_tick')
   stop.wait(30)
 threading.Thread(target=loop,daemon=True).start();return stop


def generate(connect,user,d,profile,day,automatic,now):
 source=None;kind=d.get('content_kind','project')
 with connect() as c:
  config=settings.load(c);row=c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone();workspace=json.loads(row[0]);current=workspace.get('profile',{})
  if not current.get('auto_ai') or not current.get('ai_consent'):raise ProviderUnavailable('AI generation was paused.')
  if not config.get('gemini_key') or not config.get('model') or config.get('free_tier_confirmed') is not True:raise ProviderUnavailable('Gemini is not configured for free-tier use.')
 if kind=='news':source=news.select([x.get('source') for x in workspace.get('drafts',[])],now)
 seed=d['text']
 if kind=='technology':seed='Write a practical professional perspective on '+str(profile.get('interests') or 'user-friendly web applications and software engineering')[:500]+'. This is general commentary, not breaking news. Connect the perspective to the supplied professional background without inventing experience.'
 if source:seed='Write a short source-attributed technology update, followed by a clearly identified practical perspective for businesses and engineering teams. Source material: '+json.dumps(source)
 with connect() as c:
  try:c.execute('INSERT INTO scheduled_ai_attempts VALUES(?,?,?)',(user,day,d['id']))
  except sqlite3.IntegrityError:raise ProviderUnavailable('AI generation was already attempted.') from None
 text=refine(profile,seed,config,source=source)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone();w=json.loads(row[0]);current=w.get('profile',{});target=next(x for x in w['drafts'] if x['id']==d['id'])
  if target['text']!=d['text'] or target.get('status')!='review':raise ProviderUnavailable('Draft changed during generation.')
  target.update(text=text,mode='scheduled-gemini',source=source['url'] if source else None)
  if source:target['topic']=source['title'];target['news_source']=source
  if kind=='technology':target['topic']='Practical technology for business'
  media.automatic(c,user,target,current,len(w['drafts'])-1)
  if automatic and current.get('daily_enabled') and current.get('auto_publish') and current.get('automation_consent') and current.get('auto_ai') and current.get('ai_consent'):
   target['status']='approved';c.execute('INSERT INTO approvals VALUES(?,?,?) ON CONFLICT(user,draft) DO UPDATE SET digest=excluded.digest',(user,d['id'],media.approval_digest(c,user,target)))
  c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),user));image=media.get(c,user,d['id'])
  return target,image['id'] if image else None
