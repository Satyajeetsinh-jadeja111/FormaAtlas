"""Fixed-host Google OAuth, channel data, analytics and resumable uploads."""
import datetime,hashlib,json,re,secrets,time,threading,urllib.error,urllib.parse,urllib.request
import youtube_store as store
from youtube_store import YouTubeError
OAUTH='https://oauth2.googleapis.com/token'
DATA='https://www.googleapis.com/youtube/v3/'
REQUEST_GUARD=threading.local()
def check_guard():
 hook=getattr(REQUEST_GUARD,'callback',None)
 if hook:hook()

class GooglePermissionError(YouTubeError):pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def http(url,data=None,headers=None,method=None,form=False):
 check_guard()
 payload=urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode() if data is not None else None
 req=urllib.request.Request(url,data=payload,headers=headers or {},method=method)
 try:
  with urllib.request.build_opener(NoRedirect()).open(req,timeout=30) as r:return json.loads(r.read(4000000)) if r.status!=204 else {}
 except urllib.error.HTTPError as e:
  try:problem=json.loads(e.read(12000))
  except (ValueError,OSError):problem={}
  reasons=[x.get('reason') for x in problem.get('error',{}).get('errors',[])] if isinstance(problem.get('error'),dict) else []
  if e.code==401 or isinstance(problem.get('error'),str) and problem['error'] in ('invalid_grant','invalid_token') or any(x in ('insufficientPermissions','authError') for x in reasons):raise GooglePermissionError('Google authorization was revoked or expired. Reconnect this permission.') from None
  if e.code in (401,403):raise YouTubeError('Google access or quota unavailable. Reconnect or check API permissions; no paid fallback was used.') from None
  if e.code==429:raise YouTubeError('Google quota exhausted. Work is held until a later check.') from None
  raise YouTubeError('Google request failed. Check the job state before retrying.') from None
 except (OSError,ValueError):raise YouTubeError('Google response unavailable. No successful operation was confirmed.') from None

