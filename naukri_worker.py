"""Visible local browser worker. Login happens only on Naukri. See NAUKRI_SETUP.md."""
import argparse, json, os, re, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import naukri, server, settings, naukri_gemini

def answer(question,p,user=None):
 saved=json.loads(p.get('answers') or '{}')
 if question in saved:return saved[question]
 if user is None:return None
 return naukri_gemini.answer(server.db,user,question,p)

class DailyLimit(RuntimeError):pass

def blocked(page):
 text=page.locator('body').inner_text().lower()
 if re.search(r'(daily|today|24 hours).{0,70}(application limit|apply limit|limit reached)|(?:application|apply).{0,50}limit.{0,40}(reached|exceeded)|maximum.{0,25}applications|already applied.{0,30}(?:jobs|applications).{0,15}(?:today|24 hours)',text):raise DailyLimit('Naukri reported its application limit.')
 if any(x in text for x in ('verify you are human','unusual activity','captcha','too many requests')):raise RuntimeError('Naukri verification or rate limit: stop and resolve manually.')
 if '/nlogin/' in page.url:raise RuntimeError('Session expired. Run --login again.')

def run(user,login=False):
 if login:
  from naukri_login import login_browser
  return login_browser(user)
 os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(Path('.forma/naukri-browsers').resolve()))
 from playwright.sync_api import sync_playwright
 with server.db() as c:
  row=c.execute('SELECT data,resume FROM nk_profiles WHERE user=?',(user,)).fetchone()
 if not row:raise RuntimeError('Save your Naukri workspace first.')
 p=json.loads(row['data'])
 with server.db() as c:
  cap=settings.load(c).get('nk_daily_limit',50)
  day=datetime.now(ZoneInfo(p['timezone'])).date().isoformat()
  done=c.execute('SELECT 1 FROM nk_days WHERE user=? AND day=?',(user,day)).fetchone()
 if done and not login:return
 if not login and (not p['enabled'] or datetime.now(ZoneInfo(p['timezone'])).strftime('%H:%M')<p['time']):return
 root=Path('.forma/naukri')/user;root.mkdir(parents=True,exist_ok=True);os.chmod(root,0o700)
 with sync_playwright() as pw:
  ctx=pw.chromium.launch_persistent_context(str(root/'browser'),headless=False,accept_downloads=False)
  try:
   if not login:ctx.route('**/*',lambda route:route.abort() if route.request.is_navigation_request() and not route.request.url.startswith('https://www.naukri.com/') else route.continue_())
   page=ctx.new_page()
   if not p['enabled']:return
   now=datetime.now(ZoneInfo(p['timezone']))
   if now.strftime('%H:%M')<p['time']:return
   page.goto('https://www.naukri.com/mnjuser/profile');blocked(page)
   # Daily refresh is claimed first: failure/uncertainty requires manual investigation.
   key='refresh:'+now.date().isoformat()
   with server.db() as c:refresh=naukri.claim(c,user,key)
   if refresh:
    upload=page.locator('input[type=file][accept*="pdf"], input#attachCV')
    if upload.count()!=1:raise RuntimeError('Resume upload selector changed; inspect the profile manually.')
    upload.set_input_files({'name':'resume.pdf','mimeType':'application/pdf','buffer':row['resume']})
    page.get_by_text(re.compile(r'resume.*uploaded successfully',re.I)).wait_for(timeout=15000)
    with server.db() as c:c.execute('UPDATE nk_jobs SET status=?,detail=? WHERE user=? AND id=?',('refreshed','Naukri displayed upload confirmation.',user,key))
   if not p['auto_apply']:return
   with server.db() as c:remaining=naukri.allowance(c,user,cap)
   if not remaining:naukri.finish(server.db,user,p,'Daily attempt cap reached');return
   from urllib.parse import quote
   page.goto('https://www.naukri.com/'+quote(p['search'].strip().replace(' ','-'),safe='-')+'-jobs');blocked(page)
   # Walk search pagination; canonical IDs deduplicate repeat listings and tracking URLs.
   seen=set()
   while True:
    links=page.locator('a[href*="/job-listings-"]').evaluate_all('(els)=>els.map(e=>e.href)')
    for url in dict.fromkeys(links):
     jid=naukri.job_id(url)
     if not jid or jid in seen:continue
     seen.add(jid)
     with server.db() as c:
      if c.execute('SELECT 1 FROM nk_jobs WHERE user=? AND id=?',(user,jid)).fetchone():continue
     job=ctx.new_page()
     try:
      job.goto(url);blocked(job)
      text=job.locator('body').inner_text()
      if not naukri.matches(text,p['skills']):continue
      if job.get_by_role('button',name=re.compile('company site|external',re.I)).count():continue
      apply=job.get_by_role('button',name='Apply',exact=True)
      if apply.count()!=1 or not p['auto_apply']:continue
      with server.db() as c:
       c.execute('BEGIN IMMEDIATE')
       if not naukri.allowance(c,user,cap):
        reached=True
       else:reached=False
       if not reached and not naukri.claim(c,user,jid):continue
      if reached:naukri.finish(server.db,user,p,'Daily attempt cap reached');return
      # Block every off-domain navigation, including popup destinations.
      job.route('**/*',lambda route:route.abort() if route.request.is_navigation_request() and not route.request.url.startswith('https://www.naukri.com/') else route.continue_())
      apply.click();job.wait_for_timeout(1500);blocked(job)
      # Unknown form layouts stop, rather than guessing consent or answers.
      fields=job.locator('input:visible:not([type=hidden]), textarea:visible, select:visible')
      for field in fields.all():
       typ=field.get_attribute('type')
       if typ in ('checkbox','radio','file'):raise RuntimeError('Screening choices require manual review.')
       q=field.get_attribute('aria-label')
       if not q:raise RuntimeError('Unrecognized question label; manual review required.')
       value=answer(q,p,user)
       if not value:raise RuntimeError('Missing supported answer: '+q)
       if field.evaluate('(e)=>e.tagName')=='SELECT':field.select_option(label=value)
       else:field.fill(value)
      submit=job.get_by_role('button',name='Submit',exact=True)
      if submit.count()==1:submit.click()
      job.get_by_text(re.compile(r'(successfully applied|application.*submitted)',re.I)).wait_for(timeout=15000)
      with server.db() as c:c.execute('UPDATE nk_jobs SET status=?,detail=? WHERE user=? AND id=?',('applied','Naukri displayed application confirmation.',user,jid))
     except Exception:
      with server.db() as c:c.execute('UPDATE nk_jobs SET status=?,detail=? WHERE user=? AND id=?',('needs_review','Inspect Naukri application history before retrying.',user,jid))
      raise
     finally:job.close()
     with server.db() as c:remaining=naukri.allowance(c,user,cap)
     if not remaining:naukri.finish(server.db,user,p,'Daily attempt cap reached');return
     time.sleep(3)
    nxt=page.get_by_role('link',name='Next',exact=True)
    if nxt.count()!=1:break
    before=page.url;nxt.click();page.wait_for_load_state();blocked(page)
    if page.url==before:break
   naukri.finish(server.db,user,p,'Search completed; no more matching jobs found')
  except DailyLimit:
   naukri.finish(server.db,user,p,'Naukri reported its application limit')
  except Exception:
   if not login:naukri.finish(server.db,user,p,'Stopped for review; daily goal may be incomplete')
   raise
  finally:ctx.close()

