import AppKit

let service = "/Library/Application Support/PandaFanEgressGuard/guard.py"
let launchDaemon = "/Library/LaunchDaemons/local.pandafan-egress-guard.plist"
let pandaApp = URL(fileURLWithPath: "/Applications/PandaFan.app")

func request(_ action: String) -> [String: Any] {
    guard FileManager.default.fileExists(atPath: service) else { return [:] }
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    p.arguments = ["-I", "-B", service, action]
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
    do {
        try p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile(); p.waitUntilExit()
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    } catch { return [:] }
}

func admin(_ command: String) -> Bool {
    let escaped = command.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"")
    let script = NSAppleScript(source: "do shell script \"\(escaped)\" with administrator privileges")!
    var error: NSDictionary?
    script.executeAndReturnError(&error)
    return error == nil
}

func shellQuote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }

func notify(_ text: String) {
    if #available(macOS 14.0, *) { NSApp.activate() }
    else { NSApp.activate(ignoringOtherApps: true) }
    let a = NSAlert(); a.messageText = "PandaFan 断线保护"; a.informativeText = text
    a.addButton(withTitle: "好"); a.runModal()
}

enum ProtectionMenuState: String {
    case checking, protected, trial, locked, off, missing, unavailable, error
    case needsPanda, firstSetup, waiting, connect, ready, retry

    init(status: [String: Any], installed: Bool) {
        switch status["mode"] as? String {
        case "connected": self = status["trial_active"] as? Bool == true ? .trial : .protected
        case "locked": self = .locked
        case "off":
            if status["panda_installed"] as? Bool == false {
                self = .needsPanda
            } else {
                switch status["setup_state"] as? String {
                case "initial": self = .firstSetup
                case "waiting": self = .waiting
                case "connect": self = .connect
                case "ready": self = .ready
                case "retry": self = .retry
                default: self = .off
                }
            }
        case "error": self = .error
        default: self = installed ? .unavailable : .missing
        }
    }

    var title: String {
        switch self {
        case .checking: return "盾 · 检查中"
        case .protected: return "盾 · 已保护"
        case .trial: return "盾 · 试用中"
        case .locked: return "盾 · 公网锁定"
        case .off, .ready: return "盾 · 已关闭"
        case .needsPanda, .firstSetup, .retry: return "盾 · 待设置"
        case .waiting, .connect: return "盾 · 设置中"
        case .missing: return "盾 · 待安装"
        case .unavailable, .error: return "盾 · 服务异常"
        }
    }

    var description: String {
        switch self {
        case .checking: return "正在读取保护状态…"
        case .protected: return "PandaFan 断线时会阻止公网直连。"
        case .trial: return "联网正常后，选择「保持开启」。"
        case .locked: return "PandaFan 连接异常，公网已锁定。"
        case .off: return "断线保护已关闭。"
        case .needsPanda: return "请先把 PandaFan 安装到「应用程序」。"
        case .firstSetup: return "首次使用需要设置 PandaFan。"
        case .waiting: return "请在 90 秒内从 PandaFan 菜单选择「退出」。"
        case .connect: return "在 PandaFan 中连接并开启增强模式。"
        case .ready: return "PandaFan 增强模式已就绪，可以开启保护。"
        case .retry: return "首次设置未完成。"
        case .missing: return "首次使用需要安装保护服务。"
        case .unavailable: return "保护服务未响应。"
        case .error: return "保护状态异常。"
        }
    }
}

