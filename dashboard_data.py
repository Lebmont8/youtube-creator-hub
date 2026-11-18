"""Read-only YouTube collection and bounded local dashboard cache. No uploads."""
import datetime as dt
import json, re, sqlite3, threading, secrets
from collections import OrderedDict
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from core import stamp
import google_api

VIDEO=re.compile(r'^[A-Za-z0-9_-]{11}$')
CURRENCIES={'USD','EUR','GBP','UAH','RUB'}
METRICS='views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost,likes,comments'
def period(start=None,end=None):
    yesterday=dt.date.today()
    finish=dt.date.fromisoformat(end) if end else yesterday
    begin=dt.date.fromisoformat(start) if start else finish-dt.timedelta(days=27)
    if finish>yesterday or begin>finish or (finish-begin).days>365 or begin<dt.date(2005,1,1):
        raise ValueError('invalid_period')
    return begin.isoformat(),finish.isoformat()

def public_error(exc):
    from googleapiclient.errors import HttpError
    if isinstance(exc,HttpError):
        status=int(exc.resp.status)
        return {401:'reconnect_required',403:'not_authorized_or_unavailable',
                404:'not_found',429:'rate_limited'}.get(status,'provider_unavailable')
    if isinstance(exc,ValueError) and exc.args and exc.args[0] in {
        'identity_mismatch','reconnect_channel','channel_not_connected','invalid_video'}:
        return exc.args[0]
    return 'provider_unavailable'

def number(value):
    try:return int(value)
    except (TypeError,ValueError):return None

def thumbnail(raw):
    from urllib.parse import urlsplit
    for size in ('medium','default','high'):
        url=raw.get(size,{}).get('url','')
        p=urlsplit(url)
        if p.scheme=='https' and p.hostname in {'i.ytimg.com','i9.ytimg.com'} and not p.username and not p.password:return url
    return None

def report(service,channel,start,end,metrics,dimensions='day',filters=None,sort=None,max_results=None):
    kwargs=dict(ids='channel==MINE',startDate=start,endDate=end,metrics=metrics,dimensions=dimensions)
    if filters:kwargs['filters']=filters
    if sort:kwargs['sort']=sort
    if max_results:kwargs['maxResults']=max_results
    if 'estimatedRevenue' in metrics:kwargs['currency']='USD'
    try:
        raw=service.reports().query(**kwargs).execute(num_retries=0)
        headers=[x['name'] for x in raw.get('columnHeaders',[])]
        rows=[dict(zip(headers,row)) for row in raw.get('rows',[])]
        return {'available':True,'rows':rows,'reason':None,
                'reported_through':max((r['day'] for r in rows if 'day' in r),default=None)}
    except Exception as exc:
        return {'available':False,'rows':None,'reason':public_error(exc),'reported_through':None}

def collect(store,channel,start,end,video=None):
    cred=google_api.credentials_for(store,channel)
    yt=google_api.service_from_credentials('youtube','v3',cred)
    rows=yt.channels().list(part='id,snippet,statistics,contentDetails',mine=True).execute(num_retries=0).get('items',[])
    if len(rows)!=1 or rows[0]['id']!=channel:raise ValueError('identity_mismatch')
    raw=rows[0]
    info={'id':channel,'name':raw['snippet']['title'],
          'totals':{k:number(raw.get('statistics',{}).get(k)) for k in ('viewCount','subscriberCount','videoCount')},
          'subscriber_count_hidden':bool(raw.get('statistics',{}).get('hiddenSubscriberCount',False))}
    if info['subscriber_count_hidden']:info['totals']['subscriberCount']=None
    service=google_api.service_from_credentials('youtubeAnalytics','v2',cred)
    if video:
        if not VIDEO.fullmatch(video):raise ValueError('invalid_video')
        items=yt.videos().list(part='snippet,statistics,contentDetails,status',id=video).execute(num_retries=0).get('items',[])
        if len(items)!=1 or items[0]['snippet']['channelId']!=channel:raise ValueError('invalid_video')
        selected=video_record(items[0])
        return {'channel':info,'video':selected,'activity':report(service,channel,start,end,
                'views,estimatedMinutesWatched,averageViewDuration,likes,comments',filters='video=='+video),
                'revenue':report(service,channel,start,end,'estimatedRevenue',filters='video=='+video),
                'retention':report(service,channel,start,end,'audienceWatchRatio','elapsedVideoTimeRatio',filters='video=='+video),
                'fetched_at':stamp(),'start':start,'end':end}
    videos=[];token=None;complete=True;videos_error=None
    try:
        playlist=raw.get('contentDetails',{}).get('relatedPlaylists',{}).get('uploads')
        if playlist:
            for _ in range(10):
                args={'part':'contentDetails','playlistId':playlist,'maxResults':50}
                if token:args['pageToken']=token
                page=yt.playlistItems().list(**args).execute(num_retries=0)
                ids=[x.get('contentDetails',{}).get('videoId','') for x in page.get('items',[])]
                ids=[v for v in ids if VIDEO.fullmatch(v)]
                if ids:
                    details=yt.videos().list(part='snippet,statistics,contentDetails,status',id=','.join(ids),maxResults=50).execute(num_retries=0)
                    videos.extend(video_record(x) for x in details.get('items',[]) if x.get('snippet',{}).get('channelId')==channel)
                token=page.get('nextPageToken')
                if not token:break
            complete=not bool(token)
    except Exception as exc:videos_error=public_error(exc);complete=False
    return {'channel':info,'videos':videos,'videos_complete':complete,'videos_error':videos_error,
            'activity':report(service,channel,start,end,METRICS),
            'revenue':report(service,channel,start,end,'estimatedRevenue'),
            'video_activity':report(service,channel,start,end,'views,estimatedMinutesWatched,likes,comments','video',sort='-views',max_results=200),
            'fetched_at':stamp(),'start':start,'end':end}

