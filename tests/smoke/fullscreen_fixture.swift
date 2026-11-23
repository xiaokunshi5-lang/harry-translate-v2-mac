import AppKit
import Foundation

final class FixtureDelegate: NSObject, NSApplicationDelegate {
    private var window: NSWindow?
    private var observers: [NSObjectProtocol] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let screen = NSScreen.main else {
            NSApp.terminate(nil)
            return
        }
        let window = NSWindow(
            contentRect: screen.visibleFrame,
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "R155S Fullscreen Fixture"
        window.center()
        window.makeKeyAndOrderFront(nil)
        self.window = window
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            let center = NotificationCenter.default
            self.observers.append(
                center.addObserver(
                    forName: NSWindow.didEnterFullScreenNotification,
                    object: window,
                    queue: .main
                ) { _ in
                    print("{\"event\":\"fullscreen_entered\"}")
                    fflush(stdout)
                }
            )
            window.toggleFullScreen(nil)
        }
    }
}

let application = NSApplication.shared
let delegate = FixtureDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
