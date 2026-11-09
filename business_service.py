"""Authenticated business-agent and incident-control API boundary."""
import json,time
from urllib.parse import urlsplit,parse_qs,quote
import business_store as s,business_runtime as runtime,business_connectors as connectors,business_actions as actions,observability

PREFIXES=('/api/projects','/api/business-agents','/api/connections','/api/orchestration','/api/business-workers','/api/admin/errors','/api/admin/business')

def get(connect,user,path,session=''):
 parsed=urlsplit(path);parts=parsed.path.strip('/').split('/');q={k:v[0] for k,v in parse_qs(parsed.query).items()};uid=user['id']
 with connect() as c:
  if parts[1]=='projects':
   if len(parts)==2 and q.get('handoff') in s.EXISTING:
    target=q['handoff'];items=[]
    for row in c.execute("SELECT a.id,a.project,a.created,a.data,p.name FROM biz_artifacts a JOIN biz_projects p ON p.id=a.project WHERE a.user=? AND a.kind='adapter_result' ORDER BY a.created DESC LIMIT 100",(uid,)):
     data=s.decode(row['data'])
     if data.get('agent')==target:items.append({'id':row['id'],'project':row['project'],'name':row['name'],'created':row['created'],'data':data})
    return {'handoffs':items}
   if len(parts)==2:return {'projects':[{'id':r['id'],'name':r['name'],'revision':r['revision'],'paused':bool(r['paused']),'agents':s.decode(r['config'])['agents']} for r in c.execute('SELECT * FROM biz_projects WHERE user=? ORDER BY created DESC',(uid,))]}
   if len(parts)==3:return s.snapshot(c,uid,parts[2])
   if len(parts)==5 and parts[3]=='evidence':
    s.project(c,uid,parts[2]);r=c.execute('SELECT * FROM biz_evidence WHERE id=? AND user=? AND project=?',(parts[4],uid,parts[2])).fetchone()
    if not r:raise s.BusinessError('Evidence not found or expired.')
    import settings
    return {'_binary':settings.cipher().decrypt(r['data']),'type':r['kind']}
   if len(parts)==5 and parts[3]=='export':
    a=runtime.reference(c,uid,parts[2],parts[4]);return {'_binary':json.dumps(a['data'],indent=2).encode(),'type':'application/json'}
  if parts[1]=='business-agents':
   aid=parts[2] if len(parts)>2 else ''
   if aid not in s.AGENTS:raise s.BusinessError('Agent not found.')
   import agent_platform
   agent_platform.visible(c,aid,user)
   return {'agent':aid,'name':s.AGENTS[aid][0],'operations':sorted(runtime.OPERATIONS[aid]),'projects':[{'id':r['id'],'name':r['name'],'paused':bool(r['paused'])} for r in c.execute('SELECT * FROM biz_projects WHERE user=?',(uid,)) if aid in s.decode(r['config'])['agents']],'workers':[dict(r) for r in c.execute('SELECT id,browser,seen,enabled FROM biz_workers WHERE user=?',(uid,))]}
  if parts[1]=='connections':
   if len(parts)==2:return {'connections':connectors.public(c,uid)}
   if len(parts)==4 and parts[3]=='picker':
    cfg=connectors.admin(c);connectors.get(c,uid,parts[2],'google')
    if not cfg.get('google_picker_key') or not cfg.get('google_project_number'):raise s.BusinessError('Ask the administrator to configure Google Picker for explicit file selection.')
    return {'access_token':connectors.google_token(connect,uid,parts[2]),'developer_key':cfg['google_picker_key'],'app_id':cfg['google_project_number']}
   if len(parts)==4 and parts[3]=='files':return connectors.google_files(connect,uid,parts[2])
   if len(parts)==4 and parts[2:] == ['google','callback']:
    pass
   else:raise s.BusinessError('Connection route not found.')
  elif parts[1]=='business-workers':return {'workers':[dict(r) for r in c.execute('SELECT id,browser,seen,enabled FROM biz_workers WHERE user=?',(uid,))]}
  elif parts[1]=='orchestration':return s.snapshot(c,uid,q.get('project',''))
  elif parts[1]=='admin':
   if not user['is_admin']:raise PermissionError('Administrator access required.')
   if parts[2]=='business':return connectors.admin_public(c)
   if len(parts)>3:return observability.detail(c,parts[3])
   return observability.incidents(c,q)
  else:raise s.BusinessError('Business endpoint not found.')
 if parts[1:]==['connections','google','callback']:
  connectors.google_callback(connect,uid,session,parse_qs(parsed.query));return {'_redirect':'/#projects'}
 raise s.BusinessError('Business endpoint not found.')

