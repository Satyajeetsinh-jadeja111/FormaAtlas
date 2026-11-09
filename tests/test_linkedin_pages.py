import json,secrets,time,unittest,urllib.parse
from unittest.mock import patch
import server,linkedin,linkedin_pages,settings,media,test_server as accounts
class Pages(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  r=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'linkedin-pages-password-123'});self.uid=r[1]['user']['id'];self.cookie=r[2]
  self.a={'token':'private-token','expires':int(time.time())+3600,'sub':'member','name':'Member','page_access':True};self.page='urn:li:organization:123'
  with server.db() as c:
   c.execute('INSERT INTO admins VALUES(?)',(self.uid,));c.execute('INSERT INTO linkedin_accounts VALUES(?,?)',(self.uid,settings.cipher().encrypt(json.dumps(self.a).encode())))
  self.acl=(200,{'elements':[{'organizationTarget':self.page,'role':'ADMINISTRATOR','state':'APPROVED','roleAssignee':'urn:li:person:member'}]}, {})
 def select(self):
  with patch('linkedin.request',return_value=self.acl):self.assertEqual(self.call('linkedin/destinations',{'mode':'both','page':self.page},self.cookie)[0],200)
 def draft(self):
  self.assertEqual(self.call('workspace',{'drafts':[{'id':'draft','text':'Verified facts','status':'approved'}]},self.cookie)[0],200)
 def test_scope_opt_in_and_role_checks(self):
  with server.db() as c:
   settings.update(c,{'linkedin_client_id':'client','linkedin_client_secret':'secret','linkedin_redirect_uri':'http://localhost:8000/api/linkedin/callback'})
   ordinary=linkedin.start(c,self.uid,'session');page=linkedin.start(c,self.uid,'session',True)
  self.assertNotIn('w_organization_social',urllib.parse.unquote(ordinary));self.assertIn('w_organization_social',urllib.parse.unquote(page))
  with patch('linkedin.request',return_value=(200,{'elements':[]},{})):
   self.assertEqual(self.call('linkedin/destinations',{'mode':'both','page':self.page},self.cookie)[0],400)
  self.select()
 def test_both_publish_same_text_and_independent_records(self):
  self.select();self.draft()
  with patch('linkedin_pages.verify'),patch('linkedin.request',side_effect=[(201,{}, {'X-RestLi-Id':'urn:li:share:person'}),(201,{}, {'x-restli-id':'urn:li:share:page'})]) as req:
   result=linkedin.publish(server.db,self.uid,'draft','Verified facts');self.assertEqual(result['page'],'urn:li:share:page');self.assertEqual(req.call_args_list[0].args[1]['specificContent']['com.linkedin.ugc.ShareContent']['shareCommentary']['text'],req.call_args_list[1].args[1]['commentary']);self.assertEqual(req.call_args_list[1].args[1]['author'],self.page)
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft','Verified facts')
   self.assertEqual(req.call_count,2)
  with server.db() as c:
   self.assertEqual(c.execute('SELECT status FROM publications WHERE user=?',(self.uid,)).fetchone()[0],'published');self.assertEqual(c.execute('SELECT status FROM page_publications WHERE user=?',(self.uid,)).fetchone()[0],'published')
 def test_partial_failure_never_duplicates_personal(self):
  self.select();self.draft()
  with patch('linkedin_pages.verify'),patch('linkedin.request',side_effect=[(201,{}, {'X-RestLi-Id':'urn:li:share:person'}),linkedin.LinkedInError('Page unavailable')]) as req:
   with self.assertRaisesRegex(linkedin.LinkedInError,'Personal profile published'):linkedin.publish(server.db,self.uid,'draft','Verified facts')
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft','Verified facts')
   self.assertEqual(req.call_count,2)
 def test_destination_change_invalidates_approval(self):
  self.draft();self.select()
  with patch('linkedin_pages.verify'),patch('linkedin.request') as req:
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft','Verified facts')
   req.assert_not_called()
 def test_rejected_preflight_never_posts_either_destination(self):
  self.select();self.draft()
  with patch('linkedin_pages.verify',side_effect=linkedin.LinkedInError('Role revoked')),patch('linkedin.request') as req:
   with self.assertRaises(linkedin.LinkedInError):linkedin.publish(server.db,self.uid,'draft','Verified facts')
   req.assert_not_called()
 def test_page_image_owner_and_media_id(self):
  self.select();self.draft()
  # Verify the versioned Page post references an image owned by this Page.
  with server.db() as c:
   c.execute('INSERT INTO draft_media VALUES(?,?,?,?,?,?)',(self.uid,'draft','image1',b'png','digest','Professional graphic'))
   w=json.loads(c.execute('SELECT data FROM workspaces WHERE user=?',(self.uid,)).fetchone()[0]);d=w['drafts'][0];c.execute('UPDATE approvals SET digest=? WHERE user=? AND draft=?',(media.approval_digest(c,self.uid,d),self.uid,'draft'))
   c.execute('INSERT INTO publications VALUES(?,?,?,?)',(self.uid,'draft','published','urn:li:share:person'))
  with patch('linkedin_pages.verify'),patch('linkedin_pages.image',return_value='urn:li:image:page') as image,patch('linkedin.request',return_value=(201,{}, {'x-restli-id':'urn:li:share:page'})) as req:
   linkedin.publish(server.db,self.uid,'draft','Verified facts','image1');self.assertEqual(image.call_args.args[2],self.page);self.assertEqual(req.call_args.args[1]['content']['media']['id'],'urn:li:image:page')
