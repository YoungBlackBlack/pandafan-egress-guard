#!/usr/bin/python3
"""Builds local signed utilities. Does not install or modify networking."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import os

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'src'
identity=os.environ.get('GUARD_SIGN_IDENTITY','-')

for title,recovery in [('PandaFan 断线保护',False),('PandaFan 一键恢复',True)]:
    app=ROOT/(title+'.app')
    contents=app/'Contents'; exe=contents/'MacOS'; resources=contents/'Resources'
    exe.mkdir(parents=True,exist_ok=True);resources.mkdir(exist_ok=True)
    for name in ['guard.py','upstream_guard.py','install.sh']:
        shutil.copy2(SOURCE/name,resources/name)
    metadata={'CFBundleName':title,'CFBundleDisplayName':title,
              'CFBundleIdentifier':'local.pandafan.egressguard'+('.restore' if recovery else '.app'),
              'CFBundleExecutable':'PandaFanGuard','CFBundlePackageType':'APPL',
              'CFBundleShortVersionString':'0.1.1','CFBundleVersion':'2',
              'LSMinimumSystemVersion':'13.0','LSUIElement':True,'GuardRecoveryOnly':recovery,
              'NSHighResolutionCapable':True}
    (contents/'Info.plist').write_bytes(plistlib.dumps(metadata))
    subprocess.run(['/usr/bin/swiftc','-O','-framework','AppKit',str(SOURCE/'GuardApp.swift'),'-o',str(exe/'PandaFanGuard')],check=True)
    subprocess.run(['/usr/bin/codesign','--force','--sign',identity,'--timestamp=none',str(app)],check=True)
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True)
    print('Built:',app.name)
