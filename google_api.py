"""Official Google clients; no service accounts or password-based channel access."""
import json, time, hashlib, socket
import httplib2
import google_auth_httplib2
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from core import SCOPES,ORIGIN,PREFIX,stamp
REQUIRED=set(SCOPES[:2])
def safe_error(exc):
    if isinstance(exc,HttpError):
        reason='api_error'
        try:
            errors=json.loads(exc.content).get('error',{}).get('errors',[])
            candidate=errors[0].get('reason','api_error') if errors else 'api_error'
            if candidate.replace('_','').isalnum() and len(candidate)<80:reason=candidate
        except Exception:pass
        return 'google_http_'+str(exc.resp.status)+'_'+reason
    if isinstance(exc,ValueError):return str(exc)[:160] if str(exc).replace('_','').isalnum() else 'validation_error'
    return type(exc).__name__
def flow_for(store,state=None,verifier=None):
    cfg=store.setting('oauth')
    if not cfg:raise ValueError('oauth_not_configured')
    return Flow.from_client_config({'web':cfg},scopes=SCOPES,state=state,
        redirect_uri=ORIGIN+PREFIX+'/oauth/callback',code_verifier=verifier)
def authorization(store,state,verifier):
    flow=flow_for(store,state,verifier)
    url,_=flow.authorization_url(access_type='offline',prompt='consent',state=state,include_granted_scopes='true')
    return url
def finish_authorization(store,state,verifier,code):
    flow=flow_for(store,state,verifier)
    flow.fetch_token(code=code,timeout=30)
    cred=flow.credentials
    granted=set(cred.granted_scopes or cred.scopes or [])
    if not REQUIRED.issubset(granted):raise ValueError('upload_or_read_permission_missing')
    yt=service_from_credentials('youtube','v3',cred)
    data=yt.channels().list(part='id,snippet',mine=True).execute(num_retries=1).get('items',[])
    if len(data)!=1:raise ValueError('select_exactly_one_youtube_channel')
    channel=data[0];channel_id=channel['id']
    import re
    if not re.fullmatch(r'UC[A-Za-z0-9_-]{22}',channel_id):raise ValueError('unexpected_channel_id')
    store.save_channel(channel_id,channel['snippet']['title'],json.loads(cred.to_json()),granted)
    return channel_id
def service_from_credentials(name,version,cred):
    http=google_auth_httplib2.AuthorizedHttp(cred,http=httplib2.Http(timeout=30))
    return build(name,version,http=http,cache_discovery=False,static_discovery=True)
def credentials_for(store,channel_id):
    raw=store.channel_credentials(channel_id)
    cred=Credentials.from_authorized_user_info(raw)
    if not cred.valid:
        if not cred.refresh_token:raise ValueError('reconnect_channel')
        class TimedRequest:
            def __init__(self):self.inner=Request()
            def __call__(self,*args,**kwargs):
                kwargs['timeout']=30
                return self.inner(*args,**kwargs)
        try:cred.refresh(TimedRequest())
        except Exception:
            with store.db() as c:c.execute("UPDATE channels SET status='reconnect_required',updated=? WHERE id=?",(stamp(),channel_id))
            raise ValueError('channel_access_expired_reconnect')
        with store.db() as c:c.execute('UPDATE channels SET token=?,updated=? WHERE id=?',
            (store.seal(json.loads(cred.to_json()),'channel:'+channel_id),stamp(),channel_id))
    return cred
def youtube(store,channel_id):return service_from_credentials('youtube','v3',credentials_for(store,channel_id))
def verify_target(yt,channel_id):
    items=yt.channels().list(part='id',mine=True).execute(num_retries=1).get('items',[])
    if len(items)!=1 or items[0].get('id')!=channel_id:raise ValueError('channel_identity_mismatch_upload_blocked')
def upload(store,job,stop_event):
    req=None
    try:
        from pathlib import Path
        path=Path(job['file']).resolve(strict=True)
        if not path.is_relative_to((store.root/'inbox').resolve()):raise ValueError('upload_file_outside_inbox')
        if path.stat().st_size!=job['size']:raise ValueError('upload_file_size_changed')
        with path.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
        if sha!=job['file_sha']:raise ValueError('upload_file_content_changed')
        if job['privacy']!='private':raise ValueError('pilot_accepts_private_only')
        yt=youtube(store,job['channel_id']);verify_target(yt,job['channel_id'])
        media=MediaFileUpload(str(path),chunksize=8*1024*1024,resumable=True)
        req=yt.videos().insert(part='snippet,status',body={
           'snippet':{'title':job['title'],'description':job['description'],'categoryId':'27'},
           'status':{'privacyStatus':'private','selfDeclaredMadeForKids':False}},media_body=media)
        if job.get('resume'):
            uri=store.unseal(job['resume'],'resume:'+job['id'])
            from urllib.parse import urlsplit
            u=urlsplit(uri)
            if u.scheme!='https' or u.hostname not in {'www.googleapis.com','youtube.googleapis.com'}:
                raise ValueError('invalid_resumable_session_host')
            req.resumable_uri=uri;req.resumable_progress=job['progress'];req._in_error_state=True
        began=time.monotonic();response=None
        while response is None:
            if stop_event.is_set() or time.monotonic()-began>3600:raise ValueError('upload_interrupted_resume_existing_session')
            status,response=req.next_chunk(num_retries=2)
            fields={'progress':int(req.resumable_progress)}
            if req.resumable_uri:fields['resume']=store.seal(req.resumable_uri,'resume:'+job['id'])
            store.update_job(job['id'],**fields)
        remote_id=response.get('id')
        if not remote_id:raise ValueError('upload_response_missing_video_id')
        store.update_job(job['id'],status='uploaded',remote_id=remote_id,error=None,progress=job['size'])
    except Exception as exc:
        fields={'status':'needs_reconcile' if req is not None else 'failed','error':safe_error(exc)}
        if req is not None and req.resumable_uri:
            fields.update(resume=store.seal(req.resumable_uri,'resume:'+job['id']),progress=int(req.resumable_progress))
        store.update_job(job['id'],**fields)
def channel_stats(store,channel_id):
    import datetime
    cred=credentials_for(store,channel_id)
    yt=service_from_credentials('youtube','v3',cred);verify_target(yt,channel_id)
    channel=yt.channels().list(part='id,snippet,statistics',mine=True).execute(num_retries=1)['items'][0]
    result={'channel_id':channel_id,'name':channel['snippet']['title'],'totals':channel.get('statistics',{}),'fetched_at':stamp()}
    end=datetime.date.today()-datetime.timedelta(days=1);start=end-datetime.timedelta(days=6)
    analytics=service_from_credentials('youtubeAnalytics','v2',cred)
    for key,metrics in [('activity','views,estimatedMinutesWatched,subscribersGained,subscribersLost'),('revenue','estimatedRevenue')]:
        try:
            data=analytics.reports().query(ids='channel==MINE',startDate=start.isoformat(),endDate=end.isoformat(),metrics=metrics).execute(num_retries=1)
            result[key]={'available':True,'data':data}
        except Exception as exc:result[key]={'available':False,'data':None,'reason':safe_error(exc)}
    result.update(start_date=start.isoformat(),end_date=end.isoformat())
    return result
