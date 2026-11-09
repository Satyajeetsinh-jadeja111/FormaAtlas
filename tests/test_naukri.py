import json,sqlite3,unittest
from unittest.mock import patch
import naukri,naukri_worker

class NaukriTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row;naukri.init(self.c)
 def tearDown(self):self.c.close()
 def test_internal_canonical_ids(self):
  self.assertEqual(naukri.job_id('https://www.naukri.com/job-listings-dev-123456789012?src=x'),'123456789012')
  for u in ['https://www.naukri.com.evil.org/job-listings-dev-123456789012','http://www.naukri.com/job-listings-dev-123456789012','https://evil@www.naukri.com/job-listings-dev-123456789012','https://www.naukri.com/redirect-123456789012']:
   self.assertIsNone(naukri.job_id(u))
 def test_claims_and_isolation(self):
  self.assertTrue(naukri.claim(self.c,'a','job'));self.assertFalse(naukri.claim(self.c,'a','job'));self.assertTrue(naukri.claim(self.c,'b','job'))
  self.assertEqual(len(naukri.state(self.c,'a')['jobs']),1);self.assertEqual(naukri.state(self.c,'c')['jobs'],[])
 def test_skill_boundaries(self):
  self.assertTrue(naukri.matches('Python and SQL developer','Python,SQL'))
  self.assertFalse(naukri.matches('JavaScript developer','Java'));self.assertFalse(naukri.matches('Python developer','Python,SQL'))
 def test_resume_and_validation(self):
  import base64
  d=dict(resume_text='Python engineer',skills='Python',search='Python developer',time='08:00',timezone='Asia/Kolkata',answers='{}',resume=base64.b64encode(b'%PDF-example').decode())
  naukri.save(self.c,'a',d);self.assertNotIn('resume',naukri.state(self.c,'a')['profile']);self.assertEqual(naukri.state(self.c,'b')['profile'],{})
  d['timezone']='invalid/timezone'
  with self.assertRaises(naukri.NaukriError):naukri.save(self.c,'a',d)
 def test_answers_no_network_without_model(self):
  p=dict(answers=json.dumps({'Notice period?':'30 days'}),model='',resume_text='Python engineer')
  with patch('urllib.request.build_opener',side_effect=AssertionError('network')):
   self.assertEqual(naukri_worker.answer('Notice period?',p),'30 days');self.assertIsNone(naukri_worker.answer('Salary?',p))

import test_server
class NaukriHTTP(test_server.Accounts):
 def test_private_endpoints(self):
  import base64
  a=self.call('signup',{'email':'nk-a@example.com','password':'long-password-nka'})
  b=self.call('signup',{'email':'nk-b@example.com','password':'long-password-nkb'})
  self.assertEqual(self.call('naukri')[0],401)
  d=dict(resume_text='Private Python engineer',skills='Python',search='Python developer',time='08:00',timezone='Asia/Kolkata',answers='{}',resume=base64.b64encode(b'%PDF-example').decode())
  self.assertEqual(self.call('naukri/save',d,a[2])[0],200)
  self.assertEqual(self.call('naukri',cookie=b[2])[1]['profile'],{})
  self.assertEqual(self.call('naukri',cookie=a[2])[1]['profile']['resume_text'],d['resume_text'])

 def test_login_requires_session_and_uses_its_owner(self):
  self.assertEqual(self.call('naukri/login',{})[0],401)
  a=self.call('signup',{'email':'nk-login@example.com','password':'long-password-nkl'})
  with patch('naukri_login.start',return_value={'state':'opening','detail':'Opening browser'}) as start:
   result=self.call('naukri/login',{'user':'someone-else'},a[2])
   self.assertEqual(result[0],200);self.assertEqual(start.call_args.args[1],a[1]['user']['id'])
  self.assertEqual(self.call('naukri',cookie=a[2])[1]['connection']['state'],'not_connected')