def post(connect,user,path,d,session=''):
 uid=user['id'];parts=urlsplit(path).path.strip('/').split('/')
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  if parts[1]=='projects':
   if len(parts)==2:return s.save_project(c,uid,d)
   pid=parts[2];p=s.project(c,uid,pid)
   if len(parts)==4 and parts[3]=='pause':
    if type(d.get('paused')) is not bool:raise s.BusinessError('Use a boolean pause setting.')
    c.execute('UPDATE biz_projects SET paused=? WHERE id=? AND user=?',(int(d['paused']),pid,uid));s.event(c,uid,pid,'','project_pause_changed',{'paused':d['paused']});return {'saved':True}
   raise s.BusinessError('Project route not found.')
  if parts[1]=='connections':
   if parts[2:]==['google','start']:return connectors.google_start(c,uid,session)
   if len(parts)==2:return connectors.save(c,uid,d)
   if len(parts)==4 and parts[3]=='select':
    cfg=connectors.get(c,uid,parts[2],'google');files=d.get('files')
    if not isinstance(files,list) or len(files)>50 or any(not isinstance(x,str) or not __import__('re').fullmatch('[A-Za-z0-9_-]{1,150}',x) for x in files):raise s.BusinessError('Select at most fifty file IDs.')
    # Confirm each selected file is actually accessible under drive.file before storing it.
    c.commit()
    token=connectors.google_token(connect,uid,parts[2])
    for fid in files:connectors.http('https://www.googleapis.com/drive/v3/files/'+fid+'?fields=id',headers={'Authorization':'Bearer '+token})
    cfg=connectors.get(c,uid,parts[2],'google');cfg['selected_files']=files;c.execute('UPDATE biz_connections SET private=? WHERE id=? AND user=?',(s.encode(cfg),parts[2],uid));return {'saved':True}
   raise s.BusinessError('Connection route not found.')
  if parts[1]=='business-workers':
   if d.get('action')=='register':return runtime.register(c,uid,d.get('browser') is True)
   if d.get('action')=='disable':
    if not c.execute('UPDATE biz_workers SET enabled=0 WHERE id=? AND user=?',(d.get('id'),uid)).rowcount:raise s.BusinessError('Worker not found.')
    observability.heartbeat(c,'business:'+d['id'],'business_worker',uid,'stopped');return {'saved':True}
   raise s.BusinessError('Unknown worker action.')
  if parts[1]=='orchestration' or parts[1]=='business-agents':
   if d.get('action')=='prepare_action':return actions.build(c,uid,d)
   if d.get('action')=='approve':return actions.approve(c,uid,d)
   if d.get('action')=='retry':
    t=c.execute('SELECT * FROM biz_tasks WHERE id=? AND user=?',(d.get('task'),uid)).fetchone()
    if not t or t['state'] not in ('held','failed','blocked'):raise s.BusinessError('Stopped task not found.')
    if d.get('confirmed') is not True:raise s.BusinessError('Confirm you inspected remote outcomes before retrying.')
    return {'task':s.enqueue(c,uid,t['project'],t['agent'],t['operation'],s.decode(t['input']))}
   aid=parts[2] if parts[1]=='business-agents' and len(parts)>2 else d.get('agent');op=d.get('operation','run');data=d.get('input',{})
   if op not in runtime.OPERATIONS.get(aid,set()) or not isinstance(data,dict):raise s.BusinessError('Unsupported agent operation or input.')
   if op=='record_quote':raise s.BusinessError('This operation is reserved for verified internal handoffs.')
   if op=='accept' and data.get('confirmed') is not True:raise s.BusinessError('Confirm quotation acceptance explicitly.')
   if len(json.dumps(data))>16000:raise s.BusinessError('Task input is too large.')
   return {'task':s.enqueue(c,uid,d.get('project',''),aid,op,data)}
  if parts[1]=='admin':
   if not user['is_admin']:raise PermissionError('Administrator access required.')
   if parts[2]=='business':return connectors.admin_save(c,d)
   if parts[2]=='errors':
    if len(parts)==4 and parts[3]=='settings':return observability.save_config(c,d)
    if len(parts)==4:return observability.set_state(c,parts[3],d.get('state'))
  raise s.BusinessError('Business endpoint not found.')

def worker_post(connect,wid,token,d):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');worker=runtime.authenticate(c,wid,token);action=d.get('action')
  if action=='heartbeat':return runtime.heartbeat(c,wid)
  if action=='stop':observability.heartbeat(c,'business:'+wid,'business_worker',worker['user'],'stopped');return {'stopped':True}
  if action=='claim':
   # Remote workers receive browser tasks only; business providers stay on the control host.
   if not worker['browser']:return {'task':None}
   task=runtime.claim(c,worker,browser_only=True)
   if not task:return {'task':None}
   cfg=task['config'];values=cfg.get('test_values',{}).copy()
   if cfg['connections'].get('browser'):values.update(connectors.get(c,worker['user'],cfg['connections']['browser'],'browser')['values'])
   return {'task':{k:task[k] for k in ('id','lease','agent','project')}|{'config':{k:cfg[k] for k in ('sites','journeys','allow_local')},'values':values}}
  if action=='complete':return runtime.complete(c,worker,d.get('task'),d.get('lease'),d.get('result'))
  if action=='fail':
   task,p=runtime.guard(c,worker,d.get('task'),d.get('lease'));reference={'id':task['id'],'lease':task['lease']}
  else:raise s.BusinessError('Unsupported worker action.')
 runtime.fail(connect,worker,reference['id'],reference['lease'],RuntimeError('Remote browser worker failure'));return {'recorded':True}
