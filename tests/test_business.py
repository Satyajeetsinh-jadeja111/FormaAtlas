import base64,json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import settings,agent_platform,business_store as s,business_runtime as rt,business_actions as actions,business_connectors as connectors,business_service,observability

class Business(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db';self.key=patch.object(settings,'KEY_PATH',Path(self.tmp.name)/'key');self.key.start();self.user='a'*32;self.other='b'*32
  with self.connect() as c:
   settings.init(c);agent_platform.init(c);s.init(c);observability.init(c)
   c.execute('CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT)');c.execute('CREATE TABLE workspaces(user TEXT PRIMARY KEY,data TEXT)');c.execute('INSERT INTO users VALUES(?,?)',(self.user,'owner@example.com'))
   self.project=s.save_project(c,self.user,{'name':'Agency','config':{'currency':'INR','customer':{'name':'Client','email':'client@example.com'},'catalogue':[{'id':'web','name':'Website','price':'100.10'}],'tax_rate':'18','discount_rate':'10','requirements':{'scope':'Website','timeline':'Four weeks','items':[{'id':'web','quantity':3}]},'checklist':[{'title':'Scope approved','required':True,'done':True}]}})
   reg=rt.register(c,self.user,True);self.worker=rt.authenticate(c,reg['id'],reg['token']);self.token=reg['token']
 def tearDown(self):self.key.stop();self.tmp.cleanup()
 def connect(self):
  c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row;return c
 def task(self,agent,operation='run',data=None):
  with self.connect() as c:return s.enqueue(c,self.user,self.project['id'],agent,operation,data)
 def snapshot(self):
  with self.connect() as c:return s.snapshot(c,self.user,self.project['id'])
 def test_additive_registration_preserves_existing(self):
  with self.connect() as c:
   c.execute('UPDATE managed_agents SET live=1 WHERE id="youtube"');before=[tuple(r) for r in c.execute('SELECT * FROM managed_agents WHERE id IN ("linkedin","youtube","naukri")')];s.init(c)
   self.assertEqual(before,[tuple(r) for r in c.execute('SELECT * FROM managed_agents WHERE id IN ("linkedin","youtube","naukri")')]);self.assertEqual(c.execute('SELECT count(*) FROM managed_agents').fetchone()[0],8)
 def test_project_and_artifact_ownership(self):
  with self.connect() as c:
   with self.assertRaises(s.BusinessError):s.project(c,self.other,self.project['id'])
   aid=s.artifact(c,self.user,self.project['id'],'','quotation',{'state':'ready'})
   with self.assertRaises(s.BusinessError):s.get_artifact(c,self.other,aid)
   p=s.save_project(c,self.other,{'name':'Other','config':{}})
   with self.assertRaises(s.BusinessError):rt.reference(c,self.user,p['id'],aid)
 def test_quote_decimal_and_missing_requirements(self):
  result=rt.quote(self.project['config'],{})
  self.assertEqual(result['subtotal'],'300.30');self.assertEqual(result['discount'],'30.03');self.assertEqual(result['tax'],'48.65');self.assertEqual(result['total'],'318.92')
  self.assertEqual(result['state'],'ready');self.assertEqual(rt.quote(s.validate_config({}),{})['state'],'needs_input')
  for value in ('NaN','Infinity','-1'):
   with self.assertRaises(s.BusinessError):s.number(value)
 def test_sales_quote_acceptance_onboarding_chain(self):
  self.task('sales-journey');rt.tick(self.connect,self.worker);rt.tick(self.connect,self.worker);rt.tick(self.connect,self.worker)
  p=self.snapshot();quote=next(a for a in p['artifacts'] if a['kind']=='quotation');self.assertTrue(any(a['data'].get('stage')=='quoted' for a in p['artifacts']))
  self.task('sales-journey','accept',{'quotation':quote['id'],'confirmed':True});rt.tick(self.connect,self.worker);rt.tick(self.connect,self.worker)
  p=self.snapshot();self.assertEqual(len([a for a in p['artifacts'] if a['kind']=='onboarding']),1)
  self.task('sales-journey','accept',{'quotation':quote['id'],'confirmed':True});rt.tick(self.connect,self.worker);rt.tick(self.connect,self.worker)
  self.assertEqual(len([a for a in self.snapshot()['artifacts'] if a['kind']=='onboarding']),1)
 def test_handoff_limits_and_deduplication(self):
  with self.connect() as c:
   tid=s.enqueue(c,self.user,self.project['id'],'quotation',dedupe='same');self.assertEqual(s.enqueue(c,self.user,self.project['id'],'quotation',dedupe='same'),tid)
   flow=c.execute('SELECT workflow FROM biz_tasks WHERE id=?',(tid,)).fetchone()[0]
   self.assertEqual(c.execute('SELECT count(*) FROM biz_workflows').fetchone()[0],1)
   with self.assertRaises(s.BusinessError):s.enqueue(c,self.user,self.project['id'],'quotation',workflow=flow,depth=6)
   for i in range(19):s.enqueue(c,self.user,self.project['id'],'quotation',workflow=flow)
   with self.assertRaises(s.BusinessError):s.enqueue(c,self.user,self.project['id'],'quotation',workflow=flow)
 def test_stale_lease_pause_and_worker_isolation(self):
  tid=self.task('quotation')
  with self.connect() as c:
   c.execute('BEGIN IMMEDIATE');task=rt.claim(c,self.worker)
  with self.connect() as c:
   c.execute('UPDATE biz_projects SET paused=1 WHERE id=?',(self.project['id'],))
   with self.assertRaises(s.BusinessError):rt.guard(c,self.worker,tid,task['lease'])
   c.execute('UPDATE biz_tasks SET lease_until=0 WHERE id=?',(tid,));rt.claim(c,self.worker)
   self.assertEqual(c.execute('SELECT state FROM biz_tasks WHERE id=?',(tid,)).fetchone()[0],'held')
 def test_action_approval_revision_and_ambiguous_write(self):
  self.task('quotation');rt.tick(self.connect,self.worker)
  aid=self.snapshot()['artifacts'][0]['id']
  with self.connect() as c:
   action=actions.build(c,self.user,{'artifact':aid,'kind':'email','destination':'client@example.com'});self.assertEqual(action['state'],'awaiting_approval')
   with self.assertRaises(s.BusinessError):actions.approve(c,self.other,action)
   actions.approve(c,self.user,action)
  with self.connect() as c:
   connection=connectors.save(c,self.user,{'kind':'email','name':'Mail','config':{'smtp_host':'smtp.example.com','smtp_port':465,'smtp_security':'tls','smtp_username':'owner@example.com','smtp_from':'owner@example.com','smtp_password':'secret','imap_host':'imap.example.com','imap_port':993}})
   project=s.project(c,self.user,self.project['id']);project['config']['connections']['email']=connection['id'];c.execute('UPDATE biz_projects SET config=? WHERE id=?',(s.encode(project['config']),project['id']))
  with patch('business_connectors.execute_action',side_effect=OSError('private password contents')):
   actions.dispatch(self.connect,self.user)
  self.assertEqual(self.snapshot()['actions'][0]['state'],'unknown')
  with patch('business_connectors.execute_action') as call:actions.dispatch(self.connect,self.user);call.assert_not_called()
  with self.connect() as c:
   p=s.project(c,self.user,self.project['id']);s.save_project(c,self.user,{'id':p['id'],'revision':p['revision'],'name':p['name'],'config':p['config']})
   with self.assertRaises(s.BusinessError):actions.approve(c,self.user,action)
 def test_exact_rule_destination_and_limit(self):
  with self.connect() as c:
   connection=connectors.save(c,self.user,{'kind':'email','name':'Mail','config':{'smtp_host':'smtp.example.com','smtp_port':465,'smtp_security':'tls','smtp_username':'owner@example.com','smtp_from':'owner@example.com','smtp_password':'secret','imap_host':'imap.example.com','imap_port':993}})
   p=s.project(c,self.user,self.project['id']);p['config']['connections']['email']=connection['id'];p['config']['rules']=[{'kind':'email','destination':'client@example.com','template':'quote_v1','enabled':True,'daily_limit':1}];s.save_project(c,self.user,{'id':p['id'],'revision':p['revision'],'name':p['name'],'config':p['config']})
  self.task('quotation');rt.tick(self.connect,self.worker)
  with self.connect() as c:
   aid=self.snapshot()['artifacts'][0]['id'];other=actions.build(c,self.user,{'artifact':aid,'kind':'email','destination':'wrong@example.com'});self.assertEqual(other['state'],'awaiting_approval')
  with patch('business_connectors.execute_action',return_value={'status':'sent'}) as call:actions.dispatch(self.connect,self.user);self.assertEqual(call.call_count,1)
  self.task('quotation');rt.tick(self.connect,self.worker)
  self.assertTrue(any(a['state']=='awaiting_approval' for a in self.snapshot()['actions']))
 def test_watchdog_transition_handoff_and_recovery(self):
  result={'kind':'watch_report','data':{'agent':'revenue-watchdog','state':'failed','journeys':[{'journey':'home','state':'failed'}]}}
  with patch('business_runtime.compute',return_value=result):self.task('revenue-watchdog');rt.tick(self.connect,self.worker)
  p=self.snapshot();self.assertTrue(any(t['agent']=='software-qa' for t in p['tasks']));self.assertTrue(any(e['kind']=='watch_failed' for e in p['events']))
  # Finish the queued QA without contacting a website.
  with patch('business_runtime.compute',return_value={'kind':'qa_report','data':{'state':'failed'}}):rt.tick(self.connect,self.worker)
  with patch('business_runtime.compute',return_value={'kind':'watch_report','data':{'state':'passed','journeys':[{'journey':'home','state':'passed'}]}}):self.task('revenue-watchdog');rt.tick(self.connect,self.worker)
  self.assertTrue(any(e['kind']=='watch_passed' for e in self.snapshot()['events']))
 def test_safe_browser_validation_and_destination_checks(self):
  with self.assertRaises(s.BusinessError):s.validate_config({'sites':['https://example.com'],'journeys':[{'id':'pay','site':'https://example.com','authorized':True,'steps':[{'action':'click','label':'Pay now','safe':True}]}]})
  from business_browser import destinations
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('169.254.169.254',80))]):
   with self.assertRaises(s.BusinessError):destinations({'sites':['http://metadata.example'],'allow_local':True})
 def test_connection_secrets_and_blank_keep(self):
  with self.connect() as c:
   r=connectors.save(c,self.user,{'kind':'github','name':'Repo','config':{'token':'private-token','repositories':['owner/repo'],'issues_enabled':True}})
   connectors.save(c,self.user,{'id':r['id'],'kind':'github','name':'Repo','config':{'token':'','repositories':['owner/repo']}})
   self.assertEqual(connectors.get(c,self.user,r['id'])['token'],'private-token');self.assertNotIn('private-token',json.dumps(connectors.public(c,self.user)))
   with self.assertRaises(s.BusinessError):connectors.get(c,self.other,r['id'])
 def test_remote_browser_claim_scoped_to_owner_and_no_provider_secrets(self):
  self.task('quotation');self.task('software-qa')
  result=business_service.worker_post(self.connect,self.worker['id'],self.token,{'action':'claim'})
  self.assertEqual(result['task']['agent'],'software-qa');self.assertNotIn('customer',result['task']['config']);self.assertNotIn('connections',result['task']['config'])
  with self.assertRaises(s.BusinessError):business_service.worker_post(self.connect,self.worker['id'],'wrong',{'action':'claim'})

