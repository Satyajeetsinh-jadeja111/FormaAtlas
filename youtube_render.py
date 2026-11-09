"""Bounded local speech and FFmpeg composition; no replacement footage."""
import json,math,os,shutil,subprocess
from pathlib import Path
from youtube_store import YouTubeError

def run(args,timeout=1800):
 def limits():
  import resource
  resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 try:
  r=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout,preexec_fn=limits if os.name=='posix' else None)
  if r.returncode:raise YouTubeError('Local media processing failed. No video was published.')
  return r.stdout
 except (OSError,subprocess.TimeoutExpired):raise YouTubeError('Media runtime unavailable or timed out. Work is held.') from None

def probe(path):
 try:
  data=json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,wav','-show_streams','-show_format','-of','json',str(path)],60))
  return float(data['format']['duration']),data.get('streams',[])
 except (ValueError,KeyError,TypeError):raise YouTubeError('Rendered media has no valid duration.') from None

def validate_storyboard(data,seconds):
 if not isinstance(data,dict) or not isinstance(data.get('title'),str) or not 1<=len(data['title'])<=100 or not isinstance(data.get('description'),str) or not 1<=len(data['description'])<=5000:raise YouTubeError('Invalid generated title or description.')
 if not isinstance(data.get('tags'),list) or any(not isinstance(t,str) or len(t)>80 for t in data['tags']) or sum(len(t)+1 for t in data['tags'])>450:raise YouTubeError('Invalid generated video tags.')
 scenes=data.get('scenes')
 if not isinstance(scenes,list) or not 1<=len(scenes)<=600:raise YouTubeError('Storyboard requires 1–600 original scenes.')
 total=0
 for s in scenes:
  if not isinstance(s,dict) or type(s.get('seconds')) is not int or not 1<=s['seconds']<=30 or not all(isinstance(s.get(k),str) and 1<=len(s[k])<=2500 for k in ('narration','prompt')):raise YouTubeError('Invalid narration or footage segment.')
  total+=s['seconds']
 if total>seconds or total<max(1,seconds*.8):raise YouTubeError('Storyboard does not fit the requested duration. No padding or repeated footage was inserted.')
 return data

def render(folder,clips,story,fmt,voice,guard=lambda:None):
 folder=Path(folder)
 if shutil.disk_usage(folder).free<1024**3:raise YouTubeError('At least 1 GB of free working storage is required.')
 if not shutil.which('espeak-ng'):raise YouTubeError('Install the licensed local espeak-ng narration runtime first.')
 width,height=(720,1280) if fmt=='shorts' else (1280,720)
 parts=[];srt=[];cursor=0
 def stamp(value):
  n=int(value*1000);return f'{n//3600000:02}:{n//60000%60:02}:{n//1000%60:02},{n%1000:03}'
 for i,(clip,scene) in enumerate(zip(clips,story['scenes'])):
  guard();seconds,streams=probe(clip)
  if not any(s.get('codec_type')=='video' for s in streams) or seconds+.1<scene['seconds']:raise YouTubeError('Generated footage is shorter than its scene. It will not be looped.')
  speech=folder/f'speech-{i}.txt';speech.write_text(scene['narration']);audio=folder/f'speech-{i}.wav';part=folder/f'part-{i}.mp4'
  run(['espeak-ng','-v',voice,'-s','155','-w',str(audio),'-f',str(speech)],180)
  narration_duration,_=probe(audio)
  if narration_duration>scene['seconds']+.5:raise YouTubeError('Narration does not fit its scene. Adjust the script rather than truncating speech.')
  filt=f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1'
  run(['ffmpeg','-nostdin','-y','-threads','2','-protocol_whitelist','file,pipe','-f','mov','-i',str(clip),'-i',str(audio),'-map','0:v:0','-map','1:a:0','-vf',filt,'-af','apad','-t',str(scene['seconds']),'-c:v','libx264','-preset','ultrafast','-threads','2','-c:a','aac',str(part)])
  parts.append(part);srt.append(f"{i+1}\n{stamp(cursor)} --> {stamp(cursor+scene['seconds'])}\n{scene['narration'].replace(chr(10),' ')}\n");cursor+=scene['seconds']
 if len(parts)!=len(story['scenes']):raise YouTubeError('Footage missing from storyboard.')
 captions=folder/'captions.srt';captions.write_text('\n'.join(srt));manifest=folder/'parts.txt';manifest.write_text('\n'.join("file '"+p.resolve().as_posix()+"'" for p in parts));joined=folder/'joined.mp4';output=folder/'video.mp4';thumb=folder/'thumbnail.jpg'
 guard();run(['ffmpeg','-nostdin','-y','-f','concat','-safe','0','-i',str(manifest),'-c','copy',str(joined)])
 run(['ffmpeg','-nostdin','-y','-i',str(joined),'-vf','subtitles='+str(captions.resolve()),'-t',str(max(.1,cursor-.1)),'-c:v','libx264','-preset','ultrafast','-threads','2','-c:a','copy','-movflags','+faststart',str(output)])
 run(['ffmpeg','-nostdin','-y','-i',str(output),'-frames:v','1','-q:v','3',str(thumb)],120)
 measured,streams=probe(output)
 if not math.isfinite(measured) or measured<=0 or fmt=='shorts' and measured>180.05:raise YouTubeError('Rendered video failed duration validation.')
 return {'video':output,'thumbnail':thumb,'captions':captions,'seconds':math.ceil(measured)}
