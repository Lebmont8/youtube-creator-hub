import unittest,tempfile,pathlib,shutil,hashlib,json,threading,os,secrets
from unittest.mock import patch,MagicMock
from core import Store,ROOT,ORIGIN,PREFIX,SCOPES,digest,stamp
import google_api
from app import make_app
class Fixture(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
  (self.root/'inbox').mkdir();(self.root/'logs').mkdir()
  shutil.copyfile(pathlib.Path(__file__).resolve().parent/'storage_gate.py',self.root/'storage_gate.py')
  self.s=Store(self.root);self.password='owner-test-'+secrets.token_urlsafe(32);self.s.set_password(self.password)
 def tearDown(self):self.temp.cleanup()
 def channel(self,channel='UC'+'a'*22):
  raw={'refresh_token':'private-refresh-marker','token':'private-access-marker','client_id':'test.apps.googleusercontent.com','client_secret':'private-client-marker','token_uri':'https://oauth2.googleapis.com/token','scopes':SCOPES}
  self.s.save_channel(channel,'Demo <script>alert(1)</script>',raw,SCOPES);return channel
 def job(self):
  channel=self.channel();payload=b'fake video used only for non-network boundary tests'
  sha=hashlib.sha256(payload).hexdigest();p=self.root/'inbox'/(sha+'.mp4');p.write_bytes(payload)
  with patch('subprocess.run',return_value=type('P',(),{'returncode':0,'stdout':'{"streams":[{"codec_type":"video"}]}'})()):
   r=self.s.enqueue(p,channel,'Private test')
  return self.s.job(r['job_id'])
class Security(Fixture):
 def test_vault_bound_to_channel(self):
  x=self.s.seal({'token':'secret'},'channel:A')
  with self.assertRaises(Exception):self.s.unseal(x,'channel:B')
 def test_credentials_not_plaintext_on_disk(self):
  self.channel()
  raw=b''.join(p.read_bytes() for p in (self.root/'private').glob('state.sqlite3*'))
  for marker in [b'private-refresh-marker',b'private-access-marker',b'private-client-marker']:self.assertNotIn(marker,raw)
 def test_private_permissions(self):
  self.assertEqual((self.root/'private/vault.key').stat().st_mode&0o777,0o600)
  self.assertEqual((self.root/'private').stat().st_mode&0o777,0o700)
 def test_password_not_stored(self):
  self.assertTrue(self.s.password_ok(self.password));self.assertFalse(self.s.password_ok('wrong'))
  self.assertNotIn(self.password,(self.root/'private/state.sqlite3').read_bytes().decode(errors='ignore'))
 def test_oauth_state_one_use(self):
  state,v=self.s.oauth_state('session1');self.assertEqual(self.s.consume_state(state,'session1'),v)
  with self.assertRaises(ValueError):self.s.consume_state(state,'session1')
 def test_oauth_state_bound_to_owner(self):
  state,v=self.s.oauth_state('A')
  with self.assertRaises(ValueError):self.s.consume_state(state,'B')
  self.assertEqual(self.s.consume_state(state,'A'),v)
 def test_expired_state_rejected(self):
  state,v=self.s.oauth_state('A')
  with self.s.db() as c:c.execute('UPDATE oauth_states SET expires=0')
  with self.assertRaises(ValueError):self.s.consume_state(state,'A')
 def test_public_upload_denied(self):
  with self.assertRaisesRegex(ValueError,'private_only'):self.s.enqueue('/not/read','any','x',privacy='public')
 def test_outside_file_denied(self):
  channel=self.channel();p=self.root/'sensitive.mp4';p.write_bytes(b'private')
  with self.assertRaisesRegex(ValueError,'outside_allowed'):self.s.enqueue(p,channel,'x')
 def test_symlink_escape_denied(self):
  channel=self.channel();p=self.root/'sensitive.mp4';p.write_bytes(b'private')
  link=self.root/'inbox/escape.mp4';link.symlink_to(p)
  with self.assertRaisesRegex(ValueError,'outside_allowed'):self.s.enqueue(link,channel,'x')
 def test_duplicate_enqueue_returns_same_job(self):
  row=self.job()
  with patch('subprocess.run') as probe:
   r=self.s.enqueue(row['file'],row['channel_id'],'Different title')
  self.assertTrue(r['already_present']);self.assertEqual(row['id'],r['job_id']);probe.assert_not_called()
class Upload(Fixture):
 def test_wrong_channel_blocks_before_upload(self):
  row=self.job();yt=MagicMock();yt.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'UC'+'b'*22}]}
  with patch('google_api.youtube',return_value=yt):google_api.upload(self.s,row,threading.Event())
  yt.videos.assert_not_called();self.assertEqual(self.s.job(row['id'])['status'],'failed')
 def test_changed_source_blocks_before_google(self):
  row=self.job();pathlib.Path(row['file']).write_bytes(b'changed')
  with patch('google_api.youtube') as yt:google_api.upload(self.s,row,threading.Event())
  yt.assert_not_called();self.assertEqual(self.s.job(row['id'])['status'],'failed')
 def test_ambiguous_upload_preserves_session_no_new_job(self):
  row=self.job();yt=MagicMock();yt.channels.return_value.list.return_value.execute.return_value={'items':[{'id':row['channel_id']}]}
  req=MagicMock();req.resumable_uri='https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private-marker';req.resumable_progress=10;req.next_chunk.side_effect=TimeoutError()
  yt.videos.return_value.insert.return_value=req
  with patch('google_api.youtube',return_value=yt):google_api.upload(self.s,row,threading.Event())
  result=self.s.job(row['id']);self.assertEqual(result['status'],'needs_reconcile');self.assertTrue(result['resume'])
  self.assertNotIn('private-marker',result['resume']);self.assertEqual(len(self.s.jobs()),1)
  self.s.recover_jobs();self.assertEqual(self.s.job(row['id'])['status'],'needs_reconcile')
 def test_resume_does_not_accept_arbitrary_host(self):
  row=self.job();row['resume']=self.s.seal('https://attacker.invalid/token','resume:'+row['id'])
  yt=MagicMock();yt.channels.return_value.list.return_value.execute.return_value={'items':[{'id':row['channel_id']}]}
  req=MagicMock();req.resumable_uri=None;yt.videos.return_value.insert.return_value=req
  with patch('google_api.youtube',return_value=yt):google_api.upload(self.s,row,threading.Event())
  req.next_chunk.assert_not_called()
 def test_success_upload_always_private(self):
  row=self.job();yt=MagicMock();yt.channels.return_value.list.return_value.execute.return_value={'items':[{'id':row['channel_id']}]}
  req=MagicMock();req.resumable_uri='https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fake';req.resumable_progress=row['size'];req.next_chunk.return_value=(None,{'id':'testVideoID'})
  yt.videos.return_value.insert.return_value=req
  with patch('google_api.youtube',return_value=yt):google_api.upload(self.s,row,threading.Event())
  kwargs=yt.videos.return_value.insert.call_args.kwargs
  self.assertEqual(kwargs['body']['status']['privacyStatus'],'private')
  self.assertEqual(self.s.job(row['id'])['remote_id'],'testVideoID')
 def test_claim_single_owner_and_recover(self):
  row=self.job();self.assertIsNotNone(self.s.claim());self.assertIsNone(self.s.claim())
  self.s.recover_jobs();self.assertEqual(self.s.job(row['id'])['status'],'needs_reconcile')
