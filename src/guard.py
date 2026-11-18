#!/usr/bin/python3 -I
"""PandaFan fail-closed egress guard. All privileged paths are fixed.

Uses the audited low-level process/pf helpers from madgiraffe1's MIT project.
No subscription, proxy configuration, account data, or traffic is collected.
"""
import ctypes
import hashlib
import ipaddress
import json
import os
import pwd
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

BASE = Path('/Library/Application Support/PandaFanEgressGuard')
DB = Path('/private/var/db/pandafan-egress-guard')
SOCK = '/var/run/local.pandafan-egress-guard.sock'
ANCHOR = 'com.apple/000.pandafan-egress-guard'
LABEL = 'local.pandafan-egress-guard'
_owner_file = BASE / 'owner.json'
if _owner_file.exists():
    _owner_stat = _owner_file.stat()
    if _owner_stat.st_uid != 0 or _owner_stat.st_mode & 0o022:
        raise RuntimeError('安装身份文件权限不安全。')
    OWNER = int(json.loads(_owner_file.read_text())['uid'])
else:
    OWNER = os.stat('/dev/console').st_uid or os.getuid()
GUI = '/Applications/PandaFan.app/Contents/MacOS/PandaFan'
CORE = str(Path(pwd.getpwuid(OWNER).pw_dir) / 'Library/Application Support/PandaFan/pandacore')
GROUP = '_pandafanguard'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import upstream_guard as U

U.GUI, U.CORE, U.GROUP, U.DB, U.ANCHOR = GUI, CORE, GROUP, DB, ANCHOR
U.BOOT_LABEL = LABEL

class GuardError(Exception):
    pass

def require(value, message):
    if not value:
        raise GuardError(message)

def run(args, check=True, data=None, timeout=15):
    p = subprocess.run(args, input=data, capture_output=True, text=True,
                       env=U.ENV, cwd='/', timeout=timeout)
    if check and p.returncode:
        raise GuardError(Path(args[0]).name + ' 操作失败；未输出可能含地址的原始信息。')
    return p

def read_json(name, fallback=None):
    p = DB / name
    return json.loads(p.read_text()) if p.exists() else fallback

def save_json(name, value):
    U.json_save(name, value)

def owned_tunnel(pid, rows=None):
    rows = rows if rows is not None else run(['/usr/sbin/netstat','-anv','-f','system']).stdout.splitlines()
    units = set()
    for line in rows:
        f = line.split()
        if (len(f) >= 6 and f[0] == 'kctl' and f[-1] == 'com.apple.net.utun_control'
                and any(x.endswith(':' + str(pid)) for x in f) and f[-3].isdigit()):
            units.add(int(f[-3]))
    require(len(units) == 1 and min(units) > 0, '无法确认核心持有的唯一 TUN。')
    nic = 'utun' + str(next(iter(units)) - 1)
    cfg = run(['/sbin/ifconfig', nic]).stdout
    require(re.search(r'flags=\d+<[^>]*\bUP\b', cfg), 'TUN 未启用。')
    sources = []
    for family, raw in re.findall(r'^\s*(inet6?)\s+(\S+)', cfg, re.M):
        addr = ipaddress.ip_address(raw.split('%')[0])
        if not (addr.is_link_local or addr.is_loopback or addr.is_unspecified):
            sources.append((family, str(addr)))
    require(sources, 'TUN 没有可用源地址。')
    return nic, tuple(sorted(set(sources)))

U.tunnel = owned_tunnel

LOCAL_RULES = '''pass out quick on lo0 all flags any no state label "pfg-local"
pass out quick inet to { 10/8, 172.16/12, 192.168/16, 169.254/16 } flags any no state label "pfg-lan4"
pass out quick inet to 224.0.0.0/24 flags any no state label "pfg-link4"
pass out quick inet proto udp from any port 68 to 255.255.255.255 port 67 no state label "pfg-dhcp"
pass out quick inet6 to { fc00::/7, fe80::/10, ff01::/16, ff02::/16 } flags any no state label "pfg-lan6"
'''

def endpoint(raw):
    # lsof -nP numeric endpoint, including bracketed IPv6 and wildcards.
    if raw.startswith('['):
        m = re.fullmatch(r'\[([^]]+)\]:(\d+|\*)', raw)
        require(m, '无法解析套接字地址。')
        host, port = m.groups()
    else:
        host, port = raw.rsplit(':', 1)
    if host != '*':
        host = str(ipaddress.ip_address(host.split('%')[0]))
    require(port.isdigit() and 0 < int(port) < 65536, '无效端口。')
    return host, int(port)

