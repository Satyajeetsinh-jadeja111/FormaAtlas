"""Opt-in, zero-spend video adapter. No provider is bundled or enabled."""
import http.client,ipaddress,json,socket,ssl,urllib.parse
from youtube_store import YouTubeError

def validate_endpoint(value):
 try:
  u=urllib.parse.urlsplit(value)
  if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or u.query or u.port not in (None,443):raise ValueError()
 except ValueError:raise YouTubeError('Use a public HTTPS adapter URL on port 443 without credentials or query parameters.') from None
 return u

def connection(url):
 u=validate_endpoint(url);addrs=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
 if not addrs or any(not ipaddress.ip_address(a[4][0]).is_global for a in addrs):raise YouTubeError('Nonpublic video-provider addresses are not allowed.')
 c=http.client.HTTPSConnection(u.hostname,timeout=30);sock=socket.create_connection(addrs[0][4][:2],timeout=30)
 c.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=u.hostname)
 return c,u

def call(cfg,action,data=None):
 if cfg.get('provider')!='forma-video-v1' or cfg.get('free_confirmed') is not True:raise YouTubeError('No verified free video provider is enabled.')
 url=cfg['provider_endpoint'].rstrip('/')+'/'+action;c,u=connection(url)
 headers={'Content-Type':'application/json','Accept':'application/json'}
 if cfg.get('provider_token'):headers['Authorization']='Bearer '+cfg['provider_token']
 try:
  c.request('POST',u.path,body=json.dumps(data or {}).encode(),headers=headers);r=c.getresponse();raw=r.read(1000001)
  if r.status!=200 or len(raw)>1000000:raise YouTubeError('Video provider refused this operation or its free allowance is exhausted.')
  result=json.loads(raw)
  if not isinstance(result,dict):raise ValueError()
  return result
 except (OSError,ValueError):raise YouTubeError('Video provider response could not be confirmed. Work is held, without a paid fallback.') from None
 finally:c.close()
def readiness(cfg):
 r=call(cfg,'capabilities',{'protocol':'forma-video-v1'})
 if r.get('protocol')!='forma-video-v1' or r.get('zero_spend_enforced') is not True or r.get('commercial_use_allowed') is not True or r.get('free_available') is not True:raise YouTubeError('Provider must enforce zero spending and allow commercial use within a currently available free allowance.')
 return r

def fetch(cfg,url,path):
 # Artifact URLs are confined to the reviewed adapter host. Credentials never go to a different host.
 u=validate_endpoint(url);base=validate_endpoint(cfg['provider_endpoint'])
 if u.hostname!=base.hostname:raise YouTubeError('Provider artifact must use the reviewed HTTPS host.')
 c,u=connection(url)
 try:
  c.request('GET',u.path,headers={'Authorization':'Bearer '+cfg.get('provider_token','')});r=c.getresponse()
  if r.status!=200:raise YouTubeError('Footage artifact unavailable.')
  size=0
  with open(path,'wb') as f:
   while True:
    chunk=r.read(65536)
    if not chunk:break
    size+=len(chunk)
    if size>128*1024*1024:raise YouTubeError('Generated segment exceeds the 128 MB transfer limit.')
    f.write(chunk)
 finally:c.close()
