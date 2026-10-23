#!/bin/bash
# Builds "Event Picks.app" into ~/Applications and schedules the weekly refresh
# (Mondays 9 AM via launchd). The app bundles picker.py and a default profile;
# your live profile, picks and cache live in ~/Library/Application Support/EventPicks.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/Applications/Event Picks.app"
DATA="$HOME/Library/Application Support/EventPicks"
LABEL="com.eventpicks.weekly"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -rf "$APP" "$PLIST"
  echo "Removed the app and its schedule (your data in $DATA is kept)."
  exit 0
fi

BUILD="$(mktemp -d)"
mkdir -p "$BUILD/Event Picks.app/Contents/MacOS" "$BUILD/Event Picks.app/Contents/Resources"
# The Command Line Tools sometimes ship a default SDK newer than their compiler;
# try the default, then each installed SDK newest-first until one builds.
built=""
for sdk in "" $(ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX[0-9]*.sdk 2>/dev/null | sort -rV); do
  if swiftc -O ${sdk:+-sdk "$sdk"} -o "$BUILD/Event Picks.app/Contents/MacOS/EventPicks" \
       "$SRC"/app/main.swift "$SRC"/app/Preferences.swift -framework AppKit -framework WebKit -framework SwiftUI 2>/dev/null; then
    built="yes"; break
  fi
done
[[ -n "$built" ]] || { echo "Swift build failed with every installed SDK." >&2; exit 1; }
cp "$SRC/picker.py" "$SRC/profile.json" "$BUILD/Event Picks.app/Contents/Resources/"
[[ -f "$SRC/app/AppIcon.icns" ]] && cp "$SRC/app/AppIcon.icns" "$BUILD/Event Picks.app/Contents/Resources/"
cat > "$BUILD/Event Picks.app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Event Picks</string>
  <key>CFBundleDisplayName</key><string>Event Picks</string>
  <key>CFBundleIdentifier</key><string>com.eventpicks.app</string>
  <key>CFBundleExecutable</key><string>EventPicks</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
EOF
codesign --force --deep -s - "$BUILD/Event Picks.app" >/dev/null

mkdir -p "$HOME/Applications"
rm -rf "$APP"
mv "$BUILD/Event Picks.app" "$APP"
rm -rf "$BUILD"

# First install seeds your profile; later builds leave your edits alone.
mkdir -p "$DATA/output"
[[ -f "$DATA/profile.json" ]] || cp "$SRC/profile.json" "$DATA/profile.json"

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$APP/Contents/Resources/picker.py</string>
    <string>--no-open</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>EVENTPICKS_DATA</key><string>$DATA</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DATA/output/weekly-run.log</string>
  <key>StandardErrorPath</key><string>$DATA/output/weekly-run.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Installed: $APP"
echo "Weekly refresh: Mondays 9:00 AM (runs on wake if the Mac was asleep)"
