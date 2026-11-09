"""Launch the account owner's local browser login without handling credentials."""
import os,re,subprocess,threading,time
from pathlib import Path
from naukri import NaukriError
ROOT=Path(__file__).resolve().parent
_lock=threading.Lock()
_children={}

def init(c):
 c.execute('CREATE TABLE IF NOT EXISTS nk_login(user TEXT PRIMARY KEY,state TEXT,updated INTEGER)')

def mark(c,user,state):
 c.execute('INSERT INTO nk_login VALUES(?,?,?) ON CONFLICT(user) DO UPDATE SET state=excluded.state,updated=excluded.updated',(user,state,int(time.time())))

def status(c,user):
 r=c.execute('SELECT state,updated FROM nk_login WHERE user=?',(user,)).fetchone()
 if not r:return {'state':'not_connected','detail':'Sign in to Naukri to save a browser session for this workspace.'}
 state=r['state']
 process=_children.get(user)
 if state=='opening' and ((process and process.poll() is not None) or time.time()-r['updated']>360):
  state='needs_attention';mark(c,user,state)
 return {'state':state,'detail':{'opening':'A Naukri login window is open on this computer. Finish signing in there.','session_saved':'Browser session saved. Naukri may ask you to sign in again when it expires.','needs_attention':'Login was closed, timed out or could not open. Try again; stop any running worker first.'}.get(state,'Sign in to Naukri.'),'updated':r['updated']}

def start(c,user,db_path):
 if not re.fullmatch('[a-f0-9]{32}',user):raise NaukriError('Invalid workspace account.')
 python=ROOT/'.venv-naukri/bin/python'
 if not python.is_file():raise NaukriError('Install the Naukri browser worker first; see NAUKRI_SETUP.md.')
 with _lock:
  old=_children.get(user)
  if old and old.poll() is None:return status(c,user)
  mark(c,user,'opening')
  env=os.environ.copy();env['FORMA_DB']=str(Path(db_path).resolve())
  try:_children[user]=subprocess.Popen([str(python),str(ROOT/'naukri_worker.py'),'--user',user,'--login'],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
  except OSError:mark(c,user,'needs_attention');raise NaukriError('Could not open the Naukri browser. Check local browser setup.') from None
 return {'state':'opening','detail':'A Naukri login window is opening on this computer. Enter your credentials on Naukri.'}

def login_browser(user):
 from playwright.sync_api import sync_playwright
 import server
 os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(ROOT/'.forma/naukri-browsers'))
 root=ROOT/'.forma/naukri'/user;root.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(root,0o700)
 with server.db() as c:mark(c,user,'opening')
 try:
  with sync_playwright() as pw:
   ctx=pw.chromium.launch_persistent_context(str(root/'browser'),headless=False,accept_downloads=False)
   try:
    page=ctx.new_page();page.goto('https://www.naukri.com/nlogin/login')
    page.wait_for_url(re.compile(r'^https://www\.naukri\.com/mnjuser/'),timeout=300000)
    # Close the browser first so persisted cookies are flushed before reporting success.
   finally:ctx.close()
  with server.db() as c:mark(c,user,'session_saved')
 except Exception:
  with server.db() as c:mark(c,user,'needs_attention')
  raise RuntimeError('Naukri sign-in needs attention. Try again from Forma.') from None
