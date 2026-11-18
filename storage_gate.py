#!/usr/bin/env python3
"""Fail closed before bulk writes. Budget simultaneous allocations by real filesystem."""
import argparse,json,os,pathlib,sys
GIB=1024**3

def evaluate(plan, stat=os.stat, statvfs=os.statvfs):
    groups={}
    for item in plan['writes']:
        p=pathlib.Path(item['destination']).resolve(strict=True)
        expected=item.get('mount')
        if expected:
            m=pathlib.Path(expected).resolve(strict=True)
            if not os.path.ismount(m) or not p.is_relative_to(m):
                raise ValueError('Required destination mount is absent or mismatched')
            if item.get('device') and os.stat(m).st_dev!=os.stat(item['device']).st_rdev:
                raise ValueError('Required destination device mismatch')
        s=stat(p);v=statvfs(p)
        k=str(s.st_dev)
        g=groups.setdefault(k,{'paths':[],'available':v.f_bavail*v.f_frsize,'total':v.f_blocks*v.f_frsize,'freeInodes':v.f_favail,'bytes':0,'files':0})
        b=item['bytes'];n=item.get('files',0)
        if not isinstance(b,int) or b<0 or not isinstance(n,int) or n<0:raise ValueError('Invalid allocation budget')
        g['paths'].append(str(p));g['bytes']+=b;g['files']+=n
    for g in groups.values():
        g['reserve']=max(plan.get('reserveBytes',5*GIB),int(g['total']*plan.get('reserveFraction',.10)))
        g['required']=g['bytes']+g['reserve']
        g['ok']=g['available']>=g['required'] and g['freeInodes']>=g['files']+10000
    return {'ok':all(g['ok'] for g in groups.values()),'filesystems':list(groups.values())}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--plan',required=True);a=ap.parse_args()
    try:
        r=evaluate(json.loads(pathlib.Path(a.plan).read_text()))
        print(json.dumps(r));return 0 if r['ok'] else 78
    except Exception as e:
        print(json.dumps({'ok':False,'error':str(e)}));return 78
if __name__=='__main__':sys.exit(main())
