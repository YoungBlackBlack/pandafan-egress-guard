# PandaFan Egress Guard

PandaFan Egress Guard is a macOS kill switch that blocks apps from falling back to direct internet access when PandaFan's local tunnel goes down or its core stops.

English · [简体中文](README.zh-CN.md)

PandaFan's smart routing continues while PandaFan is connected. Local network traffic and scoped Tailscale traffic remain available under protection. You can restore normal internet access in one click.

This is an independent project with no affiliation to PandaFan or Tailscale.

**Development preview.** The protection policy was previously tested on Apple silicon with PandaFan 3.3.3. This app uses administrator authorization to install its firewall service. Boot-time protection is still unverified.

## Install from source

You need macOS 13 or later, the Xcode Command Line Tools, and PandaFan installed in **Applications**. Sign in to PandaFan and confirm it connects in enhanced mode. Run `xcode-select --install` if you need the command line tools.

1. **Build the apps.** Download and unzip the source. Open Terminal in the extracted folder and run the command below. [Download source ZIP](https://github.com/YoungBlackBlack/pandafan-egress-guard/archive/refs/heads/main.zip)

   ```sh
   /usr/bin/python3 build.py
   ```

2. **Install the service.** Move both apps to your personal Applications folder (`~/Applications`). Open **PandaFan 断线保护**, click the shield in the menu bar and choose **安装保护服务…** (Install protection service). Approve the macOS prompt.
3. **Complete first-time setup.** At the Mac, choose **首次设置…** (First-time setup). If PandaFan is running, quit it from its own menu within 90 seconds as prompted. The guard reopens PandaFan. Connect in enhanced mode and wait for **开启保护** (Enable protection) to appear.
4. **Enable protection.** Choose **开启保护**, check that you can reach the internet, then choose **保持开启** (Keep protection on) within the five-minute trial. If the trial expires, the guard removes its restrictions.

When the shield shows **已保护** (Protected), setup is complete. Choose **功能介绍…** for a quick guide or **修复保护服务…** if the service stops responding.

If an older service is installed, turn off protection and choose **更新保护服务…** (Update protection service) to get the first-time setup menu.

## Recovery

Open **PandaFan 一键恢复.app** or use **关闭保护** in the guard menu. When public networking is locked, that action is labeled **恢复联网（关闭保护）**.

If the local service is unavailable, the recovery application asks for macOS administrator authorization and uses the separate emergency path. The same offline command is:

```sh
sudo /usr/bin/python3 -I -B '/Library/Application Support/PandaFanEgressGuard/guard.py' emergency
```

The root service and its policy persist independently of the menu application. At boot the service reloads an enabled policy, but there is no guarantee covering the first packet during system startup. A PandaFan instance launched by its ordinary login item may lack the dedicated group; in that case protection remains locked until locally restarted through the guard.

## Design

- A root LaunchDaemon manages one dedicated pf anchor, `com.apple/000.pandafan-egress-guard`.
- A dedicated empty group identifies the PandaFan instance started by the guard. Only root sockets carrying that group and the positively identified PandaFan TUN receive ordinary public egress exceptions.
- The guard preserves PandaFan's smart-routing decisions, including DIRECT. It does not force all websites to use a proxy exit IP.
- Core, identity, or tunnel changes withdraw the ordinary public exceptions. LAN and loopback remain available, including DNS sent to LAN resolvers.
- Authenticated Tailscale processes receive exceptions limited to their observed sockets; the identified Tailscale TUN receives tailnet-only exceptions. Arbitrary utun interfaces and root programs are not whitelisted. Tailscale exit nodes are not a public egress exception.
- Restore clears this anchor and releases this tool's own pf reference. It does not globally disable pf, flush other anchors or states, or modify `/etc/pf.conf`.

## Validation

`/usr/bin/python3 -m unittest discover -s tests -v` checks rules and state changes without altering networking.

`verify_safe.py` keeps PandaFan running and tests actual public APIs, physical-interface blocking, Tailscale peer communication, and the recovery application. It requires an already managed PandaFan core and an online tailnet peer. It temporarily starts a stopped Tailscale and restores that state afterward. It enables the guard and commits it only if its acceptance checks pass.

`verify_runtime.py --allow-disruptive-tests` is a **disruptive local-only test**. It intentionally locks public networking. Never run it through a connection that depends on PandaFan; never run it unattended. The safe check is the default choice.

The build targets the current Mac's architecture and uses ad hoc signing by default. Set `GUARD_SIGN_IDENTITY` to use a local signing identity. Generated apps and local acceptance results are ignored by Git.

## Attribution

The low-level pf, group, and process helpers in `src/upstream_guard.py` are vendored without changes from [madgiraffe1/flclash-egress-guard](https://github.com/madgiraffe1/flclash-egress-guard), commit `e78f4becf0346bb8a4820a1022e80277456c2a59`. Their MIT license is retained in `LICENSE.upstream`. The PandaFan adapter does not call the upstream FlClash-specific installer, boot configuration, or rollback routines.
