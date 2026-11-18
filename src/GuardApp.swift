import AppKit

let service = "/Library/Application Support/PandaFanEgressGuard/guard.py"

func request(_ action: String) -> [String: Any] {
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
    let a = NSAlert(); a.messageText = "PandaFan 断线保护"; a.informativeText = text
    a.addButton(withTitle: "好"); a.runModal()
}

final class App: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var statusLine: NSMenuItem!
    var timer: Timer?
    var busy = false
    var status = [String:Any]()
    var recoveryOnly: Bool { Bundle.main.object(forInfoDictionaryKey: "GuardRecoveryOnly") as? Bool ?? false }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        if recoveryOnly { restore(); NSApp.terminate(nil); return }
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "盾 · 检查中"
        let menu = NSMenu()
        statusLine = NSMenuItem(title: "读取保护状态…", action: nil, keyEquivalent: "")
        menu.addItem(statusLine); menu.addItem(.separator())
        for (title, action) in [("开启 / 重新检查保护", #selector(enable)), ("确认持续保护", #selector(commit)), ("重新连接 PandaFan", #selector(reconnect)), ("一键恢复正常联网", #selector(restore)), ("安装 / 修复系统服务…", #selector(install)), ("打开 PandaFan", #selector(openPanda))] {
            let m = NSMenuItem(title: title, action: action, keyEquivalent: ""); m.target=self; menu.addItem(m)
        }
        menu.addItem(.separator())
        let info=NSMenuItem(title:"退出菜单栏（保护继续运行）",action:#selector(quit),keyEquivalent:"");info.target=self;menu.addItem(info)
        item.menu=menu
        refresh()
        timer=Timer.scheduledTimer(withTimeInterval:4,repeats:true) { [weak self] _ in self?.refresh() }
        if CommandLine.arguments.contains("--install") { install() }
    }

    func refresh() {
        guard !busy else { return }; busy=true
        DispatchQueue.global().async {
            let response=request("status")
            DispatchQueue.main.async {
                self.busy=false
                self.status=response["result"] as? [String:Any] ?? [:]
                let mode=self.status["mode"] as? String ?? "missing"
                let names=["connected":"盾 · 已保护","locked":"盾 · 公网锁定","off":"盾 · 已恢复","error":"盾 · 异常","missing":"盾 · 待安装"]
                self.item.button?.title=names[mode] ?? "盾 · 检查中"
                self.statusLine.title=self.status["reason"] as? String ?? "系统服务尚未安装。"
            }
        }
    }

    func perform(_ action:String, success:String?=nil) {
        guard !busy else { notify("正在检查状态，请稍后再试。");return }
        busy=true
        DispatchQueue.global().async {
            let response=request(action)
            DispatchQueue.main.async {
                self.busy=false
                if response["ok"] as? Bool != true { notify(response["message"] as? String ?? "服务未响应，请安装或修复系统服务。") }
                else if let text=success { notify(text) }
                self.refresh()
            }
        }
    }

    @objc func install() {
        guard let resources=Bundle.main.resourceURL else { return }
        let command="/bin/sh " + shellQuote(resources.appendingPathComponent("install.sh").path)
        if !admin(command) { notify("安装未完成。管理员密码只在 macOS 授权窗口中输入。");return }
        refresh()
    }
    @objc func enable() { perform("enable",success:"保护已启用，当前为五分钟安全验收。确认联网正常后，可从菜单确认持续保护。") }
    @objc func reconnect() { perform("reconnect") }
    @objc func commit() { perform("commit") }
    @objc func restore() {
        let response=request("restore")
        if response["ok"] as? Bool == true {
            if !recoveryOnly { refresh() }
        } else {
            if admin("/usr/bin/python3 -I -B " + shellQuote(service) + " emergency") { notify("限制已解除，联网恢复正常。") }
            else { notify("恢复未完成，请使用使用说明中的紧急恢复命令。") }
        }
    }
    @objc func openPanda() { NSWorkspace.shared.openApplication(at: URL(fileURLWithPath:"/Applications/PandaFan.app"),configuration:NSWorkspace.OpenConfiguration(),completionHandler:nil) }
    @objc func quit() { NSApp.terminate(nil) }
}

let app=NSApplication.shared
let delegate=App();app.delegate=delegate;app.run()
