#!/usr/bin/env python3
"""
SkyWatcher - Terminal Weather Program

Free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software
Foundation, version 3 or any later version.

This program is distributed WITHOUT ANY WARRANTY; without even the implied
warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

DATA SOURCES  (all free, no API key, no account required)
----------------------------------------------------------
Weather:      Open-Meteo          open-meteo.com
Geocoding:    Open-Meteo Geocoding geocoding-api.open-meteo.com
Reverse geo:  Nominatim/OSM       nominatim.openstreetmap.org
Alerts (US):  NOAA/NWS            api.weather.gov
Alerts (CA):  ECCC/MSC            weather.gc.ca
Alerts (EU):  MeteoAlarm          feeds.meteoalarm.org
IP detect:    ip-api.com          (fallback: ipwho.is)

ALERT SEVERITY LEVELS
---------------------
  Extreme          -> level 3  EVACUATE
  Severe           -> level 2  CRITICAL
  Moderate / Minor -> level 1  WARNING
Highest level across all active alerts wins.

VERBOSITY
---------
  0 = Minimal  : condition, temp, feel, high/low, sun times
  1 = Standard : + humidity, wind/gusts, precip, pressure, UV, visibility, cloud
  2 = Detailed : + dew point, hourly 12h strip, 7-day forecast table

TEMPERATURE UNITS
-----------------
  C = Celsius (default)   F = Fahrenheit   Toggle with U key.

OFFLINE CACHE
-------------
  Last successful fetch is saved to disk.  On network failure the cached
  data is shown with a staleness warning so the program keeps working.

CLI FLAGS
---------
  --location "City"  --lat F --lon F  : set location from command line
  --verbosity 0|1|2                   : start at given verbosity
  --unit c|f                          : celsius or fahrenheit
  --once                              : print one reading to stdout and exit
  --help                              : show this text

WEATHER ALERT COVERAGE
----------------------
  US  : NOAA/NWS           api.weather.gov
  CA  : ECCC/MSC           weather.gc.ca
  EU  : MeteoAlarm         feeds.meteoalarm.org  (40+ countries)
  AU  : BoM                bom.gov.au
  NZ  : MetService         metservice.com
  IN  : IMD                mausam.imd.gov.in

  Global : USGS            usgs.gov        -- real-time earthquakes M2.5+ worldwide
                                              (location-aware: within 300km of you)

SEARCH TIPS
-----------
  Use "City, Country" for ambiguous names: "Alexandria, Romania"
  Use "City, Region"  for large countries: "Shanghai, China"
  Results are sorted by population so major cities appear first.

CROSS-PLATFORM
--------------
  Python 3.8+  --  Linux, BSD, macOS, Windows
"""

# ─────────────────────────────────────────────────────────────────────────────
# STANDARD LIBRARY IMPORTS  (zero non-stdlib deps except "requests")
# ─────────────────────────────────────────────────────────────────────────────
import io
import os
import sys
import json
import time
import math
import argparse
import platform
import threading
import datetime
import configparser
import textwrap
import xml.etree.ElementTree as ET

# ─────────────────────────────────────────────────────────────────────────────
# PLATFORM DETECTION
# ─────────────────────────────────────────────────────────────────────────────
_SYS      = platform.system().lower()
IS_WINDOWS = _SYS == "windows"
IS_MACOS   = _SYS == "darwin"
IS_BSD     = "bsd" in _SYS
IS_LINUX   = _SYS == "linux"

import curses
import requests

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
if IS_WINDOWS:
    _BASE = os.environ.get("APPDATA", os.path.expanduser("~"))
else:
    _BASE = os.path.join(os.path.expanduser("~"), ".config")

