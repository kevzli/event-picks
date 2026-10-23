// Preferences window: sliders and toggles over the most useful profile.json fields.
// Everything else in the profile (keywords, busy times, skip lists) is left untouched
// and stays editable via "Edit full profile…".
import AppKit
import SwiftUI

struct HomeBase: Hashable {
    let name: String, lat: Double, lng: Double
}

let homeBases = [
    HomeBase(name: "Manhattan – Union Square", lat: 40.7359, lng: -73.9911),
    HomeBase(name: "Manhattan – Midtown", lat: 40.7549, lng: -73.9840),
    HomeBase(name: "Manhattan – Upper West Side", lat: 40.7870, lng: -73.9754),
    HomeBase(name: "Brooklyn – Downtown", lat: 40.6928, lng: -73.9903),
    HomeBase(name: "Brooklyn – Williamsburg", lat: 40.7081, lng: -73.9571),
    HomeBase(name: "Brooklyn – Park Slope", lat: 40.6710, lng: -73.9814),
    HomeBase(name: "Queens – Long Island City", lat: 40.7447, lng: -73.9485),
    HomeBase(name: "Queens – Flushing", lat: 40.7580, lng: -73.8303),
    HomeBase(name: "Queens – Astoria", lat: 40.7644, lng: -73.9235),
    HomeBase(name: "Bronx", lat: 40.8448, lng: -73.8648),
    HomeBase(name: "Jersey City", lat: 40.7178, lng: -74.0431),
]

struct Interest: Identifiable {
    let id: String
    let label: String
    var weight: Double
}

final class Prefs: ObservableObject {
    let url: URL
    var raw: [String: Any] = [:]

    @Published var home = homeBases[0]
    @Published var maxMiles: Double = 15
    @Published var maxPrice: Double = 155          // 155 = no limit
    @Published var picks: Double = 8
    @Published var avoidLate = false
    @Published var avoidEarly = false
    @Published var interests: [Interest] = []
    @Published var organizers = ""

    static let noLimit: Double = 155

    init(url: URL) {
        self.url = url
        load()
    }

    func load() {
        guard let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        raw = obj
        let h = obj["home"] as? [String: Any] ?? [:]
        let lat = h["lat"] as? Double ?? homeBases[0].lat, lng = h["lng"] as? Double ?? homeBases[0].lng
        home = homeBases.min(by: { abs($0.lat - lat) + abs($0.lng - lng) < abs($1.lat - lat) + abs($1.lng - lng) })!
        let f = obj["filters"] as? [String: Any] ?? [:]
        let km = f["max_km"] as? Double ?? h["max_km"] as? Double ?? 25
        maxMiles = (km / 1.609).rounded()
        if let p = f["max_price"] as? Double { maxPrice = p } else { maxPrice = Prefs.noLimit }
        avoidLate = f["avoid_late"] as? Bool ?? false
        avoidEarly = f["avoid_early"] as? Bool ?? false
        picks = Double(obj["picks_max"] as? Int ?? 8)
        let lanes = obj["lanes"] as? [String: [String: Any]] ?? [:]
        interests = lanes.map { Interest(id: $0.key, label: $0.value["label"] as? String ?? $0.key,
                                         weight: $0.value["weight"] as? Double ?? 5) }
            .sorted { $0.weight != $1.weight ? $0.weight > $1.weight : $0.label < $1.label }
        organizers = (obj["followed_organizers"] as? [String] ?? []).joined(separator: ", ")
    }

    func save() throws {
        var obj = raw
        var h = obj["home"] as? [String: Any] ?? [:]
        h["name"] = home.name; h["lat"] = home.lat; h["lng"] = home.lng
        h.removeValue(forKey: "max_km")
        obj["home"] = h
        var f = obj["filters"] as? [String: Any] ?? [:]
        f["max_km"] = (maxMiles * 1.609 * 10).rounded() / 10
        f["max_price"] = maxPrice >= Prefs.noLimit ? NSNull() : maxPrice
        f["avoid_late"] = avoidLate
        f["avoid_early"] = avoidEarly
        obj["filters"] = f
        obj["picks_max"] = Int(picks)
        obj["picks_min"] = min(5, Int(picks))
        var lanes = obj["lanes"] as? [String: [String: Any]] ?? [:]
        for i in interests { lanes[i.id]?["weight"] = Int(i.weight) }
        obj["lanes"] = lanes
        obj["followed_organizers"] = organizers.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        let data = try JSONSerialization.data(withJSONObject: obj,
                                              options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
        try data.write(to: url, options: .atomic)
        raw = obj
    }
}

struct PreferencesView: View {
    @ObservedObject var prefs: Prefs
    var onSave: (_ refresh: Bool) -> Void
    var onCancel: () -> Void
    var onEditJSON: () -> Void

