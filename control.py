"""Operator CLI. Never prints credentials, tokens, resumable URLs or owner password."""
import os,sys,pathlib,json,secrets,base64,hashlib,argparse,subprocess,signal,time
from core import Store,ROOT,PROJECT,ORIGIN,private_write,stamp
def initialize_transport(store):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    private=store.private/'bootstrap_private.pem';public=store.root/'bootstrap_public.pem'
    if not private.exists():
        key=rsa.generate_private_key(public_exponent=65537,key_size=4096)
        private_write(private,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        private_write(public,key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    return {'transport_ready':True,'public_key_file':str(public),'configured':store.configured()}
def import_bootstrap(store,path):
    if store.configured():raise ValueError('already_configured_no_overwrite')
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw=json.loads(pathlib.Path(path).read_text())
    key=serialization.load_pem_private_key((store.private/'bootstrap_private.pem').read_bytes(),password=None)
    aes=key.decrypt(base64.b64decode(raw['wrapped']),padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    nonce=base64.b64decode(raw['nonce']);cipher=base64.b64decode(raw['ciphertext'])
    data=json.loads(AESGCM(aes).decrypt(nonce,cipher,b'YouTubeHub bootstrap v1'))
    web=data['web']
    if web.get('project_id')!=PROJECT:raise ValueError('unexpected_google_project')
    if web.get('token_uri')!='https://oauth2.googleapis.com/token':raise ValueError('unexpected_token_endpoint')
    if web.get('auth_uri') not in {'https://accounts.google.com/o/oauth2/auth','https://accounts.google.com/o/oauth2/v2/auth'}:raise ValueError('unexpected_auth_endpoint')
    if not web.get('client_id','').endswith('.apps.googleusercontent.com') or not web.get('client_secret'):raise ValueError('invalid_oauth_credentials')
    if len(data.get('owner_password',''))<32:raise ValueError('owner_password_too_short')
    store.set_password(data['owner_password']);store.set_setting('oauth',web)
    store.set_setting('origin',ORIGIN)
    return {'configured':True,'project_matches':True,'owner_password_set':True,'secret_values_printed':False}
def main():
    os.umask(0o077)
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='action',required=True)
    sub.add_parser('init-transport')
    p=sub.add_parser('import-bootstrap');p.add_argument('file')
    sub.add_parser('status');sub.add_parser('channels');sub.add_parser('jobs')
    p=sub.add_parser('enqueue');p.add_argument('--channel',required=True);p.add_argument('--file',required=True);p.add_argument('--title',required=True);p.add_argument('--description',default='');p.add_argument('--due',type=int)
    p=sub.add_parser('stats');p.add_argument('--channel',required=True)
    p=sub.add_parser('resume');p.add_argument('job_id')
    sub.add_parser('stop')
    args=ap.parse_args();store=Store()
    if args.action=='init-transport':result=initialize_transport(store)
    elif args.action=='import-bootstrap':result=import_bootstrap(store,args.file)
    elif args.action=='status':
        result={'host':os.uname().nodename,'configured':store.configured(),'origin':ORIGIN,
                'callback':ORIGIN+'/youtube-hub/oauth/callback','channels':store.channels(),'jobs':store.jobs(),'stopped':(ROOT/'STOP').exists(),'mode':'private_upload_pilot'}
    elif args.action=='channels':result=store.channels()
    elif args.action=='jobs':result=store.jobs()
    elif args.action=='enqueue':result=store.enqueue(args.file,args.channel,args.title,args.description,args.due)
    elif args.action=='stats':
        from google_api import channel_stats
        result=channel_stats(store,args.channel)
        private_write(ROOT/'snapshots'/(args.channel+'-'+str(stamp())+'.json'),json.dumps(result,ensure_ascii=False).encode())
    elif args.action=='resume':
        row=store.job(args.job_id)
        if not row or row['status']!='needs_reconcile' or not row['resume']:
            raise ValueError('no_existing_session_to_resume_never_create_duplicate')
        store.update_job(args.job_id,status='queued',error=None);result={'queued_existing_session':True,'job_id':args.job_id}
    elif args.action=='stop':
        private_write(ROOT/'STOP',b'Owner stopped uploader\n');p=ROOT/'private/process.json'
        if p.exists():
            pid=json.loads(p.read_text())['pid']
            cmd=pathlib.Path('/proc')/str(pid)/'cmdline'
            if cmd.exists() and str(ROOT/'app.py').encode() in cmd.read_bytes().split(b'\0'):
                os.kill(pid,signal.SIGTERM)
        result={'stopped':True}
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':
    try:main()
    except Exception as exc:
        from google_api import safe_error
        print(json.dumps({'error':safe_error(exc)},ensure_ascii=False));raise SystemExit(1)
