# SkyWatcher

SkyWatcher is a terminal weather program I built because I believe weather information should be freely accessible to everyone, without accounts, without API keys, and without giving your data to a company just to know if it's going to rain. Weather is a public resource. The data that powers SkyWatcher comes entirely from free, open government and scientific sources: Open-Meteo for forecasts, NOAA/NWS for US alerts, ECCC for Canada, MeteoAlarm for Europe, and more. You don't sign up for anything. You just run it.

The program follows software freedom principles. You can read the source, modify it, share it, and run it however you like under the terms of the GNU General Public License v3.

---

## Features

- Current conditions, hourly strip, and 7-day forecast
- Live weather alerts for US, Canada, Europe, Australia, New Zealand, and India
- Real-time earthquake alerts worldwide via USGS (within 300km of your location)
- Offline mode: if your connection drops, it shows cached data with a staleness warning
- Three verbosity levels: minimal, standard, detailed
- Celsius or Fahrenheit, toggled at any time
- Languages: English, Romanian, French, German, Spanish
- Auto-detects your location from IP on first run, or you can search by city name
- No API key, no account, no tracking

---

## Requirements

Python 3.8 or newer and the `requests` library. The launcher handles installing `requests` automatically if it's missing.

---

## How to run

```
python run.py
```

On first launch it will detect your location and ask you to confirm it. If it gets it wrong, press S to search for your city manually.

**CLI options:**

```
python run.py --location "Berlin"
python run.py --lat 51.5074 --lon -0.1278 --unit f --verbosity 2
python run.py --once --unit f
```

`--once` prints a plain-text weather summary and exits, no TUI. Useful for scripts.

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| R | Refresh weather now |
| V | Change verbosity level |
| U | Toggle Celsius / Fahrenheit |
| L | Change language |
| S | Change location |
| P | Read active alert details |
| Q | Quit |
| F12 | Debug overlay |

---

## Platform notes

SkyWatcher is written to run on any platform Python 3.8+ supports.

**Linux:** Works out of the box on any modern distro. The `curses` module is part of the standard library. Just install Python and run `python run.py`. Tested on Debian, Ubuntu, Fedora, and Arch.

**BSD (FreeBSD, OpenBSD, NetBSD):** Python's `curses` binds directly to the system ncurses library, which ships with every major BSD. Install Python from ports or pkgsrc (`pkg install python3` on FreeBSD), then run as normal. No additional dependencies.

**macOS:** Python 3 is available via Homebrew (`brew install python`) or the official installer from python.org. The built-in Terminal app and iTerm2 both work well. `curses` is included in the standard library on macOS. Run with `python3 run.py`.

**Windows:** Install Python 3.8+ from python.org and make sure "Add Python to PATH" is checked. Run SkyWatcher from Command Prompt or PowerShell, not by double-clicking. The `windows-curses` package is not required because `requests` is the only dependency and SkyWatcher uses the stdlib `curses` module. If curses is unavailable on your Windows Python build, install `windows-curses` via pip. The app will display a helpful message if it detects it is not running in a real terminal.

---

## Data sources

Free with no account required.

| Data | Source |
|------|--------|
| Weather forecasts | Open-Meteo (open-meteo.com) |
| Geocoding | Open-Meteo Geocoding + Nominatim/OSM |
| US alerts | NOAA/NWS (api.weather.gov) |
| Canada alerts | ECCC/MSC (weather.gc.ca) |
| Europe alerts | MeteoAlarm/EUMETNET (40+ countries) |
| Australia alerts | Bureau of Meteorology |
| New Zealand alerts | MetService |
| India alerts | IMD (mausam.imd.gov.in) |
| Earthquakes | USGS (earthquake.usgs.gov) |
| IP geolocation | ip-api.com / ipwho.is |

---

## License

GNU General Public License v3 or later. See licence in project page for more info.
