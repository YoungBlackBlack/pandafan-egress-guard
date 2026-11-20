# PandaFan 断线保护

[English](README.md) · 简体中文

PandaFan Egress Guard 是一款 macOS 断线保护工具。当 PandaFan 核心退出或本机隧道失效时，它会阻止应用回退到公网直连。PandaFan 正常运行时，智能分流照常工作。保护开启期间，局域网与符合放行规则的 Tailscale 流量仍可用。需要解除限制时，可以一键恢复联网。

这是独立项目，与 PandaFan 或 Tailscale 官方无关。

**开发预览版。** 本工具需使用管理员权限管理防火墙，已在一台搭载 Apple 芯片的 Mac 上用 PandaFan 3.3.3 测试。启用前请查阅源码和恢复方法。保护范围见下文，开机阶段的保护仍待验证。

## 功能与保护范围

- **阻止断线直连。** 后台服务通过 macOS 的 `pf` 防火墙管理一组独立规则，仅为经过验证的 PandaFan 核心和隧道放行普通公网流量。核心、进程身份或隧道发生变化时，会撤销这些放行规则。单个代理节点故障时，如果本机隧道和路由仍有效，保护不会因此锁网。
- **保留智能分流。** PandaFan 原有的分流与直连（DIRECT）规则继续生效。局域网、回环流量和发往局域网 DNS 的查询仍可用。
- **按范围放行 Tailscale。** 放行经过验证的 Tailscale 进程所持有的特定连接，以及经确认的 Tailscale 隧道内的组网流量。通过 Tailscale 出口节点访问公网不在此例外范围内。
- **一键恢复联网。** 恢复操作只清除本工具的规则，并释放它对防火墙的启用引用。

## 构建与安装

需要 macOS 13 或更新版本、Xcode 命令行工具，以及安装在 `/Applications/PandaFan.app` 并开启增强模式的 PandaFan。构建使用系统自带的 `/usr/bin/python3`，生成与当前 Mac 架构匹配的应用。

```sh
python3 -m unittest discover -s tests -v
python3 build.py
```

构建默认使用临时签名。若要使用本机已有的签名身份，可设置 `GUARD_SIGN_IDENTITY`。

1. 将生成的两个应用复制到 `~/Applications`。打开 **PandaFan 断线保护.app**，选择 **安装保护服务**。如果服务无响应，菜单会显示 **修复保护服务**。请只在 macOS 的系统授权窗口输入管理员密码。
2. 启用前需要让 PandaFan 以保护服务的专用组身份运行。若当前已用普通方式启动，需要在本机退出并重新启动。若当前远程控制或 AI 应用依赖这条连接，请先安排本机操作和恢复方式。
3. 核心与隧道就绪后，选择 **开启保护**。试用有五分钟。确认可以联网后选择 **保持开启**，到期未确认时会自动解除限制。

日常看到盾牌显示「已保护」即可正常使用。菜单底部的 **功能介绍** 可以查看保护范围和恢复方法。更多操作见[中文使用说明](使用说明.md)。

## 恢复联网

在盾牌菜单中选择 **关闭保护**。如果菜单显示「公网锁定」，请选择 **恢复联网（关闭保护）**。也可以直接打开 **PandaFan 一键恢复.app**。

本机服务无响应时，恢复应用会请求 macOS 管理员授权，并走独立的紧急恢复流程。离线时也可以在终端执行以下命令：

```sh
sudo /usr/bin/python3 -I -B '/Library/Application Support/PandaFanEgressGuard/guard.py' emergency
```

后台服务独立于菜单栏应用运行。系统重启后，服务会重新载入已启用的保护规则。如果 PandaFan 由原来的登录项启动，可能没有专用组身份，此时公网会保持锁定，直到你在本机通过保护菜单重新连接。开机第一包是否受保护仍待验证。

## 验证方式

- `tests/test_guard.py` 验证规则和状态变化，不修改联网状态。
- `verify_safe.py` 验证公共 API 访问、物理网卡直连阻断、Tailscale 对端通信和恢复应用。需要已经纳入保护的 PandaFan 核心及在线的 Tailscale 对端。脚本会恢复 Tailscale 原来的启停状态，验收通过后保留保护配置。
- `verify_runtime.py --allow-disruptive-tests` 会故意锁住公网，仅在本机且可现场恢复时运行。日常验证请使用前两项。

生成的应用和本机验收结果已加入 Git 忽略列表。请将凭证、订阅、流量记录和设备验收数据保存在本机。

## 许可与来源

`src/upstream_guard.py` 中的防火墙、权限组和进程辅助代码来自 [madgiraffe1/flclash-egress-guard](https://github.com/madgiraffe1/flclash-egress-guard) 的提交 `e78f4becf0346bb8a4820a1022e80277456c2a59`。上游 MIT 许可见 [LICENSE.upstream](LICENSE.upstream)，本项目许可见 [LICENSE](LICENSE)。
