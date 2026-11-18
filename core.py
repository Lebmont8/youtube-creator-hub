"""Private state and admission rules for the single-owner YouTube uploader."""
import os, json, base64, secrets, hashlib, hmac, sqlite3, time, shutil
from pathlib import Path
from contextlib import contextmanager
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
CODE_ROOT=Path(__file__).resolve().parent
ROOT=Path(os.environ.get('YOUTUBE_HUB_HOME',CODE_ROOT/'.state'))
PREFIX='/youtube-hub'
PROJECT=os.environ.get('YOUTUBE_GOOGLE_PROJECT','your-google-project-id')
ORIGIN=os.environ.get('YOUTUBE_HUB_ORIGIN','https://localhost:8443').rstrip('/')
SCOPES=['https://www.googleapis.com/auth/youtube.upload','https://www.googleapis.com/auth/youtube.readonly',
        'https://www.googleapis.com/auth/yt-analytics.readonly','https://www.googleapis.com/auth/yt-analytics-monetary.readonly']
def stamp(): return int(time.time())
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def hash_file(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def private_write(path,data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path);os.chmod(path,0o600)
class Store:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.private=self.root/'private'; self.private.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(self.private,0o700)
        self.dbpath=self.private/'state.sqlite3'
        keypath=self.private/'vault.key'
        if not keypath.exists():
            try:
                fd=os.open(keypath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as f:f.write(secrets.token_bytes(32))
            except FileExistsError:pass
        self.key=keypath.read_bytes()
        if len(self.key)!=32:raise RuntimeError('invalid_vault_key')
        os.chmod(keypath,0o600)
        with self.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts(at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS oauth_states(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,verifier TEXT NOT NULL,expires INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,name TEXT NOT NULL,token TEXT NOT NULL,scopes TEXT NOT NULL,status TEXT NOT NULL,updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,channel_id TEXT NOT NULL,file TEXT NOT NULL,file_sha TEXT NOT NULL,size INTEGER NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,privacy TEXT NOT NULL,status TEXT NOT NULL,due INTEGER NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL,remote_id TEXT,resume TEXT,progress INTEGER NOT NULL DEFAULT 0,error TEXT,UNIQUE(channel_id,file_sha));
            """)
        os.chmod(self.dbpath,0o600)
    @contextmanager
    def db(self):
        c=sqlite3.connect(self.dbpath,timeout=15);c.row_factory=sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA busy_timeout=15000')
        try:yield c;c.commit()
        except Exception:c.rollback();raise
        finally:c.close()
    def seal(self,obj,aad):
        nonce=secrets.token_bytes(12);raw=json.dumps(obj,separators=(',',':')).encode()
        return base64.urlsafe_b64encode(nonce+AESGCM(self.key).encrypt(nonce,raw,aad.encode())).decode()
    def unseal(self,value,aad):
        raw=base64.urlsafe_b64decode(value)
        return json.loads(AESGCM(self.key).decrypt(raw[:12],raw[12:],aad.encode()))
    def setting(self,name,default=None):
        with self.db() as c:r=c.execute('SELECT v FROM settings WHERE k=?',(name,)).fetchone()
        return self.unseal(r['v'],'setting:'+name) if r else default
    def set_setting(self,name,value):
        with self.db() as c:c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(name,self.seal(value,'setting:'+name)))
    def configured(self):return bool(self.setting('oauth'))
    def set_password(self,password):
        if len(password)<24:raise ValueError('owner_password_too_short')
        salt=secrets.token_bytes(16); hashed=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,600000)
        self.set_setting('owner_password',{'salt':salt.hex(),'hash':hashed.hex()})
    def password_ok(self,password):
        if not isinstance(password,str) or len(password)>512:return False
        cfg=self.setting('owner_password')
        if not cfg:return False
        candidate=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(cfg['salt']),600000)
        return hmac.compare_digest(candidate,bytes.fromhex(cfg['hash']))
    def session(self,token):
        if not token:return None
        with self.db() as c:r=c.execute('SELECT * FROM sessions WHERE id=? AND expires>?',(digest(token),stamp())).fetchone()
        return dict(r) if r else None
    def create_session(self):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.db() as c:
            c.execute('DELETE FROM sessions WHERE expires<=?',(stamp(),))
            c.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),csrf,stamp()+12*3600))
        return token,csrf
    def oauth_state(self,session_id):
        state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
        with self.db() as c:
            c.execute('DELETE FROM oauth_states WHERE expires<?',(stamp(),))
            c.execute('INSERT INTO oauth_states(id,session_id,verifier,expires) VALUES(?,?,?,?)',
                      (digest(state),session_id,self.seal(verifier,'oauth:'+digest(state)),stamp()+600))
        return state,verifier
    def consume_state(self,state,session_id):
        if not state or len(state)>512:raise ValueError('oauth_state_invalid')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM oauth_states WHERE id=?',(digest(state),)).fetchone()
            if not row or row['used'] or row['expires']<stamp() or not hmac.compare_digest(row['session_id'],session_id):
                raise ValueError('oauth_state_invalid_or_expired')
            c.execute('UPDATE oauth_states SET used=1 WHERE id=?',(digest(state),))
        return self.unseal(row['verifier'],'oauth:'+digest(state))
    def channels(self):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT id,name,scopes,status,updated FROM channels ORDER BY name')]
    def channel_credentials(self,channel_id):
        with self.db() as c:r=c.execute('SELECT * FROM channels WHERE id=?',(channel_id,)).fetchone()
        if not r:raise ValueError('channel_not_connected')
        return self.unseal(r['token'],'channel:'+channel_id)
    def save_channel(self,channel_id,name,credentials,scopes):
        if not credentials.get('refresh_token'):
            try:credentials['refresh_token']=self.channel_credentials(channel_id).get('refresh_token')
            except ValueError:pass
        if not credentials.get('refresh_token'):raise ValueError('offline_access_missing_reconnect')
        with self.db() as c:c.execute('INSERT OR REPLACE INTO channels VALUES(?,?,?,?,?,?)',
           (channel_id,name,self.seal(credentials,'channel:'+channel_id),json.dumps(sorted(scopes)),'connected',stamp()))
    def jobs(self):
        with self.db() as c:return [dict(r) for r in c.execute('SELECT id,channel_id,title,privacy,status,due,remote_id,progress,error,updated FROM jobs ORDER BY created DESC LIMIT 100')]
    def job(self,job_id):
        with self.db() as c:r=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        return dict(r) if r else None
    def update_job(self,job_id,**fields):
        allowed={'status','remote_id','resume','progress','error'}
        if set(fields)-allowed:raise ValueError('invalid_job_fields')
        fields['updated']=stamp()
        with self.db() as c:c.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),job_id))
    def recover_jobs(self):
        with self.db() as c:c.execute("UPDATE jobs SET status='needs_reconcile',error='worker_interrupted_verify_existing_session',updated=? WHERE status='uploading'",(stamp(),))
    def claim(self):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT * FROM jobs WHERE status='queued' AND due<=? ORDER BY due,created LIMIT 1",(stamp(),)).fetchone()
            if row:c.execute("UPDATE jobs SET status='uploading',updated=? WHERE id=?",(stamp(),row['id']))
        return dict(row) if row else None
    def enqueue(self,source,channel_id,title,description='',due=None,privacy='private'):
        if privacy!='private':raise ValueError('pilot_accepts_private_only')
        if not 1<=len(title)<=100 or len(description)>5000:raise ValueError('invalid_metadata')
        self.channel_credentials(channel_id)
        source=Path(source).resolve(strict=True)
        allowed=[(self.root/'inbox').resolve(),(self.root/'inbox').resolve()]
        if not any(source.is_relative_to(a) for a in allowed) or not source.is_file():
            raise ValueError('video_outside_allowed_roots')
        if source.suffix.lower() not in {'.mp4','.mov','.mkv','.webm'}:raise ValueError('unsupported_video_type')
        size=source.stat().st_size
        if size<1:raise ValueError('empty_video')
        sha=hash_file(source)
        with self.db() as c:
            old=c.execute('SELECT id FROM jobs WHERE channel_id=? AND file_sha=?',(channel_id,sha)).fetchone()
            if old:return {'job_id':old['id'],'already_present':True}
        target=self.root/'inbox'/(sha+source.suffix.lower())
        if target!=source and not target.exists():
            import importlib.util
            spec=importlib.util.spec_from_file_location('yt_capacity',CODE_ROOT/'storage_gate.py')
            gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)
            capacity=gate.evaluate({'writes':[{'destination':str(self.root),'bytes':size+64*1024**2,'files':20}]})
            if not capacity['ok']:raise ValueError('storage_capacity_denied')
            temp=target.with_suffix(target.suffix+'.part')
            try:
                with source.open('rb') as a,temp.open('xb') as b:shutil.copyfileobj(a,b,1024*1024);b.flush();os.fsync(b.fileno())
                if hash_file(temp)!=sha:raise ValueError('source_changed_during_import')
                os.chmod(temp,0o600);os.replace(temp,target)
            except Exception:
                # Preserve failed partials for explicit operator inspection; never retry or delete source data.
                raise
        import subprocess
        result=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=codec_type','-of','json',str(target)],capture_output=True,text=True,timeout=30)
        if result.returncode or not json.loads(result.stdout).get('streams'):raise ValueError('not_a_decodable_video')
        job_id=secrets.token_hex(12)
        with self.db() as c:c.execute('INSERT INTO jobs(id,channel_id,file,file_sha,size,title,description,privacy,status,due,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (job_id,channel_id,str(target),sha,size,title,description,privacy,'queued',due or stamp(),stamp(),stamp()))
        return {'job_id':job_id,'already_present':False}
