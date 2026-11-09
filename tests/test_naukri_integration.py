import contextlib,json,sqlite3,tempfile,time,unittest
from unittest.mock import patch
import naukri,naukri_gemini,settings

class Integrations(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db'
  with self.connect() as c:
   naukri.init(c);c.execute('CREATE TABLE users(id TEXT,email TEXT)');c.execute('INSERT INTO users VALUES(?,?)',('a','member@example.com'))
  self.s={'nk_gemini_enabled':True,'free_tier_confirmed':True,'gemini_key':'private-key','model':'gemini-test','nk_ai_daily_limit':2,'nk_email_enabled':True}
  self.p={'timezone':'Asia/Kolkata','resume_text':'name@example.com\nPython engineer','answers':'{}','gemini_consent':True}
 def connect(self):
  c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;return c
 def tearDown(self):self.tmp.cleanup()
 def response(self,value):
  r=unittest.mock.MagicMock();r.__enter__.return_value.read.return_value=json.dumps({'steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps({'answer':value})}]}]}).encode();return r
 def test_gemini_cache_and_no_contact_lines(self):
  with patch('settings.load',return_value=self.s),patch('urllib.request.urlopen',return_value=self.response('Python engineer')) as call:
   self.assertEqual(naukri_gemini.answer(self.connect,'a','Experience?',self.p),'Python engineer')
   self.assertEqual(naukri_gemini.answer(self.connect,'a','Experience?',self.p),'Python engineer')
   self.assertEqual(call.call_count,1);self.assertNotIn('name@example.com',call.call_args.args[0].data.decode())
   self.assertEqual(call.call_args.args[0].get_header('X-goog-api-key'),'private-key')
 def test_budget_failures_consume_attempt_and_no_paid_fallback(self):
  from urllib.error import URLError
  with patch('settings.load',return_value=self.s),patch('urllib.request.urlopen',side_effect=URLError('quota')) as call:
   for _ in range(3):
    with self.assertRaises(naukri.NaukriError):naukri_gemini.answer(self.connect,'a','Experience?',self.p)
   self.assertEqual(call.call_count,2)
 def test_unsupported_and_no_consent(self):
  with patch('settings.load',return_value=self.s),patch('urllib.request.urlopen',return_value=self.response('invented experience')):
   self.assertIsNone(naukri_gemini.answer(self.connect,'a','Experience?',self.p))
  with patch('urllib.request.urlopen',side_effect=AssertionError('network')):
   self.assertIsNone(naukri_gemini.answer(self.connect,'a','Experience?',self.p|{'gemini_consent':False}))
  with patch('settings.load',return_value=self.s|{'free_tier_confirmed':False}),patch('urllib.request.urlopen',side_effect=AssertionError('network')):
   with self.assertRaises(naukri.NaukriError):naukri_gemini.answer(self.connect,'a','Experience?',self.p)
 def test_rolling_cap_counts_uncertain_attempts(self):
  with self.connect() as c:
   naukri.claim(c,'a','one');naukri.claim(c,'a','refresh:today');naukri.claim(c,'b','two')
   self.assertEqual(naukri.allowance(c,'a',2),1)
   c.execute('UPDATE nk_jobs SET created=? WHERE id=?',(int(time.time())-86401,'one'))
   self.assertEqual(naukri.allowance(c,'a',2),2)
 def test_summary_confirmed_only_once_to_member(self):
  with self.connect() as c:
   naukri.claim(c,'a','one');naukri.claim(c,'a','two');naukri.claim(c,'b','three')
   c.execute("UPDATE nk_jobs SET status='applied' WHERE id IN ('one','three')")
  with patch('settings.load',return_value=self.s),patch('settings.send',return_value='sent') as send:
   naukri.finish(self.connect,'a',self.p,'Daily attempt cap reached');naukri.finish(self.connect,'a',self.p,'done')
   self.assertEqual(send.call_count,1);self.assertEqual(send.call_args.args[0]['notify_to'],'member@example.com');self.assertIn('Confirmed applications: 1.',send.call_args.args[1]);self.assertNotIn('Python',send.call_args.args[1])
  with self.connect() as c:self.assertEqual(c.execute('SELECT delivery FROM nk_days').fetchone()[0],'sent')
 def test_admin_validation_and_redaction(self):
  with self.connect() as c:
   settings.init(c)
   with self.assertRaises(ValueError):settings.update(c,{'nk_daily_limit':51})
   out=settings.update(c,{'nk_daily_limit':50,'nk_ai_daily_limit':25,'nk_email_enabled':True,'nk_gemini_enabled':True,'smtp_password':'secret'})
   self.assertNotIn('smtp_password',out);self.assertTrue(out['smtp_password_saved'])

 def test_naukri_limit_messages_stop(self):
  import naukri_worker
  for text in ["You have reached today's application limit",'Daily apply limit reached','You have already applied to 50 jobs today']:
   page=unittest.mock.MagicMock();page.locator.return_value.inner_text.return_value=text;page.url='https://www.naukri.com/'
   with self.assertRaises(naukri_worker.DailyLimit):naukri_worker.blocked(page)

 def test_login_launcher_and_status(self):
  import naukri_login
  user='a'*32
  with self.connect() as c:
   naukri_login.init(c)
   self.assertEqual(naukri_login.status(c,user)['state'],'not_connected')
   with patch('naukri_login.subprocess.Popen') as popen:
    popen.return_value.poll.return_value=None
    result=naukri_login.start(c,user,self.path)
    self.assertEqual(result['state'],'opening')
    naukri_login.start(c,user,self.path)
    self.assertEqual(popen.call_count,1)
    args=popen.call_args.args[0];self.assertEqual(args[-3:],['--user',user,'--login'])
    self.assertNotIn('password',' '.join(args))
    naukri_login.mark(c,user,'session_saved')
    self.assertEqual(naukri_login.status(c,user)['state'],'session_saved')
   naukri_login._children.pop(user,None)
