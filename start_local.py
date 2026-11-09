"""Start a detached loopback preview that survives a terminal session ending."""
import os,subprocess,sys,urllib.request,time
from pathlib import Path
root=Path(__file__).resolve().parent
os.chdir(root)
def ensure_monitor():
 pidfile=root/'.forma'/'monitoring.pid'
 try:
  pid=int(pidfile.read_text());cmd=Path('/proc')/str(pid)/'cmdline'
  if cmd.is_file() and str(root/'monitoring_worker.py').encode() in cmd.read_bytes():return
 except (OSError,ValueError):pass
 log=root/'.forma'/'monitoring.log';log.parent.mkdir(mode=0o700,exist_ok=True)
 with log.open('ab') as output:
  process=subprocess.Popen([sys.executable,str(root/'monitoring_worker.py')],cwd=root,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
 pidfile.write_text(str(process.pid));pidfile.chmod(0o600)

try:
 with urllib.request.urlopen('http://localhost:8000/api/session',timeout=2) as r:
  if r.status==200:ensure_monitor();print('Forma is already available at http://localhost:8000');raise SystemExit(0)
except (OSError,urllib.error.URLError):pass
log=root/'.forma'/'server.log';log.parent.mkdir(mode=0o700,exist_ok=True)
with log.open('ab') as out:
 process=subprocess.Popen([sys.executable,str(root/'server.py')],cwd=root,stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
for _ in range(20):
 time.sleep(.1)
 if process.poll() is not None:raise SystemExit('Forma could not start. Check .forma/server.log.')
 try:
  with urllib.request.urlopen('http://localhost:8000/api/session',timeout=1) as r:
   if r.status==200:ensure_monitor();print('Forma is running at http://localhost:8000');raise SystemExit(0)
 except (OSError,urllib.error.URLError):pass
raise SystemExit('Forma did not become available. Check .forma/server.log.')
