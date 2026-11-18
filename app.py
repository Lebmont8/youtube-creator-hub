"""Single-owner creator dashboard and private uploader. Served by Waitress."""
import os,sys,json,time,secrets,hmac,threading,signal,fcntl,logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from flask import Flask,request,session,redirect,make_response,abort,render_template_string
from core import Store,ROOT,CODE_ROOT,PREFIX,ORIGIN,digest,stamp
import google_api
def make_app(store=None,start_worker=False):
    store=store or Store();app=Flask(__name__,static_url_path=PREFIX+'/assets',static_folder='dashboard_assets',template_folder='dashboard_templates')
    key=store.setting('flask_key')
    if not key:key=secrets.token_hex(32);store.set_setting('flask_key',key)
    app.config.update(SECRET_KEY=key,MAX_CONTENT_LENGTH=65536,SESSION_COOKIE_NAME='youtube_hub_owner',
      SESSION_COOKIE_PATH=PREFIX,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SECURE=True,SESSION_COOKIE_SAMESITE='Lax',
      PREFERRED_URL_SCHEME='https',PROPAGATE_EXCEPTIONS=False)
    app.extensions['yt_store']=store
    def owner():
        s=store.session(session.get('owner'))
        if not s:abort(401)
        return s
    @app.before_request
    def guards():
        if request.path!=PREFIX+'/health':
            host=request.host.split(':')[0]
            if host not in {'creator.example.invalid','localhost','127.0.0.1'}:abort(400)
        if request.method=='POST':
            origin=request.headers.get('Origin')
            if origin!=ORIGIN:abort(403)
        if (store.root/'STOP').exists() and request.path!=PREFIX+'/health':abort(503)
    @app.after_request
    def headers(resp):
        resp.headers['Cache-Control']='no-store, max-age=0'
        resp.headers['Pragma']='no-cache';resp.headers['X-Content-Type-Options']='nosniff'
        # Chromium sends Origin:null on form POSTs from no-referrer documents.
        # Only the home page renders owner login/connect/logout forms; keep
        # OAuth callbacks and every other response on the stricter policy.
        resp.headers['Referrer-Policy']=(
            'strict-origin-when-cross-origin' if request.path in {PREFIX+'/',PREFIX+'/dashboard'} and resp.mimetype=='text/html'
            else 'no-referrer'
        )
        # Chromium checks form-action again on the OAuth redirect.
        # Only Google Accounts is an external destination, never arbitrary hosts.
        form_action="'self' https://accounts.google.com" if request.path in {PREFIX+'/',PREFIX+'/dashboard',PREFIX+'/connect'} else "'self'"
        resp.headers['Content-Security-Policy']=f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://i.ytimg.com https://i9.ytimg.com; frame-src https://www.youtube-nocookie.com; connect-src 'self'; form-action {form_action}; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
        return resp
    @app.errorhandler(401)
    def need_login(e):return redirect(PREFIX+'/')
    @app.errorhandler(Exception)
    def errors(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e,HTTPException):return e
        # Never log arbitrary exception text, request URLs, bodies or tokens.
        allowed={'oauth_state_invalid','oauth_state_invalid_or_expired',
            'oauth_not_configured','upload_or_read_permission_missing',
            'select_exactly_one_youtube_channel','unexpected_channel_id'}
        reason=e.args[0] if type(e) is ValueError and len(e.args)==1 and isinstance(e.args[0],str) and e.args[0] in allowed else 'unclassified'
        location='unknown'
        tb=e.__traceback__
        while tb is not None:
            filename=Path(tb.tb_frame.f_code.co_filename)
            if filename.parent.resolve()==CODE_ROOT and filename.name in {'app.py','core.py','google_api.py'}:
                location=f'{filename.name}:{tb.tb_lineno}'
            tb=tb.tb_next
        app.logger.error('request_failed type=%s reason=%s at=%s',type(e).__name__,reason,location)
        return 'Ошибка операции. Повтори подключение с главной страницы; подробности доступны оператору без секретных данных.',400
    @app.get(PREFIX+'/health')
    def health():return {'status':'ok'}
    @app.get(PREFIX+'/')
    def home():
        s=store.session(session.get('owner'))
        if not s:
            challenge=secrets.token_urlsafe(24);session['login_csrf']=challenge
            return render_template_string(TEMPLATE,logged=False,csrf=challenge,ready=store.configured(),channels=[])
        return app.extensions['dashboard_page'](s)
    @app.post(PREFIX+'/login')
    def login():
        expected=session.pop('login_csrf',None)
        if not expected or not hmac.compare_digest(request.form.get('csrf',''),expected):abort(403)
        with store.db() as c:
            c.execute('DELETE FROM login_attempts WHERE at<?',(stamp()-900,))
            count=c.execute('SELECT COUNT(*) FROM login_attempts').fetchone()[0]
            if count>=10:return 'Слишком много попыток. Подожди 15 минут.',429
            c.execute('INSERT INTO login_attempts VALUES(?)',(stamp(),))
        if not store.password_ok(request.form.get('password','')):return 'Неверный пароль. Вернись на страницу входа.',403
        token,_=store.create_session();session.clear();session['owner']=token
        return redirect(PREFIX+'/')
    @app.post(PREFIX+'/logout')
    def logout():
        s=owner()
        if not hmac.compare_digest(request.form.get('csrf',''),s['csrf']):abort(403)
        with store.db() as c:c.execute('DELETE FROM sessions WHERE id=?',(s['id'],))
        session.clear();return redirect(PREFIX+'/')
    @app.post(PREFIX+'/connect')
    def connect():
        s=owner()
        if not hmac.compare_digest(request.form.get('csrf',''),s['csrf']):abort(403)
        if not store.configured():return 'Реквизиты Google ещё не подключены.',503
        state,verifier=store.oauth_state(s['id'])
        return redirect(google_api.authorization(store,state,verifier))
    @app.get(PREFIX+'/oauth/callback')
    def callback():
        s=owner()
        verifier=store.consume_state(request.args.get('state',''),s['id'])
        if request.args.get('error'):return 'Доступ не выдан. Можно снова нажать «Подключить канал».',400
        code=request.args.get('code','')
        if not code or len(code)>8192:abort(400)
        channel_id=google_api.finish_authorization(store,request.args['state'],verifier,code)
        return redirect(PREFIX+'/')
    stop=threading.Event();app.extensions['stop']=stop
    from dashboard_web import register
    app.extensions['dashboard_page']=register(app,store,stop)
    if start_worker:
        store.recover_jobs()
        def loop():
            while not stop.wait(3):
                if (store.root/'STOP').exists():continue
                job=store.claim()
                if job:
                    try:google_api.upload(store,job,stop)
                    except Exception as e:
                        app.logger.error('worker_failed type=%s',type(e).__name__)
                        store.update_job(job['id'],status='needs_reconcile',error=type(e).__name__)
        threading.Thread(target=loop,name='youtube-upload-worker',daemon=True).start()
    return app
