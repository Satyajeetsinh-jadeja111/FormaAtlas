"""Local business worker, or scoped remote browser worker. Never enrolls implicitly."""
import argparse,json,os,signal,sys,time,urllib.request,threading
from pathlib import Path
import server,business_runtime as rt,business_store as s,observability

def remote(config,d):
 req=urllib.request.Request(config['url'].rstrip('/')+'/api/business-workers/dispatch',json.dumps(d).encode(),{'Content-Type':'application/json','X-Forma-Worker':config['id'],'Authorization':'Bearer '+config['token']})
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args):return None
 with urllib.request.build_opener(NoRedirect()).open(req,timeout=45) as r:return json.load(r)

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--config',default='.forma/business-worker.json');parser.add_argument('--enroll-local',action='store_true');parser.add_argument('--user');parser.add_argument('--browser',action='store_true');parser.add_argument('--once',action='store_true');parser.add_argument('--remote-browser',action='store_true');a=parser.parse_args();path=Path(a.config)
 if a.enroll_local:
  if not a.user:parser.error('Specify the existing account email or ID with --user.')
  if path.exists():parser.error('Worker config already exists; choose a new --config path.')
  server.init()
  with server.db() as c:
   row=c.execute('SELECT id FROM users WHERE id=? OR email=?',(a.user,a.user.lower())).fetchone()
   if not row:parser.error('Existing account not found.')
   cfg=rt.register(c,row['id'],a.browser)|{'user':row['id'],'url':os.environ.get('FORMA_PUBLIC_ORIGIN','http://localhost:8000')}
  path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:json.dump(cfg,f)
  print('Worker enrolled. Run again without --enroll-local. No tasks executed.');return
 if not path.is_file() or path.is_symlink() or path.stat().st_mode&0o077:parser.error('Provide a private 0600 worker configuration file.')
 cfg=json.loads(path.read_text());stopping=False
 def stop(*args):
  nonlocal stopping
  stopping=True
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 if not a.remote_browser:
  server.init()
  with server.db() as c:worker=rt.authenticate(c,cfg['id'],cfg['token'])
 else:
  from urllib.parse import urlsplit
  u=urlsplit(cfg['url'])
  if u.username or u.password or not (u.scheme=='https' or u.scheme=='http' and u.hostname in ('localhost','127.0.0.1')):parser.error('Worker URL requires HTTPS or localhost.')
 heartbeat_stop=threading.Event()
 def keepalive():
  while not heartbeat_stop.wait(20):
   try:
    if a.remote_browser:remote(cfg,{'action':'heartbeat'})
    else:
     with server.db() as c:
      if c.execute('SELECT 1 FROM biz_workers WHERE id=? AND enabled=1',(worker['id'],)).fetchone():rt.heartbeat(c,worker['id'])
   except Exception:pass
 thread=threading.Thread(target=keepalive,daemon=True);thread.start()
 try:
  while not stopping:
   if a.remote_browser:
    remote(cfg,{'action':'heartbeat'});task=remote(cfg,{'action':'claim'}).get('task')
    if task:
     try:
      from business_browser import run
      result=run(task['config'],task['values']);remote(cfg,{'action':'complete','task':task['id'],'lease':task['lease'],'result':{'kind':'qa_report' if task['agent']=='software-qa' else 'watch_report','data':{'agent':task['agent']}|result}})
     except Exception:remote(cfg,{'action':'fail','task':task['id'],'lease':task['lease']})
   else:
    try:
     with server.db() as c:c.execute('BEGIN IMMEDIATE');rt.schedule(c)
     rt.tick(server.db,worker)
    except Exception as exc:observability.report(server.db,exc,service='business_worker',operation='worker_loop',worker=worker['id'])
   if a.once:break
   for _ in range(20):
    if stopping:break
    time.sleep(.25)
 finally:
  heartbeat_stop.set();thread.join(timeout=2)
  if a.remote_browser:
   try:remote(cfg,{'action':'stop'})
   except Exception:pass
  else:
   with server.db() as c:observability.heartbeat(c,'business:'+worker['id'],'business_worker',worker['user'],'stopped')

if __name__=='__main__':main()
