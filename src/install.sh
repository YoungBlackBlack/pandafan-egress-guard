#!/bin/sh
set -eu
BASE='/Library/Application Support/PandaFanEgressGuard'
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LABEL='local.pandafan-egress-guard'
PLIST="/Library/LaunchDaemons/$LABEL.plist"
[ "$(id -u)" = 0 ] || exit 1
# This installer is embedded in the app approved through macOS's admin dialog.
[ ! -L "$BASE" ] || exit 1
/usr/bin/install -d -o root -g wheel -m 755 "$BASE"
/bin/launchctl bootout "system/$LABEL" >/dev/null 2>&1 || true
for FILE in guard.py upstream_guard.py; do
    /usr/bin/install -o root -g wheel -m 644 "$SOURCE/$FILE" "$BASE/$FILE"
done
/usr/bin/python3 -I - "$PLIST" "$BASE" <<'PY'
import plistlib,sys,json,os
from pathlib import Path
p=Path(sys.argv[1]); base=sys.argv[2]
uid=os.stat('/dev/console').st_uid
if uid==0:raise SystemExit('请先登录桌面。')
owner=Path(base)/'owner.json'
owner.write_text(json.dumps({'uid':uid})+'\n');owner.chmod(0o644)
data={'Label':'local.pandafan-egress-guard',
      'ProgramArguments':['/usr/bin/python3','-I','-B',base+'/guard.py','serve'],
      'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':3,
      'ProcessType':'Background','Umask':63,
      'StandardOutPath':'/dev/null','StandardErrorPath':'/dev/null'}
p.write_bytes(plistlib.dumps(data));p.chmod(0o644)
PY
/usr/sbin/chown root:wheel "$PLIST"
/usr/sbin/chown root:wheel "$BASE/owner.json"
/bin/launchctl bootstrap system "$PLIST"
