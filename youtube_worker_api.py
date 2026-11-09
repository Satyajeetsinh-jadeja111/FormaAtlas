"""Authenticated, fenced worker transport and bounded private asset transfer."""
import json,re,secrets,time
import youtube_store as s,youtube_runtime as rt
from youtube_store import YouTubeError
MAX_ASSET=512*1024*1024

def action(c,wid,path,data):
 if path=='ops/stop':
  import observability
  observability.heartbeat(c,'youtube:'+wid,'youtube_worker',state='stopped');return {'stopped':True}
 if path=='ops/error':
  import observability
  job=rt.guard(c,wid,data)
  return {'error_id':observability.remote_record(c,data.get('frames'),service='youtube_worker',agent='youtube',operation='execute',task=job['id'],worker=wid)}
 if path=='heartbeat':return rt.heartbeat(c,wid,data.get('capabilities',{}))
 if path=='claim':return rt.claim(c,wid,data.get('maintenance') is True)
 if path=='guard':
  import observability
  rt.guard(c,wid,data);observability.heartbeat(c,'youtube:'+wid,'youtube_worker');return {'valid':True}
 if path=='update':return rt.update(c,wid,data)
 if path=='asset/start':
  job=rt.guard(c,wid,data)
  if data.get('type')!='video' or data.get('kind') not in ('video','thumbnail','captions','segment'):raise YouTubeError('Invalid private media kind.')
  aid=secrets.token_hex(20);s.ROOT.joinpath('assets').mkdir(parents=True,exist_ok=True,mode=0o700)
  c.execute('INSERT INTO yt_assets VALUES(?,?,?,?,?,?,0)',(aid,job['id'],job['user'],data['kind'],0,int(time.time())));(s.ROOT/'assets'/aid).touch(mode=0o600)
  return {'id':aid}
 if path=='asset/read':
  import base64
  job=rt.guard(c,wid,data);r=c.execute('SELECT * FROM yt_assets WHERE id=? AND job=? AND complete=1',(data.get('asset'),job['id'])).fetchone()
  if not r or type(data.get('offset',0)) is not int or data.get('offset',0)<0:raise YouTubeError('Private asset unavailable.')
  with (s.ROOT/'assets'/r['id']).open('rb') as f:f.seek(data.get('offset',0));chunk=f.read(512*1024)
  return {'data':base64.b64encode(chunk).decode(),'size':r['size']}
 if path in ('asset/chunk','asset/finish'):
  job=rt.guard(c,wid,data);r=c.execute('SELECT * FROM yt_assets WHERE id=? AND job=? AND complete=0',(data.get('asset'),job['id'])).fetchone()
  if not r:raise YouTubeError('Private media transfer not found.')
  pathfile=s.ROOT/'assets'/r['id']
  if path=='asset/chunk':
   import base64,binascii
   try:raw=base64.b64decode(data.get('data',''),validate=True)
   except (ValueError,binascii.Error):raise YouTubeError('Invalid media chunk.') from None
   if not 0<len(raw)<=512*1024 or data.get('offset')!=r['size'] or r['size']+len(raw)>MAX_ASSET:raise YouTubeError('Invalid media offset or asset size.')
   import shutil
   if shutil.disk_usage(s.ROOT).free<len(raw)+512*1024*1024:raise YouTubeError('Control storage is low. Media transfer held to preserve workspace availability.')
   with pathfile.open('ab') as f:f.write(raw)
   c.execute('UPDATE yt_assets SET size=size+? WHERE id=?',(len(raw),r['id']));return {'offset':r['size']+len(raw)}
  if not r['size']:raise YouTubeError('Empty media asset.')
  c.execute('UPDATE yt_assets SET complete=1 WHERE id=?',(r['id'],));return {'saved':True,'id':r['id']}
 raise YouTubeError('Unknown worker action.')