class Web(Fixture):
 def setUp(self):
  super().setUp();self.app=make_app(self.s);self.app.config['TESTING']=True;self.client=self.app.test_client()
 def login(self):
  self.client.get(PREFIX+'/',base_url=ORIGIN)
  with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as s:csrf=s['login_csrf']
  return self.client.post(PREFIX+'/login',base_url=ORIGIN,data={'csrf':csrf,'password':self.password},headers={'Origin':ORIGIN})
 def test_csrf_missing_origin_denied(self):
  r=self.client.post(PREFIX+'/login',base_url=ORIGIN,data={'password':self.password})
  self.assertEqual(r.status_code,403)
 def test_login_requires_challenge(self):
  r=self.client.post(PREFIX+'/login',base_url=ORIGIN,data={'password':self.password},headers={'Origin':ORIGIN})
  self.assertEqual(r.status_code,403)
 def test_session_cookie_secure(self):
  response=self.login();self.assertEqual(response.status_code,302)
  value=response.headers.get('Set-Cookie','')
  for s in ['Secure','HttpOnly','SameSite=Lax','Path=/youtube-hub']:self.assertIn(s,value)
 def test_channel_names_html_escaped(self):
  self.channel();self.login();r=self.client.get(PREFIX+'/',base_url=ORIGIN)
  self.assertIn(b'&lt;script&gt;',r.data);self.assertNotIn(b'<script>alert',r.data)
 def test_oauth_callback_wrong_state_no_google_call(self):
  self.login()
  with patch('google_api.finish_authorization') as finish:
   r=self.client.get(PREFIX+'/oauth/callback?state=forged&code=private-code',base_url=ORIGIN)
  finish.assert_not_called();self.assertEqual(r.status_code,400)
 def test_callback_logs_allowlisted_reason_without_request_secrets(self):
  self.login()
  with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as ss:owner=ss['owner']
  state,verifier=self.s.oauth_state(self.s.session(owner)['id'])
  code='synthetic-code-should-never-be-logged'
  with patch('google_api.finish_authorization',side_effect=ValueError('select_exactly_one_youtube_channel')):
   with self.assertLogs(self.app.logger,level='ERROR') as logs:
    r=self.client.get(PREFIX+'/oauth/callback?state='+state+'&code='+code,base_url=ORIGIN)
  self.assertEqual(r.status_code,400)
  out='\n'.join(logs.output)
  self.assertIn('reason=select_exactly_one_youtube_channel',out)
  self.assertRegex(out,r'at=app\.py:[0-9]+')
  for secret in (state,verifier,code,self.password):self.assertNotIn(secret,out)
 def test_callback_unknown_exception_text_never_logged(self):
  self.login()
  for secret in ('ALPHANUMERICTOKEN_0123456789','https://oauth.invalid/token?code=private&access_token=private','line1\nFORGED_LOG_ENTRY'):
   with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as ss:owner=ss['owner']
   state,verifier=self.s.oauth_state(self.s.session(owner)['id'])
   with patch('google_api.finish_authorization',side_effect=ValueError(secret)):
    with self.assertLogs(self.app.logger,level='ERROR') as logs:
     r=self.client.get(PREFIX+'/oauth/callback?state='+state+'&code=synthetic',base_url=ORIGIN)
   out='\n'.join(logs.output)
   self.assertEqual(r.status_code,400);self.assertIn('reason=unclassified',out)
   for value in (secret,state,verifier):self.assertNotIn(value,out);self.assertNotIn(value,r.get_data(as_text=True))
 def test_callback_wrong_state_reason_logged_before_google(self):
  self.login()
  with patch('google_api.finish_authorization') as finish:
   with self.assertLogs(self.app.logger,level='ERROR') as logs:
    r=self.client.get(PREFIX+'/oauth/callback?state=synthetic-forged-state&code=synthetic-code',base_url=ORIGIN)
  finish.assert_not_called();self.assertEqual(r.status_code,400)
  out='\n'.join(logs.output)
  self.assertIn('reason=oauth_state_invalid_or_expired',out)
  self.assertRegex(out,r'at=core\.py:[0-9]+')
  self.assertNotIn('synthetic-forged-state',out);self.assertNotIn('synthetic-code',out)
 def test_form_document_policy_before_and_after_login(self):
  for authenticated in (False,True):
   if authenticated:self.assertEqual(self.login().status_code,302)
   r=self.client.get(PREFIX+'/',base_url=ORIGIN)
   self.assertEqual(r.headers['Referrer-Policy'],'strict-origin-when-cross-origin')
   self.assertIn("form-action 'self'",r.headers['Content-Security-Policy'])
   self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
 def test_non_form_policy_remains_no_referrer(self):
  for path in (PREFIX+'/health',PREFIX+'/oauth/callback'):
   r=self.client.get(path,base_url=ORIGIN)
   self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')
  r=self.login()
  self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')
 def test_forged_origin_rejected_even_with_valid_challenge(self):
  for origin in ('null','https://attacker.invalid'):
   self.client.get(PREFIX+'/',base_url=ORIGIN)
   with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as s:csrf=s['login_csrf']
   r=self.client.post(PREFIX+'/login',base_url=ORIGIN,data={'csrf':csrf,'password':self.password},headers={'Origin':origin})
   self.assertEqual(r.status_code,403)
   with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as s:self.assertNotIn('owner',s)
 def test_login_response_not_cacheable(self):
  r=self.client.get(PREFIX+'/',base_url=ORIGIN);self.assertIn('no-store',r.headers['Cache-Control'])
 def test_google_redirect_and_destination_policy(self):
  self.s.set_setting('oauth',{'client_id':'synthetic-csp-test.apps.googleusercontent.com','client_secret':'synthetic-only','project_id':'your-google-project-id','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'})
  self.assertEqual(self.login().status_code,302)
  r=self.client.get(PREFIX+'/',base_url=ORIGIN)
  self.assertIn("form-action 'self' https://accounts.google.com;",r.headers['Content-Security-Policy'])
  with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as ss:token=ss['owner']
  csrf=self.s.session(token)['csrf']
  r=self.client.post(PREFIX+'/connect',base_url=ORIGIN,data={'csrf':csrf},headers={'Origin':ORIGIN})
  self.assertEqual(r.status_code,302)
  from urllib.parse import urlsplit
  self.assertEqual(urlsplit(r.headers['Location']).netloc,'accounts.google.com')
  self.assertIn("form-action 'self' https://accounts.google.com;",r.headers['Content-Security-Policy'])
  self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
 def test_google_destination_not_allowed_on_other_responses(self):
  for path in (PREFIX+'/health',PREFIX+'/oauth/callback'):
   r=self.client.get(path,base_url=ORIGIN)
   self.assertIn("form-action 'self';",r.headers['Content-Security-Policy'])
   self.assertNotIn('accounts.google.com',r.headers['Content-Security-Policy'])
 def test_connect_requires_csrf(self):
  self.login();r=self.client.post(PREFIX+'/connect',base_url=ORIGIN,headers={'Origin':ORIGIN})
  self.assertEqual(r.status_code,403)
 def test_unexpected_host_rejected(self):
  r=self.client.get(PREFIX+'/',base_url='https://attacker.invalid');self.assertEqual(r.status_code,400)
 def test_oauth_url_has_pkce_offline_and_no_delete_scope(self):
  self.s.set_setting('oauth',{'client_id':'test.apps.googleusercontent.com','client_secret':'not-real','project_id':'your-google-project-id','auth_uri':'https://accounts.google.com/o/oauth2/auth','token_uri':'https://oauth2.googleapis.com/token'})
  state,v=self.s.oauth_state('x');url=google_api.authorization(self.s,state,v)
  from urllib.parse import urlsplit,parse_qs
  params=parse_qs(urlsplit(url).query)
  self.assertEqual(params['state'],[state]);self.assertEqual(params['access_type'],['offline'])
  self.assertIn('code_challenge',params);self.assertNotIn('youtube.force-ssl',url)
if __name__=='__main__':unittest.main(verbosity=2)
