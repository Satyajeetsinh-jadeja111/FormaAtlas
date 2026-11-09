"""Bounded, explicitly authorized test journeys with pinned destination hosts."""
import base64,ipaddress,json,os,re,socket,time
from pathlib import Path
from urllib.parse import urlsplit,urljoin
import business_store as s

def destinations(config):
 mappings=[];origins=set()
 for site in config['sites']:
  u=urlsplit(site);addresses={a[4][0] for a in socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM)}
  local=config.get('allow_local') and u.hostname in ('localhost','127.0.0.1','::1')
  if not addresses or any(not ipaddress.ip_address(a).is_global and not (local and ipaddress.ip_address(a).is_loopback) for a in addresses):raise s.BusinessError('Website resolves to an unapproved private or metadata address.')
  address=sorted(addresses,key=lambda a:(ipaddress.ip_address(a).version,a))[0];address='['+address+']' if ':' in address else address
  mappings.append('MAP '+u.hostname+' '+address);origins.add((u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80)))
 return origins,mappings

def run(config,values=None):
 from playwright.sync_api import sync_playwright
 os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH',str(Path('.forma/naukri-browsers').resolve()))
 origins,mappings=destinations(config);deadline=time.monotonic()+480;results=[];evidence=[];values=values or {}
 if not config['journeys']:raise s.BusinessError('Configure at least one authorized test journey.')
 def scrub(value):
  value=str(value)
  for v in values.values():
   if v:value=value.replace(str(v),'[test value]')
  return value[:500]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=['--host-resolver-rules='+', '.join(mappings+['MAP * ~NOTFOUND'])])
  try:
   for journey in config['journeys']:
    for label,width,height in [('desktop',1280,800),('mobile',390,844)]:
     if time.monotonic()>deadline:raise s.BusinessError('Browser test time budget reached.')
     ctx=browser.new_context(viewport={'width':width,'height':height},service_workers='block',accept_downloads=False)
     page=ctx.new_page();errors=[];steps=[]
     def intercept(route):
      u=urlsplit(route.request.url);origin=(u.scheme,u.hostname,u.port or (443 if u.scheme=='https' else 80))
      mutation=route.request.method not in ('GET','HEAD','OPTIONS')
      allowed_test_write=bool(re.match(r'^/(?:test|sandbox|login|signin|auth)(?:/|$)',u.path,re.I))
      risky=bool(re.search(r'(?i)/(?:pay|payments|purchase|delete|remove|transfer|place-order|subscribe)(?:/|$)',u.path))
      if origin not in origins or risky or mutation and not allowed_test_write or route.request.method not in ('GET','HEAD','POST','PUT','PATCH','OPTIONS'):route.abort()
      else:route.continue_()
     ctx.route('**/*',intercept);page.on('pageerror',lambda e:errors.append('console: '+scrub(e)))
     page.on('requestfailed',lambda r:errors.append('request failed: '+scrub(urlsplit(r.url).path)))
     page.on('response',lambda r:errors.append('HTTP '+str(r.status)+' '+scrub(urlsplit(r.url).path)) if r.status>=400 else None)
     state='passed';failed_step=None
     try:
      page.set_default_timeout(5000);page.goto(journey['site'],timeout=15000)
      for index,step in enumerate(journey['steps']):
       if time.monotonic()>deadline:raise s.BusinessError('Browser test time budget reached.')
       failed_step=index+1;a=step['action'];steps.append({'step':failed_step,'action':a,'label':scrub(step.get('label',''))})
       if a=='goto':page.goto(urljoin(journey['site'],step['path']),timeout=15000)
       elif a=='click':page.get_by_role(step.get('role','button'),name=step['label'],exact=True).click()
       elif a=='fill':
        key=step.get('value_key','')
        if key not in values:raise s.BusinessError('A test-only form value is missing.')
        page.get_by_label(step['label'],exact=True).fill(str(values[key]))
       elif a=='expect_text':page.get_by_text(step['text'],exact=step.get('exact',False)).wait_for()
       elif a=='expect_url':
        if urlsplit(page.url).path!=step['path']:raise s.BusinessError('Expected page path did not match.')
      if errors:state='failed'
     except Exception:
      state='failed';errors.append('Journey expectation failed at step '+str(failed_step or 'initial navigation')+'.')
     try:
      shot=page.screenshot(full_page=False,mask=[page.locator('input'),page.locator('textarea')]);evidence.append({'kind':'image/png','name':journey['id']+'-'+label+'.png','data':base64.b64encode(shot).decode()})
     except Exception:pass
     results.append({'journey':journey['id'],'site':journey['site'],'viewport':label,'state':state,'failed_step':failed_step if state=='failed' else None,'errors':errors[:30],'steps':steps})
     ctx.close()
  finally:browser.close()
 while sum(len(x['data']) for x in evidence)>6_000_000:evidence.pop(0)
 evidence.append({'kind':'application/json','name':'trace.json','data':base64.b64encode(json.dumps({'journeys':results,'notice':'Sanitized step trace; no cookies, request payloads or credential values.'}).encode()).decode()})
 return {'state':'failed' if any(r['state']=='failed' for r in results) else 'passed','journeys':results,'evidence':evidence}