def socket_rules(text, uid):
    """Permit only sockets observed on authenticated Tailscale processes.

    TCP: exact local port and remote endpoint. UDP: held local port (the
    extension's unconnected WireGuard/STUN socket serves many endpoints).
    No generic root or destination-port-only public exception exists.
    """
    proto = None
    out = set()
    for line in text.splitlines():
        if line.startswith('f'):
            proto = None
        elif line.startswith('P'):
            proto = line[1:].lower()
        elif line.startswith('n') and proto in ('tcp','udp'):
            try:
                value = line[1:]
                local, sep, remote = value.partition('->')
                host, port = endpoint(local)
                if host not in ('*','0.0.0.0','::') and ipaddress.ip_address(host).is_loopback:
                    continue
                families = ['inet','inet6'] if host == '*' else ['inet6' if ':' in host else 'inet']
                if proto == 'tcp' and not sep:
                    continue  # Never turn a TCP listener into a public bypass.
                for family in families:
                    source = 'any' if host in ('*','0.0.0.0','::') else host
                    if sep:
                        dest, dport = endpoint(remote)
                        if dest == '*' or (':' in dest) != (family == 'inet6'):
                            continue
                        target = f'{dest} port {dport}'
                    else:
                        target = 'any'
                    out.add(f'pass out quick {family} proto {proto} from {source} port {port} '
                            f'to {target} user {int(uid)} flags any no state label "pfg-ts-socket"')
            except (ValueError, GuardError):
                continue
    return out

def trusted_tailscale_path(path):
    if path == '/Applications/Tailscale.app/Contents/MacOS/Tailscale':
        return True
    return bool(re.fullmatch(r'/Library/SystemExtensions/[A-Fa-f0-9-]+/'
                            r'io\.tailscale\.ipn\.macsys\.network-extension\.systemextension/'
                            r'Contents/MacOS/io\.tailscale\.ipn\.macsys\.network-extension', path))

_signature_cache = {}
def tailscale_signature(path):
    if not trusted_tailscale_path(path):
        return False
    try:
        info = os.stat(path)
        key = (path, info.st_ino, info.st_mtime_ns, info.st_size)
        if key not in _signature_cache:
            verified = run(['/usr/bin/codesign','--verify','--strict',path], check=False)
            details = run(['/usr/bin/codesign','-dv','--verbose=2',path], check=False)
            _signature_cache[key] = verified.returncode == 0 and 'TeamIdentifier=W5364U7YZB' in details.stderr
        return _signature_cache[key]
    except OSError:
        return False

def tailscale_rules(processes):
    rules, count = set(), 0
    for p in processes:
        if not tailscale_signature(p.path):
            continue
        count += 1
        sockets = run(['/usr/sbin/lsof','-nP','-a','-p',str(p.pid),'-i','-F','fPn'], check=False)
        rules.update(socket_rules(sockets.stdout, p.euid))
        try:
            nic, addresses = owned_tunnel(p.pid)
            for family, addr in addresses:
                dest = 'fd7a:115c:a1e0::/48' if family == 'inet6' else '100.64.0.0/10'
                rules.add(f'pass out quick on {nic} {family} from {addr} to {dest} '
                          'flags any no state label "pfg-tailnet"')
        except (GuardError, OSError, subprocess.SubprocessError):
            pass  # A GUI process need not own a tunnel.
    return sorted(rules), count

def render(gid=None, path=None, ts_rules=()):
    lines = [LOCAL_RULES.rstrip()]
    lines.extend(ts_rules)
    if path:
        _, nic, addresses = path
        require(re.fullmatch(r'utun\d+', nic), '无效 TUN 名称。')
        for family, addr in addresses:
            ipaddress.ip_address(addr)
            lines.append(f'pass out quick on {nic} {family} from {addr} to any flags any no state label "pfg-tun"')
    lines.append('block drop out quick all fragment label "pfg-fragment"')
    if path:
        require(isinstance(gid,int) and gid > 0 and gid not in (20,80), '必须使用专用组。')
        lines.append(f'pass out quick proto {{ tcp, udp }} all user 0 group {gid} flags any no state label "pfg-core"')
    lines.append('block drop out quick all label "pfg-deny"')
    return '\n'.join(lines) + '\n'

def root_state_directory():
    DB.mkdir(mode=0o700, exist_ok=True)
    s = DB.lstat()
    require(stat.S_ISDIR(s.st_mode) and not DB.is_symlink() and s.st_uid == 0, '状态目录身份错误。')
    os.chmod(DB, 0o700)