    var priceLabel: String {
        if prefs.maxPrice >= Prefs.noLimit { return "No limit" }
        return prefs.maxPrice == 0 ? "Free only" : "Up to $\(Int(prefs.maxPrice))"
    }

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section("Where & when") {
                    Picker("Home base", selection: $prefs.home) {
                        ForEach(homeBases, id: \.self) { Text($0.name).tag($0) }
                    }
                    LabeledContent("Distance") {
                        HStack {
                            Slider(value: $prefs.maxMiles, in: 1...30, step: 1)
                            Text("\(Int(prefs.maxMiles)) mi").monospacedDigit().frame(width: 52, alignment: .trailing)
                        }
                    }
                    LabeledContent("Price") {
                        HStack {
                            Slider(value: $prefs.maxPrice, in: 0...Prefs.noLimit, step: 5)
                            Text(priceLabel).monospacedDigit().frame(width: 84, alignment: .trailing)
                        }
                    }
                    Toggle("Skip late-night events (after 10 PM)", isOn: $prefs.avoidLate)
                    Toggle("Skip early-morning events", isOn: $prefs.avoidEarly)
                }
                Section("Picks") {
                    LabeledContent("Events per week") {
                        HStack {
                            Slider(value: $prefs.picks, in: 3...12, step: 1)
                            Text("\(Int(prefs.picks))").monospacedDigit().frame(width: 52, alignment: .trailing)
                        }
                    }
                }
                Section {
                    ForEach($prefs.interests) { $i in
                        LabeledContent(i.label) {
                            HStack {
                                Slider(value: $i.weight, in: 0...10, step: 1)
                                Text(i.weight == 0 ? "Off" : "\(Int(i.weight))")
                                    .monospacedDigit()
                                    .foregroundStyle(i.weight == 0 ? .secondary : .primary)
                                    .frame(width: 52, alignment: .trailing)
                            }
                        }
                    }
                } header: {
                    Text("Interests")
                } footer: {
                    Text("Higher means more picks like this. Off pushes them down.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("Organizers you like") {
                    TextField("Comma-separated names", text: $prefs.organizers, axis: .vertical)
                        .lineLimit(2...4)
                }
            }
            .formStyle(.grouped)

            Divider()
            HStack {
                Button("Edit full profile…", action: onEditJSON).buttonStyle(.link)
                Spacer()
                Button("Cancel", action: onCancel).keyboardShortcut(.cancelAction)
                Button("Save") { onSave(false) }
                Button("Save & Refresh") { onSave(true) }.keyboardShortcut(.defaultAction)
            }
            .padding(12)
        }
        .frame(minWidth: 520, idealWidth: 560, minHeight: 560, idealHeight: 760)
    }
}

final class PreferencesController {
    private var window: NSWindow?
    private let prefs: Prefs
    private let onRefresh: () -> Void
    private let onEditJSON: () -> Void

    init(profileURL: URL, onRefresh: @escaping () -> Void, onEditJSON: @escaping () -> Void) {
        prefs = Prefs(url: profileURL)
        self.onRefresh = onRefresh
        self.onEditJSON = onEditJSON
    }

    func show() {
        prefs.load()
        if window == nil {
            let view = PreferencesView(
                prefs: prefs,
                onSave: { [weak self] refresh in
                    guard let self = self else { return }
                    do {
                        try self.prefs.save()
                        self.window?.close()
                        if refresh { self.onRefresh() }
                    } catch {
                        NSAlert(error: error).runModal()
                    }
                },
                onCancel: { [weak self] in self?.window?.close() },
                onEditJSON: { [weak self] in self?.window?.close(); self?.onEditJSON() })
            let w = NSWindow(contentViewController: NSHostingController(rootView: view))
            w.title = "Preferences"
            w.styleMask = [.titled, .closable, .resizable]
            w.setContentSize(NSSize(width: 560, height: 760))
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
}