final class App: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var menuState: ProtectionMenuState?
    var menuDescription: String?
    var timer: Timer?
    var busy = false
    var recoveryOnly: Bool { Bundle.main.object(forInfoDictionaryKey: "GuardRecoveryOnly") as? Bool ?? false }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if recoveryOnly { restore(); NSApp.terminate(nil); return }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        updateMenu(.checking)
        refresh()
        timer=Timer.scheduledTimer(withTimeInterval:4,repeats:true) { [weak self] _ in self?.refresh() }
        if CommandLine.arguments.contains("--install") { install() }
    }

    func updateMenu(_ state: ProtectionMenuState, message: String? = nil) {
        let description = message ?? state.description
        guard state != menuState || description != menuDescription else { return }
        menuState = state
        menuDescription = description
        item.button?.title = state.title

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: description, action: nil, keyEquivalent: ""))

        func add(_ title: String, _ action: Selector) {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
            entry.target = self
            menu.addItem(entry)
        }

        if state != .checking {
            menu.addItem(.separator())
        }
        switch state {
        case .checking:
            break
        case .protected:
            add("关闭保护", #selector(restore))
            add("打开 PandaFan", #selector(openPanda))
        case .trial:
            add("保持开启", #selector(commit))
            add("关闭保护", #selector(restore))
        case .locked:
            add("重新连接 PandaFan", #selector(reconnect))
            add("恢复联网（关闭保护）", #selector(restore))
            add("打开 PandaFan", #selector(openPanda))
        case .off:
            add("开启保护", #selector(enable))
            add("更新保护服务…", #selector(install))
            add("打开 PandaFan", #selector(openPanda))
        case .ready:
            add("开启保护", #selector(enable))
            add("打开 PandaFan", #selector(openPanda))
        case .needsPanda, .waiting:
            break
        case .firstSetup, .retry:
            add("首次设置…", #selector(firstSetup))
        case .connect:
            add("重新进行首次设置…", #selector(firstSetup))
        case .missing:
            add("安装保护服务…", #selector(install))
        case .unavailable:
            add("修复保护服务…", #selector(install))
            add("恢复联网（关闭保护）", #selector(restore))
        case .error:
            add("重新检查保护", #selector(enable))
            add("恢复联网（关闭保护）", #selector(restore))
        }
        if state != .checking {
            menu.addItem(.separator())
        }
        add("功能介绍…", #selector(showIntroduction))
        item.menu = menu
    }

    func refresh() {
        guard !busy else { return }; busy=true
        DispatchQueue.global().async {
            let response=request("status")
            DispatchQueue.main.async {
                self.busy=false
                self.showStatus(response["result"] as? [String: Any] ?? [:])
            }
        }
    }

    func showStatus(_ status: [String: Any]) {
        let installed = FileManager.default.fileExists(atPath: launchDaemon)
        let state = ProtectionMenuState(status: status, installed: installed)
        updateMenu(state, message: state == .retry ? status["setup_message"] as? String : nil)
    }

    func perform(_ action:String) {
        guard !busy else { notify("正在检查状态，请稍后再试。");return }
        busy=true
        DispatchQueue.global().async {
            let response=request(action)
            DispatchQueue.main.async {
                self.busy=false
                if response["ok"] as? Bool != true { notify(response["message"] as? String ?? "服务未响应，请安装或修复系统服务。") }
                else if let result = response["result"] as? [String: Any], result["mode"] != nil {
                    self.showStatus(result)
                    if action == "enable", result["trial_active"] as? Bool == true {
                        notify("保护已试用开启。联网正常后，请在五分钟内从菜单选择「保持开启」。")
                    } else if action == "prepare", result["setup_state"] as? String == "waiting" {
                        notify("请现在从 PandaFan 的菜单选择「退出」。保护服务会自动重新打开它。重新连接并开启增强模式后，盾牌菜单会显示「开启保护」。")
                    }
                }
                self.refresh()
            }
        }
    }

    @objc func install() {
        guard let resources=Bundle.main.resourceURL else { return }
        let command = "/bin/sh " + shellQuote(resources.appendingPathComponent("install.sh").path)
        if !admin(command) { notify("安装未完成。管理员密码只在 macOS 授权窗口中输入。");return }
        refresh()
    }
    @objc func firstSetup() {
        if #available(macOS 14.0, *) { NSApp.activate() }
        else { NSApp.activate(ignoringOtherApps: true) }
        let alert = NSAlert()
        alert.messageText = "首次设置 PandaFan"
        alert.informativeText = "请在这台 Mac 前操作。设置会短暂中断 PandaFan 连接。开始后，请在 90 秒内从 PandaFan 菜单选择「退出」。保护服务会自动重新打开 PandaFan，然后请连接并开启增强模式。"
        alert.addButton(withTitle: "开始")
        alert.addButton(withTitle: "取消")
        if alert.runModal() == .alertFirstButtonReturn { perform("prepare") }
    }
    @objc func enable() { perform("enable") }
    @objc func reconnect() { perform("reconnect") }
    @objc func commit() { perform("commit") }
    @objc func showIntroduction() {
        notify("""
        断线保护
        PandaFan 断线时阻止应用直接访问公网，局域网仍可用。PandaFan 连通时保留原有智能分流。

        日常使用
        显示「已保护」时无需操作。开启保护后有五分钟试用，确认能联网后选「保持开启」。

        恢复联网
        公网锁定时，可以重新连接 PandaFan，或选「恢复联网（关闭保护）」。也可以直接打开「PandaFan 一键恢复」解除限制。
        """)
    }
    @objc func restore() {
        let response=request("restore")
        if response["ok"] as? Bool == true {
            if !recoveryOnly { refresh() }
        } else {
            if admin("/usr/bin/python3 -I -B " + shellQuote(service) + " emergency") { notify("限制已解除，联网恢复正常。") }
            else { notify("恢复未完成，请使用使用说明中的紧急恢复命令。") }
        }
    }
    @objc func openPanda() { NSWorkspace.shared.openApplication(at: pandaApp,configuration:NSWorkspace.OpenConfiguration(),completionHandler:nil) }
}

let app=NSApplication.shared
let delegate=App();app.delegate=delegate;app.run()
