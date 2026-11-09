"""Consistent, private complete state snapshots; never changes running schedules."""
import argparse,json,os,sqlite3,stat,tarfile,tempfile,shutil
from pathlib import Path
import server,settings

def _pack(destination,exclude=()):
 destination=Path(destination)
 if destination.exists():raise ValueError('Backup destination already exists.')
 with tempfile.TemporaryDirectory() as folder:
  root=Path(folder);source=sqlite3.connect(server.DB);target=sqlite3.connect(root/'database.sqlite3')
  try:source.backup(target)
  finally:source.close();target.close()
  if not settings.KEY_PATH.is_file() or settings.KEY_PATH.is_symlink():raise ValueError('Encryption key unavailable.')
  (root/'server.key').write_bytes(settings.KEY_PATH.read_bytes())
  (root/'manifest.json').write_text(json.dumps({'format':'forma-state-v1','database':'database.sqlite3','key':'server.key','notice':'Stop writers during host migration. This archive includes credentials encrypted with the included key; protect the entire archive.'}))
  fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as out:
   with tarfile.open(fileobj=out,mode='w:gz') as archive:
    for p in root.iterdir():archive.add(p,arcname=p.name)
    # Preserve retained media, browser profiles, local worker enrollments and sanitized spools.
    state=Path('.forma')
    if state.exists():
     for p in state.rglob('*'):
      if p.is_symlink():continue
      if p.is_file() and p.resolve()!=destination.resolve() and p.resolve() not in exclude and 'backups' not in p.parts and 'naukri-browsers' not in p.parts and p.suffix not in ('.sqlite3','.log') and not p.name.endswith(('.sqlite3-wal','.sqlite3-shm')):archive.add(p,arcname='state/'+str(p.relative_to(state)))
 return str(destination)

def _unpack(archive,destination):
 destination=Path(destination)
 if destination.exists() and any(destination.iterdir()):raise ValueError('Restore destination must be empty.')
 with tarfile.open(archive,'r:gz') as tar:
  members=tar.getmembers()
  if len(members)>100000 or sum(m.size for m in members)>20*1024**3:raise ValueError('Backup exceeds restore limits.')
  names=[m.name for m in members]
  if len(names)!=len(set(names)):raise ValueError('Duplicate backup members.')
  for m in members:
   p=Path(m.name)
   if p.is_absolute() or '..' in p.parts or '\\' in m.name or not (m.isfile() or m.isdir()) or m.name not in ('database.sqlite3','server.key','manifest.json') and not m.name.startswith('state/'):raise ValueError('Unsafe backup path or member type.')
  manifest=tar.extractfile('manifest.json')
  if not manifest or json.load(manifest).get('format')!='forma-state-v1':raise ValueError('Unsupported backup format.')
  destination.mkdir(mode=0o700,parents=True,exist_ok=True)
  for m in members:
   target=destination/m.name
   if m.isdir():target.mkdir(mode=0o700,parents=True,exist_ok=True);continue
   target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
   with tar.extractfile(m) as source,target.open('wb') as output:shutil.copyfileobj(source,output,1024*1024)
   target.chmod(0o600)
 # Restoring does not initialize the server, execute workers or alter schedules.
 return str(destination)


def _key(path,create=False):
 path=Path(path)
 if create and not path.exists():
  path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as out:out.write(os.urandom(32))
 if not path.is_file() or path.is_symlink() or path.stat().st_mode&0o077:raise ValueError('Provide a private 0600 backup encryption key file.')
 key=path.read_bytes()
 if len(key)!=32:raise ValueError('Invalid backup encryption key.')
 return key

def backup(destination,key_file='.forma/backup-encryption.key'):
 from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
 destination=Path(destination)
 if destination.exists():raise ValueError('Backup destination already exists.')
 key=_key(key_file,True)
 with tempfile.TemporaryDirectory() as folder:
  plain=Path(folder)/'state.tar.gz';_pack(plain,exclude=(Path(key_file).resolve(),));iv=os.urandom(12);encryptor=Cipher(algorithms.AES(key),modes.GCM(iv)).encryptor()
  fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'wb') as out,plain.open('rb') as source:
   out.write(b'FST1'+iv)
   while True:
    chunk=source.read(1024*1024)
    if not chunk:break
    out.write(encryptor.update(chunk))
   out.write(encryptor.finalize());out.write(encryptor.tag)
 return str(destination)

def restore(archive,destination,key_file='.forma/backup-encryption.key'):
 from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
 from cryptography.exceptions import InvalidTag
 key=_key(key_file)
 with tempfile.TemporaryDirectory() as folder:
  plain=Path(folder)/'state.tar.gz'
  with open(archive,'rb') as source,plain.open('wb') as out:
   if source.read(4)!=b'FST1':raise ValueError('Expected an encrypted Forma state backup.')
   iv=source.read(12);source.seek(-16,2);tag=source.read(16);size=source.tell()-32
   if size<0:raise ValueError('Invalid backup size.')
   source.seek(16);decryptor=Cipher(algorithms.AES(key),modes.GCM(iv,tag)).decryptor()
   while size:
    chunk=source.read(min(size,1024*1024))
    if not chunk:raise ValueError('Truncated backup.')
    size-=len(chunk);out.write(decryptor.update(chunk))
   try:out.write(decryptor.finalize())
   except InvalidTag:raise ValueError('Backup authentication failed. No state was restored.') from None
  return _unpack(plain,destination)

if __name__=='__main__':
 p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--backup');g.add_argument('--restore');p.add_argument('--destination');p.add_argument('--key-file',default='.forma/backup-encryption.key');a=p.parse_args()
 if a.restore and not a.destination:p.error('--restore requires --destination')
 try:print(backup(a.backup,a.key_file) if a.backup else restore(a.restore,a.destination,a.key_file))
 except (ValueError,OSError,tarfile.TarError) as e:p.exit(1,str(e)+'\n')
