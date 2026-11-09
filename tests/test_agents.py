import base64,io,json,secrets,unittest,zipfile
from unittest.mock import patch
import server,agent_platform,test_server as accounts
class Platform(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  self.admin=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'administrator-password-123'});self.member=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'member-password-123'})
  with server.db() as c:
   c.execute('INSERT INTO admins VALUES(?)',(self.admin[1]['user']['id'],));c.execute('UPDATE managed_agents SET live=1 WHERE id="linkedin"')
  self.config={'name':'A new agent','description':'Useful work','category':'Tools','kind':'http','endpoint':'https://example.com/agent','token':'hidden-secret','reviewed':True,'no_charge_confirmed':True}
 def create(self):
  r=self.call('admin/agents/save',self.config,self.admin[2]);self.assertEqual(r[0],200);return r[1]['id']
 def test_admin_only_registration_private_visibility_and_live_gate(self):
  self.assertEqual(self.call('admin/agents/save',self.config,self.member[2])[0],403);aid=self.create()
  self.assertNotIn(aid,[a['id'] for a in self.call('agents',cookie=self.member[2])[1]])
  self.assertEqual(self.call('admin/agents',cookie=self.member[2])[0],403)
  self.assertEqual(self.call('admin/agents/toggle',{'id':aid,'live':True},self.admin[2])[0],400)
  with patch('agent_platform.hosted',return_value='Tested output') as run:
   self.assertEqual(self.call('agents/run',{'id':aid,'input':'private'},self.member[2])[0],400);run.assert_not_called()
   self.assertEqual(self.call('admin/agents/test',{'id':aid,'input':'test'},self.admin[2])[0],200)
   self.assertEqual(self.call('admin/agents/toggle',{'id':aid,'live':True},self.admin[2])[0],200)
   self.assertEqual(self.call('agents/run',{'id':aid,'input':'member task'},self.member[2])[0],200)
   self.assertEqual(len(self.call('agent-runs/'+aid,cookie=self.member[2])[1]),1)
   self.assertEqual(len(self.call('agent-runs/'+aid,cookie=self.admin[2])[1]),1)
  self.assertEqual(self.call('admin/agents/toggle',{'id':aid,'live':False},self.admin[2])[0],200)
  self.assertEqual(self.call('agent-runs/'+aid,cookie=self.member[2])[0],404)
 def test_secret_non_disclosure_and_edit_invalidates_live(self):
  aid=self.create();r=self.call('admin/agents',cookie=self.admin[2]);self.assertNotIn('hidden-secret',json.dumps(r));self.assertTrue(next(a for a in r[1] if a['id']==aid)['token_saved'])
  with patch('agent_platform.hosted',return_value='Output'):self.call('admin/agents/test',{'id':aid,'input':'test'},self.admin[2])
  self.call('admin/agents/toggle',{'id':aid,'live':True},self.admin[2]);r=self.call('admin/agents/save',dict(self.config,id=aid,name='Edited'),self.admin[2]);self.assertFalse(r[1]['live']);self.assertEqual(r[1]['test_status'],'Not tested')
 def test_builtin_off_blocks_regular_routes_but_not_admin(self):
  self.call('admin/agents/toggle',{'id':'linkedin','live':False},self.admin[2])
  self.assertEqual(self.call('workspace',cookie=self.member[2])[0],404);self.assertEqual(self.call('linkedin/status',cookie=self.member[2])[0],404);self.assertEqual(self.call('workspace',{},self.member[2])[0],404)
  self.assertEqual(self.call('workspace',cookie=self.admin[2])[0],200)
  self.call('admin/agents/toggle',{'id':'linkedin','live':True},self.admin[2])
 def test_unsafe_packages_and_private_network_rejected(self):
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w') as z:z.writestr('../escape.py','print(1)')
  with self.assertRaises(agent_platform.AgentError):agent_platform.unpack(buf.getvalue())
  with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]),patch('socket.create_connection') as network:
   with self.assertRaises(agent_platform.AgentError):agent_platform.hosted({'endpoint':'https://example.com/agent'},{'input':'x'})
   network.assert_not_called()
 def test_python_package_runs_only_through_isolated_adapter(self):
  from pathlib import Path
  cfg=dict(self.config,kind='python',endpoint='',package=base64.b64encode(Path('examples/agents/text-utility.zip').read_bytes()).decode())
  result=self.call('admin/agents/save',cfg,self.admin[2]);self.assertEqual(result[0],200)
  with patch('agent_platform.local_python',return_value='Container output') as run:
   r=self.call('admin/agents/test',{'id':result[1]['id'],'input':'test'},self.admin[2]);self.assertEqual(r[0],200);run.assert_called_once()
