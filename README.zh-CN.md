# PandaFan 断线保护

PandaFan Egress Guard 是一款 macOS 断线保护工具，在 PandaFan 的本机隧道断开或核心退出时，阻止应用回退到公网直连。

[English](README.md) · 简体中文

PandaFan 正常运行时，智能分流照常工作。保护开启期间，局域网与符合放行规则的 Tailscale 流量仍可用。需要解除限制时，可以一键恢复联网。

这是独立项目，与 PandaFan 或 Tailscale 官方无关。

**开发预览版。** 断线保护策略此前已在搭载 Apple 芯片的 Mac 上用 PandaFan 3.3.3 测试。安装防火墙服务需要管理员授权。开机阶段的保护仍待验证。

## 从源码安装

需要 macOS 13 或更新版本、Xcode 命令行工具，并已在「应用程序」中安装 PandaFan。先登录 PandaFan，确认开启增强模式后能正常连接。如果缺少命令行工具，可运行 `xcode-select --install`。

1. **构建应用。** 下载并解压源码，在解压后的项目目录打开终端，运行以下命令。[下载源码 ZIP](https://github.com/YoungBlackBlack/pandafan-egress-guard/archive/refs/heads/main.zip)

   ```sh
   /usr/bin/python3 build.py
   ```

2. **安装保护服务。** 把生成的两个应用移到个人的「应用程序」文件夹（`~/Applications`）。打开「PandaFan 断线保护」，点菜单栏的盾牌，选「安装保护服务…」，在 macOS 的授权窗口确认。
3. **按菜单完成首次设置。** 在 Mac 本机点「首次设置…」。如果 PandaFan 正在运行，按提示在 90 秒内从它的菜单选择「退出」。保护服务会重新打开 PandaFan。连接并开启增强模式后，盾牌菜单会出现「开启保护」。
4. **开启保护。** 点「开启保护」，确认可以正常联网后，在五分钟内选择「保持开启」。试用到期会自动解除限制。

盾牌显示「已保护」就可以正常使用。点「功能介绍…」查看保护范围和恢复方法。服务无响应时可点「修复保护服务…」。[中文使用说明](使用说明.md)

如果装过旧版服务，关闭保护后可点「更新保护服务…」，即可使用新版首次设置菜单。

## 恢复联网

在盾牌菜单中选择 **关闭保护**。如果菜单显示「公网锁定」，请选择 **恢复联网（关闭保护）**。也可以直接打开 **PandaFan 一键恢复.app**。

本机服务无响应时，恢复应用会请求 macOS 管理员授权，并走独立的紧急恢复流程。离线时也可以在终端执行以下命令：

```sh
sudo /usr/bin/python3 -I -B '/Library/Application Support/PandaFanEgressGuard/guard.py' emergency
```

后台服务独立于菜单栏应用运行。系统重启后，服务会重新载入已启用的保护规则。如果 PandaFan 由原来的登录项启动，可能没有专用组身份，此时公网会保持锁定，直到你在本机通过保护菜单重新连接。开机第一包是否受保护仍待验证。

## 功能与保护范围

- **阻止断线直连。** 后台服务通过 macOS 的 `pf` 防火墙管理一组独立规则，仅为经过验证的 PandaFan 核心和隧道放行普通公网流量。核心、进程身份或隧道发生变化时，会撤销这些放行规则。单个代理节点故障时，如果本机隧道和路由仍有效，保护不会因此锁网。
- **保留智能分流。** PandaFan 原有的分流与直连（DIRECT）规则继续生效。局域网、回环流量和发往局域网 DNS 的查询仍可用。
- **按范围放行 Tailscale。** 放行经过验证的 Tailscale 进程所持有的特定连接，以及经确认的 Tailscale 隧道内的组网流量。通过 Tailscale 出口节点访问公网不在此例外范围内。
- **一键恢复联网。** 恢复操作只清除本工具的规则，并释放它对防火墙的启用引用。

## 验证方式

- `/usr/bin/python3 -m unittest discover -s tests -v` 验证规则和状态变化，不修改联网状态。
- `verify_safe.py` 验证公共 API 访问、物理网卡直连阻断、Tailscale 对端通信和恢复应用。需要已经纳入保护的 PandaFan 核心及在线的 Tailscale 对端。脚本会恢复 Tailscale 原来的启停状态，验收通过后保留保护配置。
- `verify_runtime.py --allow-disruptive-tests` 会故意锁住公网，仅在本机且可现场恢复时运行。日常验证请使用前两项。

构建生成与当前 Mac 架构匹配的应用，默认使用临时签名。若要使用本机已有的签名身份，可设置 `GUARD_SIGN_IDENTITY`。生成的应用和本机验收结果已加入 Git 忽略列表。

## 许可与来源

`src/upstream_guard.py` 中的防火墙、权限组和进程辅助代码来自 [madgiraffe1/flclash-egress-guard](https://github.com/madgiraffe1/flclash-egress-guard) 的提交 `e78f4becf0346bb8a4820a1022e80277456c2a59`。上游 MIT 许可见 [LICENSE.upstream](LICENSE.upstream)，本项目许可见 [LICENSE](LICENSE)。
