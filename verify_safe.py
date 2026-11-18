#!/usr/bin/python3
"""Acceptance checks that keep PandaFan running and never enter lock mode."""
import importlib.util
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('runtime',ROOT/'verify_runtime.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)
results=[]

def result(stage,**values):
    record={'stage':stage,**values};results.append(record)
    print(json.dumps(record,ensure_ascii=False),flush=True)

def ts_status():
    p=subprocess.run(['/usr/local/bin/tailscale','status','--json'],capture_output=True,text=True,timeout=10)
    return json.loads(p.stdout)

def peer_probe(peer):
    p=subprocess.run(['/usr/local/bin/tailscale','ping','--tsmp','--c','1','--until-direct=false','--timeout','3s',peer],capture_output=True,text=True,timeout=7)
    system=subprocess.run(['/sbin/ping','-c','1','-W','2000',peer],capture_output=True,timeout=4)
    return {'encrypted_mesh_ping':p.returncode==0,'host_tailnet_ping':system.returncode==0}

def websites():
    values=[R.probe('GitHubAPI','https://api.github.com/zen'),R.probe('Baidu','https://www.baidu.com/')]
    for i,value in enumerate(values):
        if value['exit']:
            values[i]=R.probe(value['target'],'https://api.github.com/zen' if i==0 else 'https://www.baidu.com/')
    return values

def main():
    restore_tailscale=False
    try:
        initial=ts_status();restore_tailscale=initial.get('BackendState')=='Stopped'
        if restore_tailscale:
            p=subprocess.run(['/usr/local/bin/tailscale','up'],capture_output=True,text=True,timeout=15)
            assert p.returncode==0,'Tailscale requires user action'
        peers=[]
        for _ in range(15):
            peers=[x for x in (ts_status().get('Peer')or{}).values() if x.get('Online')]
            if peers:break
            time.sleep(0.5)
        assert peers,'No online tailnet peer is available for acceptance'
        peer=next(a for a in peers[0]['TailscaleIPs'] if ':' not in a)
        before_peer=peer_probe(peer)
        before_sites=websites();before_dns=R.physical_dns()
        result('baseline',websites=before_sites,tailnet=before_peer,physical_dns_reply=before_dns)
        assert before_peer['encrypted_mesh_ping'],'Tailnet baseline failed'
        assert all(x['exit']==0 for x in before_sites),'Website baseline failed'
        assert before_dns,'Physical-interface baseline failed'
        R.action('enable');state=R.wait_mode('connected')
        current_peer=peer_probe(peer);current_sites=websites();current_dns=R.physical_dns()
        result('protected',status=state,websites=current_sites,tailnet=current_peer,physical_dns_reply=current_dns)
        assert all(x['exit']==0 for x in current_sites),'Protected websites failed'
        assert not current_dns,'Direct public DNS bypass succeeded'
        assert current_peer['encrypted_mesh_ping'],'Encrypted Tailscale transport failed'
        if before_peer['host_tailnet_ping']:
            assert current_peer['host_tailnet_ping'],'Host-stack tailnet routing failed'
        recovery=Path.home()/'Applications/PandaFan 一键恢复.app'
        subprocess.run(['/usr/bin/open','-a',str(recovery)],check=True)
        state=R.wait_mode('off');dns=R.physical_dns()
        result('one_click_restore',status=state,physical_dns_reply=dns)
        assert dns,'Recovery did not restore the physical route'
        R.action('enable');R.wait_mode('connected')
        if restore_tailscale:
            subprocess.run(['/usr/local/bin/tailscale','down'],capture_output=True,check=True,timeout=10)
            restore_tailscale=False
        sites=websites();assert all(x['exit']==0 for x in sites),'Final website check failed'
        R.action('commit')
        result('committed',status=R.action('status'),websites=sites,tailscale_backend=ts_status().get('BackendState'))
    except BaseException as e:
        try:R.action('restore')
        except Exception:pass
        result('failed',reason=str(e))
        raise
    finally:
        if restore_tailscale:
            subprocess.run(['/usr/local/bin/tailscale','down'],capture_output=True,timeout=10)
        (ROOT/'安全验收结果.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':main()
