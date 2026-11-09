import io,json,os,unittest
from unittest.mock import patch
from providers import refine,ProviderUnavailable
class Provider(unittest.TestCase):
 def test_disabled_never_calls_network(self):
  with patch.dict(os.environ,{},clear=True),patch('urllib.request.urlopen') as request:
   with self.assertRaises(ProviderUnavailable):refine({},'draft')
   request.assert_not_called()
 def test_stateless_bounded_request_and_output(self):
  response=io.BytesIO(json.dumps({'steps':[{'type':'thought','content':[{'type':'text','text':'private thought'}]},{'type':'model_output','content':[{'type':'text','text':'Reviewed draft'}]}]}).encode())
  with patch.dict(os.environ,{'FORMA_GEMINI_FREE_TIER_CONFIRMED':'yes','GEMINI_API_KEY':'test-only-not-a-key','GEMINI_MODEL':'gemini-test'},clear=True),patch('urllib.request.urlopen',return_value=response) as request:
   self.assertEqual(refine({'resume':'facts'},'draft'),'Reviewed draft');payload=json.loads(request.call_args.args[0].data);self.assertFalse(payload['store']);self.assertNotIn('tools',payload);self.assertEqual(payload['generation_config']['max_output_tokens'],500)
