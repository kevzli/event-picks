// Draws the app icon: a frosted calendar card on a deep graphite-to-indigo tile,
// with one warm marked day. Follows the macOS icon grid (824pt body in 1024).
// Usage: swiftc -sdk <sdk> -o mkicon make_icon.swift && ./mkicon AppIcon.iconset
import AppKit

let out = CommandLine.arguments[1]
let sizes = [("icon_16x16", 16), ("icon_16x16@2x", 32), ("icon_32x32", 32), ("icon_32x32@2x", 64),
             ("icon_128x128", 128), ("icon_128x128@2x", 256), ("icon_256x256", 256), ("icon_256x256@2x", 512),
             ("icon_512x512", 512), ("icon_512x512@2x", 1024)]

func rgb(_ r: CGFloat, _ g: CGFloat, _ b: CGFloat, _ a: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: r / 255, green: g / 255, blue: b / 255, alpha: a)
}

for (name, px) in sizes {
    let s = CGFloat(px)
    let u = s / 1024
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                               bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

    // tile: soft drop shadow, then a dark diagonal gradient
    let tileRect = NSRect(x: 100 * u, y: 100 * u, width: 824 * u, height: 824 * u)
    let tile = NSBezierPath(roundedRect: tileRect, xRadius: 185 * u, yRadius: 185 * u)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = rgb(0, 0, 0, 0.35)
    shadow.shadowBlurRadius = 24 * u
    shadow.shadowOffset = NSSize(width: 0, height: -10 * u)
    shadow.set()
    rgb(20, 20, 28).setFill()
    tile.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient(colors: [rgb(44, 46, 66), rgb(22, 22, 32), rgb(14, 14, 20)],
               atLocations: [0, 0.55, 1], colorSpace: .sRGB)!.draw(in: tile, angle: -60)
    // faint top highlight for depth
    NSGraphicsContext.saveGraphicsState()
    tile.addClip()
    NSGradient(starting: rgb(255, 255, 255, 0.10), ending: rgb(255, 255, 255, 0))!
        .draw(in: NSRect(x: tileRect.minX, y: tileRect.midY, width: tileRect.width, height: tileRect.height / 2), angle: -90)
    NSGraphicsContext.restoreGraphicsState()

    // calendar card
    let card = NSRect(x: 292 * u, y: 286 * u, width: 440 * u, height: 420 * u)
    let cardPath = NSBezierPath(roundedRect: card, xRadius: 72 * u, yRadius: 72 * u)
    NSGradient(starting: rgb(246, 246, 250), ending: rgb(214, 216, 228))!.draw(in: cardPath, angle: -90)
    // header band in a warm coral
    NSGraphicsContext.saveGraphicsState()
    cardPath.addClip()
    let band = NSRect(x: card.minX, y: card.maxY - 112 * u, width: card.width, height: 112 * u)
    NSGradient(starting: rgb(255, 128, 96), ending: rgb(232, 84, 74))!.draw(in: band, angle: -90)
    NSGraphicsContext.restoreGraphicsState()

    // binder rings
    for x in [card.minX + 132 * u, card.maxX - 132 * u] {
        let ring = NSBezierPath(roundedRect: NSRect(x: x - 18 * u, y: card.maxY - 52 * u, width: 36 * u, height: 104 * u),
                                xRadius: 18 * u, yRadius: 18 * u)
        rgb(36, 36, 48).setFill()
        ring.fill()
    }

    // a 3x2 grid of days, one highlighted
    let dot = 46 * u
    let cols: [CGFloat] = [card.minX + 110 * u, card.midX, card.maxX - 110 * u]
    let rows: [CGFloat] = [card.minY + 210 * u, card.minY + 100 * u]
    for (ri, y) in rows.enumerated() {
        for (ci, x) in cols.enumerated() {
            let r = NSRect(x: x - dot / 2, y: y - dot / 2, width: dot, height: dot)
            if ri == 0 && ci == 2 {
                rgb(232, 84, 74).setFill()
                NSBezierPath(ovalIn: r.insetBy(dx: -10 * u, dy: -10 * u)).fill()
            } else {
                rgb(150, 154, 172).setFill()
                NSBezierPath(roundedRect: r, xRadius: 12 * u, yRadius: 12 * u).fill()
            }
        }
    }

    NSGraphicsContext.restoreGraphicsState()
    try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: "\(out)/\(name).png"))
}
