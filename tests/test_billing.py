import hashlib,hmac,http.client,json,secrets,time,unittest
from unittest.mock import patch
import billing,server,test_server as accounts
class Billing(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def setUp(self):
  self.admin=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'administrator-password-123'})
  self.member=self.call('signup',{'email':secrets.token_hex(8)+'@example.com','password':'member-password-123'})
  with server.db() as c:c.execute('INSERT INTO admins VALUES(?)',(self.admin[1]['user']['id'],))
 def setup_offer(self,mode='test'):
  with server.db() as c:
   b=billing.offers(c,True);billing.approve(c,b['draft']['version'])
   billing.configure(c,{'mode':mode,'key_id':'rzp_'+mode+'_example','secret':'key-secret','webhook_secret':'hook-secret','enabled':True,'live_confirmed':True})
  return b['draft']['version']
 def test_admin_only_configuration_and_secret_redaction(self):
  self.assertEqual(self.call('admin/billing',cookie=self.member[2])[0],403)
  self.assertEqual(self.call('admin/billing/settings',{'mode':'test'},self.member[2])[0],403)
  self.setup_offer();result=self.call('admin/billing',cookie=self.admin[2]);self.assertNotIn('key-secret',json.dumps(result));self.assertTrue(result[1]['settings']['secret_saved'])
  with server.db() as c:
   with self.assertRaises(billing.BillingError):billing.configure(c,{'mode':'live','key_id':'rzp_live_example','enabled':True,'live_confirmed':True})
 def test_draft_approval_and_snapshot_entitlements(self):
  v=self.setup_offer('live');uid=self.member[1]['user']['id']
  with server.db() as c:
   c.execute('INSERT INTO billing_subscriptions VALUES(?,?,?,?,?,?,?,?)',('sub_old',uid,'pro',v,'live','active',int(time.time())+3600,int(time.time())))
   plans=billing.offers(c)['plans'];plans[1]['amount']=49900;plans[1]['posts_per_day']=5
   draft=billing.save_offer(c,plans)['draft'];self.assertNotEqual(draft['version'],v)
   self.assertEqual(billing.offers(c)['plans'][1]['amount'],29900)
   billing.approve(c,draft['version']);self.assertEqual(billing.offers(c)['plans'][1]['amount'],49900)
   self.assertEqual(billing.entitlement(c,uid)['posts_per_day'],3)
   c.execute('UPDATE billing_subscriptions SET valid_until=0 WHERE id="sub_old"');self.assertEqual(billing.entitlement(c,uid)['id'],'free')
 def test_checkout_signature_owner_and_test_isolation(self):
  self.setup_offer();uid=self.member[1]['user']['id']
  with patch('billing.request',side_effect=[{'id':'plan_example'},{'id':'sub_example'}]):
   r=self.call('billing/subscribe',{'plan':'pro','amount':1},self.member[2]);self.assertEqual(r[0],200)
  self.assertEqual(self.call('billing/verify',{'razorpay_subscription_id':'sub_example','razorpay_signature':'fake'},self.member[2])[0],400)
  signature=hmac.new(b'key-secret',b'pay_example|sub_example',hashlib.sha256).hexdigest()
  response={'razorpay_subscription_id':'sub_example','razorpay_payment_id':'pay_example','razorpay_signature':signature}
  with patch('billing.request',return_value={'id':'sub_example','status':'active','current_end':int(time.time())+3600}):
   self.assertEqual(self.call('billing/verify',response,self.member[2])[0],200)
   self.assertEqual(self.call('billing/verify',response,self.admin[2])[0],400)
  with server.db() as c:self.assertEqual(billing.entitlement(c,uid)['id'],'free')
 def test_forged_and_replayed_webhooks(self):
  self.setup_offer();raw=json.dumps({'payload':{'subscription':{'entity':{'id':'sub_unrelated'}}}}).encode()
  with self.assertRaises(billing.BillingError):billing.webhook(server.db,raw,'bad','event1')
  signature=hmac.new(b'hook-secret',raw,hashlib.sha256).hexdigest()
  self.assertTrue(billing.webhook(server.db,raw,signature,'event1')['received']);self.assertTrue(billing.webhook(server.db,raw,signature,'event1')['duplicate'])
 def test_free_automation_rejected_and_agent_quota_atomic(self):
  r=self.call('workspace',{'profile':{'daily_enabled':True,'posts_per_day':1}},self.member[2]);self.assertEqual(r[0],400)
  uid=self.member[1]['user']['id']
  with server.db() as c:
   for i in range(10):c.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?)',(secrets.token_hex(8),uid,'example','','completed',int(time.time())))
   with self.assertRaises(billing.BillingError):billing.check_agent(c,uid)
   billing.check_agent(c,self.admin[1]['user']['id'])

 def test_cancel_ownership_and_reconcile_current_state(self):
  v=self.setup_offer('live');uid=self.member[1]['user']['id']
  with server.db() as c:c.execute('INSERT INTO billing_subscriptions VALUES(?,?,?,?,?,?,?,?)',('sub_cancel',uid,'pro',v,'live','active',int(time.time())+3600,int(time.time())))
  with patch('billing.request',side_effect=[{}, {'id':'sub_cancel','status':'active','current_end':int(time.time())+3600}]) as remote:
   self.assertEqual(self.call('billing/cancel',{'id':'sub_cancel'},self.admin[2])[0],400);remote.assert_not_called()
   self.assertEqual(self.call('billing/cancel',{'id':'sub_cancel'},self.member[2])[0],200)
   self.assertEqual(remote.call_args_list[0].args[2],{'cancel_at_cycle_end':True})
  # A signed old activation event cannot overwrite the current halted provider state.
  raw=json.dumps({'payload':{'subscription':{'entity':{'id':'sub_cancel','status':'active'}}}}).encode();sig=hmac.new(b'hook-secret',raw,hashlib.sha256).hexdigest()
  with patch('billing.request',return_value={'id':'sub_cancel','status':'halted','current_end':int(time.time())+3600}):billing.webhook(server.db,raw,sig,secrets.token_hex(8))
  with server.db() as c:self.assertEqual(billing.entitlement(c,uid)['id'],'free')
 def test_draft_quota_uses_server_day_not_client_date(self):
  a=self.call('workspace',{'drafts':[{'id':'first','text':'Facts','day':'2020-01-01'}]},self.member[2]);self.assertEqual(a[0],200)
  b=self.call('workspace',{'drafts':[{'id':'second','text':'Facts','day':'2040-01-01'}]},self.member[2]);self.assertEqual(b[0],403)
