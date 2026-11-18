"""Authenticated web routes for the creator dashboard."""
import hmac
from functools import wraps
from flask import request,session,abort,jsonify,render_template
from core import PREFIX
from dashboard_data import Dashboard,period

def register(app,store,stop):
    engine=Dashboard(store,stop)
    app.extensions['dashboard_data']=engine
    def auth(fn):
        @wraps(fn)
        def run(*args,**kwargs):
            owner=store.session(session.get('owner'))
            if not owner:return jsonify(error='login_required'),401
            if request.method=='POST' and not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),owner['csrf']):
                return jsonify(error='csrf_required'),403
            try:return fn(*args,**kwargs)
            except (ValueError,TypeError,KeyError):
                return jsonify(error='invalid_request'),400
        return run
    def dates():return period(request.args.get('start'),request.args.get('end'))
    @app.get(PREFIX+'/api/dashboard')
    @auth
    def data():return engine.snapshot(*dates())
    @app.post(PREFIX+'/api/refresh')
    @auth
    def refresh():
        body=request.get_json(silent=True) or {}
        start,end=period(body.get('start'),body.get('end'))
        channels=[body['channel']] if body.get('channel') else [x['id'] for x in store.channels()]
        if not channels:return {'queued':0}
        return engine.enqueue(channels,start,end,body.get('video','')),202
    @app.get(PREFIX+'/api/video/<channel>/<video>')
    @auth
    def video_data(channel,video):return engine.detail(channel,video,*dates())
    @app.post(PREFIX+'/api/finance')
    @auth
    def finance_save():return engine.save_finance(request.get_json(silent=True) or {}),201
    @app.post(PREFIX+'/api/finance/<ident>/delete')
    @auth
    def finance_delete(ident):return engine.delete_finance(ident)
    @app.get(PREFIX+'/dashboard')
    def deep_link():
        owner=store.session(session.get('owner'))
        if not owner:
            from flask import redirect
            return redirect(PREFIX+'/')
        return page(owner)
    def page(owner):
        return render_template('dashboard.html',csrf=owner['csrf'],channels=store.channels(),prefix=PREFIX,ready=store.configured())
    return page
