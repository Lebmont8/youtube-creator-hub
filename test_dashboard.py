"""Contract/security tests for the dashboard; temporary stores, no real Google requests."""
import unittest,json,datetime as dt,threading
from unittest.mock import patch,MagicMock
import test_uploader as fixtures
from app import make_app
from dashboard_data import Dashboard,period,collect,thumbnail
from core import PREFIX,ORIGIN,stamp
CHANNEL='UC'+'a'*22

def payload():
    return {'fetched_at':stamp(),'channel':{'id':CHANNEL,'name':'Fixture','totals':{'viewCount':7}},
            'activity':{'available':True,'rows':[{'day':'2026-11-01','views':7}]},
            'revenue':{'available':False,'rows':None,'reason':'not_authorized_or_unavailable'},'videos':[]}

class Data(fixtures.Fixture):
    def setUp(self):
        super().setUp();self.channel();self.d=Dashboard(self.s);self.start,self.end=period()
    def test_period_rejects_reverse_future_and_overlong(self):
        for start,end in [('2026-01-10','2026-01-01'),('2020-01-01','2026-01-01'),('2026-01-01',(dt.date.today()+dt.timedelta(days=1)).isoformat())]:
            with self.assertRaises(ValueError):period(start,end)
    def test_failed_refresh_preserves_previous_payload_and_timestamp(self):
        key=self.d.key(CHANNEL,self.start,self.end)
        self.d.collector=lambda *args:payload();self.d.refresh_one(key)
        previous=self.d.snapshot()['channels'][0]['data']
        self.d.collector=MagicMock(side_effect=RuntimeError('secret-token-do-not-log'))
        self.d.refresh_one(key);current=self.d.snapshot()['channels'][0]
        self.assertEqual(current['data'],previous)
        self.assertEqual(current['error'],'provider_unavailable')
        self.assertNotIn('secret-token',json.dumps(current))
    def test_cache_does_not_mix_date_ranges(self):
        self.d.collector=lambda *args:payload();self.d.refresh_one(self.d.key(CHANNEL,self.start,self.end))
        old=(dt.date.fromisoformat(self.start)-dt.timedelta(days=1)).isoformat()
        self.assertIsNone(self.d.snapshot(old,old)['channels'][0]['data'])
    def test_finance_exact_minor_units_and_separate_currencies(self):
        a={'kind':'sponsorship','currency':'USD','status':'received','date':self.end,'amount':'1.25','note':'test','channel':CHANNEL}
        self.d.save_finance(a);self.d.save_finance({**a,'currency':'EUR','amount':'2.50'})
        rows=self.d.finance(self.start,self.end)
        self.assertEqual({r['currency']:r['amount_minor'] for r in rows},{'USD':125,'EUR':250})
    def test_finance_rejects_nonfinite_negative_precision_unknown_channel(self):
        base={'kind':'payout','currency':'USD','status':'received','date':self.end,'amount':'1'}
        for amount in ('NaN','Infinity','-2','0','1.001'):
            with self.assertRaises(ValueError):self.d.save_finance({**base,'amount':amount})
        with self.assertRaises(ValueError):self.d.save_finance({**base,'channel':'not-ours'})
    def test_finance_update_and_delete(self):
        data={'kind':'expense','currency':'EUR','status':'planned','date':self.end,'amount':'10','note':'first'}
        ident=self.d.save_finance(data)['id']
        self.d.save_finance({**data,'id':ident,'amount':'12','status':'received'})
        rows=self.d.finance(self.start,self.end);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['amount_minor'],1200)
        self.d.delete_finance(ident);self.assertEqual(self.d.finance(self.start,self.end),[])
    def test_duplicate_refresh_single_worker(self):
        started=threading.Event();release=threading.Event();calls=[]
        def worker(*args):calls.append(1);started.set();release.wait(3);return payload()
        self.d.collector=worker;self.d.enqueue([CHANNEL],self.start,self.end)
        self.assertTrue(started.wait(2));self.d.enqueue([CHANNEL],self.start,self.end)
        release.set();thread=self.d.thread
        if thread:thread.join(3)
        self.assertEqual(len(calls),1);self.assertIsNone(self.d.active)
    def test_foreign_video_key_rejected_without_provider_call(self):
        with self.assertRaises(ValueError):self.d.detail('foreign','abcdefghijk',self.start,self.end)
        with self.assertRaises(ValueError):self.d.detail(CHANNEL,'../../token',self.start,self.end)
    def test_identity_mismatch_stops_before_video_or_analytics(self):
        yt=MagicMock();yt.channels.return_value.list.return_value.execute.return_value={'items':[{'id':'UC'+'z'*22}]}
        with patch('google_api.credentials_for',return_value=object()),patch('google_api.service_from_credentials',return_value=yt):
            with self.assertRaisesRegex(ValueError,'identity_mismatch'):collect(self.s,CHANNEL,self.start,self.end)
        yt.videos.assert_not_called()
    def test_thumbnail_accepts_only_youtube_image_host(self):
        self.assertIsNone(thumbnail({'medium':{'url':'https://attacker.invalid/a.png'}}))
        self.assertEqual(thumbnail({'medium':{'url':'https://i9.ytimg.com/vi/abcdefghijk/mqdefault.jpg'}}),'https://i9.ytimg.com/vi/abcdefghijk/mqdefault.jpg')
        self.assertIsNone(thumbnail({'medium':{'url':'https://i9.ytimg.com.attacker.invalid/a.png'}}))
        self.assertEqual(thumbnail({'medium':{'url':'https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg'}}),'https://i.ytimg.com/vi/abcdefghijk/mqdefault.jpg')
    def test_queue_progress_is_percent_not_byte_count(self):
        row=self.job();self.s.update_job(row['id'],progress=row['size']//2)
        job=self.d.snapshot()['jobs'][0]
        self.assertGreater(job['progress_percent'],40)
        self.assertLess(job['progress_percent'],60)
    def test_cache_bound_keeps_current_and_never_prunes_finance(self):
        self.d.CACHE_MAX_ENTRIES=2
        self.d.save_finance({'kind':'expense','currency':'EUR','status':'received','date':self.end,'amount':'1'})
        self.d.collector=lambda *args:payload()
        for offset in range(3):
            start=(dt.date.fromisoformat(self.end)-dt.timedelta(days=offset)).isoformat()
            self.d.refresh_one(self.d.key(CHANNEL,start,self.end))
        with self.d.db() as c:self.assertEqual(c.execute('SELECT count(*) FROM cache').fetchone()[0],2)
        self.assertIsNotNone(self.d.snapshot(start,self.end)['channels'][0]['data'])
        self.assertEqual(len(self.d.finance(self.start,self.end)),1)
    def test_private_cache_permission(self):
        self.assertEqual(self.d.path.stat().st_mode&0o777,0o600)

class Routes(fixtures.Fixture):
    login=fixtures.Web.login
    def setUp(self):
        super().setUp();self.channel();self.app=make_app(self.s);self.app.config['TESTING']=True;self.client=self.app.test_client()
    def csrf(self):
        with self.client.session_transaction(path=PREFIX+'/',base_url=ORIGIN) as ss:owner=ss['owner']
        return self.s.session(owner)['csrf']
    def test_unauthenticated_api_never_returns_data(self):
        for route in ('/api/dashboard','/api/video/'+CHANNEL+'/abcdefghijk'):
            r=self.client.get(PREFIX+route,base_url=ORIGIN)
            self.assertEqual(r.status_code,401);self.assertEqual(r.json,{'error':'login_required'})
    def test_mutations_need_origin_and_csrf(self):
        self.login()
        for route in ('/api/refresh','/api/finance','/api/finance/'+'a'*24+'/delete'):
            r=self.client.post(PREFIX+route,base_url=ORIGIN,json={},headers={'Origin':ORIGIN})
            self.assertEqual(r.status_code,403)
            r=self.client.post(PREFIX+route,base_url=ORIGIN,json={},headers={'Origin':'https://attacker.invalid','X-CSRF-Token':self.csrf()})
            self.assertEqual(r.status_code,403)
    def test_dashboard_escapes_channel_and_loads_self_only_scripts(self):
        self.login();r=self.client.get(PREFIX+'/',base_url=ORIGIN)
        self.assertEqual(r.status_code,200);self.assertIn(b'Creator Hub',r.data)
        self.assertIn(b'&lt;script&gt;',r.data);self.assertNotIn(b'<script>alert',r.data)
        csp=r.headers['Content-Security-Policy']
        self.assertIn("script-src 'self';",csp);self.assertIn("frame-ancestors 'none'",csp)
        self.assertIn('frame-src https://www.youtube-nocookie.com;',csp)
        self.assertEqual(r.headers['Referrer-Policy'],'strict-origin-when-cross-origin')
    def test_snapshot_excludes_credentials_and_local_paths(self):
        self.login();r=self.client.get(PREFIX+'/api/dashboard',base_url=ORIGIN)
        raw=r.get_data(as_text=True)
        self.assertEqual(r.status_code,200)
        for marker in ('private-refresh-marker','private-access-marker','private-client-marker','vault.key','state.sqlite3','flask_key'):
            self.assertNotIn(marker,raw)
        self.assertIsNone(r.json['channels'][0]['data'])
        self.assertEqual(r.json['sources'][1],{'id':'instagram','connected':0})
    def test_period_validation_returns_safe_error(self):
        self.login();r=self.client.get(PREFIX+'/api/dashboard?start=secret-invalid-date',base_url=ORIGIN)
        self.assertEqual(r.status_code,400);self.assertEqual(r.json,{'error':'invalid_request'})
    def test_finance_api_validates_and_persists(self):
        self.login()
        data={'kind':'payout','currency':'EUR','status':'received','date':period()[1],'amount':'9.99','channel':CHANNEL}
        r=self.client.post(PREFIX+'/api/finance',base_url=ORIGIN,json=data,headers={'Origin':ORIGIN,'X-CSRF-Token':self.csrf()})
        self.assertEqual(r.status_code,201)
        r=self.client.get(PREFIX+'/api/dashboard',base_url=ORIGIN)
        self.assertEqual(r.json['finance'][0]['amount_minor'],999)
    def test_unknown_channel_cannot_trigger_google(self):
        self.login()
        with patch('dashboard_data.collect') as provider:
            r=self.client.post(PREFIX+'/api/refresh',base_url=ORIGIN,json={'channel':'foreign'},headers={'Origin':ORIGIN,'X-CSRF-Token':self.csrf()})
        self.assertEqual(r.status_code,400);provider.assert_not_called()
    def test_assets_exist_and_have_correct_types(self):
        for filename,mime in [('dashboard.js','javascript'),('dashboard.css','text/css')]:
            r=self.client.get(PREFIX+'/assets/'+filename,base_url=ORIGIN)
            self.assertEqual(r.status_code,200);self.assertIn(mime,r.content_type)
            r.close()

if __name__=='__main__':unittest.main()