def start(c,user,session,kind):
 if kind not in store.SCOPES:raise YouTubeError('Unknown connection permission.')
 cfg=store.config(c)
 if not all(cfg.get(k) for k in ('client_id','client_secret','redirect_uri')):raise YouTubeError('An administrator must configure Google OAuth first.')
 if kind!='upload' and not store.account(c,user):raise YouTubeError('Connect your publishing channel first.')
 state=secrets.token_urlsafe(32);c.execute('DELETE FROM yt_oauth WHERE expires<?',(time.time(),));c.execute('INSERT INTO yt_oauth VALUES(?,?,?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),user,session,kind,int(time.time())+600))
 scopes=store.SCOPES[kind]+(['openid','email','https://www.googleapis.com/auth/youtube.readonly'] if kind!='upload' else [])
 return 'https://accounts.google.com/o/oauth2/v2/auth?'+urllib.parse.urlencode({'client_id':cfg['client_id'],'redirect_uri':cfg['redirect_uri'],'response_type':'code','scope':' '.join(scopes),'access_type':'offline','prompt':'consent','state':state})

def callback(connect,user,session,query):
 state=query.get('state',[''])[0]
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT * FROM yt_oauth WHERE state=?',(hashlib.sha256(state.encode()).hexdigest(),)).fetchone()
  if not row or row['user']!=user or row['session']!=session or row['expires']<time.time():raise YouTubeError('Invalid or expired Google authorization state.')
  c.execute('DELETE FROM yt_oauth WHERE state=?',(row['state'],));cfg=store.config(c);old=store.account(c,user)
 if query.get('error') or not query.get('code'):raise YouTubeError('Google authorization was not granted. Existing permissions remain unchanged.')
 token=http(OAUTH,{'client_id':cfg['client_id'],'client_secret':cfg['client_secret'],'redirect_uri':cfg['redirect_uri'],'code':query['code'][0],'grant_type':'authorization_code'},{'Content-Type':'application/x-www-form-urlencoded'},form=True)
 if not isinstance(token.get('access_token'),str) or not isinstance(token.get('expires_in'),(int,float)):raise YouTubeError('Invalid OAuth token response.')
 head={'Authorization':'Bearer '+token['access_token']}
 identity=http('https://openidconnect.googleapis.com/v1/userinfo',headers=head)
 if not isinstance(identity.get('sub'),str) or not 1<=len(identity['sub'])<=200:raise YouTubeError('Google returned no verified account identity.')
 channels=http(DATA+'channels?'+urllib.parse.urlencode({'part':'snippet','mine':'true'}),headers=head).get('items',[])
 if len(channels)!=1:raise YouTubeError('Select one YouTube channel in Google authorization and reconnect.')
 channel=channels[0];cid=channel.get('id','')
 if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',cid):raise YouTubeError('No valid channel returned by YouTube.')
 kind=row['kind']
 if old and old['channel']!=cid:raise YouTubeError('Different channel selected. Disconnect the current channel before switching.')
 required=set(store.SCOPES[kind])-{'openid','email'};granted=set(token.get('scope','').split())
 if not required<=granted:raise YouTubeError('Required permission was declined. Existing access remains unchanged.')
 prefix='' if kind=='upload' else kind+'_'
 previous=old or {};record=previous|{'channel':cid,'title':channel['snippet'].get('title','YouTube channel'),'email':str(identity.get('email','')).lower() if identity.get('email_verified') is True else '', 'sub':identity.get('sub','')}
 if previous.get('sub') and previous['sub']!=record['sub']:raise YouTubeError('Google account identity changed. Disconnect before switching accounts.')
 record[prefix+'token']=token['access_token'];record[prefix+'expires']=int(time.time()+token['expires_in']);record[prefix+'scopes']=list(granted)
 refresh=token.get('refresh_token') or previous.get(prefix+'refresh')
 if refresh:record[prefix+'refresh']=refresh
 if kind!='upload':record[kind+'_connected']=True
 with connect() as c:
  store.put_account(c,user,record)
  if not old:
   p=store.profile(c,user);p['channel_confirmed']=False;p['enabled']=False;store.save_profile(c,user,p)

def token(connect,user,kind='upload'):
 prefix='' if kind=='upload' else kind+'_'
 with connect() as c:a=store.account(c,user);cfg=store.config(c)
 if not a or not a.get(prefix+'token'):raise YouTubeError('Connect '+kind+' permissions to load this report.')
 if a.get(prefix+'expires',0)>time.time()+60:return a[prefix+'token']
 if not a.get(prefix+'refresh'):raise YouTubeError('Google session expired. Reconnect the requested permission.')
 fresh=http(OAUTH,{'client_id':cfg['client_id'],'client_secret':cfg['client_secret'],'refresh_token':a[prefix+'refresh'],'grant_type':'refresh_token'},{'Content-Type':'application/x-www-form-urlencoded'},form=True)
 if not fresh.get('access_token'):raise YouTubeError('Reconnect Google; token refresh was not confirmed.')
 with connect() as c:
  current=store.account(c,user)
  if not current or current.get('channel')!=a['channel'] or current.get(prefix+'refresh')!=a[prefix+'refresh']:raise YouTubeError('Connection changed while refreshing. Start again.')
  current[prefix+'token']=fresh['access_token'];current[prefix+'expires']=int(time.time()+fresh.get('expires_in',3600));store.put_account(c,user,current)
 return fresh['access_token']
def get(connect,user,path,params,kind='upload'):
 if not re.fullmatch('[A-Za-z]+',path):raise YouTubeError('Invalid Google resource.')
 return http(DATA+path+'?'+urllib.parse.urlencode(params),headers={'Authorization':'Bearer '+token(connect,user,kind)})
def channel_snapshot(connect,user,cid=None):
 params={'part':'snippet,statistics,contentDetails,status'}
 params.update({'id':cid} if cid else {'mine':'true'})
 items=get(connect,user,'channels',params).get('items',[])
 if len(items)!=1:raise YouTubeError('Channel is unavailable or no longer accessible.')
 r=items[0];s=r.get('statistics',{});thumb=r['snippet'].get('thumbnails',{}).get('medium',{}).get('url','')
 return {'id':r['id'],'title':r['snippet']['title'],'thumbnail':thumb,'statistics':{k:s.get(k) for k in ('viewCount','videoCount','subscriberCount','hiddenSubscriberCount')},'uploads':r.get('contentDetails',{}).get('relatedPlaylists',{}).get('uploads'),'long_uploads_status':r.get('status',{}).get('longUploadsStatus')}
def recent_videos(connect,user,channel,limit=20,page_token=''):
 params={'part':'contentDetails','playlistId':channel['uploads'],'maxResults':min(50,limit)}
 if page_token:params['pageToken']=page_token
 listing=get(connect,user,'playlistItems',params);ids=[r.get('contentDetails',{}).get('videoId') for r in listing.get('items',[])]
 rows=get(connect,user,'videos',{'part':'snippet,contentDetails,statistics,status','id':','.join(ids)}).get('items',[]) if ids else []
 return {'items':[{'id':r['id'],'title':r['snippet']['title'],'published':r['snippet'].get('publishedAt'),'thumbnail':r['snippet'].get('thumbnails',{}).get('medium',{}).get('url',''),'duration':r.get('contentDetails',{}).get('duration'),'statistics':r.get('statistics',{}),'status':r.get('status',{}).get('privacyStatus')} for r in rows],'next_page':listing.get('nextPageToken')}
def resolve_competitor(connect,user,value):
 value=str(value).strip()
 if value.startswith('https://'):
  p=urllib.parse.urlsplit(value)
  if p.hostname not in ('youtube.com','www.youtube.com') or p.username or p.password or p.query or p.fragment:raise YouTubeError('Use a youtube.com channel URL or @handle.')
  value=p.path.strip('/')
  if value.startswith('channel/'):value=value[len('channel/'):]
 if re.fullmatch(r'UC[A-Za-z0-9_-]{22}',value):params={'id':value}
 elif re.fullmatch(r'@[A-Za-z0-9_.\-\u0080-\uffff]{1,100}',value):params={'forHandle':value}
 else:raise YouTubeError('Use a channel ID, /channel/ URL or @handle URL. Legacy custom URLs are not supported.')
 rows=get(connect,user,'channels',{'part':'snippet,statistics,contentDetails'}|params).get('items',[])
 if len(rows)!=1:raise YouTubeError('No matching public YouTube channel found.')
 return {'id':rows[0]['id'],'title':rows[0]['snippet']['title']}

def dates(params):
 try:
  end=datetime.date.fromisoformat(params.get('end') or (datetime.datetime.now(ZoneInfo('America/Los_Angeles')).date()-datetime.timedelta(days=2)).isoformat())
  start=datetime.date.fromisoformat(params.get('start') or (end-datetime.timedelta(days=27)).isoformat())
  if not 0<=(end-start).days<366 or end>datetime.datetime.now(ZoneInfo('America/Los_Angeles')).date():raise ValueError()
 except (ValueError,TypeError):raise YouTubeError('Choose a valid analytics date range of at most one year.') from None
 return start.isoformat(),end.isoformat()
from zoneinfo import ZoneInfo
REPORTS={
 'overview':{'metrics':'views,engagedViews,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost'},
 'timeline':{'dimensions':'day','metrics':'views,estimatedMinutesWatched,subscribersGained,subscribersLost','sort':'day'},
 'top':{'dimensions':'video','metrics':'views,estimatedMinutesWatched,averageViewDuration','sort':'-views','maxResults':'20'},
 'traffic':{'dimensions':'insightTrafficSourceType','metrics':'views,estimatedMinutesWatched','sort':'-views'},
 'geography':{'dimensions':'country','metrics':'views,estimatedMinutesWatched','sort':'-views','maxResults':'25'},
 'retention':{'dimensions':'elapsedVideoTimeRatio','metrics':'audienceWatchRatio,relativeRetentionPerformance','sort':'elapsedVideoTimeRatio'},
 'revenue':{'metrics':'estimatedRevenue,estimatedAdRevenue,monetizedPlaybacks,playbackBasedCpm','currency':'INR'}}
def analytics(connect,user,params):
 report=params.get('report','overview');fmt=params.get('format','all');video=params.get('video','');start,end=dates(params)
 if report not in REPORTS or fmt not in ('all','shorts','long'):raise YouTubeError('Unsupported report or format.')
 if video and not re.fullmatch(r'[A-Za-z0-9_-]{11}',video):raise YouTubeError('Invalid video ID.')
 if report=='retention' and not video:raise YouTubeError('Select one of your videos for audience retention.')
 with connect() as c:a=store.account(c,user)
 if not a:raise YouTubeError('Connect your channel first.')
 if video:
  r=get(connect,user,'videos',{'part':'snippet','id':video}).get('items',[])
  if not r or r[0]['snippet']['channelId']!=a['channel']:raise YouTubeError('Reports are limited to your connected channel.')
 filters=[]
 if video:filters.append('video=='+video)
 if fmt!='all':filters.append('creatorContentType=='+('SHORTS' if fmt=='shorts' else 'VIDEO_ON_DEMAND'))
 query={'ids':'channel==MINE','startDate':start,'endDate':end}|REPORTS[report]
 if filters:query['filters']=';'.join(filters)
 kind='revenue' if report=='revenue' else 'analytics'
 body=http('https://youtubeanalytics.googleapis.com/v2/reports?'+urllib.parse.urlencode(query),headers={'Authorization':'Bearer '+token(connect,user,kind)})
 titles={}
 if report=='top' and body.get('rows'):
  vids=','.join(str(row[0]) for row in body['rows'] if re.fullmatch('[A-Za-z0-9_-]{11}',str(row[0])))
  if vids:titles={r['id']:r['snippet']['title'] for r in get(connect,user,'videos',{'part':'snippet','id':vids}).get('items',[])}
 return {'video_titles':titles,'columns':[h['name'] for h in body.get('columnHeaders',[])],'rows':body.get('rows',[]),'start':start,'end':end,'timezone':'America/Los_Angeles','source':'YouTube Analytics API','report':report,'unsupported_metrics':['Thumbnail impressions','Thumbnail click-through rate','Unique viewers'],'note':'Reports can be delayed. Watch time is not a monetization eligibility counter.'}
