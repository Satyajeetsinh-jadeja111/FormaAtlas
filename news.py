"""Bounded RSS research from fixed official technology publishers; no paid search."""
import datetime,email.utils,re,time,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from html import unescape
FEEDS=[('Google AI','https://blog.google/technology/ai/rss/',{'blog.google'}),('Django','https://www.djangoproject.com/rss/weblog/',{'www.djangoproject.com','djangoproject.com'}),('GitHub Engineering','https://github.blog/engineering/feed/',{'github.blog'})]
CACHE=[];FETCHED=0
class NewsUnavailable(Exception):pass
def plain(text):return re.sub(r'\s+',' ',unescape(re.sub('<[^>]*>',' ',text or ''))).strip()
def parse(raw,publisher,hosts,now):
 if len(raw)>1000000:raise NewsUnavailable('Feed exceeds the research size limit.')
 text=raw.decode('utf-8','strict')
 if re.search(r'<!\s*(DOCTYPE|ENTITY)',text,re.I):raise NewsUnavailable('Unsafe XML feed rejected.')
 root=ET.fromstring(text);items=[]
 for node in root.findall('.//item')+root.findall('.//{http://www.w3.org/2005/Atom}entry'):
  def value(name):
   e=node.find(name)
   if e is None:e=node.find('{http://www.w3.org/2005/Atom}'+name)
   return e.text if e is not None else ''
  url=value('link')
  if not url:
   e=node.find('{http://www.w3.org/2005/Atom}link');url=e.get('href','') if e is not None else ''
  link=urllib.parse.urlsplit(url or '')
  if link.scheme!='https' or link.hostname not in hosts or link.username or link.password or link.port not in (None,443):continue
  stamp=value('pubDate') or value('published') or value('updated')
  try:
   try:date=email.utils.parsedate_to_datetime(stamp)
   except (TypeError,ValueError):date=datetime.datetime.fromisoformat(stamp.replace('Z','+00:00'))
   if not date.tzinfo:continue
   age=now-date.astimezone(datetime.timezone.utc)
   if age.total_seconds()<0 or age>datetime.timedelta(days=7):continue
  except (TypeError,ValueError,AttributeError):continue
  title=plain(value('title'))[:220];summary=plain(value('description') or value('summary') or value('content'))[:1800]
  if title:items.append({'title':title,'summary':summary,'url':url,'publisher':publisher,'published':date.isoformat()})
 return items
class FixedRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  old=urllib.parse.urlsplit(req.full_url);new=urllib.parse.urlsplit(newurl)
  if new.scheme!='https' or new.hostname!=old.hostname:raise NewsUnavailable('Unexpected news-feed redirect.')
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch(now):
 items=[]
 for publisher,url,hosts in FEEDS:
  try:
   req=urllib.request.Request(url,headers={'User-Agent':'FormaTechnologyResearch/1.0','Accept':'application/rss+xml,application/atom+xml,application/xml'})
   with urllib.request.build_opener(FixedRedirect()).open(req,timeout=8) as r:raw=r.read(1000001)
   items.extend(parse(raw,publisher,hosts,now))
  except Exception:continue
 return sorted(items,key=lambda item:item['published'],reverse=True)
def select(used=(),now=None):
 global CACHE,FETCHED
 now=now or datetime.datetime.now(datetime.timezone.utc)
 if time.time()-FETCHED>3600:
  CACHE=fetch(now);FETCHED=time.time()
 candidates=[item for item in CACHE if item['url'] not in used and datetime.timedelta(0)<=now-datetime.datetime.fromisoformat(item['published']).astimezone(datetime.timezone.utc)<=datetime.timedelta(days=7)]
 if not candidates:raise NewsUnavailable('No unused official technology story published in the last seven days is available.')
 return candidates[0]
