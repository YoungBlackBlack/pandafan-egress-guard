#!/usr/bin/python3
"""Explicit local acceptance test. Produces aggregate results only.

Enables the guard, tests connected/locked/recovered behavior, then leaves a
new five-minute trial active. A failure restores connectivity immediately.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import socket
import struct
import random
import sys
import time

GUARD='/Library/Application Support/PandaFanEgressGuard/guard.py'
RESULTS=[]
ENV={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy')}

def action(name):
    p=subprocess.run(['/usr/bin/python3','-I','-B',GUARD,name],capture_output=True,text=True,timeout=40)
    d=json.loads(p.stdout)
    if not d.get('ok'): raise RuntimeError(d.get('message','guard failed'))
    return d['result']

def wait_mode(mode):
    for _ in range(20):
        d=action('status')
        if d.get('mode')==mode:return d
        time.sleep(0.7)
    raise RuntimeError('guard did not reach '+mode)

def probe(name,url,interface=None):
    args=['/usr/bin/curl','-q','--noproxy','*','--connect-timeout','4','--max-time','6','-sS','-o','/dev/null','-w','%{http_code}',url]
    if interface:args.extend(['--interface',interface])
    start=time.monotonic()
    p=subprocess.run(args,capture_output=True,text=True,env=ENV,timeout=9)
    return {'target':name,'exit':p.returncode,'http':p.stdout.strip()[-3:],'seconds':round(time.monotonic()-start,2)}

def probes():
    targets=[('Google','https://www.google.com/generate_204'),('Baidu','https://www.baidu.com/'),('PublicIPv4','https://1.1.1.1/')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda args:probe(*args),targets))

def physical_dns():
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.settimeout(3)
    sock.setsockopt(socket.IPPROTO_IP,25,struct.pack('I',socket.if_nametoindex('en0')))
    ident=random.randrange(65536)
    query=struct.pack('!HHHHHH',ident,256,1,0,0,0)+b'\x07example\x03com\x00'+struct.pack('!HH',1,1)
    try:
        sock.sendto(query,('223.5.5.5',53));response,_=sock.recvfrom(4096)
        return response[:2]==struct.pack('!H',ident)
    except OSError:return False
    finally:sock.close()

def tailscale_probe():
    p=subprocess.run(['/usr/local/bin/tailscale','netcheck','--format=json'],capture_output=True,text=True,timeout=30)
    d=json.loads(p.stdout)
    p=subprocess.run(['/usr/local/bin/tailscale','status','--json'],capture_output=True,text=True,timeout=15)
    status=json.loads(p.stdout)
    return {'UDP':d.get('UDP'),'IPv4':d.get('IPv4'),
            'relay_regions_reachable':len(d.get('RegionLatency') or {}),
            'backend':status.get('BackendState'),
            'online_peers':sum(bool(x.get('Online')) for x in (status.get('Peer') or {}).values())}

def save(stage,**values):
    entry={'stage':stage,**values};RESULTS.append(entry)
    print(json.dumps(entry,ensure_ascii=False),flush=True)

def main():
    try:
        baseline_dns=physical_dns()
        save('baseline',probes=probes(),physical_dns_reply=baseline_dns,tailscale=tailscale_probe())
        assert baseline_dns,'physical DNS baseline is not reachable'
        action('enable');wait_mode('connected')
        connected=probes();physical=probe('physical','https://1.1.1.1/','en0')
        dns=physical_dns();ts=tailscale_probe()
        save('connected',probes=connected,physical=physical,physical_dns_reply=dns,tailscale=ts)
        assert all(x['exit']==0 for x in connected[:2]),'connected website requests failed'
        assert physical['exit']!=0,'physical internet bypass succeeded'
        assert not dns,'physical UDP DNS bypass succeeded'
        assert ts.get('UDP') is True and ts.get('backend')=='Running','Tailscale transport failed'
        action('lock');wait_mode('locked')
        locked=probes()
        loopback=subprocess.run(['/sbin/ping','-c','1','127.0.0.1'],capture_output=True,timeout=5).returncode==0
        ts=tailscale_probe();dns=physical_dns()
        save('locked',probes=locked,loopback=loopback,physical_dns_reply=dns,tailscale=ts)
        assert all(x['exit']!=0 for x in locked),'public request succeeded while locked'
        assert loopback,'loopback failed'
        assert not dns,'physical UDP DNS bypass succeeded while locked'
        assert ts.get('UDP') is True and ts.get('backend')=='Running','Tailscale stopped while locked'
        # Exercise the actual one-click app, not merely the same Python action.
        subprocess.run(['/usr/bin/open','-a',str(Path.home()/'Applications/PandaFan 一键恢复.app')],check=True)
        wait_mode('off')
        recovered=probes()
        save('restored',probes=recovered,physical_dns_reply=physical_dns(),status=action('status'))
        assert all(x['exit']==0 for x in recovered[:2]),'website requests failed after recovery'
        action('enable');state=wait_mode('connected')
        save('final_trial',status=state)
    except BaseException as e:
        try:action('restore')
        except Exception:pass
        save('failed',reason=str(e))
        raise
    finally:
        destination=Path(__file__).resolve().parent/'验证结果.json'
        destination.write_text(json.dumps(RESULTS,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':
    if '--allow-disruptive-tests' not in sys.argv:
        raise SystemExit('此验收会锁住公网。只在独立终端并准备好恢复入口后，显式传入 --allow-disruptive-tests；远程或 AI 连接依赖 PandaFan 时不要运行。')
    main()
