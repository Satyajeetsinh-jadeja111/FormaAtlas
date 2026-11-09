"""Bounded, repeatable agent audit. Temporary DB, loopback HTTP, no live providers."""
import argparse,concurrent.futures,gc,hashlib,http.client,json,os,resource,sqlite3,sys,tempfile,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server,settings,observability,business_store as store,business_runtime as runtime

ROUTES=['workspace','youtube','naukri']+['business-agents/'+a for a in store.AGENTS]

def percentile(values,p):return round(sorted(values)[min(len(values)-1,int((len(values)-1)*p))]*1000,2) if values else 0

def run(requests=800,concurrency=16):
 with tempfile.TemporaryDirectory(prefix='forma-stress-') as folder:
  server.DB=folder+'/audit.sqlite3';settings.KEY_PATH=Path(folder)/'key';observability.LOG_ROOT=Path(folder)/'logs';server.init();user='a'*32;session='stress-only-session';stamp=int(time.time())
  with server.db() as c:
   c.execute('INSERT INTO users VALUES(?,?,?,?)',(user,'stress@example.test','not-used','salt'));c.execute('INSERT INTO admins VALUES(?)',(user,));c.execute('INSERT INTO sessions VALUES(?,?,?)',(hashlib.sha256(session.encode()).hexdigest(),user,stamp+3600));c.execute('UPDATE managed_agents SET live=1 WHERE id="youtube"')
   project=store.save_project(c,user,{'name':'Load fixture','config':{'currency':'INR','customer':{'name':'Fixture'},'catalogue':[{'id':'web','name':'Website','price':'100'}],'requirements':{'scope':'Test only','timeline':'One week','items':[{'id':'web','quantity':1}]}}})
   encoded=store.encode({'agent':'quotation','state':'ready','total':'100','currency':'INR','items':[]})
   for i in range(16000):
    # Deliberately put relevant rows after a large unrelated history to measure indexes.
    owner='b'*32 if i<15000 else user;pid='other-project' if i<15000 else project['id']
    c.execute('INSERT INTO biz_artifacts VALUES(?,?,?,?,?,?,?,?)',(f'{i:032x}',owner,pid,'','quotation',i+1,encoded,stamp))
  listener=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=listener.serve_forever,daemon=True).start();port=listener.server_port
  def call(path,body=None):
   started=time.perf_counter();connection=http.client.HTTPConnection('127.0.0.1',port,timeout=12)
   try:
    connection.request('POST' if body is not None else 'GET','/api/'+path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json','Cookie':'session='+session});response=connection.getresponse();data=json.loads(response.read());return {'status':response.status,'seconds':time.perf_counter()-started,'data':data}
   except Exception as e:return {'status':0,'seconds':time.perf_counter()-started,'error':type(e).__name__}
   finally:connection.close()
  try:
   routes=ROUTES+['projects/'+project['id'],'admin/errors']
   started=time.perf_counter()
   with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:results=list(pool.map(lambda i:call(routes[i%len(routes)]),range(requests)))
   elapsed=time.perf_counter()-started;byroute={}
   for route in routes:
    subset=[r for i,r in enumerate(results) if routes[i%len(routes)]==route];byroute[route]={'requests':len(subset),'failed':sum(r['status']!=200 for r in subset),'p95_ms':percentile([r['seconds'] for r in subset],.95)}
   malformed=[{'agents':[{}]},{'currency':[]},{'sites':[None]},{'sites':['https://example.com:notaport']},{'checklist':[{'title':None}]},{'customer':{'name':{}}},{'connections':{'email':[]}},{'journeys':[{'id':{},'site':'https://example.com','authorized':True}]},{'rules':[{'kind':{},'destination':'x','template':'qa_v1'}]}]
   fuzz=[]
   for config in malformed:
    result=call('projects',{'name':'Malformed fixture','config':config});fuzz.append({'input':config,'status':result['status']})
   # Measure connection lifecycle under delayed garbage collection, as occurs under load.
   gc.collect();gc.disable();before=len(os.listdir('/proc/self/fd'))
   try:
    for _ in range(300):
     with server.db() as c:c.execute('SELECT 1').fetchone()
    fd_growth=len(os.listdir('/proc/self/fd'))-before
   finally:gc.enable();gc.collect()
   query_times=[]
   for _ in range(20):
    started=time.perf_counter()
    with server.db() as c:store.snapshot(c,user,project['id'])
    query_times.append(time.perf_counter()-started)
   report={'safety':'Temporary database and loopback fixtures only; no live provider calls.','requests':requests,'concurrency':concurrency,'seconds':round(elapsed,2),'requests_per_second':round(requests/elapsed,2),'status_counts':{str(status):sum(r['status']==status for r in results) for status in sorted({r['status'] for r in results})},'p50_ms':percentile([r['seconds'] for r in results],.5),'p95_ms':percentile([r['seconds'] for r in results],.95),'p99_ms':percentile([r['seconds'] for r in results],.99),'routes':byroute,'malformed_inputs':fuzz,'db_file_descriptor_growth_300_contexts':fd_growth,'project_snapshot_p95_ms':percentile(query_times,.95),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
   return report
  finally:listener.shutdown();listener.server_close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--requests',type=int,default=800);p.add_argument('--concurrency',type=int,default=16);p.add_argument('--output',required=True);a=p.parse_args()
 if not 10<=a.requests<=5000 or not 1<=a.concurrency<=32:p.error('Use 10–5000 requests and 1–32 clients.')
 report=run(a.requests,a.concurrency);Path(a.output).write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ('requests','concurrency','status_counts','p95_ms','db_file_descriptor_growth_300_contexts','project_snapshot_p95_ms')}))
