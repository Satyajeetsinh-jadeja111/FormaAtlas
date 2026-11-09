"""Private, validated draft images and no-API branded graphics."""
import base64,hashlib,io,json,secrets,textwrap,warnings
from PIL import Image,ImageDraw,ImageFont,ImageOps
Image.MAX_IMAGE_PIXELS=16000000
class MediaError(Exception):pass
def init(c):c.execute('CREATE TABLE IF NOT EXISTS draft_media(user TEXT,draft TEXT,id TEXT UNIQUE,data BLOB,digest TEXT,alt TEXT,PRIMARY KEY(user,draft))')
def get(c,user,draft):return c.execute('SELECT * FROM draft_media WHERE user=? AND draft=?',(user,draft)).fetchone()
def info(row):return {'id':row['id'],'alt':row['alt'],'url':'/api/media/'+row['id']} if row else None
def enrich(c,user,w):
 for d in w.get('drafts',[]):d['image']=info(get(c,user,d['id']))
 return w
def approval_digest(c,user,d):
 row=get(c,user,d['id']);text=d['text']
 if row:text+='\nIMAGE:'+row['digest']+'\nALT:'+row['alt']
 import linkedin_pages
 target=linkedin_pages.destination(c,user)
 if target['mode']=='both':text+='\nDESTINATION:'+json.dumps(target,sort_keys=True)
 return hashlib.sha256(text.encode()).hexdigest()
def normalize(raw):
 if len(raw)>8*1024*1024:raise MediaError('Choose an image smaller than 8 MB.')
 try:
  with warnings.catch_warnings():
   warnings.simplefilter('error',Image.DecompressionBombWarning)
   with Image.open(io.BytesIO(raw)) as image:
    if image.format not in ('PNG','JPEG','WEBP'):raise MediaError('Use PNG, JPEG or WebP.')
    image=ImageOps.exif_transpose(image);image.load();image.thumbnail((4096,4096));image=image.convert('RGB');out=io.BytesIO();image.save(out,format='PNG');result=out.getvalue()
    if len(result)>12*1024*1024:raise MediaError('Image is too complex. Choose a smaller image.')
    return result
 except (OSError,ValueError,Image.DecompressionBombError,Image.DecompressionBombWarning):raise MediaError('Invalid or oversized image.') from None
def graphic(headline,brand):
 if not isinstance(headline,str) or not 1<=len(headline.strip())<=120:raise MediaError('Use a headline of 1–120 characters.')
 if not isinstance(brand,str) or not 1<=len(brand.strip())<=40:raise MediaError('Use a brand name of 1–40 characters.')
 im=Image.new('RGB',(1200,1200),'#f3f5ef');draw=ImageDraw.Draw(im)
 def font(size,bold=False):
  try:return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans'+('-Bold' if bold else '')+'.ttf',size)
  except OSError:return ImageFont.load_default()
 draw.rounded_rectangle((65,65,1135,1135),radius=45,fill='#173f34')
 draw.ellipse((840,-70,1360,450),fill='#285646');draw.ellipse((970,50,1230,310),fill='#74947a')
 draw.text((125,145),brand.strip(),font=font(32,True),fill='#d9e6cf')
 lines=textwrap.wrap(headline.strip(),width=22,break_long_words=True)[:6];y=370
 for line in lines:draw.text((120,y),line,font=font(67,True),fill='#ffffff');y+=85
 draw.rectangle((125,990,320,999),fill='#b2cc92');draw.text((125,1040),'Ideas into action.',font=font(25),fill='#d9e6cf')
 out=io.BytesIO();im.save(out,format='PNG');return out.getvalue()
def attach(connect,user,draft_id,raw,alt):
 if not isinstance(alt,str) or not 1<=len(alt.strip())<=500:raise MediaError('Add a descriptive image caption of 1–500 characters.')
 image=normalize(raw)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone();w=json.loads(r[0]) if r else {};d=next((d for d in w.get('drafts',[]) if d['id']==draft_id),None)
  if not d:raise MediaError('Draft not found.')
  if c.execute('SELECT 1 FROM publications WHERE user=? AND draft=?',(user,draft_id)).fetchone():raise MediaError('This draft has a publication attempt. Use a new draft for another post.')
  c.execute('INSERT INTO draft_media VALUES(?,?,?,?,?,?) ON CONFLICT(user,draft) DO UPDATE SET id=excluded.id,data=excluded.data,digest=excluded.digest,alt=excluded.alt',(user,draft_id,secrets.token_hex(20),image,hashlib.sha256(image).hexdigest(),alt.strip()))
  d['status']='review';c.execute('DELETE FROM approvals WHERE user=? AND draft=?',(user,draft_id));c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),user))
  return enrich(c,user,w)
def remove(connect,user,draft_id):
 with connect() as c:
  c.execute('BEGIN IMMEDIATE')
  if c.execute('SELECT 1 FROM publications WHERE user=? AND draft=?',(user,draft_id)).fetchone():raise MediaError('Cannot change a draft after publication was attempted.')
  r=c.execute('SELECT data FROM workspaces WHERE user=?',(user,)).fetchone();w=json.loads(r[0]) if r else {};d=next((d for d in w.get('drafts',[]) if d['id']==draft_id),None)
  if not d:raise MediaError('Draft not found.')
  c.execute('DELETE FROM draft_media WHERE user=? AND draft=?',(user,draft_id));c.execute('DELETE FROM approvals WHERE user=? AND draft=?',(user,draft_id));d['status']='review';c.execute('UPDATE workspaces SET data=? WHERE user=?',(json.dumps(w),user));return enrich(c,user,w)

def automatic(c,user,draft,profile,index=0):
 """Create local template media for a new draft; no network or AI calls."""
 if not profile.get('name') or profile.get('auto_graphic',True) is False or get(c,user,draft['id']):return False
 labels=[]
 if profile.get('rustico'):labels.append('Rasiko')
 if profile.get('outreach'):labels.append('Outreach Studio')
 headline=(draft.get('topic') or ('A closer look at '+labels[index%len(labels)] if labels else 'Building practical digital products'))[:120]
 brand=(profile.get('brand') or profile.get('name') or 'My work')[:40]
 image=graphic(headline,brand);alt=f'{brand}: {headline}. Branded graphic created from a local design template.'
 c.execute('INSERT INTO draft_media VALUES(?,?,?,?,?,?)',(user,draft['id'],secrets.token_hex(20),image,hashlib.sha256(image).hexdigest(),alt))
 return True
