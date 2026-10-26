// Event Picks: a small Mac window around picker.py's output.
// Shows ~/Library/Application Support/EventPicks/output/picks.html, refreshes on
// demand (⌘R) or automatically when the picks are more than a week old.
import AppKit
import WebKit

let dataDir = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/EventPicks")
let picksFile = dataDir.appendingPathComponent("output/picks.html")
let staleAfter: TimeInterval = 7 * 24 * 3600

extension NSToolbarItem.Identifier {
    static let refresh = NSToolbarItem.Identifier("refresh")
    static let spinner = NSToolbarItem.Identifier("spinner")
    static let profile = NSToolbarItem.Identifier("profile")
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, NSToolbarDelegate {
    var window: NSWindow!
    let web = WKWebView()
    let spinner = NSProgressIndicator()
    var refreshItem: NSToolbarItem?
    var running: Process?
    var loadedAt = Date.distantPast
    lazy var preferences = PreferencesController(
        profileURL: dataDir.appendingPathComponent("profile.json"),
        onRefresh: { [weak self] in self?.refresh() },
        onEditJSON: { [weak self] in self?.editProfile() })

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.isDisplayedWhenStopped = false

        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 820, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Event Picks"
        window.contentView = web
        window.minSize = NSSize(width: 420, height: 400)

        let toolbar = NSToolbar(identifier: "main")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false
        window.toolbar = toolbar
        window.toolbarStyle = .unified
        window.titlebarSeparatorStyle = .automatic

        window.setFrameAutosaveName("EventPicksWindow")
        if window.frame.origin == .zero { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        load()
        if isStale() { refresh() }

        // Coming back to the app: pick up a newer picks file (e.g. from the Monday
        // refresh) or at least drop events that have started since it was shown.
        NotificationCenter.default.addObserver(forName: NSApplication.didBecomeActiveNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            guard let self = self, self.running == nil else { return }
            let attrs = try? FileManager.default.attributesOfItem(atPath: picksFile.path)
            if let d = attrs?[.modificationDate] as? Date, d > self.loadedAt {
                self.load()
            } else {
                self.web.evaluateJavaScript("typeof prune === 'function' && prune()")
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    // MARK: toolbar

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [.flexibleSpace, .spinner, .refresh, .profile]
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        toolbarDefaultItemIdentifiers(toolbar)
    }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier id: NSToolbarItem.Identifier,
                 willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: id)
        switch id {
        case .refresh:
            item.label = "Refresh"
            item.toolTip = "Find new picks (⌘R)"
            item.image = NSImage(systemSymbolName: "arrow.clockwise", accessibilityDescription: "Refresh")
            item.isBordered = true
            item.target = self
            item.action = #selector(refresh)
            refreshItem = item
        case .profile:
            item.label = "Preferences"
            item.toolTip = "Distance, price and interests (⌘,)"
            item.image = NSImage(systemSymbolName: "slider.horizontal.3", accessibilityDescription: "Preferences")
            item.isBordered = true
            item.target = self
            item.action = #selector(showPreferences)
        case .spinner:
            item.view = spinner
        default:
            return nil
        }
        return item
    }

    // MARK: content

    func isStale() -> Bool {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: picksFile.path),
              let modified = attrs[.modificationDate] as? Date else { return true }
        return Date().timeIntervalSince(modified) > staleAfter
    }

    func load() {
        if FileManager.default.fileExists(atPath: picksFile.path) {
            web.loadFileURL(picksFile, allowingReadAccessTo: dataDir)
            loadedAt = Date()
            let attrs = try? FileManager.default.attributesOfItem(atPath: picksFile.path)
            if let d = attrs?[.modificationDate] as? Date {
                let f = DateFormatter()
                f.dateFormat = "EEE MMM d, h:mm a"
                window.subtitle = "Updated \(f.string(from: d)) · refreshes Mondays"
            }
        } else {
            web.loadHTMLString("<body style='font:15px -apple-system;color:#888;padding:60px 40px'>Finding this week’s picks… this takes a minute or two.</body>", baseURL: nil)
        }
    }

    @objc func refresh() {
        guard running == nil,
              let script = Bundle.main.url(forResource: "picker", withExtension: "py") else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [script.path, "--no-open", "--quiet"]
        var env = ProcessInfo.processInfo.environment
        env["EVENTPICKS_DATA"] = dataDir.path
        p.environment = env
        let log = dataDir.appendingPathComponent("output/app-refresh.log")
        try? FileManager.default.createDirectory(at: log.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let h = try? FileHandle(forWritingTo: log) {
            p.standardOutput = h
            p.standardError = h
        }
        p.terminationHandler = { proc in
            DispatchQueue.main.async {
                self.running = nil
                self.spinner.stopAnimation(nil)
                self.refreshItem?.isEnabled = true
                self.load()
                if proc.terminationStatus != 0 {
                    self.window.subtitle = "Refresh failed · see output/app-refresh.log"
                }
            }
        }
        do {
            try p.run()
            running = p
            refreshItem?.isEnabled = false
            spinner.startAnimation(nil)
            window.subtitle = "Scanning Eventbrite, Luma and Partiful…"
        } catch {
            window.subtitle = "Couldn’t start python3: \(error.localizedDescription)"
        }
    }

    @objc func showPreferences() {
        ensureProfile()
        preferences.show()
    }

    func ensureProfile() {
        let profile = dataDir.appendingPathComponent("profile.json")
        if !FileManager.default.fileExists(atPath: profile.path),
           let bundled = Bundle.main.url(forResource: "profile", withExtension: "json") {
            try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
            try? FileManager.default.copyItem(at: bundled, to: profile)
        }
    }

    @objc func editProfile() {
        let profile = dataDir.appendingPathComponent("profile.json")
        if !FileManager.default.fileExists(atPath: profile.path),
           let bundled = Bundle.main.url(forResource: "profile", withExtension: "json") {
            try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
            try? FileManager.default.copyItem(at: bundled, to: profile)
        }
        NSWorkspace.shared.open([profile], withApplicationAt: URL(fileURLWithPath: "/System/Applications/TextEdit.app"),
                                configuration: NSWorkspace.OpenConfiguration())
    }

    // Event links open in the default web browser (whatever handles https links),
    // never inside the app window.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, ["http", "https"].contains(url.scheme ?? "") {
            openInBrowser(url)
            decisionHandler(.cancel)
        } else {
            decisionHandler(.allow)
        }
    }

    func openInBrowser(_ url: URL) {
        let ws = NSWorkspace.shared
        if let browser = ws.urlForApplication(toOpen: URL(string: "https://example.com")!) {
            ws.open([url], withApplicationAt: browser, configuration: NSWorkspace.OpenConfiguration())
        } else {
            ws.open(url)
        }
    }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Refresh Picks", action: #selector(refresh), keyEquivalent: "r")
        appMenu.addItem(withTitle: "Preferences…", action: #selector(showPreferences), keyEquivalent: ",")
        appMenu.addItem(withTitle: "Edit Full Profile (JSON)…", action: #selector(editProfile), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Event Picks", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem()
        main.addItem(editItem)
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