TEMPLATE="""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Creator Hub — вход в кабинет</title><link rel="icon" type="image/svg+xml" href="/youtube-hub/assets/favicon.svg?v=2438"><link rel="stylesheet" href="/youtube-hub/assets/dashboard.css"><style>body{font:17px "Golos Text",sans-serif;max-width:600px;margin:70px auto;padding:0 24px;color:#f1f1f1;background:#0f0f0f}section{background:#171717;padding:28px;border:1px solid #333;border-radius:12px}button{padding:12px 22px;border:0;border-radius:24px;background:#f1f1f1;color:#111;font:inherit;cursor:pointer}input{font:inherit;padding:12px;width:100%;margin:12px 0 22px;background:#222;color:#eee;border:1px solid #555;border-radius:7px}li{margin:12px 0}small{color:#aaa;font-size:14px}a{color:#8cc8ff}</style>
<h1>Creator Hub</h1><section>
{% if not logged %}<h2>Вход владельца</h2><p>Пароль сохранён на твоём ПК в файле YouTubeHub/owner-login.txt.</p>
<form method="post" action="/youtube-hub/login"><input type="hidden" name="csrf" value="{{csrf}}"><label>Пароль<input type="password" name="password" autocomplete="current-password" required></label><button>Войти</button></form>
{% else %}<h2>Подключённые каналы</h2><ul>{% for c in channels %}<li><b>{{c.name}}</b><br><small>{{c.id}} · {{c.status}}</small></li>{% else %}<li>Пока нет подключённых каналов.</li>{% endfor %}</ul>
{% if ready %}<form method="post" action="/youtube-hub/connect"><input type="hidden" name="csrf" value="{{csrf}}"><button>Подключить канал через Google</button></form>{% else %}<p>Подготовка подключения ещё не завершена.</p>{% endif %}
<p>При подключении выбери нужный канал и проверь разрешения. Повтори для каждого канала.</p>
<form method="post" action="/youtube-hub/logout"><input type="hidden" name="csrf" value="{{csrf}}"><button>Выйти</button></form>
{% endif %}</section><p><small>Личный кабинет каналов: статистика, видео и монетизация. Вход и подключение канала не публикуют видео.</small></p></html>"""
def main():
    os.umask(0o077);root=ROOT;root.mkdir(exist_ok=True);(root/'logs').mkdir(exist_ok=True)
    lock=open(root/'private/service.lock','a+')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    from core import private_write
    private_write(root/'private/process.json',json.dumps({'pid':os.getpid(),'entry':str(Path(__file__).resolve())}).encode())
    log=logging.getLogger();log.setLevel(logging.INFO)
    h=RotatingFileHandler(root/'logs/service.log',maxBytes=5*1024**2,backupCount=2)
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));log.addHandler(h)
    app=make_app(start_worker=True)
    app.logger.info('request_diagnostics version=2413 reason_allowlist=1 raw_exception=0')
    app.logger.info('creator_dashboard version=2428 readonly_youtube=1')
    app.logger.info('creator_ui version=2432 theme=black font=golos svg_icons=1')
    from waitress import create_server
    server=create_server(app,host='127.0.0.1',port=58120,threads=4,channel_timeout=45,max_request_body_size=65536,clear_untrusted_proxy_headers=True)
    def stop(signum,frame):app.extensions['stop'].set();server.close()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    server.run()
if __name__=='__main__':main()
