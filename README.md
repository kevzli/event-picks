# Event Picks

A small Mac app that scans **Eventbrite**, **Luma** and **Partiful** every week. It picks 5–12 events for the rest of the month that match what you're into.

It runs entirely on your Mac. There are no accounts or API keys, and nothing is sent anywhere except normal page requests to the three event sites.

## Install

You'll need macOS 13 or later and Apple's Command Line Tools (`xcode-select --install`), which provide `swiftc` and `python3`.

```bash
git clone https://github.com/kevzli/event-picks.git
cd event-picks
./build_app.sh
```

This builds **Event Picks.app** into `~/Applications` and schedules a refresh every Monday at 9 AM. If your Mac is asleep then, the refresh runs when it wakes. Open the app, and the first scan takes a minute or two.

To remove it:

```bash
./build_app.sh --uninstall
```

Uninstalling keeps your settings in `~/Library/Application Support/EventPicks`.

## Setting your preferences

Click the **sliders button** in the toolbar, or press **⌘,**, to open Preferences:

| Setting | What it does |
|---|---|
| **Home base** | The neighborhood distances are measured from |
| **Distance** | Skip events farther than this (1–30 mi) |
| **Price** | Skip events above this price; slide all the way left for free only, or all the way right for no limit |
| **Skip late-night / early-morning** | Hide events starting after 10 PM or before 8 AM |
| **Events per week** | How many picks to show (3–12) |
| **Interests** | One slider per type of event: games, art, music, food, crafts, books & ideas, culture, outdoors, social & dating, tech & career. Higher means more picks like it; **Off** pushes that type down |
| **Organizers you like** | Hosts that get a boost, comma-separated |

Click **Save & Refresh** to rescan with the new settings.

### Advanced settings

**Edit Full Profile (JSON)…** in the app menu opens `profile.json`, where you can also change:

- **`lanes.<name>.keywords`**: the words that put an event into that interest. A keyword in the title counts fully; one that appears only in the description counts 60%.
- **`lanes.<name>.search`**: extra Eventbrite searches to run while that interest is switched on.
- **`busy`**: times you're already booked, for example `{"label": "Dinner", "start": "2026-11-07T18:00", "end": "2026-11-07T22:00"}`. Events starting inside those times are skipped.
- **`skip_urls`**: events you never want suggested.
- **`luma_calendars`**: Luma calendar slugs to always check, such as a club you follow.
- **`penalties`**: keywords that push events down.

You can also add new interests: copy any entry in `lanes` and give it a new name, label and keywords. It shows up as a new slider in Preferences.

## How picks are chosen

1. **Fetch:** pull upcoming events from Eventbrite searches, Luma's city feed (plus any calendars you follow), and Partiful's Explore page. Pages are cached for 6 hours.
2. **Filter:** drop events outside your distance, price and time filters, events during your `busy` times, and sold-out events.
3. **Score:** match each event's text against your interests (weighted by your sliders), then apply penalties, a bonus for organizers you like, and adjustments for price and time of day.
4. **Pick:** choose a varied set (runners-up in the "More" tab follow the same variety rules):
   - one strong pick from each site,
   - one "something different" pick from outside your top three interests,
   - then the best of the rest, with at most 2 per day and 1 per organizer, and each repeat of the same interest counting a little less.

Event links open in your default web browser.

## Other cities

The default is New York. To use another city, change the `city` block in `profile.json`:

- **`eventbrite_slug`**: the part of an Eventbrite browse URL like `/d/ca--san-francisco/`.
- **`luma_place_id`**: the `discplace-…` id that appears in the page source of `luma.com/<city>`.
- **`partiful_explore`**: the city's Explore URL.

Also update `timezone`, and the `home` latitude and longitude. The Home base menu in Preferences only lists New York neighborhoods for now.

## Running without the app

```bash
python3 picker.py            # writes output/picks.html and opens it
python3 picker.py --debug    # also prints the top 60 scored events and why
```

This uses only the Python standard library (3.9+).

## Notes

- The app reads public event listings the same way a browser does. The sites can change their pages at any time and break a source. If that happens, the error is shown at the bottom of the picks page.
- Your profile, picks and cache stay on your Mac in `~/Library/Application Support/EventPicks`.
