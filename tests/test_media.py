import base64,http.client,io,json,unittest
from unittest.mock import patch
from PIL import Image
import server,media,linkedin,settings,test_server as accounts
class Media(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  import secrets
  self.a=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'private-password-123'});self.uid=self.a[1]['user']['id'];self.cookie=self.a[2]
  with server.db() as c:c.execute('INSERT INTO admins VALUES(?)',(self.uid,))
  self.call('workspace',{'drafts':[{'id':'draft','text':'Verified text','status':'approved'}]},self.cookie)
  buf=io.BytesIO();Image.new('RGB',(40,40),'blue').save(buf,'PNG');self.image=base64.b64encode(buf.getvalue()).decode()
 def test_private_upload_and_review_invalidation(self):
  self.assertEqual(self.call('media/attach',{'id':'draft','image':self.image,'alt':'Blue square'})[0],401)
  r=self.call('media/attach',{'id':'draft','image':self.image,'alt':'Blue square'},self.cookie);self.assertEqual(r[0],200);d=r[1]['drafts'][0];self.assertEqual(d['status'],'review');self.assertIn('image',d)
  other=self.call('signup',{'email':'other-image@example.com','password':'other-password-123'})
  for cookie,status in ((other[2],404),(self.cookie,200)):
   c=http.client.HTTPConnection('127.0.0.1',self.port);c.request('GET',d['image']['url'],headers={'Cookie':cookie});response=c.getresponse();self.assertEqual(response.status,status);response.read();c.close()
  with server.db() as c:self.assertIsNone(c.execute('SELECT * FROM approvals WHERE user=?',(self.uid,)).fetchone())
  stale={'drafts':[{'id':'draft','text':'Verified text','status':'approved'}]};self.assertEqual(self.call('workspace',stale,self.cookie)[0],409)
  d['status']='approved';self.assertEqual(self.call('workspace',r[1],self.cookie)[0],200)
  removed=self.call('media/remove',{'id':'draft'},self.cookie);self.assertEqual(removed[1]['drafts'][0]['status'],'review');self.assertIsNone(removed[1]['drafts'][0]['image'])
 def test_validation_and_graphic(self):
  self.assertEqual(self.call('media/attach',{'id':'draft','image':'invalid!','alt':'Example'},self.cookie)[0],400)
  self.assertEqual(self.call('media/attach',{'id':'draft','image':base64.b64encode(b'<svg/>').decode(),'alt':'Example'},self.cookie)[0],400)
  r=self.call('media/graphic',{'id':'draft','headline':'Practical technology for real businesses','brand':'SR Infotech','alt':'A branded card about practical technology'},self.cookie);self.assertEqual(r[0],200)
  with server.db() as c:
   attachment=media.get(c,self.uid,'draft');im=Image.open(io.BytesIO(attachment['data']));self.assertEqual(im.size,(1200,1200))
 def test_image_publish_uses_reviewed_asset(self):
  r=self.call('media/attach',{'id':'draft','image':self.image,'alt':'Blue square'},self.cookie);d=r[1]['drafts'][0];d['status']='approved';self.call('workspace',r[1],self.cookie)
  import time
  with server.db() as c:c.execute('INSERT INTO linkedin_accounts VALUES(?,?)',(self.uid,settings.cipher().encrypt(json.dumps({'token':'mock-token','sub':'member','expires':time.time()+3600,'name':'Test'}).encode())))
  with patch('linkedin.upload_image',return_value='urn:li:digitalmediaAsset:example') as upload,patch('linkedin.request',return_value=(201,{}, {'X-RestLi-Id':'urn:li:share:1'})) as request:
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft','Verified text','stale-image')
   upload.assert_not_called();linkedin.publish(server.db,self.uid,'draft','Verified text',d['image']['id']);upload.assert_called_once();content=request.call_args.args[1]['specificContent']['com.linkedin.ugc.ShareContent'];self.assertEqual(content['shareMediaCategory'],'IMAGE');self.assertEqual(content['media'][0]['description']['text'],'Blue square')
  self.assertEqual(self.call('media/remove',{'id':'draft'},self.cookie)[0],400)
 def test_upload_destination_is_validated(self):
  with patch('linkedin.request',return_value=(200,{'value':{'asset':'urn:li:digitalmediaAsset:test','uploadMechanism':{'com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest':{'uploadUrl':'https://evil.example/upload'}}}},{})),patch('urllib.request.build_opener') as opener:
   with self.assertRaises(linkedin.LinkedInError):linkedin.upload_image({'token':'private','sub':'id'},{'data':b'image'})
   opener.assert_not_called()

 def test_automatic_graphic_for_new_draft_and_opt_out(self):
  with patch('urllib.request.urlopen') as network:
   r=self.call('workspace',{'profile':{'name':'Test Person'},'drafts':[{'id':'auto','text':'Facts','status':'approved'}]},self.cookie);self.assertEqual(r[0],200)
   w=self.call('workspace',cookie=self.cookie)[1];d=next(d for d in w['drafts'] if d['id']=='auto');self.assertEqual(d['status'],'review');self.assertIsNotNone(d['image'])
   self.call('workspace',{'profile':{'name':'Test Person','auto_graphic':False},'drafts':[{'id':'plain','text':'Facts','status':'review'}]},self.cookie);w=self.call('workspace',cookie=self.cookie)[1];self.assertIsNone(next(d for d in w['drafts'] if d['id']=='plain')['image']);network.assert_not_called()