def video_record(item):
    snippet=item.get('snippet',{});stats=item.get('statistics',{})
    return {'id':item['id'],'channel_id':snippet.get('channelId'),'title':snippet.get('title','Без названия'),
            'description':snippet.get('description','')[:1200],'published_at':snippet.get('publishedAt'),
            'thumbnail':thumbnail(snippet.get('thumbnails',{})),'duration':item.get('contentDetails',{}).get('duration'),
            'privacy':item.get('status',{}).get('privacyStatus','unknown'),
            'statistics':{k:number(stats.get(k)) for k in ('viewCount','likeCount','commentCount')}}

class Dashboard:
    CACHE_MAX_BYTES=64*1024*1024
    CACHE_MAX_ENTRIES=256
    def __init__(self,store,stop=None,collector=collect):
        self.store=store;self.stop=stop or threading.Event();self.collector=collector
        self.path=store.private/'dashboard.sqlite3'
        self.lock=threading.Lock();self.pending=OrderedDict();self.active=None;self.thread=None
        with self.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS cache(channel TEXT, start TEXT, end TEXT, video TEXT,
                payload TEXT, fetched_at INTEGER, last_attempt INTEGER, error TEXT,
                PRIMARY KEY(channel,start,end,video));
            CREATE TABLE IF NOT EXISTS finance(id TEXT PRIMARY KEY,channel TEXT,kind TEXT,date TEXT,
                amount_minor INTEGER,currency TEXT,status TEXT,note TEXT,created INTEGER,updated INTEGER);
            """)
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        c.execute('PRAGMA busy_timeout=10000')
        try:
            with c:yield c
        finally:c.close()
    def known(self,channel):
        if channel not in {x['id'] for x in self.store.channels()}:raise ValueError('channel_not_connected')
    def key(self,channel,start,end,video=''):
        self.known(channel);start,end=period(start,end)
        if video and not VIDEO.fullmatch(video):raise ValueError('invalid_video')
        return channel,start,end,video or ''
    def snapshot(self,start=None,end=None):
        start,end=period(start,end)
        channels=[]
        with self.db() as c:
            for row in self.store.channels():
                saved=c.execute('SELECT * FROM cache WHERE channel=? AND start=? AND end=? AND video=?',(row['id'],start,end,'')).fetchone()
                channels.append({'id':row['id'],'name':row['name'],'status':row['status'],
                    'data':json.loads(saved['payload']) if saved and saved['payload'] else None,
                    'last_attempt':saved['last_attempt'] if saved else None,
                    'error':saved['error'] if saved else None})
        with self.lock:
            running=[list(k) for k in self.pending]
            if self.active:running.insert(0,list(self.active))
        jobs=self.store.jobs()
        with self.store.db() as c:
            sizes={row['id']:row['size'] for row in c.execute('SELECT id,size FROM jobs')}
        for job in jobs:
            size=sizes.get(job['id'],0)
            job['progress_percent']=min(100,round(job['progress']*100/size,1)) if size else None
        return {'start':start,'end':end,'channels':channels,'running':running,
                'jobs':jobs,'finance':self.finance(start,end),
                'sources':[{'id':'youtube','connected':len(channels)},{'id':'instagram','connected':0}]}
    def detail(self,channel,video,start,end):
        key=self.key(channel,start,end,video)
        with self.db() as c:
            row=c.execute('SELECT * FROM cache WHERE channel=? AND start=? AND end=? AND video=?',key).fetchone()
        return {'data':json.loads(row['payload']) if row and row['payload'] else None,
                'error':row['error'] if row else None,'last_attempt':row['last_attempt'] if row else None}
    def enqueue(self,channels,start,end,video=''):
        keys=[self.key(ch,start,end,video) for ch in channels]
        with self.lock:
            if len(self.pending)+len(keys)>100:raise ValueError('refresh_queue_full')
            for key in keys:
                if key!=self.active:self.pending[key]=True
            if keys and (self.thread is None or not self.thread.is_alive()):
                self.thread=threading.Thread(target=self._run,name='dashboard-refresh',daemon=True);self.thread.start()
        return {'queued':len(keys)}
    def _run(self):
        while not self.stop.is_set():
            with self.lock:
                if not self.pending:self.active=None;self.thread=None;return
                key,_=self.pending.popitem(last=False);self.active=key
            self.refresh_one(key)
            with self.lock:self.active=None
        with self.lock:self.active=None;self.pending.clear();self.thread=None
    def refresh_one(self,key):
        channel,start,end,video=key
        attempt=stamp()
        try:
            payload=self.collector(self.store,channel,start,end,video or None)
            raw=json.dumps(payload,ensure_ascii=False,allow_nan=False)
            if len(raw.encode())>2*1024*1024:raise ValueError('snapshot_too_large')
            with self.db() as c:
                c.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?,?,?,?,?,?)',
                    (*key,raw,payload['fetched_at'],attempt,None))
                self.prune_cache(c,key)
        except Exception as exc:
            with self.db() as c:
                c.execute('INSERT INTO cache(channel,start,end,video,last_attempt,error) VALUES(?,?,?,?,?,?) ON CONFLICT(channel,start,end,video) DO UPDATE SET last_attempt=excluded.last_attempt,error=excluded.error',
                    (*key,attempt,public_error(exc)))
                self.prune_cache(c,key)
    def prune_cache(self,c,keep):
        rows=c.execute('SELECT rowid,channel,start,end,video,coalesce(length(CAST(payload AS BLOB)),0) AS bytes FROM cache ORDER BY last_attempt ASC,rowid ASC').fetchall()
        size=sum(r['bytes'] for r in rows);count=len(rows)
        for row in rows:
            if size<=self.CACHE_MAX_BYTES and count<=self.CACHE_MAX_ENTRIES:break
            if tuple(row[k] for k in ('channel','start','end','video'))==keep:continue
            c.execute('DELETE FROM cache WHERE rowid=?',(row['rowid'],))
            size-=row['bytes'];count-=1
    def finance(self,start,end):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT * FROM finance WHERE date>=? AND date<=? ORDER BY date DESC,created DESC',(start,end))]
    def save_finance(self,data):
        channel=data.get('channel') or None
        if channel:self.known(channel)
        kind=data.get('kind');currency=data.get('currency');status=data.get('status')
        if kind not in {'sponsorship','payout','expense'} or currency not in CURRENCIES or status not in {'planned','received'}:raise ValueError('invalid_finance')
        date=dt.date.fromisoformat(str(data.get('date',''))).isoformat()
        try:
            amount=Decimal(str(data.get('amount','')))
            if not amount.is_finite() or amount<=0 or amount>Decimal('100000000') or amount.as_tuple().exponent<-2:raise ValueError('invalid_amount')
            minor=int(amount*100)
        except (InvalidOperation,ValueError):raise ValueError('invalid_amount')
        note=str(data.get('note','')).strip()
        if len(note)>300:raise ValueError('invalid_note')
        ident=data.get('id') or secrets.token_hex(12)
        if not re.fullmatch('[a-f0-9]{24}',ident):raise ValueError('invalid_finance')
        with self.db() as c:
            old=c.execute('SELECT created FROM finance WHERE id=?',(ident,)).fetchone()
            c.execute('INSERT OR REPLACE INTO finance VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (ident,channel,kind,date,minor,currency,status,note,old['created'] if old else stamp(),stamp()))
        return {'id':ident}
    def delete_finance(self,ident):
        if not re.fullmatch('[a-f0-9]{24}',ident):raise ValueError('invalid_finance')
        with self.db() as c:c.execute('DELETE FROM finance WHERE id=?',(ident,))
        return {'deleted':True}
