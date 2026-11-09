"""Version-bound approvals and exact-destination automation rules."""
import json,re,time
import business_store as s,business_connectors as connectors,observability
TEMPLATES={'quotation':'quote_v1','onboarding':'onboarding_v1','qa_report':'qa_v1','watch_report':'incident_v1','followup':'followup_v1'}

def build(c,user,d):
 a=s.get_artifact(c,user,d.get('artifact',''));p=s.project(c,user,a['project']);kind=d.get('kind');destination=s.text(d.get('destination',''),300);template=TEMPLATES.get(a['kind'])
 if kind not in ('email','github_issue','drive_document','drive_folder','sheet_export') or not template or not destination:raise s.BusinessError('Select an exportable artifact, supported action and exact destination.')
 if kind=='email' and not re.fullmatch(r'[^\s@<>,;:]+@[^\s@<>,;:]+\.[^\s@<>,;:]+',destination):raise s.BusinessError('Enter one recipient email address.')
 if kind=='github_issue' and not re.fullmatch('[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',destination):raise s.BusinessError('Enter a selected GitHub owner/repository.')
 if kind.startswith('drive_') or kind=='sheet_export':
  if not re.fullmatch('[A-Za-z0-9_-]{1,150}',destination):raise s.BusinessError('Enter a selected folder ID or root.')
 payload={'kind':kind,'destination':destination,'template':template,'artifact':a['id'],'artifact_revision':a['revision'],'project_revision':p['revision'],'subject':s.AGENTS.get(a['data'].get('agent'),('Forma '+a['kind'],))[0]+' · '+p['name'],'body':format_report(a)}
 dg=s.digest(payload);aid=s.uid();state='queued' if permitted(c,p,payload) else 'awaiting_approval'
 c.execute('INSERT OR IGNORE INTO biz_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(aid,user,p['id'],a['id'],kind,destination,template,dg,s.encode(payload),state,'rule' if state=='queued' else '',s.now(),s.now(),''))
 row=c.execute('SELECT id,state,digest FROM biz_actions WHERE user=? AND digest=?',(user,dg)).fetchone()
 if row['state']=='blocked':
  c.execute('UPDATE biz_actions SET state="awaiting_approval",approved="",detail="Prepared again after connection inspection." WHERE id=?',(row['id'],));row=c.execute('SELECT id,state,digest FROM biz_actions WHERE id=?',(row['id'],)).fetchone()
 s.event(c,user,p['id'],'','action_prepared',dict(row));return dict(row)

def permitted(c,p,payload):
 if p['paused'] or payload['project_revision']!=p['revision']:return False
 # A blocked or incomplete quote cannot be auto-committed.
 a=s.get_artifact(c,p['user'],payload['artifact'])
 if a['data'].get('state') in ('needs_input','blocked'):return False
 for r in p['config']['rules']:
  if r.get('enabled') and all(r.get(k)==payload[k] for k in ('kind','destination','template')):
   count=c.execute('SELECT count(*) FROM biz_actions WHERE user=? AND project=? AND kind=? AND destination=? AND template=? AND state IN ("sending","succeeded","unknown") AND updated>?',(p['user'],p['id'],payload['kind'],payload['destination'],payload['template'],s.now()-86400)).fetchone()[0]
   if count<r.get('daily_limit',5):return True
 return False

def approve(c,user,d):
 r=c.execute('SELECT * FROM biz_actions WHERE id=? AND user=?',(d.get('id'),user)).fetchone()
 if not r or r['state']!='awaiting_approval' or d.get('digest')!=r['digest']:raise s.BusinessError('Action approval is stale or unavailable.')
 p=s.project(c,user,r['project']);payload=s.decode(r['payload'])
 if p['paused'] or p['revision']!=payload['project_revision']:raise s.BusinessError('Project changed or paused; prepare the action again.')
 c.execute('UPDATE biz_actions SET state="queued",approved=?,updated=? WHERE id=?',(r['digest'],s.now(),r['id']));s.event(c,user,p['id'],'','action_approved',{'id':r['id']});return {'saved':True}

def dispatch(connect,user=None):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  c.execute('UPDATE biz_actions SET state="unknown",detail="Execution lost; inspect provider history before retrying." WHERE state="sending" AND updated<?',(s.now()-120,))
  r=c.execute('SELECT * FROM biz_actions WHERE state="queued"'+(' AND user=?' if user else '')+' ORDER BY created LIMIT 1',(user,) if user else ()).fetchone()
  if not r:return False
  p=s.project(c,r['user'],r['project']);payload=s.decode(r['payload'])
  if p['paused'] or p['revision']!=payload['project_revision']:
   c.execute('UPDATE biz_actions SET state="awaiting_approval",detail="Project changed or paused; prepare a new action." WHERE id=?',(r['id'],));return True
  if r['approved']!=r['digest'] and not permitted(c,p,payload):
   c.execute('UPDATE biz_actions SET state="awaiting_approval",approved="",detail="Automation rule no longer permits this action." WHERE id=?',(r['id'],));return True
  try:
   connection_kind='email' if r['kind']=='email' else 'github' if r['kind']=='github_issue' else 'google'
   cfg=connectors.get(c,r['user'],p['config']['connections'].get(connection_kind,''),connection_kind)
   if connection_kind=='github' and (r['destination'] not in cfg.get('repositories',[]) or cfg.get('issues_enabled') is not True):raise s.BusinessError('GitHub issue creation is not enabled for this repository.')
   if connection_kind=='google' and r['destination']!='root' and r['destination'] not in cfg.get('selected_files',[]):raise s.BusinessError('Select this Google parent folder in the connection first.')
  except s.BusinessError as exc:
   error=observability.record(c,exc,service='business_worker',agent='external_action',operation=r['kind'],project=r['project'])
   c.execute('UPDATE biz_actions SET state="blocked",detail=?,updated=? WHERE id=?',('Connection setup required. Error '+error,s.now(),r['id']));return True
  c.execute('UPDATE biz_actions SET state="sending",updated=? WHERE id=?',(s.now(),r['id']))
 try:
  result=connectors.execute_action(connect,r['user'],p,payload);state='succeeded';detail='Confirmed by provider.'
 except Exception as exc:
  error=observability.report(connect,exc,service='business_worker',agent='external_action',operation=r['kind'],project=r['project'],impact='External action outcome uncertain. Inspect remote history before retrying.')
  result={'error':error};state='unknown';detail='Inspect provider history before retrying. Error '+error
 with connect() as c:
  c.execute('UPDATE biz_actions SET state=?,detail=?,updated=? WHERE id=?',(state,detail,s.now(),r['id']));s.event(c,r['user'],r['project'],'','external_action_'+state,{'id':r['id'],'result':result})
 return True


def format_report(artifact):
 d=artifact['data'];kind=artifact['kind'];lines=['Forma '+kind.replace('_',' ').title()+' · Revision '+str(artifact['revision'])]
 if kind=='quotation':
  lines+=['Customer: '+str(d.get('customer',{}).get('name','')),'Currency: '+str(d.get('currency','')),'']
  for row in d.get('items',[]):lines.append(f"{row['name']} | Quantity {row['quantity']} | Unit price {row['unit_price']} | Amount {row['total']}")
  lines+=['','Subtotal: '+d.get('subtotal',''),'Discount: '+d.get('discount',''),'Tax: '+d.get('tax',''),'Total: '+d.get('total','')]
 elif kind=='onboarding':
  for item in d.get('items',[]):lines.append(('Completed: ' if item.get('done') else 'Pending: ')+str(item.get('title',''))+(' | Due '+item['deadline'] if item.get('deadline') else ''))
  lines+=['Readiness: '+str(d.get('state',''))]
 elif kind in ('qa_report','watch_report'):
  lines.append('Result: '+str(d.get('state','')))
  for j in d.get('journeys',[]):
   lines.append(str(j.get('journey'))+' / '+str(j.get('viewport'))+' / '+str(j.get('state')))
   lines+=['  '+str(e) for e in j.get('errors',[])]
 elif kind=='followup':lines.append(d.get('message',''))
 if d.get('missing'):lines+=['','Requires review:']+[str(x) for x in d['missing']]
 lines+=['','Open Forma to review the complete report and evidence.']
 return '\n'.join(lines)
