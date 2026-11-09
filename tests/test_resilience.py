"""Adversarial input, concurrency and recovery regressions; no live providers."""
import concurrent.futures,gc,json,sqlite3,threading,time,unittest
from unittest.mock import patch
import test_business as fixtures
import business_store as s,business_runtime as rt,business_actions as actions,business_connectors as connectors

class Resilience(unittest.TestCase):
 setUp=fixtures.Business.setUp
 tearDown=fixtures.Business.tearDown
 connect=fixtures.Business.connect
 task=fixtures.Business.task
 snapshot=fixtures.Business.snapshot
 def test_malformed_project_configuration_is_validation_not_crash(self):
  cases=[{'agents':[{}]},{'currency':[]},{'sites':['https://example.com:notaport']},{'checklist':[{'title':None}]},{'customer':{'name':{}}},{'connections':{'email':[]}},{'journeys':[{'id':{},'site':'https://example.com','authorized':True}]},{'rules':[{'kind':{},'destination':'x','template':'qa_v1'}]},{'checklist':[{'title':'Do work','done':'false'}]}]
  for config in cases:
   with self.subTest(config=config),self.assertRaises(s.BusinessError):s.validate_config(config)
 def test_non_browser_work_is_not_starved_by_first_hundred_browser_tasks(self):
  with self.connect() as c:
   for i in range(105):s.enqueue(c,self.user,self.project['id'],'software-qa')
   wanted=s.enqueue(c,self.user,self.project['id'],'quotation');w=rt.register(c,self.user,False);worker=rt.authenticate(c,w['id'],w['token']);task=rt.claim(c,worker)
   self.assertIsNotNone(task);self.assertEqual(task['id'],wanted)
 def test_two_task_limit_is_atomic_across_thirty_two_claimants(self):
  with self.connect() as c:
   for _ in range(60):s.enqueue(c,self.user,self.project['id'],'quotation')
   workers=[]
   for _ in range(32):
    w=rt.register(c,self.user,False);workers.append(rt.authenticate(c,w['id'],w['token']))
  def claim(worker):
   with self.connect() as c:c.execute('BEGIN IMMEDIATE');return rt.claim(c,worker)
  with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:claimed=list(pool.map(claim,workers))
  tasks=[t for t in claimed if t];self.assertEqual(len(tasks),2);self.assertEqual(len({t['id'] for t in tasks}),2)
 def test_one_browser_limit_is_atomic(self):
  with self.connect() as c:
   for _ in range(24):s.enqueue(c,self.user,self.project['id'],'software-qa')
   workers=[]
   for _ in range(16):
    w=rt.register(c,self.user,True);workers.append(rt.authenticate(c,w['id'],w['token']))
  def claim(worker):
   with self.connect() as c:c.execute('BEGIN IMMEDIATE');return rt.claim(c,worker)
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:claimed=list(pool.map(claim,workers))
  self.assertEqual(len([t for t in claimed if t]),1)
 def test_same_dedupe_key_enqueues_once_under_concurrency(self):
  def enqueue(_):
   with self.connect() as c:c.execute('BEGIN IMMEDIATE');return s.enqueue(c,self.user,self.project['id'],'quotation',dedupe='same-request')
  with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:ids=list(pool.map(enqueue,range(100)))
  self.assertEqual(len(set(ids)),1)
  with self.connect() as c:self.assertEqual(c.execute('SELECT count(*) FROM biz_workflows').fetchone()[0],1)
 def test_result_replay_and_worker_revocation_cannot_commit(self):
  self.task('quotation')
  with self.connect() as c:c.execute('BEGIN IMMEDIATE');task=rt.claim(c,self.worker)
  result={'kind':'quotation','data':rt.quote(self.project['config'],{})}
  with self.connect() as c:rt.complete(c,self.worker,task['id'],task['lease'],result)
  with self.connect() as c:
   with self.assertRaises(s.BusinessError):rt.complete(c,self.worker,task['id'],task['lease'],result)
   self.assertEqual(c.execute('SELECT count(*) FROM biz_artifacts').fetchone()[0],1)
  self.task('quotation')
  with self.connect() as c:
   task=rt.claim(c,self.worker);c.execute('UPDATE biz_workers SET enabled=0 WHERE id=?',(self.worker['id'],))
   with self.assertRaises(s.BusinessError):rt.complete(c,self.worker,task['id'],task['lease'],result)
 def test_queued_actions_make_progress_with_task_backlog(self):
  with self.connect() as c:
   connection=connectors.save(c,self.user,{'kind':'email','name':'Mail','config':{'smtp_host':'smtp.example.com','smtp_port':465,'smtp_security':'tls','smtp_username':'owner@example.com','smtp_from':'owner@example.com','smtp_password':'fixture-secret','imap_host':'imap.example.com','imap_port':993}})
   p=s.project(c,self.user,self.project['id']);cfg=p['config'];cfg['connections']['email']=connection['id'];s.save_project(c,self.user,{'id':p['id'],'revision':p['revision'],'name':p['name'],'config':cfg})
   artifact=s.artifact(c,self.user,p['id'],'','quotation',rt.quote(cfg,{}));action=actions.build(c,self.user,{'artifact':artifact,'kind':'email','destination':'client@example.com'});actions.approve(c,self.user,action)
   for _ in range(20):s.enqueue(c,self.user,p['id'],'quotation')
  with patch('business_connectors.execute_action',return_value={'status':'sent'}) as send:
   rt.tick(self.connect,self.worker);self.assertEqual(send.call_count,1)
  self.assertTrue(any(t['state']=='completed' for t in self.snapshot()['tasks']))
 def test_same_external_action_is_sent_once_with_parallel_dispatchers(self):
  with self.connect() as c:
   conn=connectors.save(c,self.user,{'kind':'github','name':'GitHub','config':{'token':'fixture-token','repositories':['owner/repo'],'issues_enabled':True}})
   p=s.project(c,self.user,self.project['id']);p['config']['connections']['github']=conn['id'];c.execute('UPDATE biz_projects SET config=? WHERE id=?',(s.encode(p['config']),p['id']));aid=s.artifact(c,self.user,p['id'],'','qa_report',{'agent':'software-qa','state':'failed','journeys':[]});a=actions.build(c,self.user,{'artifact':aid,'kind':'github_issue','destination':'owner/repo'});actions.approve(c,self.user,a)
  with patch('business_connectors.execute_action',return_value={'number':1}) as send:
   with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:list(pool.map(lambda _:actions.dispatch(self.connect,self.user),range(36)))
   self.assertEqual(send.call_count,1)
