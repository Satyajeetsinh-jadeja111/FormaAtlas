"""Explicit offline Forma backup/restore; never provisions cloud resources."""
import argparse,os,sqlite3,tarfile,tempfile
from pathlib import Path

def backup(destination):
 import server,settings
 target=Path(destination)
 if target.exists():raise SystemExit('Destination already exists; refusing to overwrite.')
 with server.db() as c:
  import time,json
  if c.execute('SELECT 1 FROM yt_workers WHERE heartbeat>? AND enabled=1',(time.time()-120,)).fetchone():raise SystemExit('Stop YouTube workers and wait two minutes before an offline migration.')
  if any(json.loads(r[0]).get('enabled') for r in c.execute('SELECT data FROM yt_profiles')):raise SystemExit('Pause all YouTube automation first.')
  if c.execute('SELECT 1 FROM workspaces').fetchone():
   for r in c.execute('SELECT data FROM workspaces'):
    if json.loads(r[0]).get('profile',{}).get('daily_enabled'):raise SystemExit('Pause existing LinkedIn schedules before migrating the entire Forma database.')
  with tempfile.TemporaryDirectory() as folder:
   db=Path(folder)/'forma.sqlite3';copy=sqlite3.connect(db);c.backup(copy);copy.close()
   fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
   with os.fdopen(fd,'wb') as out,tarfile.open(fileobj=out,mode='w:gz') as archive:
    archive.add(db,arcname='forma.sqlite3');archive.add(settings.KEY_PATH,arcname='.forma/server.key')
    assets=Path('.forma/youtube/assets')
    if assets.exists():
     for p in assets.rglob('*'):
      if p.is_symlink():raise SystemExit('Symlinks are not allowed in migration media.')
      if p.is_file():archive.add(p,arcname=p.as_posix())
 print('Private migration archive created. It contains all Forma accounts, credentials and retained media; transfer it only through your trusted encrypted channel.')

def restore(source,destination):
 root=Path(destination)
 if root.exists() and any(root.iterdir()):raise SystemExit('Restore requires an empty destination; existing data will not be overwritten.')
 with tarfile.open(source,'r:gz') as archive:
  entries=archive.getmembers()
  for m in entries:
   p=Path(m.name)
   if p.is_absolute() or '..' in p.parts or not m.isfile() or not (m.name=='forma.sqlite3' or m.name=='.forma/server.key' or m.name.startswith('.forma/youtube/assets/')):raise SystemExit('Unsafe migration archive.')
  names={m.name for m in entries}
  if not {'forma.sqlite3','.forma/server.key'}<=names:raise SystemExit('Database or encryption key missing.')
  root.mkdir(parents=True,exist_ok=True,mode=0o700)
  for m in entries:
   path=root/m.name;path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
   with archive.extractfile(m) as incoming:
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as output:
     import shutil;shutil.copyfileobj(incoming,output)
 print('Restored into the empty destination. Configure HTTPS/OAuth, retire the old control service, then register the Oracle worker. Schedules remain paused.')
if __name__=='__main__':
 p=argparse.ArgumentParser();group=p.add_mutually_exclusive_group(required=True);group.add_argument('--backup');group.add_argument('--restore');p.add_argument('--destination');args=p.parse_args()
 if args.backup:backup(args.backup)
 else:
  if not args.destination:p.error('--destination is required for restore')
  restore(args.restore,args.destination)