def main():
 import observability,threading,signal
 parser=argparse.ArgumentParser();parser.add_argument('--user',required=True);parser.add_argument('--login',action='store_true');parser.add_argument('--watch',action='store_true');a=parser.parse_args()
 if not re.fullmatch('[a-f0-9]{32}',a.user):parser.error('Use the workspace user ID shown in Forma.')
 server.init();stopping=threading.Event();identifier='naukri:'+a.user+':'+str(os.getpid())
 def terminate(*args):raise KeyboardInterrupt()
 signal.signal(signal.SIGTERM,terminate)
 def keepalive():
  while not stopping.is_set():
   try:
    with server.db() as c:observability.heartbeat(c,identifier,'naukri_worker',a.user)
   except Exception:pass
   stopping.wait(20)
 thread=threading.Thread(target=keepalive,daemon=True);thread.start()
 try:
  while True:
   try:run(a.user,a.login)
   except Exception as exc:
    ref=observability.report(server.db,exc,service='naukri_worker',agent='naukri',operation='run',worker=identifier)
    print('Stopped. Admin error reference:',ref);break
   if not a.watch or a.login:break
   stopping.wait(30)
 except KeyboardInterrupt:pass
 finally:
  stopping.set();thread.join(timeout=2)
  with server.db() as c:observability.heartbeat(c,identifier,'naukri_worker',a.user,'stopped')

if __name__=='__main__':main()