class Incidents(unittest.TestCase):
 setUp=Business.setUp
 tearDown=Business.tearDown
 connect=Business.connect
 def record(self):
  try:raise RuntimeError('token=SECRET email-body private-resume')
  except Exception as e:return observability.report(self.connect,e,agent='quotation',operation='quote',task='task-id')
 def test_redaction_location_correlation_and_grouping(self):
  token=observability.request_id.set('request-ref')
  try:a=self.record();b=self.record()
  finally:observability.request_id.reset(token)
  with self.connect() as c:
   result=observability.incidents(c);self.assertEqual(len(result['incidents']),1);i=result['incidents'][0];self.assertEqual(i['count'],2);self.assertEqual(i['data']['request'],'request-ref');self.assertIn('test_business.py',i['data']['location']['file']);self.assertNotIn('SECRET',json.dumps(result));self.assertEqual(len(i['alerts']),2)
   observability.set_state(c,i['id'],'resolved')
  self.record()
  with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM ops_alerts WHERE kind="first"').fetchone()[0],2)
 def test_immediate_dispatch_and_no_recursive_mail_errors(self):
  self.record()
  with self.connect() as c:observability.save_config(c,{'enabled':True,'recipient':'admin@example.com'})
  with patch('observability.mail',return_value='sent') as mail:observability.dispatch(self.connect);self.assertEqual(mail.call_args.args[1],'admin@example.com');self.assertIn('Source:',mail.call_args.args[3]);self.assertNotIn('SECRET',mail.call_args.args[3])
  self.record()
  with self.connect() as c:self.assertGreater(c.execute('SELECT due FROM ops_alerts WHERE state="pending"').fetchone()[0],time.time()+800)
 def test_known_failure_retry_and_unknown_hold(self):
  self.record()
  with self.connect() as c:observability.save_config(c,{'enabled':True,'recipient':'admin@example.com'})
  with patch('observability.mail',return_value='retry'):observability.dispatch(self.connect)
  with self.connect() as c:
   a=c.execute('SELECT * FROM ops_alerts').fetchone();self.assertEqual(a['state'],'pending');self.assertGreater(a['due'],time.time()+50);c.execute('UPDATE ops_alerts SET due=0')
  with patch('observability.mail',return_value='unknown'):observability.dispatch(self.connect)
  with self.connect() as c:self.assertEqual(c.execute('SELECT state FROM ops_alerts').fetchone()[0],'unknown');self.assertEqual(c.execute('SELECT count(*) FROM ops_errors').fetchone()[0],1)
 def test_health_planned_stop_and_lost_process(self):
  with self.connect() as c:
   observability.heartbeat(c,'planned','server',state='stopped');observability.heartbeat(c,'lost','business_worker');c.execute('UPDATE ops_health SET seen=0')
  observability.maintenance(self.connect)
  with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM ops_incidents').fetchone()[0],1)
 def test_spool_reconciliation_after_database_failure(self):
  with patch.object(observability,'LOG_ROOT',Path(self.tmp.name)/'logs'):
   with patch('observability.persist',side_effect=sqlite3.OperationalError('db failed')):error=self.record()
   self.assertTrue((Path(self.tmp.name)/'logs/spool'/ (error+'.json')).exists());observability.maintenance(self.connect)
   with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM ops_errors').fetchone()[0],1)