CONFIG_DIR  = os.path.join(_BASE, "skywatcher")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")
CACHE_FILE  = os.path.join(CONFIG_DIR, "cache.json")
os.makedirs(CONFIG_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def load_config():
    cfg = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w") as fh:
        cfg.write(fh)

def gcv(cfg, section, key, fallback=""):
    """Get config value with fallback."""
    try:
        return cfg.get(section, key)
    except Exception:
        return fallback

# ─────────────────────────────────────────────────────────────────────────────
# DISK CACHE  (keeps last good fetch so the app works on flaky connections)
# ─────────────────────────────────────────────────────────────────────────────
def cache_save(weather_data, alerts, lat, lon):
    try:
        payload = {
            "ts":      datetime.datetime.now().isoformat(),
            "lat":     lat,
            "lon":     lon,
            "weather": weather_data,
            "alerts":  alerts,
        }
        with open(CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:
        pass

def cache_load():
    """Return (weather_data, alerts, timestamp_str) or (None, [], None)."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as fh:
            p = json.load(fh)
        return p.get("weather"), p.get("alerts", []), p.get("ts")
    except Exception:
        return None, [], None

# ─────────────────────────────────────────────────────────────────────────────
# LOCALISATION STRINGS
# ─────────────────────────────────────────────────────────────────────────────
STRINGS = {
    "en": {
        "setup_title":    "LOCATION SETUP",
        "left_panel":     "Search by City / Place",
        "right_panel":    "Enter Coordinates",
        "tab_switch":     "TAB=switch panel  Enter=confirm  Esc=cancel",
        "search_hint":    "Try: 'Alexandria, Romania'  or  'Shanghai, China'",
        "coord_hint":     "Enter lat then lon, press Enter",
        "skip_hint":      "Leave blank to skip",
        "loading":        "Fetching weather...",
        "geocoding":      "Searching...",
        "error_fetch":    "Could not fetch weather.  Check connection.",
        "cached_data":    "OFFLINE -- showing cached data from",
        "no_alert_cover": "No government weather-alert feed for this region.",
        "alert_p":        "[ ! ] ACTIVE ALERT  --  Press P to read",
        "weather_warn":   "! WEATHER WARN",
        "critical_warn":  "!! CRITICAL",
        "evacuation":     "!!! EVACUATE",
        "press_r":        "R:refresh  Q:quit  V:verbosity  U:units  L:lang  S:setup  F12:debug",
        "verbosity_label":"Verbosity",
        "v0":             "Minimal",
        "v1":             "Standard",
        "v2":             "Detailed",
        "lang_label":     "Language",
        "unit_label":     "Temperature Units",
        "unit_c":         "Celsius (C)",
        "unit_f":         "Fahrenheit (F)",
        "debug_title":    "[ DEBUG ]",
        "debug_hint":     "W:cycle warn  0:clear  T:+1h  N:reset time  Z:stress  X:close",
        "today":          "Today",
        "feels_like":     "Feels like",
        "humidity":       "Humidity",
        "wind":           "Wind",
        "precip":         "Precip",
        "uv":             "UV",
        "sunrise":        "Sunrise",
        "sunset":         "Sunset",
        "pressure":       "Pressure",
        "visibility":     "Visibility",
        "cloud":          "Cloud",
        "dew":            "Dew pt",
        "no_alerts":      "No active alerts for your region.",
        "alert_title":    "ACTIVE ALERT",
        "scroll_hint":    "Up/Down/PgUp/PgDn to scroll   B to go back",
        "source":         "Source",
        "last_upd":       "Updated",
        "alert_lvl":      "Level",
        "feel":           "Feel",
        "high":           "High",
        "low":            "Low",
        "no_results":     "No results.  Try: 'City, Country'",
        "select_loc":     "Arrows to pick, Enter to confirm:",
    },
    "ro": {
        "setup_title":    "CONFIGURARE LOCATIE",
        "left_panel":     "Cauta Oras / Loc",
        "right_panel":    "Introduceti Coordonate",
        "tab_switch":     "TAB=schimba panoul  Enter=confirma  Esc=anuleaza",
        "search_hint":    "Scrie numele orasului, Enter pentru cautare",
        "coord_hint":     "Introdu lat apoi lon, apasa Enter",
        "skip_hint":      "Lasa gol pentru a sari",
        "loading":        "Se preiau datele...",
        "geocoding":      "Se cauta...",
        "error_fetch":    "Eroare la preluarea datelor.",
        "cached_data":    "OFFLINE -- date din cache din",
        "no_alert_cover": "Nicio sursa de alerte meteo pentru aceasta regiune.",
        "alert_p":        "[ ! ] ALERTA ACTIVA  --  Apasa P",
        "weather_warn":   "! AVERTIZARE",
        "critical_warn":  "!! CRITICA",
        "evacuation":     "!!! EVACUARE",
        "press_r":        "R:refresh  Q:iesire  V:verbozitate  U:unitati  L:limba  S:setup  F12:debug",
        "verbosity_label":"Verbozitate",
        "v0":             "Minimal",
        "v1":             "Standard",
        "v2":             "Detaliat",
        "lang_label":     "Limba",
        "unit_label":     "Unitati temperatura",
        "unit_c":         "Celsius (C)",
        "unit_f":         "Fahrenheit (F)",
        "debug_title":    "[ DEBUG ]",
        "debug_hint":     "W:ciclu alerta  0:sterge  T:+1h  N:reset timp  Z:stress  X:inchide",
        "today":          "Astazi",
        "feels_like":     "Senzatie",
        "humidity":       "Umiditate",
        "wind":           "Vant",
        "precip":         "Precipitatii",
        "uv":             "UV",
        "sunrise":        "Rasarit",
        "sunset":         "Apus",
        "pressure":       "Presiune",
        "visibility":     "Vizibilitate",
        "cloud":          "Nori",
        "dew":            "Punct roua",
        "no_alerts":      "Nicio alerta activa.",
        "alert_title":    "ALERTA ACTIVA",
        "scroll_hint":    "Sus/Jos/PgUp/PgDn pentru derulare   B pentru a reveni",
        "source":         "Sursa",
        "last_upd":       "Actualizat",
        "alert_lvl":      "Nivel",
        "feel":           "Senzatie",
        "high":           "Max",
        "low":            "Min",
        "no_results":     "Niciun rezultat.",
        "select_loc":     "Sageti pentru selectie, Enter pentru confirmare:",
    },
    "fr": {
        "setup_title":    "CONFIGURATION DU LIEU",
        "left_panel":     "Chercher Ville / Lieu",
        "right_panel":    "Entrer Coordonnees",
        "tab_switch":     "TAB=changer panneau  Entree=confirmer  Esc=annuler",
        "search_hint":    "Taper le nom de la ville, Entree pour chercher",
        "coord_hint":     "Entrer lat puis lon, appuyer Entree",
        "skip_hint":      "Laisser vide pour passer",
        "loading":        "Recuperation meteo...",
        "geocoding":      "Recherche...",
        "error_fetch":    "Impossible de recuperer la meteo.",
        "cached_data":    "HORS LIGNE -- donnees en cache du",
        "no_alert_cover": "Pas de flux d'alertes meteo pour cette region.",
        "alert_p":        "[ ! ] ALERTE ACTIVE  --  Appuyer P",
        "weather_warn":   "! AVERTISSEMENT",
        "critical_warn":  "!! CRITIQUE",
        "evacuation":     "!!! EVACUATION",
        "press_r":        "R:refresh  Q:quitter  V:verbosite  U:unites  L:langue  S:config  F12:debug",
        "verbosity_label":"Verbosite",
        "v0":             "Minimal",
        "v1":             "Standard",
        "v2":             "Detaille",
        "lang_label":     "Langue",
        "unit_label":     "Unites de temperature",
        "unit_c":         "Celsius (C)",
        "unit_f":         "Fahrenheit (F)",
        "debug_title":    "[ DEBUG ]",
        "debug_hint":     "W:cycle alerte  0:effacer  T:+1h  N:reset heure  Z:stress  X:fermer",
        "today":          "Aujourd'hui",
        "feels_like":     "Ressenti",
        "humidity":       "Humidite",
        "wind":           "Vent",
        "precip":         "Precipit.",
        "uv":             "UV",
        "sunrise":        "Lever",
        "sunset":         "Coucher",
        "pressure":       "Pression",
        "visibility":     "Visibilite",
        "cloud":          "Nuages",
        "dew":            "Pt rosee",
        "no_alerts":      "Aucune alerte active pour votre region.",
        "alert_title":    "ALERTE ACTIVE",
        "scroll_hint":    "Haut/Bas/PgUp/PgDn pour defiler   B pour retour",
        "source":         "Source",
        "last_upd":       "Mis a jour",
        "alert_lvl":      "Niveau",
        "feel":           "Ressenti",
        "high":           "Max",
        "low":            "Min",
        "no_results":     "Aucun resultat.",
        "select_loc":     "Fleches pour choisir, Entree pour confirmer:",
    },
    "de": {
        "setup_title":    "ORT EINRICHTEN",
        "left_panel":     "Stadt / Ort Suchen",
        "right_panel":    "Koordinaten Eingeben",
        "tab_switch":     "TAB=Panel wechseln  Eingabe=bestaetigen  Esc=abbrechen",
        "search_hint":    "Stadtname eingeben, Eingabe zum Suchen",
        "coord_hint":     "Breitengrad dann Laengengrad, Eingabe",
        "skip_hint":      "Leer lassen zum Ueberspringen",
        "loading":        "Wetter wird geladen...",
        "geocoding":      "Suche laeuft...",
        "error_fetch":    "Wetterdaten konnten nicht abgerufen werden.",
        "cached_data":    "OFFLINE -- gespeicherte Daten vom",
        "no_alert_cover": "Kein Wetterwarnungsdienst fuer diese Region.",
        "alert_p":        "[ ! ] AKTIVE WARNUNG  --  P druecken",
        "weather_warn":   "! WETTERWARNUNG",
        "critical_warn":  "!! KRITISCH",
        "evacuation":     "!!! EVAKUIERUNG",
        "press_r":        "R:aktualis.  Q:beenden  V:details  U:einheiten  L:sprache  S:ort  F12:debug",
        "verbosity_label":"Detailgrad",
        "v0":             "Minimal",
        "v1":             "Standard",
        "v2":             "Detailliert",
        "lang_label":     "Sprache",
        "unit_label":     "Temperatureinheiten",
        "unit_c":         "Celsius (C)",
        "unit_f":         "Fahrenheit (F)",
        "debug_title":    "[ DEBUG ]",
        "debug_hint":     "W:Warnstufe  0:loeschen  T:+1h  N:Zeit reset  Z:stress  X:schliessen",
        "today":          "Heute",
        "feels_like":     "Gefuehlt",
        "humidity":       "Feuchte",
        "wind":           "Wind",
        "precip":         "Niederschl.",
        "uv":             "UV",
        "sunrise":        "Sonnenaufgang",
        "sunset":         "Sonnenuntergang",
        "pressure":       "Druck",
        "visibility":     "Sicht",
        "cloud":          "Bewoelkung",
        "dew":            "Taupunkt",
        "no_alerts":      "Keine aktiven Warnungen fuer Ihre Region.",
        "alert_title":    "AKTIVE WARNUNG",
        "scroll_hint":    "Auf/Ab/BildAuf/BildAb scrollen   B zurueck",
        "source":         "Quelle",
        "last_upd":       "Aktualisiert",
        "alert_lvl":      "Stufe",
        "feel":           "Gefuehlt",
        "high":           "Max",
        "low":            "Min",
        "no_results":     "Keine Ergebnisse.",
        "select_loc":     "Pfeile zur Auswahl, Eingabe zum Bestaetigen:",
    },
    "es": {
        "setup_title":    "CONFIGURAR UBICACION",
        "left_panel":     "Buscar Ciudad / Lugar",
        "right_panel":    "Introducir Coordenadas",
        "tab_switch":     "TAB=cambiar panel  Intro=confirmar  Esc=cancelar",
        "search_hint":    "Escribir nombre de ciudad, Intro para buscar",
        "coord_hint":     "Introducir lat y lon, pulsar Intro",
        "skip_hint":      "Dejar vacio para omitir",
        "loading":        "Obteniendo el tiempo...",
        "geocoding":      "Buscando...",
        "error_fetch":    "No se pudo obtener el tiempo.",
        "cached_data":    "SIN CONEXION -- datos en cache del",
        "no_alert_cover": "Sin servicio de alertas meteo para esta region.",
        "alert_p":        "[ ! ] ALERTA ACTIVA  --  Pulsar P",
        "weather_warn":   "! AVISO METEOROLOGICO",
        "critical_warn":  "!! CRITICO",
        "evacuation":     "!!! EVACUACION",
        "press_r":        "R:actualizar  Q:salir  V:detalle  U:unidades  L:idioma  S:config  F12:debug",
        "verbosity_label":"Detalle",
        "v0":             "Minimo",
        "v1":             "Estandar",
        "v2":             "Detallado",
        "lang_label":     "Idioma",
        "unit_label":     "Unidades de temperatura",
        "unit_c":         "Celsius (C)",
        "unit_f":         "Fahrenheit (F)",
        "debug_title":    "[ DEBUG ]",
        "debug_hint":     "W:ciclo alerta  0:borrar  T:+1h  N:reset hora  Z:stress  X:cerrar",
        "today":          "Hoy",
        "feels_like":     "Sensacion",
        "humidity":       "Humedad",
        "wind":           "Viento",
        "precip":         "Precipit.",
        "uv":             "UV",
        "sunrise":        "Amanecer",
        "sunset":         "Atardecer",
        "pressure":       "Presion",
        "visibility":     "Visibilidad",
        "cloud":          "Nubosidad",
        "dew":            "Pto rocio",
        "no_alerts":      "No hay alertas activas para su region.",
        "alert_title":    "ALERTA ACTIVA",
        "scroll_hint":    "Arriba/Abajo/RePag/AvPag para desplazar   B para volver",
        "source":         "Fuente",
        "last_upd":       "Actualizado",
        "alert_lvl":      "Nivel",
        "feel":           "Sensacion",
        "high":           "Max",
        "low":            "Min",
        "no_results":     "No se encontraron resultados.",
        "select_loc":     "Flechas para elegir, Intro para confirmar:",
    },
}

LANGUAGES = [
    ("en", "English"),
    ("ro", "Romana"),
    ("fr", "Francais"),
    ("de", "Deutsch"),
    ("es", "Espanol"),
]

# ─────────────────────────────────────────────────────────────────────────────
# WMO WEATHER CODE TABLES
# ─────────────────────────────────────────────────────────────────────────────
_WMO_EN = {
    0:"Clear sky",           1:"Mainly clear",        2:"Partly cloudy",       3:"Overcast",
    45:"Fog",                48:"Depositing rime fog",
    51:"Light drizzle",      53:"Moderate drizzle",   55:"Dense drizzle",
    56:"Lt freezing drizzle",57:"Hvy freezing drizzle",
    61:"Slight rain",        63:"Moderate rain",      65:"Heavy rain",
    66:"Lt freezing rain",   67:"Hvy freezing rain",
    71:"Slight snowfall",    73:"Moderate snowfall",  75:"Heavy snowfall",     77:"Snow grains",
    80:"Slight showers",     81:"Moderate showers",   82:"Violent showers",
    85:"Slight snow showers",86:"Heavy snow showers",
    95:"Thunderstorm",       96:"Thunderstorm + hail",99:"Thunderstorm + heavy hail",
}
_WMO_RO = {
    0:"Cer senin",           1:"Predominant senin",   2:"Partial noros",       3:"Acoperit",
    45:"Ceata",              48:"Ceata cu chiciura",
    51:"Burnita slaba",      53:"Burnita moderata",   55:"Burnita deasa",
    61:"Ploaie slaba",       63:"Ploaie moderata",    65:"Ploaie puternica",
    71:"Ninsoare slaba",     73:"Ninsoare moderata",  75:"Ninsoare puternica",
    80:"Averse slabe",       81:"Averse moderate",    82:"Averse violente",
    95:"Furtuna",            96:"Furtuna cu grindina",99:"Furtuna severa",
}
_WMO_FR = {
    0:"Ciel degage",         1:"Principalement clair",2:"Partiellement nuageux",3:"Couvert",
    45:"Brouillard",         48:"Brouillard givrant",
    51:"Bruine legere",      53:"Bruine moderee",     55:"Bruine dense",
    61:"Pluie faible",       63:"Pluie moderee",      65:"Pluie forte",
    71:"Neige faible",       73:"Neige moderee",      75:"Neige forte",
    80:"Averses faibles",    81:"Averses moderees",   82:"Averses violentes",
    95:"Orage",              96:"Orage avec grele",   99:"Orage avec forte grele",
}
_WMO_DE = {
    0:"Klarer Himmel",       1:"Ueberwiegend klar",   2:"Teils bewoelkt",      3:"Bedeckt",
    45:"Nebel",              48:"Reifnebel",
    51:"Leichter Niesel",    53:"Maessiger Niesel",   55:"Dichter Niesel",
    61:"Leichter Regen",     63:"Maessiger Regen",    65:"Starker Regen",
    71:"Leichter Schneefall",73:"Maessiger Schneefall",75:"Starker Schneefall",
    80:"Leichte Schauer",    81:"Maessige Schauer",   82:"Starke Schauer",
    95:"Gewitter",           96:"Gewitter mit Hagel", 99:"Schweres Gewitter",
}
_WMO_ES = {
    0:"Cielo despejado",     1:"Principalmente claro",2:"Parcialmente nublado",3:"Cubierto",
    45:"Niebla",             48:"Niebla engelante",
    51:"Llovizna ligera",    53:"Llovizna moderada",  55:"Llovizna densa",
    61:"Lluvia ligera",      63:"Lluvia moderada",    65:"Lluvia fuerte",
    71:"Nevada ligera",      73:"Nevada moderada",    75:"Nevada fuerte",
    80:"Chubascos ligeros",  81:"Chubascos moderados",82:"Chubascos violentos",
    95:"Tormenta",           96:"Tormenta con granizo",99:"Tormenta con granizo fuerte",
}
_WMO_BY_LANG = {"en": _WMO_EN, "ro": _WMO_RO, "fr": _WMO_FR, "de": _WMO_DE, "es": _WMO_ES}

def describe_wmo(code, lang="en"):
    tbl = _WMO_BY_LANG.get(lang, _WMO_EN)
    return tbl.get(code, _WMO_EN.get(code, f"Code {code}"))

# ─────────────────────────────────────────────────────────────────────────────
# UNIT CONVERSION & FORMATTING
# ─────────────────────────────────────────────────────────────────────────────
def c_to_f(c):
    """Celsius to Fahrenheit."""
    if c is None:
        return None
    return c * 9.0 / 5.0 + 32.0

def fmt_temp(t, unit="c"):
    """Format a temperature value (always received in Celsius from API)."""
    if t is None:
        return "N/A"
    t = float(t)
    if unit == "f":
        tf = c_to_f(t)
        return f"{tf:+.1f}F" if tf < 0 else f"{tf:.1f}F"
    return f"{t:+.1f}C" if t < 0 else f"{t:.1f}C"

def fmt_time_str(iso_str):
    try:
        return datetime.datetime.fromisoformat(str(iso_str)).strftime("%H:%M")
    except Exception:
        s = str(iso_str)
        return s[11:16] if len(s) >= 16 else s

def wind_direction(deg):
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(float(deg) / 22.5) % 16]

def uv_label(uv, lang="en"):
    if uv is None:
        return "N/A"
    uv = float(uv)
    labels = {
        "en": ["Low","Moderate","High","Very High","Extreme"],
        "ro": ["Scazut","Moderat","Ridicat","F.ridicat","Extrem"],
        "fr": ["Faible","Modere","Eleve","Tres eleve","Extreme"],
        "de": ["Niedrig","Moderat","Hoch","Sehr hoch","Extrem"],
        "es": ["Bajo","Moderado","Alto","Muy alto","Extremo"],
    }
    cats = labels.get(lang, labels["en"])
    if uv < 3:  idx = 0
    elif uv < 6: idx = 1
    elif uv < 8: idx = 2
    elif uv < 11: idx = 3
    else:        idx = 4
    return f"{uv:.0f} {cats[idx]}"

# ─────────────────────────────────────────────────────────────────────────────
# GEOCODING  --  Open-Meteo (free, no key, 300k+ places, fuzzy match)
# ─────────────────────────────────────────────────────────────────────────────
_GEO_URL   = "https://geocoding-api.open-meteo.com/v1/search"
_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_HEADERS   = {"User-Agent": "SkyWatcher/3.0 (free-software terminal weather)"}

def geocode_search(query, count=15, language="en"):
    """
    Smart geocode combining Open-Meteo + Nominatim.
    Handles "Alexandria, Romania", "Shanghai, China", small towns, etc.
    Results sorted: country-hint match first, then by population descending.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    # Parse "City, Country" / "City, Region, Country" format
    parts  = [p.strip() for p in query.split(",")]
    city_p = parts[0]
    cc_hint = None
    _CHINTS = {
        "romania":"RO","china":"CN","india":"IN","united states":"US","usa":"US",
        "united kingdom":"GB","uk":"GB","britain":"GB","england":"GB",
        "germany":"DE","france":"FR","spain":"ES","italy":"IT","russia":"RU",
        "brazil":"BR","canada":"CA","australia":"AU","japan":"JP","mexico":"MX",
        "south korea":"KR","indonesia":"ID","turkey":"TR","ukraine":"UA",
        "poland":"PL","argentina":"AR","sweden":"SE","norway":"NO","denmark":"DK",
        "finland":"FI","netherlands":"NL","holland":"NL","belgium":"BE",
        "switzerland":"CH","austria":"AT","portugal":"PT","czechia":"CZ",
        "czech":"CZ","slovakia":"SK","hungary":"HU","bulgaria":"BG",
        "serbia":"RS","croatia":"HR","greece":"GR","new zealand":"NZ",
        "south africa":"ZA","kenya":"KE","colombia":"CO","chile":"CL",
        "peru":"PE","malaysia":"MY","singapore":"SG","israel":"IL",
        "moldova":"MD","iran":"IR","pakistan":"PK","nigeria":"NG",
        "egypt":"EG","ethiopia":"ET","vietnam":"VN","philippines":"PH",
        "thailand":"TH","saudi arabia":"SA","bangladesh":"BD",
        "myanmar":"MM","burma":"MM","morocco":"MA","ghana":"GH",
        "nepal":"NP","sri lanka":"LK","venezuela":"VE","iraq":"IQ",
        "afghanistan":"AF","tanzania":"TZ","sudan":"SD",
    }
    if len(parts) >= 2:
        for p in parts[1:]:
            pl = p.lower().strip()
            for frag, code in _CHINTS.items():
                if frag in pl or pl in frag:
                    cc_hint = code
                    break
            if cc_hint:
                break

    results = []

    # Open-Meteo: search full query, then city-only if comma present
    for q in ([query] + ([city_p] if "," in query and city_p != query else [])):
        try:
            r = requests.get(
                _GEO_URL,
                params={"name":q,"count":count,"language":language,"format":"json"},
                timeout=8, headers=_HEADERS,
            )
            if r.status_code == 200:
                for res in r.json().get("results", []):
                    results.append({
                        "name":         res.get("name",""),
                        "lat":          str(res.get("latitude","")),
                        "lon":          str(res.get("longitude","")),
                        "country":      res.get("country",""),
                        "country_code": res.get("country_code",""),
                        "admin1":       res.get("admin1",""),
                        "timezone":     res.get("timezone",""),
                        "population":   res.get("population",0) or 0,
                    })
        except Exception:
            pass

    # Nominatim fallback: better for small towns, non-Latin scripts, country filter
    try:
        nom_p = {"q":query,"format":"json","addressdetails":1,"limit":10}
        if cc_hint:
            nom_p["countrycodes"] = cc_hint.lower()
        r2 = requests.get(_NOMINATIM, params=nom_p, timeout=8, headers=_HEADERS)
        if r2.status_code == 200:
            for res in r2.json():
                addr = res.get("address",{})
                name = (addr.get("city") or addr.get("town") or addr.get("village")
                        or addr.get("municipality")
                        or res.get("display_name","").split(",")[0])
                results.append({
                    "name":         name,
                    "lat":          str(res.get("lat","")),
                    "lon":          str(res.get("lon","")),
                    "country":      addr.get("country",""),
                    "country_code": addr.get("country_code","").upper(),
                    "admin1":       addr.get("state",""),
                    "timezone":     "",
                    "population":   int(float(res.get("importance",0))*1_000_000),
                })
    except Exception:
        pass

    # Deduplicate by rounded coordinates
    seen, unique = set(), []
    for res in results:
        try:
            key = (round(float(res["lat"]),2), round(float(res["lon"]),2))
        except Exception:
            continue
        if key not in seen:
            seen.add(key)
            unique.append(res)

    # Sort: country-hint match first, then by population descending
    unique.sort(key=lambda r: (
        0 if (cc_hint and r.get("country_code","").upper()==cc_hint) else 1,
        -(r.get("population") or 0)
    ))
    return unique[:count]


# ─────────────────────────────────────────────────────────────────────────────
# REVERSE GEOCODING  --  Nominatim / OpenStreetMap (no key, 1 req/sec)
# ─────────────────────────────────────────────────────────────────────────────
def reverse_geocode(lat, lon):
    """Return (city_name, country_code) or (None, None)."""
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "accept-language": "en"},
            timeout=8,
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return None, None
        addr = r.json().get("address", {})
        city = (addr.get("city") or addr.get("town") or addr.get("village")
                or addr.get("municipality") or addr.get("county") or "")
        cc   = addr.get("country_code", "").upper()
        return city, cc
    except Exception:
        return None, None

# ─────────────────────────────────────────────────────────────────────────────
# IP-BASED LOCATION DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def detect_location_from_ip():
    """Try two free IP geolocation services.  Returns dict or None."""
    try:
        r = requests.get(
            "http://ip-api.com/json/",
            timeout=6,
            params={"fields": "status,lat,lon,city,countryCode"},
        )
        if r.status_code == 200:
            d = r.json()
            if d.get("status") == "success":
                return {"lat": str(d["lat"]), "lon": str(d["lon"]),
                        "city": d.get("city", ""), "country": d.get("countryCode", "")}
    except Exception:
        pass
    try:
        r = requests.get("https://ipwho.is/", timeout=6)
        if r.status_code == 200:
            d = r.json()
            if d.get("success"):
                return {"lat": str(d["latitude"]), "lon": str(d["longitude"]),
                        "city": d.get("city", ""), "country": d.get("country_code", "")}
    except Exception:
        pass
    return None

# ─────────────────────────────────────────────────────────────────────────────
# WEATHER DATA  --  Open-Meteo (free, no key, NOAA GFS + DWD ICON + ECMWF)
# ─────────────────────────────────────────────────────────────────────────────
_METEO_URL = "https://api.open-meteo.com/v1/forecast"

def fetch_weather(lat, lon):
    params = {
        "latitude":  lat,
        "longitude": lon,
        "current": [
            "temperature_2m","apparent_temperature","weather_code",
            "relative_humidity_2m","wind_speed_10m","wind_direction_10m",
            "wind_gusts_10m","precipitation","surface_pressure",
            "visibility","uv_index","cloud_cover","dew_point_2m",
        ],
        "hourly": [
            "temperature_2m","weather_code",
            "precipitation_probability","wind_speed_10m",
        ],
        "daily": [
            "weather_code","temperature_2m_max","temperature_2m_min",
            "sunrise","sunset","precipitation_sum",
            "precipitation_probability_max","uv_index_max","wind_speed_10m_max",
        ],
        "wind_speed_unit": "kmh",
        "timezone":        "auto",
        "forecast_days":   7,
    }
    r = requests.get(
        _METEO_URL, params=params, timeout=12,
        headers=_HEADERS,
    )
    r.raise_for_status()
    return r.json()

# ─────────────────────────────────────────────────────────────────────────────
# ALERTS  --  United States  (NOAA / NWS)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_alerts_us(lat, lon):
    try:
        r = requests.get(
            f"https://api.weather.gov/alerts/active?point={lat},{lon}",
            timeout=10,
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        alerts = []
        for feat in r.json().get("features", []):
            p = feat.get("properties", {})
            if p.get("status", "Actual").lower() not in ("actual", ""):
                continue
            alerts.append({
                "event":       p.get("event", "Alert"),
                "headline":    p.get("headline", ""),
                "description": p.get("description", ""),
                "instruction": p.get("instruction", ""),
                "severity":    p.get("severity", "Unknown"),
                "urgency":     p.get("urgency", ""),
                "certainty":   p.get("certainty", ""),
                "area":        p.get("areaDesc", ""),
                "expires":     p.get("expires", ""),
                "source":      "NOAA/NWS (api.weather.gov)",
            })
        return alerts
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ALERTS  --  Canada  (ECCC / MSC)
# ─────────────────────────────────────────────────────────────────────────────
_CA_BOXES = [
    ("bc", 48.3,60.0,-139.1,-114.0), ("ab",49.0,60.0,-120.0,-110.0),
    ("sk", 49.0,60.0,-110.0,-101.4), ("mb",49.0,60.0,-102.1, -88.9),
    ("on", 41.7,56.9, -95.2, -74.3), ("qc",44.9,62.6, -79.8, -57.1),
    ("nb", 44.5,48.1, -69.1, -63.8), ("ns",43.3,47.1, -66.4, -59.7),
    ("pe", 45.9,47.1, -64.5, -61.9), ("nl",46.6,60.4, -67.8, -52.6),
    ("yt", 60.0,69.7,-141.0,-123.8), ("nt",60.0,78.6,-136.5, -99.9),
    ("nu", 51.5,83.1,-120.0, -61.0),
]

def _ca_province(lat, lon):
    lat, lon = float(lat), float(lon)
    matches  = []
    for code, la, lb, lo, lp in _CA_BOXES:
        if la <= lat <= lb and lo <= lon <= lp:
            matches.append((math.hypot(lat-(la+lb)/2, lon-(lo+lp)/2), code))
    return min(matches)[1] if matches else None

def fetch_alerts_ca(lat, lon):
    prov = _ca_province(lat, lon)
    if not prov:
        return []
    try:
        r = requests.get(
            f"https://weather.gc.ca/rss/warning/{prov}_e.xml",
            timeout=10,
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        ns   = {"atom": "http://www.w3.org/2005/Atom"}
        alerts = []
        for entry in root.findall("atom:entry", ns):
            title   = entry.findtext("atom:title",   default="", namespaces=ns).strip()
            summary = entry.findtext("atom:summary", default="", namespaces=ns).strip()
            tl = title.lower()
            if not any(k in tl for k in (
                "warning","watch","advisory","statement","alert","evacuat","special weather"
            )):
                continue
            if any(k in tl for k in ("tornado","hurricane","evacuat")):
                sev = "Extreme"
            elif any(k in tl for k in (
                "severe","blizzard","ice storm","storm surge","freezing rain warning","extreme cold"
            )):
                sev = "Severe"
            elif "watch" in tl:
                sev = "Moderate"
            else:
                sev = "Minor"
            alerts.append({
                "event":       title,
                "headline":    title,
                "description": summary,
                "severity":    sev,
                "source":      f"ECCC/MSC weather.gc.ca ({prov.upper()})",
            })
        return alerts[:8]
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ALERTS  --  Europe  (MeteoAlarm / EUMETNET)
# ─────────────────────────────────────────────────────────────────────────────
METEOALARM_COUNTRIES = {
    "AD":"andorra","AT":"austria","BA":"bosnia-herzegovina","BE":"belgium",
    "BG":"bulgaria","BY":"belarus","CH":"switzerland","CY":"cyprus",
    "CZ":"czech-republic","DE":"germany","DK":"denmark","EE":"estonia",
    "ES":"spain","FI":"finland","FR":"france","GB":"united-kingdom",
    "GR":"greece","HR":"croatia","HU":"hungary","IE":"ireland",
    "IL":"israel","IS":"iceland","IT":"italy","LI":"liechtenstein",
    "LT":"lithuania","LU":"luxembourg","LV":"latvia","ME":"montenegro",
    "MK":"north-macedonia","MT":"malta","NL":"netherlands","NO":"norway",
    "PL":"poland","PT":"portugal","RO":"romania","RS":"serbia",
    "SE":"sweden","SI":"slovenia","SK":"slovakia","UK":"united-kingdom",
    # Extended coverage added
    "UA":"ukraine","MD":"moldova","GE":"georgia","AM":"armenia",
    "AL":"albania","XK":"kosovo","TR":"turkey",
}

def fetch_alerts_eu(lat, lon, country):
    name = METEOALARM_COUNTRIES.get((country or "").upper().strip())
    if not name:
        return []
    try:
        r = requests.get(
            f"https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-{name}",
            timeout=10,
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        ns   = {
            "atom": "http://www.w3.org/2005/Atom",
            "cap":  "urn:oasis:names:tc:emergency:cap:1.2",
        }
        alerts = []
        for entry in root.findall("atom:entry", ns):
            title   = entry.findtext("atom:title",   default="Alert", namespaces=ns).strip()
            summary = entry.findtext("atom:summary", default="",      namespaces=ns).strip()
            sev     = "Moderate"
            for info in entry.findall(".//cap:info", ns):
                s = info.findtext("cap:severity", namespaces=ns)
                if s:
                    sev = s
                    break
            alerts.append({
                "event":       title,
                "headline":    title,
                "description": summary,
                "severity":    sev,
                "source":      f"MeteoAlarm/EUMETNET ({country.upper()})",
            })
        return alerts[:5]
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ALERTS  --  Australia  (Bureau of Meteorology)
# ─────────────────────────────────────────────────────────────────────────────
_AU_STATE_BOXES = [
    ("nsw", -37.6,-28.2,140.9,153.6), ("vic",-39.2,-33.9,140.9,150.0),
    ("qld", -29.2,-10.7,138.0,153.6), ("sa", -38.0,-26.0,129.0,141.0),
    ("wa",  -35.1,-13.7,113.1,129.0), ("tas",-43.6,-39.6,144.5,148.5),
    ("nt",  -26.0,-10.9,129.0,138.0), ("act",-35.9,-35.1,148.7,149.4),
]

def _au_state(lat, lon):
    lat, lon = float(lat), float(lon)
    matches  = []
    for code, la, lb, lo, lp in _AU_STATE_BOXES:
        if la <= lat <= lb and lo <= lon <= lp:
            matches.append((math.hypot(lat-(la+lb)/2, lon-(lo+lp)/2), code))
    return min(matches)[1] if matches else "nsw"

def fetch_alerts_au(lat, lon):
    state = _au_state(lat, lon)
    try:
        url = f"http://www.bom.gov.au/fwo/IDZ00056.warnings_{state}.xml"
        r   = requests.get(url, timeout=10,
                           headers=_HEADERS)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        alerts = []
        for prod in root.findall(".//product"):
            title = prod.findtext("amoc/short-description", default="").strip()
            desc  = prod.findtext("forecast/text", default="").strip()
            if not title:
                continue
            tl = title.lower()
            if "severe" in tl or "extreme" in tl or "catastrophic" in tl:
                sev = "Severe"
            else:
                sev = "Minor"
            alerts.append({
                "event":       title,
                "headline":    title,
                "description": desc,
                "severity":    sev,
                "source":      f"Australian BoM ({state.upper()})",
            })
        return alerts[:6]
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ALERTS  --  New Zealand  (MetService)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_alerts_nz():
    try:
        r = requests.get(
            "https://www.metservice.com/publicData/rss",
            timeout=10,
            headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        alerts = []
        for item in root.findall(".//item"):
            title = item.findtext("title", default="").strip()
            desc  = item.findtext("description", default="").strip()
            if not title or not any(k in title.lower() for k in
                                    ("warning","watch","advisory","severe")):
                continue
            sev = "Severe" if "severe" in title.lower() else "Minor"
            alerts.append({
                "event":       title,
                "headline":    title,
                "description": desc,
                "severity":    sev,
                "source":      "MetService NZ",
            })
        return alerts[:5]
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ALERTS  --  India  (India Meteorological Department)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_alerts_in():
    try:
        r = requests.get(
            "https://mausam.imd.gov.in/imd_latest/contents/warning.php",
            timeout=10,
            headers=_HEADERS,
        )
        # IMD does not offer a machine-readable feed; we parse what we can
        if r.status_code != 200:
            return []
        # Simple heuristic: look for warning text in the page
        text = r.text
        alerts = []
        for line in text.splitlines():
            l = line.strip()
            if any(k in l.lower() for k in ("cyclone warning","red alert","orange alert","severe")):
                alerts.append({
                    "event":       l[:120],
                    "headline":    l[:120],
                    "description": "",
                    "severity":    "Severe" if "red" in l.lower() or "cyclone" in l.lower() else "Moderate",
                    "source":      "India IMD (imd.gov.in)",
                })
            if len(alerts) >= 4:
                break
        return alerts
    except Exception:
        return []

# ─────────────────────────────────────────────────────────────────────────────
# ALERT DISPATCHER
# Fetches weather alerts for the current country, plus USGS earthquakes
# (location-aware, fast single JSON call).  All sources run concurrently
# via threads so total time = slowest single source, not sum of all.
# Alerts are cleared immediately on location change so stale alerts from
# the previous location never linger.
# ─────────────────────────────────────────────────────────────────────────────

def _run_in_thread(fn, args, result_list):
    """Helper: call fn(*args), append results to result_list."""
    try:
        result_list.extend(fn(*args))
    except Exception:
        pass


def fetch_alerts_usgs(lat, lon, radius_km=300):
    """
    USGS real-time earthquake feed -- M2.5+ within radius_km.
    Single JSON call, ~200ms, genuinely location-aware. Global.
    """
    try:
        r = requests.get(
            "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
            timeout=8, headers=_HEADERS,
        )
        if r.status_code != 200:
            return []
        lat_f, lon_f = float(lat), float(lon)
        alerts = []
        for feat in r.json().get("features", []):
            p      = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [0, 0, 0])
            eq_lon, eq_lat = float(coords[0]), float(coords[1])
            eq_dep = float(coords[2] or 0)
            dlat   = math.radians(eq_lat - lat_f)
            dlon   = math.radians(eq_lon - lon_f)
            a      = (math.sin(dlat / 2) ** 2
                      + math.cos(math.radians(lat_f))
                      * math.cos(math.radians(eq_lat))
                      * math.sin(dlon / 2) ** 2)
            dist_km = 6371 * 2 * math.asin(min(1.0, math.sqrt(a)))
            if dist_km > radius_km:
                continue
            mag   = float(p.get("mag") or 0)
            place = p.get("place", "Unknown location")
            sev   = ("Extreme"  if mag >= 7.0 else
                     "Severe"   if mag >= 6.0 else
                     "Moderate" if mag >= 5.0 else "Minor")
            ts    = p.get("time")
            tstr  = ""
            if ts:
                try:
                    tstr = datetime.datetime.utcfromtimestamp(
                        ts / 1000).strftime("%H:%M UTC")
                except Exception:
                    pass
            desc = (f"M{mag:.1f} -- {place}\n"
                    f"Distance: {dist_km:.0f} km\n"
                    f"Depth: {eq_dep:.0f} km"
                    + (f"\nTime: {tstr}" if tstr else ""))
            alerts.append({
                "event":       f"M{mag:.1f} Earthquake",
                "headline":    f"M{mag:.1f} -- {place}",
                "description": desc,
                "instruction": "",
                "severity":    sev,
                "area":        place,
                "expires":     "",
                "source":      "USGS Earthquake Hazards (earthquake.usgs.gov)",
                "alert_type":  "weather",
            })
        alerts.sort(key=lambda a: -float(
            a["headline"].split("--")[0].strip().lstrip("M") or 0))
        return alerts[:5]
    except Exception:
        return []


def fetch_alerts(lat, lon, country, city=""):
    """
    Fetch weather alerts for the current country/region, plus USGS earthquakes.
    All sources run in parallel threads -- total wait = slowest source, not sum.
    Returns (alerts_list, weather_covered_bool).
    """
    cu = (country or "").upper().strip()

    # Determine which weather alert source applies
    if   cu == "US":                 wx_fn, wx_args, wx_covered = fetch_alerts_us,  (lat, lon),    True
    elif cu == "CA":                 wx_fn, wx_args, wx_covered = fetch_alerts_ca,  (lat, lon),    True
    elif cu in METEOALARM_COUNTRIES: wx_fn, wx_args, wx_covered = fetch_alerts_eu,  (lat, lon, cu),True
    elif cu == "AU":                 wx_fn, wx_args, wx_covered = fetch_alerts_au,  (lat, lon),    True
    elif cu == "NZ":                 wx_fn, wx_args, wx_covered = fetch_alerts_nz,  (),            True
    elif cu == "IN":                 wx_fn, wx_args, wx_covered = fetch_alerts_in,  (),            True
    else:                            wx_fn, wx_args, wx_covered = None,             (),            False

    # Collect results into lists (thread-safe append)
    wx_results   = []
    usgs_results = []

    threads = []
    if wx_fn:
        t = threading.Thread(target=_run_in_thread,
                             args=(wx_fn, wx_args, wx_results), daemon=True)
        threads.append(t)
    t2 = threading.Thread(target=_run_in_thread,
                          args=(fetch_alerts_usgs, (lat, lon), usgs_results),
                          daemon=True)
    threads.append(t2)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)   # hard cap: never wait more than 10s total

    return wx_results + usgs_results, wx_covered

# ─────────────────────────────────────────────────────────────────────────────
# ALERT CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────
_SEV_LEVEL = {"extreme": 3, "severe": 2, "moderate": 1, "minor": 1}

def classify_alert_level(alerts):
    level = 0
    for a in alerts:
        sev   = a.get("severity", "").lower()
        level = max(level, _SEV_LEVEL.get(sev, 1 if sev else 0))
    return level

# ─────────────────────────────────────────────────────────────────────────────
# COLOUR PAIRS
# ─────────────────────────────────────────────────────────────────────────────
C_DEFAULT  = 1
C_DIM      = 2
C_TITLE    = 3
C_VALUE    = 4
C_LABEL    = 5
C_GOOD     = 6
C_WARN     = 7
C_CRIT     = 8
C_BOX      = 9
C_BANNER_W = 10
C_BANNER_C = 11
C_DEBUG    = 12
C_HEADER   = 13
C_ACCENT   = 14
C_HILIGHT  = 15

# Extra colour slots for new alert types
C_GEO  = 16   # geophysical (yellow bold)
C_SEC  = 17   # security    (red bold)
C_HUM  = 18   # humanitarian (magenta bold)

def alert_type_color(atype):
    return {"weather":"warn","geophysical":"geo","security":"sec","humanitarian":"hum"}.get(atype,"warn")

def cp_alert(atype, bold=True):
    m = {"weather":C_WARN,"geophysical":C_GEO,"security":C_CRIT,"humanitarian":C_ACCENT}
    return cp(m.get(atype, C_WARN), bold=bold)

def init_colors():
    curses.start_color()
    curses.use_default_colors()
    bg    = -1
    has16 = curses.COLORS >= 16
    # Use ANSI bright indices (8-15) when available -- far more readable
    BW = 15 if has16 else curses.COLOR_WHITE    # bright white
    BC = 14 if has16 else curses.COLOR_CYAN     # bright cyan
    BG = 10 if has16 else curses.COLOR_GREEN    # bright green
    BY = 11 if has16 else curses.COLOR_YELLOW   # bright yellow
    BR = 9  if has16 else curses.COLOR_RED      # bright red
    BM = 13 if has16 else curses.COLOR_MAGENTA  # bright magenta
    GR = 8  if has16 else curses.COLOR_BLACK    # grey (dim)
    curses.init_pair(C_DEFAULT,  BW,  bg)
    curses.init_pair(C_DIM,      GR,  bg)
    curses.init_pair(C_TITLE,    BW,  bg)
    curses.init_pair(C_VALUE,    BC,  bg)
    curses.init_pair(C_LABEL,    BW,  bg)
    curses.init_pair(C_GOOD,     BG,  bg)
    curses.init_pair(C_WARN,     BY,  bg)
    curses.init_pair(C_CRIT,     BR,  bg)
    curses.init_pair(C_BOX,      BW,  bg)
    curses.init_pair(C_BANNER_W, BW,  curses.COLOR_BLUE)
    curses.init_pair(C_BANNER_C, BW,  curses.COLOR_RED)
    curses.init_pair(C_DEBUG,    curses.COLOR_BLACK, BC)
    curses.init_pair(C_HEADER,   BW,  bg)
    curses.init_pair(C_ACCENT,   BM,  bg)
    curses.init_pair(C_HILIGHT,  curses.COLOR_BLACK, BC)
    if curses.COLORS >= 16:
        try:
            curses.init_pair(C_GEO, BY, bg)
            curses.init_pair(C_SEC, BR, bg)
            curses.init_pair(C_HUM, BM, bg)
        except Exception:
            pass

def cp(n, bold=True, dim=False):
    """All foreground text is BOLD by default for maximum terminal readability."""
    a = curses.color_pair(n)
    if bold: a |= curses.A_BOLD
    if dim:  a |= curses.A_DIM
    return a

def cp_dim(n):
    """Explicitly non-bold -- for secondary/subdued information."""
    return curses.color_pair(n)

# ─────────────────────────────────────────────────────────────────────────────
# DRAWING PRIMITIVES  (all safe against small terminals)
# ─────────────────────────────────────────────────────────────────────────────
def safestr(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0:
        return
    avail = w - x - 1
    if avail <= 0:
        return
    try:
        win.addstr(y, x, str(text)[:avail], attr)
    except curses.error:
        pass

def centerstr(win, y, text, attr=0):
    _, w = win.getmaxyx()
    safestr(win, y, max(0, (w - len(text)) // 2), text, attr)

def hline(win, y, x=0, char="-", length=None):
    h, w = win.getmaxyx()
    if y < 0 or y >= h:
        return
    n = (w - x - 1) if length is None else min(length, w - x - 1)
    try:
        win.hline(y, x, char, n)
    except curses.error:
        pass

def draw_box(win, y, x, bh, bw, attr=0, title=""):
    safestr(win, y,      x, "+" + "-" * (bw - 2) + "+", attr)
    safestr(win, y+bh-1, x, "+" + "-" * (bw - 2) + "+", attr)
    for row in range(y+1, y+bh-1):
        safestr(win, row, x,      "|", attr)
        safestr(win, row, x+bw-1, "|", attr)
    if title:
        t  = f" {title} "
        tx = x + max(2, (bw - len(t)) // 2)
        safestr(win, y, tx, t, attr | curses.A_BOLD)

# ─────────────────────────────────────────────────────────────────────────────
# DATA COLOURING
# ─────────────────────────────────────────────────────────────────────────────
def temp_color(t):
    """Colour based on Celsius value regardless of display unit."""
    try:
        tv = float(str(t).replace("C","").replace("F","").replace("+",""))
        # If value looks like Fahrenheit, convert back for colouring
        if tv > 60:
            tv = (tv - 32) * 5.0 / 9.0
        if tv <= -15: return cp(C_VALUE, bold=True)
        if tv <= 0:   return cp(C_VALUE)
        if tv <= 14:  return cp(C_GOOD)
        if tv <= 27:  return cp(C_WARN)
        return cp(C_CRIT)
    except Exception:
        return cp(C_VALUE)

def uv_color(uv):
    try:
        u = float(uv)
        if u < 3:  return cp(C_GOOD)
        if u < 6:  return cp(C_WARN)
        return cp(C_CRIT)
    except Exception:
        return cp(C_DEFAULT)

def cloud_color(pct):
    try:
        p = float(pct)
        if p < 25: return cp(C_GOOD)
        if p < 60: return cp(C_WARN)
        return cp(C_DIM)
    except Exception:
        return cp(C_DEFAULT)

def raw_c(val):
    """Return raw Celsius float for colour lookup, ignoring formatting."""
    try:
        return float(val)
    except Exception:
        return 0.0

# ─────────────────────────────────────────────────────────────────────────────
# VERBOSITY 0 -- Minimal
# ─────────────────────────────────────────────────────────────────────────────
def draw_v0(win, data, lang, unit):
    S     = STRINGS[lang]
    cur   = data.get("current", {})
    daily = data.get("daily",   {})
    wmo   = describe_wmo(cur.get("weather_code", 0), lang)
    temp  = cur.get("temperature_2m", 0)
    feel  = cur.get("apparent_temperature", 0)
    tmax  = daily.get("temperature_2m_max", [0])[0]
    tmin  = daily.get("temperature_2m_min", [0])[0]
    rise  = fmt_time_str(daily.get("sunrise", [""])[0])
    sset  = fmt_time_str(daily.get("sunset",  [""])[0])

    safestr(win, 1, 3, wmo, cp(C_DEFAULT, bold=True))
    t_str = fmt_temp(temp, unit)
    safestr(win, 2, 3, t_str, temp_color(raw_c(temp)))
    safestr(win, 2, 3+len(t_str)+2,
            f"{S['feel']}: {fmt_temp(feel, unit)}", cp(C_DIM))

    _, w  = win.getmaxyx()
    hline(win, 4, 2, "-", w-4)
    col2  = w // 2

    safestr(win, 5, 3,    f"{S['high']}:", cp(C_LABEL))
    safestr(win, 5, 10,   fmt_temp(tmax, unit), temp_color(raw_c(tmax)))
    safestr(win, 6, 3,    f"{S['low']}:",  cp(C_LABEL))
    safestr(win, 6, 10,   fmt_temp(tmin, unit), temp_color(raw_c(tmin)))
    safestr(win, 5, col2, f"{S['sunrise']}: {rise}", cp(C_DIM))
    safestr(win, 6, col2, f"{S['sunset']}:  {sset}", cp(C_DIM))

# ─────────────────────────────────────────────────────────────────────────────
# VERBOSITY 1 -- Standard
# ─────────────────────────────────────────────────────────────────────────────
def draw_v1(win, data, lang, last_updated, unit):
    S     = STRINGS[lang]
    h, w  = win.getmaxyx()
    cur   = data.get("current", {})
    daily = data.get("daily",   {})

    wmo   = describe_wmo(cur.get("weather_code", 0), lang)
    temp  = cur.get("temperature_2m", 0)
    feel  = cur.get("apparent_temperature", 0)
    hum   = cur.get("relative_humidity_2m", 0)
    wspd  = cur.get("wind_speed_10m", 0)
    wgust = cur.get("wind_gusts_10m", 0)
    wdir  = wind_direction(cur.get("wind_direction_10m", 0))
    prec  = cur.get("precipitation", 0)
    pres  = cur.get("surface_pressure", 0)
    uv    = cur.get("uv_index", None)
    vis   = cur.get("visibility", None)
    cloud = cur.get("cloud_cover", None)
    tmax  = daily.get("temperature_2m_max", [0])[0]
    tmin  = daily.get("temperature_2m_min", [0])[0]
    rise  = fmt_time_str(daily.get("sunrise", [""])[0])
    sset  = fmt_time_str(daily.get("sunset",  [""])[0])

    safestr(win, 1, 3, wmo, cp(C_DEFAULT, bold=True))
    t_str = fmt_temp(temp, unit)
    safestr(win, 2, 3, t_str, temp_color(raw_c(temp)))
    safestr(win, 2, 3+len(t_str)+2,
            f"{S['feel']}: {fmt_temp(feel, unit)}", cp(C_DIM))
    safestr(win, 3, 3,
            f"{S['high']}: {fmt_temp(tmax, unit)}   {S['low']}: {fmt_temp(tmin, unit)}",
            cp(C_LABEL))

    hline(win, 5, 2, "-", w-4)

    lw   = 12
    col2 = max(w // 2, 36)

    def lv(row, label, value, vc=C_VALUE):
        safestr(win, row, 3,    f"{label}:", cp(C_LABEL))
        safestr(win, row, 3+lw, str(value),  cp(vc))

    lv(6, S["humidity"],  f"{hum}%")
    lv(7, S["wind"],      f"{wspd:.0f} km/h {wdir}  gust {wgust:.0f}")
    lv(8, S["precip"],    f"{prec:.1f} mm")
    lv(9, S["pressure"],  f"{pres:.0f} hPa")

    uv_v  = uv_label(uv, lang)
    vis_s = f"{int(vis/1000)} km" if vis else "N/A"
    cld_s = f"{cloud}%" if cloud is not None else "N/A"

    safestr(win, 6,  col2,    f"{S['uv']}:",         cp(C_LABEL))
    safestr(win, 6,  col2+lw, uv_v,                  uv_color(uv) if uv is not None else cp(C_VALUE))
    safestr(win, 7,  col2,    f"{S['visibility']}:",  cp(C_LABEL))
    safestr(win, 7,  col2+lw, vis_s,                  cp(C_VALUE))
    safestr(win, 8,  col2,    f"{S['cloud']}:",       cp(C_LABEL))
    safestr(win, 8,  col2+lw, cld_s,                  cloud_color(cloud))
    safestr(win, 9,  col2,    f"{S['sunrise']}:",     cp(C_LABEL))
    safestr(win, 9,  col2+lw, f"{rise}   {S['sunset']}: {sset}", cp(C_VALUE))

    if last_updated:
        upd = f"{S['last_upd']}: {last_updated.strftime('%H:%M')}"
        safestr(win, h-2, w-len(upd)-3, upd, cp(C_DIM))
    safestr(win, h-2, 3, "Open-Meteo (NOAA GFS / DWD ICON / ECMWF IFS)", cp_dim(C_DIM))

# ─────────────────────────────────────────────────────────────────────────────
# VERBOSITY 2 -- Detailed  (+ dew point, 12h hourly strip, 7-day table)
# ─────────────────────────────────────────────────────────────────────────────
def draw_v2(win, data, lang, last_updated, unit):
    S      = STRINGS[lang]
    h, w   = win.getmaxyx()
    cur    = data.get("current", {})
    daily  = data.get("daily",   {})
    hourly = data.get("hourly",  {})

    wmo   = describe_wmo(cur.get("weather_code", 0), lang)
    temp  = cur.get("temperature_2m", 0)
    feel  = cur.get("apparent_temperature", 0)
    hum   = cur.get("relative_humidity_2m", 0)
    wspd  = cur.get("wind_speed_10m", 0)
    wgust = cur.get("wind_gusts_10m", 0)
    wdir  = wind_direction(cur.get("wind_direction_10m", 0))
    prec  = cur.get("precipitation", 0)
    pres  = cur.get("surface_pressure", 0)
    uv    = cur.get("uv_index", None)
    vis   = cur.get("visibility", None)
    cloud = cur.get("cloud_cover", None)
    dew   = cur.get("dew_point_2m", None)
    tmax  = daily.get("temperature_2m_max", [0])[0]
    tmin  = daily.get("temperature_2m_min", [0])[0]
    rise  = fmt_time_str(daily.get("sunrise", [""])[0])
    sset  = fmt_time_str(daily.get("sunset",  [""])[0])

    # Rows 1-4: current conditions summary
    safestr(win, 1, 3, wmo, cp(C_DEFAULT, bold=True))
    t_str = fmt_temp(temp, unit)
    safestr(win, 2, 3, t_str, temp_color(raw_c(temp)))
    safestr(win, 2, 3+len(t_str)+2,
            f"{S['feel']}: {fmt_temp(feel, unit)}", cp(C_DIM))

    col2  = max(w // 2, 38)
    safestr(win, 1, col2,
            f"{S['high']}: {fmt_temp(tmax, unit)}   {S['low']}: {fmt_temp(tmin, unit)}", cp(C_LABEL))
    safestr(win, 2, col2,
            f"{S['sunrise']}: {rise}   {S['sunset']}: {sset}", cp(C_DIM))

    uv_v  = uv_label(uv, lang)
    vis_s = f"{int(vis/1000)} km" if vis else "N/A"
    cld_s = f"{cloud}%" if cloud is not None else "N/A"
    dew_s = fmt_temp(dew, unit) if dew is not None else "N/A"

    safestr(win, 3, 3,
            f"{S['humidity']}: {hum}%   {S['wind']}: {wspd:.0f} km/h {wdir} gust {wgust:.0f}",
            cp(C_LABEL))
    safestr(win, 3, col2,
            f"{S['pressure']}: {pres:.0f} hPa   {S['dew']}: {dew_s}", cp(C_LABEL))
    safestr(win, 4, 3,
            f"{S['uv']}: {uv_v}   {S['visibility']}: {vis_s}   {S['cloud']}: {cld_s}",
            cp(C_LABEL))

    hline(win, 5, 2, "-", w-4)

    # Next-12-hour strip
    row_h = 6
    if row_h + 4 < h - 2 and hourly:
        safestr(win, row_h, 3, "Hourly:", cp(C_ACCENT, bold=True))
        h_times  = hourly.get("time", [])
        h_temps  = hourly.get("temperature_2m", [])
        h_precip = hourly.get("precipitation_probability", [])
        now_str  = datetime.datetime.now().strftime("%Y-%m-%dT%H:00")
        start    = 0
        for idx, t in enumerate(h_times):
            if t >= now_str:
                start = idx
                break
        col_x = 12
        for i in range(start, min(start+12, len(h_times))):
            t_lbl = h_times[i][11:16] if i < len(h_times) else ""
            t_tmp = fmt_temp(h_temps[i], unit) if i < len(h_temps) else ""
            t_pp  = (f"{h_precip[i]}%"
                     if (i < len(h_precip) and h_precip[i] is not None) else "")
            cw = max(len(t_lbl), len(t_tmp), len(t_pp)) + 2
            if col_x + cw > w - 2:
                break
            safestr(win, row_h,   col_x, t_lbl, cp(C_DIM))
            safestr(win, row_h+1, col_x, t_tmp,
                    temp_color(raw_c(h_temps[i])) if i < len(h_temps) else cp(C_VALUE))
            if t_pp:
                safestr(win, row_h+2, col_x, t_pp, cp(C_VALUE))
            col_x += cw

    hline(win, row_h+3, 2, "-", w-4)

    # 7-day table
    d_row    = row_h + 4
    d_dates  = daily.get("time", [])
    d_codes  = daily.get("weather_code", [])
    d_maxs   = daily.get("temperature_2m_max", [])
    d_mins   = daily.get("temperature_2m_min", [])
    d_precip = daily.get("precipitation_probability_max", [])
    d_prsum  = daily.get("precipitation_sum", [])

    if d_row < h - 2:
        safestr(win, d_row, 3, "7-day:", cp(C_ACCENT, bold=True))
        for di in range(min(7, len(d_dates))):
            dr = d_row + 1 + di
            if dr >= h - 2:
                break
            try:
                dt      = datetime.date.fromisoformat(d_dates[di])
                day_lbl = dt.strftime("%a %d %b")
            except Exception:
                day_lbl = d_dates[di] if di < len(d_dates) else ""
            code = d_codes[di] if di < len(d_codes) else 0
            mx   = fmt_temp(d_maxs[di], unit)   if di < len(d_maxs) else "N/A"
            mn   = fmt_temp(d_mins[di], unit)    if di < len(d_mins) else "N/A"
            pp   = (f"{d_precip[di]}%"
                    if (di < len(d_precip) and d_precip[di] is not None) else "")
            ps   = (f"{d_prsum[di]:.1f}mm"
                    if (di < len(d_prsum) and d_prsum[di] is not None) else "")
            cond = describe_wmo(code, lang)
            bold = di == 0
            safestr(win, dr, 4,  day_lbl,   cp(C_LABEL, bold=bold))
            safestr(win, dr, 17, cond[:18], cp(C_DEFAULT, bold=bold))
            safestr(win, dr, 37, mx,
                    temp_color(raw_c(d_maxs[di])) if di < len(d_maxs) else cp(C_VALUE))
            safestr(win, dr, 45, mn,
                    temp_color(raw_c(d_mins[di])) if di < len(d_mins) else cp(C_VALUE))
            if pp and 53 < w - 2:
                safestr(win, dr, 53, f"rain {pp} {ps}", cp(C_VALUE))

    if last_updated:
        upd = f"{S['last_upd']}: {last_updated.strftime('%H:%M')}"
        safestr(win, h-2, w-len(upd)-3, upd, cp(C_DIM))
    safestr(win, h-2, 3, "Open-Meteo (NOAA GFS / DWD ICON / ECMWF IFS)", cp_dim(C_DIM))

# ─────────────────────────────────────────────────────────────────────────────
# VERY SMALL TERMINAL FALLBACK
# ─────────────────────────────────────────────────────────────────────────────
def draw_minimal(win, data, lang, unit):
    S     = STRINGS[lang]
    cur   = data.get("current", {})
    daily = data.get("daily",   {})
    wmo   = describe_wmo(cur.get("weather_code", 0), lang)
    temp  = cur.get("temperature_2m", 0)
    tmax  = fmt_temp(daily.get("temperature_2m_max", [0])[0], unit)
    tmin  = fmt_temp(daily.get("temperature_2m_min", [0])[0], unit)
    rise  = fmt_time_str(daily.get("sunrise", [""])[0])
    sset  = fmt_time_str(daily.get("sunset",  [""])[0])
    safestr(win, 1, 2, wmo,                    cp(C_DEFAULT, bold=True))
    safestr(win, 2, 2, fmt_temp(temp, unit),   temp_color(raw_c(temp)))
    safestr(win, 3, 2, f"{tmin} / {tmax}",    cp(C_DIM))
    safestr(win, 4, 2,
            f"{S['sunrise']}: {rise}  {S['sunset']}: {sset}", cp(C_DIM))

# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCREEN
# ─────────────────────────────────────────────────────────────────────────────
def draw_main(stdscr, state, verb, lang, city, country, ds,
              lat="", lon="", fake_time=None, unit="c"):
    S    = STRINGS[lang]
    stdscr.clear()
    h, w = stdscr.getmaxyx()

    with state.lock:
        wd = state.weather_data
        al = list(state.alerts)
        lu = state.last_updated
        is_cached       = state.from_cache
        alert_covered   = state.alert_covered

    alert_level = ds.fake_warning if ds.fake_warning else classify_alert_level(al)
    now         = fake_time if fake_time else datetime.datetime.now()

    # Row 0: datetime + verbosity + unit indicator
    dt_str = now.strftime("%a %d %b %Y  %H:%M:%S")
    safestr(stdscr, 0, 1, dt_str, cp(C_HEADER, bold=True))
    v_labels = {0:"MIN", 1:"STD", 2:"DTL"}
    unit_lbl  = "F" if unit == "f" else "C"
    v_str     = f"[{v_labels.get(verb,'?')} | {unit_lbl}]"
    safestr(stdscr, 0, w-len(v_str)-2, v_str, cp(C_DIM))

    # Row 1: title bar
    loc_parts = []
    if city:    loc_parts.append(city)
    if country: loc_parts.append(f"[{country}]")
    loc_str   = "  ".join(loc_parts)
    title_str = f"  SkyWatcher  {('-- ' + loc_str + '  ') if loc_str else ''}"
    centerstr(stdscr, 1, title_str, cp(C_TITLE, bold=True))

    # Blinking alert label (top-left of title bar)
    if alert_level > 0:
        alert_labels = {1: S["weather_warn"], 2: S["critical_warn"], 3: S["evacuation"]}
        alert_lcolor = {1: C_WARN, 2: C_CRIT, 3: C_CRIT}
        albl = f"  {alert_labels.get(alert_level, S['weather_warn'])}  "
        safestr(stdscr, 1, 1, albl,
                cp(alert_lcolor[alert_level]) | curses.A_BLINK)

    hline(stdscr, 2)

    # Offline / alert banners
    content_y = 3
    if is_cached and wd is not None:
        cached_ts = ""
        try:
            cached_ts = lu.strftime("%H:%M %d %b") if lu else ""
        except Exception:
            pass
        banner = f"  {S['cached_data']} {cached_ts}  "
        centerstr(stdscr, 3, banner, cp(C_BANNER_W, bold=True))
        content_y = 4
    # Only show "no weather feed" notice when there are truly zero alerts
    # from ANY source. A country like Ukraine has no MeteoAlarm feed but
    # will have State Dept Level-4 / FCDO advisories in `al` already.
    if not alert_covered and country and not al:
        msg = f"  {S['no_alert_cover']}  "
        centerstr(stdscr, content_y, msg, cp_dim(C_DIM))
        content_y += 1
    if alert_level > 0:
        bc  = C_BANNER_C if alert_level >= 2 else C_BANNER_W
        msg = f"  {S['alert_p']}  "
        centerstr(stdscr, content_y, msg, cp(bc, bold=True))
        content_y += 1

    # Weather box
    bottom_bar_h = 2
    box_y = content_y
    box_h = h - box_y - bottom_bar_h
    box_w = w - 2

    if box_h < 5 or box_w < 20:
        if wd:
            draw_minimal(stdscr, wd, lang, unit)
    elif wd is None:
        draw_box(stdscr, box_y, 1, box_h, box_w, cp(C_BOX))
        centerstr(stdscr, box_y + box_h//2, S["error_fetch"], cp(C_CRIT))
    else:
        draw_box(stdscr, box_y, 1, box_h, box_w, cp(C_BOX))
        try:
            inner = stdscr.derwin(box_h, box_w, box_y, 1)
            if   verb == 0: draw_v0(inner, wd, lang, unit)
            elif verb == 1: draw_v1(inner, wd, lang, lu, unit)
            else:           draw_v2(inner, wd, lang, lu, unit)
        except curses.error:
            pass

    # Bottom bar
    hline(stdscr, h-2)
    safestr(stdscr, h-1, 1, S["press_r"], cp(C_DIM))
    if lat and lon:
        try:
            coord_str = f"({float(lat):.4f}, {float(lon):.4f})"
            safestr(stdscr, h-2, w - len(coord_str) - 2, coord_str, cp(C_DIM))
        except (ValueError, TypeError):
            pass

    if ds.active:
        draw_debug_overlay(stdscr, ds, lang, al, lat=lat, lon=lon)

    stdscr.refresh()

# ─────────────────────────────────────────────────────────────────────────────
# DEBUG OVERLAY  (F12)
# ─────────────────────────────────────────────────────────────────────────────
class DebugState:
    active       = False
    fake_warning = 0
    fake_time    = None
    stress_mode  = False

def draw_debug_overlay(stdscr, ds, lang, alerts, lat="", lon=""):
    S    = STRINGS[lang]
    h, w = stdscr.getmaxyx()
    level = ds.fake_warning if ds.fake_warning else classify_alert_level(alerts)
    try:
        coord_disp = f"{float(lat):.4f}, {float(lon):.4f}" if lat and lon else "not set"
    except (ValueError, TypeError):
        coord_disp = f"{lat}, {lon}"
    lines = [
        f" {S['debug_title']}                    ",
        f" Coords   : {coord_disp}            ",
        f" Warn lvl : {ds.fake_warning} (real: {classify_alert_level(alerts)}) ",
        f" Eff level: {level}                    ",
        f" Stress   : {'ON' if ds.stress_mode else 'OFF'}       ",
        f" Fake time: {ds.fake_time.strftime('%H:%M') if ds.fake_time else 'real'}   ",
        f" OS       : {platform.system()} {platform.release()[:10]}    ",
        f" Python   : {sys.version[:12]}          ",
        f" {S['debug_hint']}              ",
    ]
    bw = max(len(l) for l in lines) + 2
    bh = len(lines) + 2
    by = h - bh - 1
    bx = w - bw - 1
    draw_box(stdscr, by, bx, bh, bw, cp(C_DEBUG))
    for i, line in enumerate(lines):
        safestr(stdscr, by+1+i, bx+1, line[:bw-2].ljust(bw-2), cp(C_DEBUG))

# ─────────────────────────────────────────────────────────────────────────────
# ALERT DETAIL SCREEN
# ─────────────────────────────────────────────────────────────────────────────
def show_alert_screen(stdscr, alerts, lang):
    S      = STRINGS[lang]
    scroll = 0
    lines  = []
    h0, w0 = stdscr.getmaxyx()

    for i, a in enumerate(alerts):
        if i > 0:
            lines += ["", "-"*50, ""]
        sev   = a.get("severity", "")
        level = _SEV_LEVEL.get(sev.lower(), 0)
        lines.append(f"  {a.get('event', 'Alert').upper()}")
        lines.append(f"  {S['source']}: {a.get('source', '')}")
        lines.append(f"  {S['alert_lvl']}: {sev}  (SkyWatcher level {level})")
        if a.get("area"):
            lines.append(f"  Area: {a['area']}")
        if a.get("expires"):
            lines.append(f"  Expires: {str(a['expires'])[:16]}")
        lines.append("")
        desc = a.get("description", a.get("headline", ""))
        for para in desc.split("\n"):
            for wrapped in textwrap.wrap(para, w0 - 6) or [""]:
                lines.append(f"  {wrapped}")
        instr = a.get("instruction", "")
        if instr:
            lines += ["", "  --- Instructions ---"]
            for para in instr.split("\n"):
                for wrapped in textwrap.wrap(para, w0 - 6) or [""]:
                    lines.append(f"  {wrapped}")

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        hline(stdscr, 0)
        centerstr(stdscr, 0, f"  {S['alert_title']}  ", cp(C_CRIT, bold=True))
        hline(stdscr, 1)
        visible = h - 4
        for i, line in enumerate(lines[scroll: scroll+visible]):
            safestr(stdscr, 2+i, 0, line, cp(C_DEFAULT))
        hline(stdscr, h-2)
        total = len(lines)
        if total > visible:
            pct = int(scroll / max(1, total - visible) * 100)
            safestr(stdscr, h-2, w-8, f" {pct:3d}% ", cp(C_DIM))
        centerstr(stdscr, h-1, S["scroll_hint"], cp(C_DIM))
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord('b'), ord('B')):
            break
        elif key == curses.KEY_DOWN  and scroll + visible < total: scroll += 1
        elif key == curses.KEY_UP    and scroll > 0:               scroll -= 1
        elif key == curses.KEY_NPAGE: scroll = min(scroll + visible, max(0, total - visible))
        elif key == curses.KEY_PPAGE: scroll = max(0, scroll - visible)

# ─────────────────────────────────────────────────────────────────────────────
# PICKER OVERLAY  (verbosity / language / units)
# ─────────────────────────────────────────────────────────────────────────────
def _picker(stdscr, title, opts, current_idx):
    h, w    = stdscr.getmaxyx()
    visible = min(len(opts), h - 6)
    bh      = visible + 4
    bw      = min(max(len(o) for o in opts) + 8, w - 4)
    by      = max(0, h - bh - 2)
    bx      = 2
    sel     = current_idx
    scroll  = max(0, sel - visible + 1)

    while True:
        draw_box(stdscr, by, bx, bh, bw, cp(C_BOX), title)
        for i in range(visible):
            idx  = scroll + i
            if idx >= len(opts): break
            mark = ">" if idx == sel else " "
            attr = cp(C_HILIGHT, bold=True) if idx == sel else cp(C_DEFAULT)
            safestr(stdscr, by+2+i, bx+2, f"{mark} {opts[idx][:bw-6]}", attr)
        safestr(stdscr, by+bh-1, bx+2, " ENTER=ok  ESC=cancel ", cp(C_DIM))
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_UP:
            if sel > 0:
                sel -= 1
                if sel < scroll: scroll = sel
        elif key == curses.KEY_DOWN:
            if sel < len(opts)-1:
                sel += 1
                if sel >= scroll + visible: scroll = sel - visible + 1
        elif key in (10, 13):
            return sel
        elif key == 27:
            return current_idx

def pick_verbosity(stdscr, current, lang):
    S = STRINGS[lang]
    return _picker(stdscr, S["verbosity_label"], [S["v0"], S["v1"], S["v2"]], current)

def pick_language(stdscr, current, lang):
    S   = STRINGS[lang]
    idx = next((i for i, (c, _) in enumerate(LANGUAGES) if c == current), 0)
    sel = _picker(stdscr, S["lang_label"], [name for _, name in LANGUAGES], idx)
    return LANGUAGES[sel][0]

def pick_unit(stdscr, current, lang):
    S   = STRINGS[lang]
    idx = 1 if current == "f" else 0
    sel = _picker(stdscr, S["unit_label"], [S["unit_c"], S["unit_f"]], idx)
    return "f" if sel == 1 else "c"

# ─────────────────────────────────────────────────────────────────────────────
# LOCATION SETUP SCREEN
# ─────────────────────────────────────────────────────────────────────────────
def run_setup(stdscr, cfg, lang):
    S            = STRINGS[lang]
    left_query   = ""
    left_results = []
    left_sel     = -1
    right_lat    = gcv(cfg, "location", "lat", "")
    right_lon    = gcv(cfg, "location", "lon", "")
    active_panel = 0
    status_msg   = ""

    def redraw():
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        curses.curs_set(0)
        curses.noecho()
        draw_box(stdscr, 0, 0, h, w, cp(C_BOX))
        centerstr(stdscr, 0, f"  {S['setup_title']}  ", cp(C_TITLE, bold=True))
        safestr(stdscr, h-1, 2, S["tab_switch"], cp(C_DIM))

        split = w // 2
        la    = cp(C_ACCENT, bold=True) if active_panel == 0 else cp(C_BOX)
        ra    = cp(C_ACCENT, bold=True) if active_panel == 1 else cp(C_BOX)
        draw_box(stdscr, 1, 1,     h-2, split-1,   la, S["left_panel"])
        draw_box(stdscr, 1, split, h-2, w-split-1, ra, S["right_panel"])

        # Left panel
        safestr(stdscr, 3, 4, S["search_hint"], cp_dim(C_DIM))
        safestr(stdscr, 5, 4, "Query:", cp(C_LABEL))
        q_disp = left_query[-(split-14):] if len(left_query) > split-14 else left_query
        safestr(stdscr, 5, 11, q_disp + ("_" if active_panel == 0 else ""),
                cp(C_VALUE, bold=True))

        if left_results:
            hdr = f"  {'City':<22} {'Region':<15} {'Country':<18} CC   Pop"
            safestr(stdscr, 7, 4, hdr[:split-6], cp_dim(C_DIM))
            for i, res in enumerate(left_results[:h-12]):
                name  = (res.get("name") or "")[:21]
                reg   = (res.get("admin1") or "")[:14]
                cntry = (res.get("country") or "")[:17]
                cc    = (res.get("country_code") or "")[:3]
                pop   = res.get("population") or 0
                pops  = (f"{pop//1000}k" if pop>=1000 else str(pop)) if pop else ""
                label = f"  {name:<22} {reg:<15} {cntry:<18} {cc:<4} {pops}"
                is_sel = i == left_sel
                attr   = cp(C_HILIGHT) if is_sel else cp(C_DEFAULT)
                if is_sel: label = ">" + label[1:]
                safestr(stdscr, 8+i, 4, label[:split-6], attr)
            if 0 <= left_sel < len(left_results):
                r = left_results[left_sel]
                try:
                    coord_preview = f"  coords: {float(r['lat']):.4f}, {float(r['lon']):.4f}"
                    safestr(stdscr, h-3, 4, coord_preview, cp(C_GOOD))
                except Exception: pass
            if len(left_results) >= 14:
                safestr(stdscr, h-4, 4,
                        "Many results -- add ', Country' to narrow"[:split-6], cp(C_WARN))
        elif left_query and not status_msg:
            safestr(stdscr, 8, 4, S["no_results"][:split-6], cp(C_WARN))

        # Right panel
        rx = split + 3
        safestr(stdscr, 3, rx, S["coord_hint"], cp(C_DIM))
        safestr(stdscr, 4, rx, S["skip_hint"],  cp(C_DIM))
        safestr(stdscr, 6, rx,    "Latitude: ", cp(C_LABEL))
        safestr(stdscr, 6, rx+10, right_lat[:w-split-14] if right_lat else "",
                cp(C_VALUE, bold=True))
        safestr(stdscr, 7, rx,    "Longitude:", cp(C_LABEL))
        safestr(stdscr, 7, rx+10, right_lon[:w-split-14] if right_lon else "",
                cp(C_VALUE, bold=True))
        if right_lat and right_lon:
            try:
                la2 = float(right_lat); lo2 = float(right_lon)
                ok  = -90 <= la2 <= 90 and -180 <= lo2 <= 180
                safestr(stdscr, 9, rx,
                        "Coordinates valid." if ok else "Out of valid range!",
                        cp(C_GOOD) if ok else cp(C_CRIT))
            except Exception:
                safestr(stdscr, 9, rx, "Invalid numbers.", cp(C_CRIT))

        if status_msg:
            centerstr(stdscr, h-2, status_msg[:w-4], cp(C_WARN))
        stdscr.refresh()

    stdscr.nodelay(False)
    while True:
        redraw()
        key = stdscr.getch()

        if key == 9:        # TAB
            active_panel = 1 - active_panel
            status_msg   = ""

        elif key == 27:     # ESC -- keep whatever was saved
            break

        elif key in (10, 13, curses.KEY_ENTER):
            if active_panel == 0:
                if left_results and left_sel >= 0:
                    res = left_results[left_sel]
                    if not cfg.has_section("location"):
                        cfg.add_section("location")
                    cfg.set("location", "lat",     res["lat"])
                    cfg.set("location", "lon",     res["lon"])
                    cfg.set("location", "city",    res["name"])
                    cfg.set("location", "country", res["country_code"])
                    save_config(cfg)
                    break
                elif left_query.strip():
                    status_msg   = S["geocoding"]
                    redraw()
                    left_results = geocode_search(left_query, language=lang)
                    left_sel     = 0 if left_results else -1
                    status_msg   = "" if left_results else S["no_results"]
            else:
                try:
                    la2 = float(right_lat)
                    lo2 = float(right_lon)
                    if not (-90 <= la2 <= 90 and -180 <= lo2 <= 180):
                        status_msg = "Coordinates out of range."
                        continue
                    status_msg = "Reverse geocoding..."
                    redraw()
                    city_r, cc_r = reverse_geocode(la2, lo2)
                    if not cfg.has_section("location"):
                        cfg.add_section("location")
                    cfg.set("location", "lat",     right_lat)
                    cfg.set("location", "lon",     right_lon)
                    cfg.set("location", "city",    city_r or "")
                    cfg.set("location", "country", cc_r   or "")
                    save_config(cfg)
                    break
                except (ValueError, TypeError):
                    status_msg = "Enter valid decimal numbers for lat and lon."

        elif active_panel == 0:
            if key in (curses.KEY_BACKSPACE, 127, 8):
                left_query   = left_query[:-1]
                left_results = []
                left_sel     = -1
                status_msg   = ""
            elif key == curses.KEY_DOWN and left_results:
                left_sel = min(left_sel + 1, len(left_results) - 1)
            elif key == curses.KEY_UP   and left_results:
                left_sel = max(0, left_sel - 1)
            elif 32 <= key <= 126:
                left_query  += chr(key)
                left_results = []
                left_sel     = -1
                status_msg   = ""

        else:   # right panel -- inline field edit
            h2, w2 = stdscr.getmaxyx()
            rx     = w2 // 2 + 3
            curses.echo()
            curses.curs_set(1)
            for row, attr_name, cur_val in (
                (6, "right_lat", right_lat),
                (7, "right_lon", right_lon),
            ):
                safestr(stdscr, row, rx+10, " " * 24, cp(C_VALUE))
                if cur_val:
                    safestr(stdscr, row, rx+10, cur_val, cp(C_VALUE, bold=True))
                stdscr.move(row, rx+10)
                stdscr.refresh()
                try:
                    v = stdscr.getstr(row, rx+10, 22).decode("utf-8", errors="replace").strip()
                    if v:
                        if attr_name == "right_lat":
                            right_lat = v
                        else:
                            right_lon = v
                except Exception:
                    pass
            curses.noecho()
            curses.curs_set(0)

    curses.noecho()
    curses.curs_set(0)
    return cfg

# ─────────────────────────────────────────────────────────────────────────────
# FIRST-RUN LOCATION CONFIRM SCREEN
# ─────────────────────────────────────────────────────────────────────────────
def location_confirm_screen(stdscr, detected):
    lat   = detected["lat"]
    lon   = detected["lon"]
    city  = detected["city"]
    cntry = detected["country"]

    stdscr.clear()
    h, w = stdscr.getmaxyx()
    centerstr(stdscr, h//2-2, "SkyWatcher", cp(C_TITLE, bold=True))
    hline(stdscr, h//2-1)
    centerstr(stdscr, h//2, "Verifying detected location with live weather...", cp(C_DIM))
    stdscr.refresh()

    live_temp = None
    timezone  = "unknown"
    try:
        wd       = fetch_weather(float(lat), float(lon))
        t        = wd.get("current", {}).get("temperature_2m")
        timezone = wd.get("timezone", "unknown")
        if t is not None:
            live_temp = fmt_temp(t, "c")
    except Exception:
        pass

    stdscr.clear()
    h, w = stdscr.getmaxyx()
    bh, bw = 17, min(70, w-4)
    by, bx = max(0, (h-bh)//2), max(0, (w-bw)//2)
    draw_box(stdscr, by, bx, bh, bw, cp(C_BOX), "Detected Location -- Please Verify")

    loc_str = f"{city}  [{cntry}]" if cntry else city
    safestr(stdscr, by+2,  bx+3,  "City/Country:", cp(C_LABEL))
    safestr(stdscr, by+2,  bx+17, loc_str,          cp(C_VALUE, bold=True))
    safestr(stdscr, by+3,  bx+3,  "Latitude:    ", cp(C_LABEL))
    safestr(stdscr, by+3,  bx+17, f"{float(lat):.6f}", cp(C_VALUE, bold=True))
    safestr(stdscr, by+4,  bx+3,  "Longitude:   ", cp(C_LABEL))
    safestr(stdscr, by+4,  bx+17, f"{float(lon):.6f}", cp(C_VALUE, bold=True))
    safestr(stdscr, by+5,  bx+3,  "Timezone:    ", cp(C_LABEL))
    safestr(stdscr, by+5,  bx+17, timezone,           cp(C_DIM))
    hline(stdscr, by+6, bx+1, "-", bw-2)

    if live_temp is not None:
        safestr(stdscr, by+7, bx+3,  "Live temp:   ", cp(C_LABEL))
        safestr(stdscr, by+7, bx+17, live_temp,        temp_color(raw_c(float(lat))))
        safestr(stdscr, by+8, bx+3,  "Weather data fetched OK.", cp(C_GOOD))
    else:
        safestr(stdscr, by+7, bx+3, "Could not fetch weather (no internet?).", cp(C_WARN))

    hline(stdscr, by+9,  bx+1, "-", bw-2)
    safestr(stdscr, by+10, bx+3, "IMPORTANT: IP-based detection is approximate.", cp(C_WARN))
    safestr(stdscr, by+11, bx+3, "Verify the timezone and coordinates match your actual city.", cp(C_DIM))
    safestr(stdscr, by+12, bx+3, "If wrong: press S and use the city search instead.", cp(C_DIM))
    hline(stdscr, by+13, bx+1, "-", bw-2)
    centerstr(stdscr, by+14, "ENTER = accept this location", cp(C_GOOD, bold=True))
    centerstr(stdscr, by+15, "S = search for my city manually", cp(C_WARN))
    stdscr.refresh()

    stdscr.nodelay(False)
    while True:
        key = stdscr.getch()
        if key in (10, 13, curses.KEY_ENTER): return True
        if key in (ord('s'), ord('S')):        return False

# ─────────────────────────────────────────────────────────────────────────────
# APP STATE & BACKGROUND REFRESH
# ─────────────────────────────────────────────────────────────────────────────
class AppState:
    def __init__(self):
        self.weather_data  = None
        self.alerts        = []
        self.loading       = False
        self.error         = False
        self.last_updated  = None
        self.from_cache    = False
        self.alert_covered = True
        self.lock          = threading.Lock()

def refresh_data(state, lat, lon, country, city=""):
    """
    Fetch weather then alerts in two independent try/except blocks.
    Weather failure -> fall back to disk cache and show OFFLINE banner.
    Alert failure   -> silently keep previous alerts; never marks data as cached.
    """
    state.loading = True
    state.error   = False

    # ── Step 1: Fetch weather (the only thing that triggers cached-data mode) ─
    weather_ok = False
    try:
        wd = fetch_weather(float(lat), float(lon))
        with state.lock:
            state.weather_data = wd
            state.last_updated = datetime.datetime.now()
            state.from_cache   = False
        weather_ok = True
    except Exception:
        # Try disk cache as a fallback
        cached_wd, cached_al, cached_ts = cache_load()
        if cached_wd is not None:
            try:
                ts = datetime.datetime.fromisoformat(cached_ts) if cached_ts else None
            except Exception:
                ts = None
            with state.lock:
                state.weather_data = cached_wd
                state.last_updated = ts
                state.from_cache   = True
                state.alerts       = cached_al
        state.error = True

    # ── Step 2: Fetch alerts independently (never cause cached-data banner) ───
    if weather_ok:
        try:
            al, covered = fetch_alerts(lat, lon, country, city)
            with state.lock:
                state.alerts        = al
                state.alert_covered = covered
            # Save to cache only after both succeed
            with state.lock:
                wd_snap = state.weather_data
                al_snap = state.alerts
            cache_save(wd_snap, al_snap, lat, lon)
        except Exception:
            # Keep whatever alerts we had; don't touch from_cache or error flag
            pass

    state.loading = False

# ─────────────────────────────────────────────────────────────────────────────
# MAIN CURSES LOOP
# ─────────────────────────────────────────────────────────────────────────────
def main(stdscr, cli_args=None):
    init_colors()
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)

    cfg     = load_config()
    lang    = gcv(cfg, "prefs", "lang",        "en")
    verb    = int(gcv(cfg, "prefs", "verbosity","1"))
    unit    = gcv(cfg, "prefs", "unit",        "c")
    lat     = gcv(cfg, "location", "lat",      "")
    lon     = gcv(cfg, "location", "lon",      "")
    city    = gcv(cfg, "location", "city",     "")
    country = gcv(cfg, "location", "country",  "")

    # Apply CLI overrides
    if cli_args:
        if cli_args.verbosity is not None:
            verb = cli_args.verbosity
        if cli_args.unit:
            unit = cli_args.unit.lower()
        if cli_args.lat and cli_args.lon:
            lat, lon = str(cli_args.lat), str(cli_args.lon)
            city    = gcv(cfg, "location", "city",    "")
            country = gcv(cfg, "location", "country", "")

    if not lat or not lon:
        stdscr.nodelay(False)
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        centerstr(stdscr, h//2-2, "SkyWatcher", cp(C_TITLE, bold=True))
        hline(stdscr, h//2-1)
        centerstr(stdscr, h//2, "Detecting location from IP address...", cp(C_DIM))
        stdscr.refresh()

        detected = detect_location_from_ip()
        if detected:
            accepted = location_confirm_screen(stdscr, detected)
            if accepted:
                lat     = detected["lat"]
                lon     = detected["lon"]
                city    = detected["city"]
                country = detected["country"]
                if not cfg.has_section("location"):
                    cfg.add_section("location")
                cfg.set("location", "lat",     lat)
                cfg.set("location", "lon",     lon)
                cfg.set("location", "city",    city)
                cfg.set("location", "country", country)
                save_config(cfg)
            else:
                cfg     = run_setup(stdscr, cfg, lang)
                lat     = gcv(cfg, "location", "lat",     "")
                lon     = gcv(cfg, "location", "lon",     "")
                city    = gcv(cfg, "location", "city",    "")
                country = gcv(cfg, "location", "country", "")
        else:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            centerstr(stdscr, h//2-2, "SkyWatcher", cp(C_TITLE, bold=True))
            hline(stdscr, h//2-1)
            centerstr(stdscr, h//2,
                      "IP auto-detect failed. Press ENTER to set location manually.",
                      cp(C_DEFAULT))
            stdscr.refresh()
            while stdscr.getch() not in (10, 13, curses.KEY_ENTER):
                pass
            cfg     = run_setup(stdscr, cfg, lang)
            lat     = gcv(cfg, "location", "lat",     "")
            lon     = gcv(cfg, "location", "lon",     "")
            city    = gcv(cfg, "location", "city",    "")
            country = gcv(cfg, "location", "country", "")

        stdscr.nodelay(True)

    if not lat or not lon:
        stdscr.nodelay(False)
        stdscr.clear()
        h, w = stdscr.getmaxyx()
        centerstr(stdscr, h//2, "No location set. Press Q to quit, S to retry setup.",
                  cp(C_CRIT))
        stdscr.refresh()
        while True:
            k = stdscr.getch()
            if k in (ord('q'), ord('Q')): return
            if k in (ord('s'), ord('S')):
                cfg     = run_setup(stdscr, cfg, lang)
                lat     = gcv(cfg, "location", "lat",     "")
                lon     = gcv(cfg, "location", "lon",     "")
                city    = gcv(cfg, "location", "city",    "")
                country = gcv(cfg, "location", "country", "")
                if lat and lon: break

    state = AppState()
    ds    = DebugState()
    t     = threading.Thread(target=refresh_data, args=(state, lat, lon, country, city), daemon=True)
    t.start()

    auto_interval = 600   # seconds between automatic refreshes
    last_refresh  = time.time()

    while True:
        if state.loading and state.weather_data is None:
            h, w = stdscr.getmaxyx()
            stdscr.clear()
            centerstr(stdscr, h//2-1, "SkyWatcher", cp(C_TITLE, bold=True))
            S = STRINGS[lang]
            centerstr(stdscr, h//2,   S["loading"],  cp(C_DIM))
            stdscr.refresh()
            time.sleep(0.1)
            if stdscr.getch() in (ord('q'), ord('Q')): break
            continue

        draw_main(stdscr, state, verb, lang, city, country, ds,
                  lat=lat, lon=lon, fake_time=ds.fake_time, unit=unit)

        if time.time() - last_refresh > auto_interval or ds.stress_mode:
            last_refresh = time.time()
            if not state.loading:
                t = threading.Thread(target=refresh_data, args=(state, lat, lon, country, city), daemon=True)
                t.start()

        time.sleep(0.1)
        key = stdscr.getch()
        if key == -1:
            continue

        S = STRINGS[lang]
        with state.lock:
            al = list(state.alerts)
        alert_level = ds.fake_warning if ds.fake_warning else classify_alert_level(al)

        if key in (ord('q'), ord('Q')):
            break

        elif key in (ord('r'), ord('R')):
            if not state.loading:
                t = threading.Thread(target=refresh_data, args=(state, lat, lon, country, city), daemon=True)
                t.start()
                last_refresh = time.time()

        elif key in (ord('p'), ord('P')):
            if alert_level > 0:
                disp = al if not ds.fake_warning else [{
                    "event":       S["critical_warn"],
                    "description": "DEBUG: Simulated alert.\n\nThis is only a test.",
                    "source":      "Debug Mode",
                    "severity":    ["", "Minor", "Severe", "Extreme"][ds.fake_warning],
                    "headline":    "Test Alert",
                }]
                if disp:
                    show_alert_screen(stdscr, disp, lang)

        elif key in (ord('v'), ord('V')):
            stdscr.nodelay(False)
            verb = pick_verbosity(stdscr, verb, lang)
            if not cfg.has_section("prefs"): cfg.add_section("prefs")
            cfg.set("prefs", "verbosity", str(verb))
            save_config(cfg)
            stdscr.nodelay(True)

        elif key in (ord('u'), ord('U')):
            stdscr.nodelay(False)
            unit = pick_unit(stdscr, unit, lang)
            if not cfg.has_section("prefs"): cfg.add_section("prefs")
            cfg.set("prefs", "unit", unit)
            save_config(cfg)
            stdscr.nodelay(True)

        elif key in (ord('l'), ord('L')):
            stdscr.nodelay(False)
            lang = pick_language(stdscr, lang, lang)
            if not cfg.has_section("prefs"): cfg.add_section("prefs")
            cfg.set("prefs", "lang", lang)
            save_config(cfg)
            stdscr.nodelay(True)

        elif key in (ord('s'), ord('S')):
            stdscr.nodelay(False)
            cfg     = run_setup(stdscr, cfg, lang)
            lat     = gcv(cfg, "location", "lat",     "")
            lon     = gcv(cfg, "location", "lon",     "")
            city    = gcv(cfg, "location", "city",    "")
            country = gcv(cfg, "location", "country", "")
            # Clear stale alerts from the previous location immediately
            with state.lock:
                state.alerts        = []
                state.alert_covered = True
            stdscr.nodelay(True)
            if lat and lon and not state.loading:
                t = threading.Thread(target=refresh_data, args=(state, lat, lon, country, city), daemon=True)
                t.start()

        elif key == curses.KEY_F12:
            ds.active = not ds.active

        elif ds.active:
            if   key in (ord('x'), ord('X')): ds.active = False
            elif key in (ord('w'), ord('W')): ds.fake_warning = (ds.fake_warning+1) % 4
            elif key == ord('0'):             ds.fake_warning = 0
            elif key in (ord('t'), ord('T')):
                base = ds.fake_time or datetime.datetime.now()
                ds.fake_time = base + datetime.timedelta(hours=1)
            elif key in (ord('n'), ord('N')): ds.fake_time  = None
            elif key in (ord('z'), ord('Z')): ds.stress_mode = not ds.stress_mode

# ─────────────────────────────────────────────────────────────────────────────
# CLI  (--help, --once, --location, --lat, --lon, --verbosity, --unit)
# ─────────────────────────────────────────────────────────────────────────────
def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="skywatcher",
        description="SkyWatcher -- free terminal weather.  No API key required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python run.py
              python run.py --location "Berlin"
              python run.py --lat 51.5074 --lon -0.1278 --unit f --verbosity 2
              python run.py --once --unit f
        """),
    )
    p.add_argument("--location",   metavar="CITY",
                   help="Set location by city name (geocoded on first run)")
    p.add_argument("--lat",        type=float, metavar="LAT",
                   help="Latitude  (decimal degrees)")
    p.add_argument("--lon",        type=float, metavar="LON",
                   help="Longitude (decimal degrees)")
    p.add_argument("--verbosity",  type=int, choices=[0,1,2], metavar="0|1|2",
                   help="Display verbosity: 0=minimal 1=standard 2=detailed")
    p.add_argument("--unit",       choices=["c","f","C","F"], metavar="c|f",
                   help="Temperature units: c=Celsius f=Fahrenheit")
    p.add_argument("--once",       action="store_true",
                   help="Print one reading to stdout and exit (no TUI)")
    return p

def run_once(lat, lon, unit="c"):
    """Fetch weather and print a plain-text summary to stdout."""
    print(f"SkyWatcher -- fetching weather for ({lat}, {lon}) ...")
    try:
        wd   = fetch_weather(float(lat), float(lon))
        cur  = wd.get("current", {})
        daily= wd.get("daily",   {})
        temp = cur.get("temperature_2m", 0)
        feel = cur.get("apparent_temperature", 0)
        wmo  = describe_wmo(cur.get("weather_code", 0))
        hum  = cur.get("relative_humidity_2m", 0)
        wspd = cur.get("wind_speed_10m", 0)
        wdir = wind_direction(cur.get("wind_direction_10m", 0))
        tmax = daily.get("temperature_2m_max", [0])[0]
        tmin = daily.get("temperature_2m_min", [0])[0]
        print(f"Condition : {wmo}")
        print(f"Temp      : {fmt_temp(temp, unit)}  (feels {fmt_temp(feel, unit)})")
        print(f"High/Low  : {fmt_temp(tmax, unit)} / {fmt_temp(tmin, unit)}")
        print(f"Humidity  : {hum}%")
        print(f"Wind      : {wspd:.0f} km/h {wdir}")
        print(f"Source    : Open-Meteo (NOAA GFS / DWD ICON / ECMWF IFS)")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# TERMINAL CHECK
# ─────────────────────────────────────────────────────────────────────────────
def _check_terminal():
    if sys.stdout is None or sys.stderr is None:
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Run SkyWatcher from Command Prompt or PowerShell.\n\n    python run.py",
                    "SkyWatcher -- Terminal Required", 0x10,
                )
            except Exception:
                pass
        sys.exit("Error: SkyWatcher requires a real terminal.")
    try:
        sys.stdout.fileno()
    except (AttributeError, io.UnsupportedOperation):
        if IS_WINDOWS:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Run SkyWatcher from Command Prompt or PowerShell.\n\n    python run.py",
                    "SkyWatcher -- Terminal Required", 0x10,
                )
            except Exception:
                pass
        sys.exit("Error: SkyWatcher requires a real terminal.")

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def run(argv=None):
    parser   = build_arg_parser()
    cli_args = parser.parse_args(argv)

    # Resolve --location to lat/lon if provided without coordinates
    if cli_args.location and not (cli_args.lat and cli_args.lon):
        results = geocode_search(cli_args.location)
        if not results:
            print(f"Could not geocode '{cli_args.location}'.", file=sys.stderr)
            sys.exit(1)
        cli_args.lat = float(results[0]["lat"])
        cli_args.lon = float(results[0]["lon"])
        print(f"Resolved '{cli_args.location}' to "
              f"{cli_args.lat:.4f}, {cli_args.lon:.4f}  ({results[0]['country']})")

    unit = (cli_args.unit or "c").lower()

    if cli_args.once:
        # Resolve coordinates from config if not given on CLI
        if not (cli_args.lat and cli_args.lon):
            cfg = load_config()
            lat = gcv(cfg, "location", "lat", "")
            lon = gcv(cfg, "location", "lon", "")
            if not lat or not lon:
                print("No location configured.  Use --location or run without --once first.",
                      file=sys.stderr)
                sys.exit(1)
            cli_args.lat = float(lat)
            cli_args.lon = float(lon)
        run_once(cli_args.lat, cli_args.lon, unit)
        return

    _check_terminal()
    try:
        curses.wrapper(main, cli_args)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    run()
