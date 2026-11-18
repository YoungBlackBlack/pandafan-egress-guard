# PandaFan Egress Guard

Experimental macOS egress protection for PandaFan, preserving smart routing and scoped Tailscale traffic. Independent project; not affiliated with PandaFan or Tailscale.

**Development preview.** This is privileged firewall software, tested on one Apple silicon Mac with PandaFan 3.3.3. Review the source and recovery path before enabling it. It is not a guarantee against every form of leakage, and boot-time protection has not been verified.

## Design

- A root LaunchDaemon manages one dedicated pf anchor, `com.apple/000.pandafan-egress-guard`.
- A dedicated empty group identifies the PandaFan instance started by the guard. Only root sockets carrying that group and the positively identified PandaFan TUN receive ordinary public egress exceptions.
- The guard preserves PandaFan's smart-routing decisions, including DIRECT. It does not force all websites to use a proxy exit IP.
- Core, identity, or tunnel changes withdraw the ordinary public exceptions. LAN and loopback remain available, including DNS sent to LAN resolvers.
- Authenticated Tailscale processes receive exceptions limited to their observed sockets; the identified Tailscale TUN receives tailnet-only exceptions. Arbitrary utun interfaces and root programs are not whitelisted. Tailscale exit nodes are not a public egress exception.
- Restore clears this anchor and releases this tool's own pf reference. It does not globally disable pf, flush other anchors or states, or modify `/etc/pf.conf`.

## Build and installation

Requires macOS 13+, Xcode Command Line Tools, and PandaFan at `/Applications/PandaFan.app` using enhanced mode. Python uses macOS's `/usr/bin/python3`; application bundles are built for the current architecture.

```sh
python3 -m unittest discover -s tests -v
python3 build.py
```

By default builds are signed ad hoc. Set `GUARD_SIGN_IDENTITY` to use a local signing identity.

Copy the two generated applications to `~/Applications`. Open **PandaFan 断线保护.app**, then choose **安装 / 修复系统服务**. Enter the administrator password only into macOS's own authorization dialog. The installer detects the logged-in account; the repository contains no user-specific path or PandaFan configuration.

PandaFan must be started with the guard's dedicated group before protection can be enabled. Starting an already running client with a different identity requires a normal quit and restart. **Do not do this while the software controlling your Mac depends on PandaFan for its connection.** Arrange that step locally with a recovery path ready. The current preview does not automate the original PandaFan login item.

Once the dedicated core and TUN are stable, **开启 / 重新检查保护** loads the connected policy directly. Initial activation creates a five-minute independent recovery deadline; confirm normal operation using **确认持续保护** to retain it. Without confirmation, the independent watchdog restores networking.

## Recovery

Open **PandaFan 一键恢复.app** or choose **一键恢复正常联网** from the guard menu. This does not close PandaFan.

If the local service is unavailable, the recovery application asks for macOS administrator authorization and uses the separate emergency path. The same offline command is:

```sh
sudo /usr/bin/python3 -I -B '/Library/Application Support/PandaFanEgressGuard/guard.py' emergency
```

The root service and its policy persist independently of the menu application. At boot the service reloads an enabled policy, but there is no guarantee covering the first packet during system startup. A PandaFan instance launched by its ordinary login item may lack the dedicated group; in that case protection remains locked until locally restarted through the guard.

## Validation

`tests/test_guard.py` checks rules and state changes without altering networking.

`verify_safe.py` keeps PandaFan running and tests actual public APIs, physical-interface blocking, Tailscale peer communication, and the recovery application. It requires an already managed PandaFan core and an online tailnet peer. It temporarily starts a stopped Tailscale and restores that state afterward. It enables the guard and commits it only if its acceptance checks pass.

`verify_runtime.py --allow-disruptive-tests` is a **disruptive local-only test**. It intentionally locks public networking. Never run it through a connection that depends on PandaFan; never run it unattended. The safe check is the default choice.

No credentials, subscriptions, traffic records, or device-specific verification output belong in the repository. Generated applications and local acceptance results are ignored.

## Attribution

The low-level pf, group, and process helpers in `src/upstream_guard.py` are vendored without changes from [madgiraffe1/flclash-egress-guard](https://github.com/madgiraffe1/flclash-egress-guard), commit `e78f4becf0346bb8a4820a1022e80277456c2a59`. Their MIT license is retained in `LICENSE.upstream`. The PandaFan adapter does not call the upstream FlClash-specific installer, boot configuration, or rollback routines.