class Controller:
    def __init__(self):
        self.mutex = threading.RLock()
        self.settings = read_json('settings.json', {'enabled':False})
        self.state = {'mode':'off','enabled':False,'reason':'保护未启用。','tailscale_processes':0}
        self.last_rules = None
        self.last_readback = 0
        self.active_path = None
        self.stable_path = None
        self.stable_since = 0
        self.manual_lock_until = 0
        self.boot = U.boot_id()
        self.pf_record = read_json('pf.json', {})
        if self.pf_record.get('boot') != self.boot:
            self.pf_record = {'boot':self.boot}
        self.stop_event = threading.Event()

    def ensure_pf(self):
        if not self.pf_record.get('token') or not U.pf_on():
            result = run(['/sbin/pfctl','-E'])
            token = re.search(r'Token\s*:\s*(\d+)', result.stdout + result.stderr)
            require(token, '无法取得本工具的防火墙引用。')
            self.pf_record.update(boot=self.boot,token=token[1])
            save_json('pf.json', self.pf_record)

    def install_rules(self, rules, force=False):
        if force or rules != self.last_rules:
            U.pf('-n','-R','-a',ANCHOR,'-f','-',data=rules)
            self.pf_record.update(boot=self.boot,owned=True)
            save_json('pf.json', self.pf_record)
            U.pf('-R','-a',ANCHOR,'-f','-',data=rules)
            self.last_rules = rules
        self.ensure_pf()
        if force or time.monotonic() - self.last_readback > 4:
            live = U.read_rules(ANCHOR)
            require('label "pfg-deny"' in live, '内核阻断规则缺失。')
            require(('label "pfg-core"' in live) == ('label "pfg-core"' in rules), '防火墙规则状态不一致。')
            self.last_readback = time.monotonic()

    def check_layout(self):
        # Uses the upstream's refusal to overwrite other outbound filters/NAT
        # or to ignore pre-existing global PF states.
        U.check_layout()

    def diagnostic(self):
        rows = U.processes()
        guis = [p for p in rows if p.path == GUI]
        cores = [p for p in rows if p.path == CORE]
        report = {'gui_count':len(guis),'core_count':len(cores),
                  'pf_enabled':U.pf_on(),'layout_ok':False,'tunnel_owned':False,
                  'tailscale_processes':sum(trusted_tailscale_path(p.path) for p in rows)}
        try:
            self.check_layout()
            report['layout_ok'] = True
        except U.GuardError as e:
            report['layout_reason'] = str(e)
        if len(cores) == 1:
            report.update(core_uid=cores[0].euid,core_gid=cores[0].egid)
            try:
                owned_tunnel(cores[0].pid)
                report['tunnel_owned'] = True
            except GuardError:
                pass
        return report

    def enable(self):
        require(not U.read_rules(ANCHOR).strip() or self.pf_record.get('owned'),
                '同名 anchor 已被其他配置占用，不会覆盖。')
        self.check_layout()
        gid = U.group_id(create=True)
        # Preflight while networking remains unchanged. An already managed
        # core can enter connected policy directly, with no temporary lock.
        path = U.owned_path(gid)
        time.sleep(1.05)
        require(U.owned_path(gid) == path, '核心或隧道仍在变化；没有启用限制。')
        self.stable_path = path
        self.stable_since = time.monotonic() - 2
        # Persist the enabled state before policy mutation for crash recovery.
        self.settings['enabled'] = True
        save_json('settings.json', self.settings)
        self.active_path = None
        self.last_rules = None
        self.manual_lock_until = 0
        # An independent process restores connectivity if verification cannot
        # finish. Commit removes this deadline only after on-device checks.
        deadline = U.session_clock() + 300
        save_json('trial.json', {'deadline':deadline,'boot':self.boot})
        with open(os.devnull,'rb+') as null:
            subprocess.Popen(['/usr/bin/python3','-I','-B',str(BASE/'guard.py'),'watchdog'],
                             env=U.ENV,cwd='/',start_new_session=True,
                             stdin=null,stdout=null,stderr=null)
        self.state.update(enabled=True,mode='locked',reason='等待受保护的 PandaFan 核心。')
        self.tick(force=True)
        return self.status()

    def restore(self):
        # Set intent first: a KeepAlive restart must never re-enable protection.
        self.settings['enabled'] = False
        save_json('settings.json', self.settings)
        (DB/'trial.json').unlink(missing_ok=True)
        require(self.pf_record.get('owned') or not U.read_rules(ANCHOR).strip(),
                '同名 anchor 未登记为本工具所有，不会清除。')
        U.pf('-a',ANCHOR,'-F','rules')
        token = self.pf_record.get('token')
        if token:
            result = run(['/sbin/pfctl','-X',token],check=False)
            require(result.returncode == 0 or not re.search(r'\b'+token+r'\b',U.pf('-s','References')),
                    '规则已解除，但防火墙引用尚待释放。')
        self.pf_record = {'boot':self.boot}
        save_json('pf.json', self.pf_record)
        self.last_rules = None
        self.active_path = None
        require(not U.read_rules(ANCHOR).strip(), '恢复未完成，请使用紧急恢复入口。')
        self.state.update(mode='off',enabled=False,reason='限制已解除，联网恢复正常。')
        return self.status()

    def start_panda(self, require_enabled=True):
        if require_enabled:
            require(self.settings.get('enabled'), '请先启用保护。')
        require(os.stat('/dev/console').st_uid == OWNER, '请在当前用户登录桌面后重新连接。')
        rows = U.processes()
        require(not any(p.path in (GUI,CORE) for p in rows), '请先从 PandaFan 菜单退出，再点重新连接。')
        gid = U.group_id()
        with open(os.devnull,'rb+') as null:
            subprocess.Popen(['/bin/launchctl','asuser',str(OWNER),'/usr/bin/python3','-I','-B',
                              str(BASE/'guard.py'),'_gui',str(OWNER),str(gid)],
                             env=U.ENV,cwd='/',start_new_session=True,
                             stdin=null,stdout=null,stderr=null)
        return {'ok':True,'message':'PandaFan 已用专用身份启动，正在等待隧道。'}

    def prepare(self):
        U.group_id(create=True)
        def after_quit():
            deadline = time.monotonic()+90
            while time.monotonic()<deadline:
                if not any(p.path in (GUI,CORE) for p in U.processes()):
                    with self.mutex:
                        self.start_panda(require_enabled=False)
                    return
                time.sleep(0.4)
        threading.Thread(target=after_quit,daemon=True).start()
        return {'message':'等待 PandaFan 从自身菜单退出，随后自动以专用身份重新启动；尚未启用防火墙限制。'}

    def tick(self, force=False):
        if not self.settings.get('enabled'):
            return
        rows = U.processes()
        ts, ts_count = tailscale_rules(rows)
        reason = '核心或隧道未就绪；公网已锁定。'
        candidate = None
        try:
            gid = U.group_id()
            candidate = U.owned_path(gid)
            if candidate != self.stable_path:
                self.stable_path, self.stable_since = candidate, time.monotonic()
            # New/replaced core and tunnel must pass two independent samples.
            if time.monotonic() - self.stable_since < 1.0:
                candidate = None
                reason = '正在核对核心与隧道。'
            if time.monotonic() < self.manual_lock_until:
                candidate = None
                reason = '正在验收锁网；可重新启用或一键恢复。'
        except (U.GuardError, GuardError, OSError, subprocess.SubprocessError):
            gid = None
            self.stable_path = None
        rules = render(gid, candidate, ts)
        self.install_rules(rules, force)
        self.active_path = candidate
        self.state.update(mode='connected' if candidate else 'locked',enabled=True,
                          reason='保护生效，智能分流保持原样。' if candidate else reason,
                          tailscale_processes=ts_count,tailscale_rules=len(ts))

    def status(self):
        result = dict(self.state)
        result['service_running'] = True
        result['pf_enabled'] = U.pf_on()
        if self.settings.get('enabled'):
            live = U.read_rules(ANCHOR)
            if not result['pf_enabled'] or 'label "pfg-deny"' not in live:
                result.update(mode='error',reason='保护异常：需要重新检查。')
        return result

    def loop(self):
        while not self.stop_event.is_set():
            try:
                with self.mutex:
                    self.tick()
            except Exception:
                with self.mutex:
                    if self.settings.get('enabled'):
                        try:
                            # Observation failure withdraws every public pass.
                            self.install_rules(render(), force=True)
                            self.state.update(mode='locked',enabled=True,reason='检查异常，已锁住公网；可一键恢复。')
                        except Exception:
                            self.state.update(mode='error',enabled=True,reason='防火墙操作异常，请运行紧急恢复。')
            self.stop_event.wait(0.8)

    def dispatch(self, action):
        with self.mutex:
            if action == 'status': return self.status()
            if action == 'diagnostic': return self.diagnostic()
            if action == 'enable': return self.enable()
            if action == 'restore': return self.restore()
            if action == 'reconnect': return self.start_panda()
            if action == 'prepare': return self.prepare()
            if action == 'commit':
                require(self.state.get('mode') == 'connected', '保护尚未通过连接核对。')
                (DB/'trial.json').unlink(missing_ok=True)
                return {'message':'保护已确认，将持续运行。'}
            if action == 'lock':
                require(self.settings.get('enabled'), '保护尚未启用。')
                self.manual_lock_until = time.monotonic() + 120
                self.install_rules(render(),force=True)
                self.state.update(mode='locked',reason='验收锁网（最多两分钟）；可重新启用或一键恢复。')
                return self.status()
            raise GuardError('不支持的操作。')

