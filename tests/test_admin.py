import json,os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import settings,server
import test_server as accounts
class Admin(unittest.TestCase):
 setUpClass=classmethod(accounts.Accounts.setUpClass.__func__)
 tearDownClass=classmethod(accounts.Accounts.tearDownClass.__func__)
 call=accounts.Accounts.call
 def test_authorization_encryption_isolation_and_removal(self):
  a=self.call('signup',{'email':'admin@example.com','password':'admin-password-123'});b=self.call('signup',{'email':'ordinary@example.com','password':'ordinary-password-123'})
  self.assertEqual(self.call('admin/settings')[0],401);self.assertEqual(self.call('admin/settings',cookie=b[2])[0],403);self.assertEqual(self.call('admin/settings',{'gemini_key':'intrusion'},b[2])[0],403)
  with server.db() as c:c.execute('INSERT INTO admins VALUES(?)',(a[1]['user']['id'],))
  secret='test-secret-must-never-be-returned';password='test-smtp-private'
  r=self.call('admin/settings',{'gemini_key':secret,'smtp_password':password,'model':'gemini-test','free_tier_confirmed':True},a[2]);self.assertEqual(r[0],200);self.assertNotIn(secret,json.dumps(r));self.assertNotIn(password,json.dumps(r));self.assertTrue(r[1]['gemini_key_saved'])
  with server.db() as c:
   encrypted=c.execute('SELECT encrypted FROM integrations').fetchone()[0];self.assertNotIn(secret.encode(),encrypted);self.assertEqual(settings.load(c)['gemini_key'],secret)
  self.assertNotIn(secret,json.dumps(self.call('admin/settings',cookie=a[2])));self.assertEqual(self.call('admin/events',cookie=b[2])[0],403)
  self.assertEqual(self.call('admin/settings',{'remove_gemini_key':True,'remove_smtp_password':True},a[2])[1]['gemini_key_saved'],False)
 def test_notifications_are_truthful_and_errors_recorded(self):
  a=self.call('signup',{'email':'notify@example.com','password':'notify-password-123'})
  with server.db() as c:settings.update(c,{'notifications_enabled':True,'events':['draft_saved','review_saved']})
  with patch('settings.send',return_value='failed') as send:
   self.assertEqual(self.call('workspace',{'drafts':[{'id':'one','text':'SECRET RESUME','status':'review'}]},a[2])[0],200);send.assert_called_once();self.assertNotIn('SECRET RESUME',send.call_args.args[1]);self.assertIn('saved',send.call_args.args[1])
   self.call('workspace',{'drafts':[{'id':'one','text':'SECRET RESUME','status':'review'}]},a[2]);self.assertEqual(send.call_count,1)
  with server.db() as c:r=c.execute('SELECT delivery FROM completion_events WHERE user=?',(a[1]['user']['id'],)).fetchone();self.assertEqual(r[0],'failed')
 def test_tls_email_and_failure(self):
  s={'smtp_host':'smtp.example.com','smtp_username':'user','smtp_password':'private','smtp_from':'from@example.com','notify_to':'to@example.com','smtp_security':'starttls','smtp_port':587}
  with patch('smtplib.SMTP') as client:
   self.assertEqual(settings.send(s,'Saved one draft.'),'sent');smtp=client.return_value.__enter__.return_value;smtp.starttls.assert_called_once();smtp.login.assert_called_once_with('user','private');self.assertIn('no resume or draft text',smtp.send_message.call_args.args[0].get_content())
  with patch('smtplib.SMTP',side_effect=OSError('secret must not appear')):self.assertEqual(settings.send(s,'summary'),'failed')