def peer_uid(conn):
    uid, gid = ctypes.c_uint(), ctypes.c_uint()
    lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
    if lib.getpeereid(conn.fileno(),ctypes.byref(uid),ctypes.byref(gid)) != 0:
        raise GuardError('无法核对客户端身份。')
    return uid.value

def serve():
    require(os.geteuid() == 0, '服务必须由系统启动。')
    root_state_directory()
    controller = Controller()
    # On service restart, withdraw stale public permissions before observing.
    if controller.settings.get('enabled'):
        controller.install_rules(render(),force=True)
    elif controller.pf_record.get('owned'):
        # Finish an interrupted restore before accepting new UI requests.
        controller.restore()
    if Path(SOCK).exists():
        s = Path(SOCK).lstat()
        require(stat.S_ISSOCK(s.st_mode), '服务入口被其他文件占用。')
        Path(SOCK).unlink()
    listener = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    listener.bind(SOCK)
    os.chown(SOCK,OWNER,0)
    os.chmod(SOCK,0o600)
    listener.listen(8)
    worker = threading.Thread(target=controller.loop,daemon=True)
    worker.start()
    while True:
        conn,_ = listener.accept()
        with conn:
            conn.settimeout(20)
            try:
                require(peer_uid(conn) in (0,OWNER), '客户端身份未获授权。')
                data = b''
                while not data.endswith(b'\n') and len(data) < 1024:
                    part = conn.recv(1024-len(data))
                    if not part: break
                    data += part
                request = json.loads(data.decode())
                require(set(request) == {'action'}, '请求格式错误。')
                answer = controller.dispatch(request['action'])
                response = {'ok':True,'result':answer}
            except (GuardError,U.GuardError) as e:
                response = {'ok':False,'message':str(e)}
            except Exception:
                response = {'ok':False,'message':'操作未完成；未输出原始系统数据。'}
            try:
                conn.sendall(json.dumps(response,ensure_ascii=False).encode()+b'\n')
            except OSError:
                pass

def client(action):
    conn = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    conn.settimeout(35)
    try:
        conn.connect(SOCK)
        conn.sendall(json.dumps({'action':action}).encode()+b'\n')
        data = b''
        while not data.endswith(b'\n') and len(data) < 65536:
            part=conn.recv(65536-len(data))
            if not part: break
            data += part
        return json.loads(data.decode())
    finally:
        conn.close()

def emergency():
    require(os.geteuid() == 0, '紧急恢复需要管理员授权。')
    root_state_directory()
    save_json('settings.json',{'enabled':False})
    run(['/bin/launchctl','bootout','system/'+LABEL],check=False)
    U.pf('-a',ANCHOR,'-F','rules')
    record=read_json('pf.json',{})
    if record.get('boot') == U.boot_id() and record.get('token'):
        run(['/sbin/pfctl','-X',record['token']],check=False)
    require(not U.read_rules(ANCHOR).strip(), '紧急恢复尚未完成。')
    print('PandaFan 保护已解除。其他防火墙规则未改动。')

def watchdog():
    # Separate from the daemon and its locks. This process survives a daemon
    # deadlock or crash, and never re-enables protection.
    require(os.geteuid() == 0, '回退检查必须由系统启动。')
    boot = U.boot_id()
    while True:
        record = read_json('trial.json', {})
        if not record or record.get('boot') != boot:
            return
        if U.session_clock() >= record['deadline']:
            emergency()
            return
        time.sleep(2)

def main():
    action=sys.argv[1] if len(sys.argv)>1 else 'status'
    if action=='serve': serve()
    elif action=='_gui':
        U.run_gui(int(sys.argv[2]),int(sys.argv[3]))
    elif action=='emergency': emergency()
    elif action=='watchdog': watchdog()
    else:
        try: response=client(action)
        except (OSError,ValueError): response={'ok':False,'message':'系统服务尚未启动；请打开保护应用或运行紧急恢复。'}
        print(json.dumps(response,ensure_ascii=False))
        sys.exit(0 if response.get('ok') else 1)

if __name__=='__main__': main()
