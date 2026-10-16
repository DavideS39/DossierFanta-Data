"""
scraper/scrape_fantacalcio.py — Scraper del listone Serie A da fantacalcio.it.

Pipeline (M3 — design doc DossierFanta v2.5, Cap.7):
  1. GET https://www.fantacalcio.it/quotazioni-fantacalcio (httpx)
  2. Parse HTML con BeautifulSoup
  3. Estrae per ogni giocatore: name, team, ruolo_fanta, ruolo_reale, fvm,
     quotazione_ufficiale
  4. Valida il risultato (assertion 400-800 giocatori, 20 squadre, range FVM)
  5. Scrive players.json nella root del repo

Il JSON in output ha struttura:
  {
    "version": "YYYY-MM-DD",
    "source": "fantacalcio.it",
    "scraped_at": "ISO 8601 UTC",
    "players": [ { "name": "...", "team": "...", "ruolo_fanta": "P|D|C|A",
                    "ruolo_reale": "...", "fvm": N, "quotazione_ufficiale": N } ]
  }

Selettori HTML (se fantacalcio.it cambia layout, modificare qui):
  - Tabella principale: div.player-list oppure table.quotazioni
  - Riga giocatore: tr.player-row oppure tbody tr (con data-player attribute)
  - Nome: td.name oppure .player-name
  - Squadra: td.team oppure .player-team
  - Ruolo fantacalcio: td.role oppure .player-role (P/D/C/A)
  - Ruolo reale: td.role-real oppure .player-role-real
  - FVM: td.fvm oppure .player-fvm
  - Quotazione ufficiale: td.quotation oppure .player-quotation

Fallback adattivo: se i selettori primari non matchano (>=10 entry),
si prova con selettori alternativi (paragraph div con class player-card),
poi si solleva AssertionError chiaro.

Exit code: 0 se OK + players.json scritto, !=0 se errore o assertion fallita.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Iterable

import httpx
from bs4 import BeautifulSoup, Tag


URL = "https://www.fantacalcio.it/quotazioni-fantacalcio"
USER_AGENT = (
    "Mozilla/5.0 (compatible; DossierFanta/0.3.0; "
    "+https://github.com/dossierfanta/dossier-fanta-data)"
)
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "players.json"

# Soglie di sanity check (assertion del design doc)
MIN_PLAYERS = 400
MAX_PLAYERS = 800
EXPECTED_TEAMS = 20
FVM_MIN = 1
# M3.1 (FIX A1): fantacalcio.it pubblica FVM grezzi fino a ~425 (es. Martinez 410,
# Malen 425). La normalizzazione a 1-100 con budget 500 avviene lato app
# (import_listone Rust, vedi design doc Cap.7). Lo scraper valida solo che il
# range grezzo è plausibile [1, 500]. Precedentemente FVM_MAX=100 mascherava
# il bug B1 perché l'assertion falliva sui dati reali prima ancora di
# verificare la coverage, confondendo i sintomi.
FVM_MAX = 500


def fetch_html(url: str = URL, *, timeout: float = 30.0) -> str:
    """GET HTML con httpx. Solleva RuntimeError su errore di rete."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


def normalize_name(raw: str) -> str:
    """Capitalize + trim + collapse spazi multipli."""
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw.strip())
    if not cleaned:
        return ""
    # Capitalize la prima lettera, lascia il resto (cognomi italiani tipo "D'Amico")
    return cleaned[0].upper() + cleaned[1:]


def normalize_team(raw: str) -> str:
    """Come normalize_name ma per squadre (es. 'inter' → 'Inter')."""
    return normalize_name(raw)


def parse_player_row(row: Tag) -> dict | None:
    """Estrae un giocatore da una riga/tabella. None se skip (header o vuoto).

    Selettori (aggiornati M3.1 — bug B1 dell'ispezione M3):
      HTML reale fantacalcio.it (ottobre 2026):
        <tr class="player-row" data-filter-role-classic="a" ...>
          <th class="player-role player-role-classic">
            <span class="role" data-value="a"></span>
          </th>
          <th class="player-name">...<span>Martinez L.</span></th>
          <td class="player-team">INT</td>
          <td class="player-classic-current-price">36</td>
          <td class="player-classic-fvm">410</td>
          <td class="player-mantra-fvm">410</td>
          <th class="player-role player-role-mantra">
            <span class="role" data-value="PC" title="Punta Centravanti"></span>
          </th>
        </tr>

      Regole di estrazione (in ordine di priorità, fallback inclusi):
        name         → td.name | th.player-name | .player-name
        team         → td.team | .player-team
        ruolo_fanta  → attr data-filter-role-classic sul <tr>, fallback
                       attr data-value sul nested span.role (entrambi lowercase,
                       normalizzati a uppercase P|D|C|A)
        ruolo_reale  → attr title sul nested span.role dentro
                       .player-role-mantra (es. "Punta Centravanti")
        fvm          → td.player-classic-fvm | td.player-mantra-fvm |
                       td.fvm | .player-fvm
        quotazione   → td.player-classic-current-price |
                       td.player-mantra-current-price | td.quotation |
                       .player-quotation
    """
    # --- name (aggiunto th.player-name per HTML reale fantacalcio.it)
    name_el = row.select_one(
        "td.name, th.player-name, .player-name"
    )
    # --- team (inalterato)
    team_el = row.select_one("td.team, .player-team")
    # --- ruolo_reale: title del nested span dentro .player-role-mantra
    role_real_el = row.select_one(".player-role-mantra .role[title]")
    # --- fvm: privilegia player-classic-fvm, fallback player-mantra-fvm
    fvm_el = row.select_one(
        "td.player-classic-fvm, td.player-mantra-fvm, td.fvm, .player-fvm"
    )
    # --- quotazione: privilegia player-classic-current-price (QA)
    quot_el = row.select_one(
        "td.player-classic-current-price, "
        "td.player-mantra-current-price, "
        "td.quotation, .player-quotation"
    )

    # Fallback adattivo: se manca il nome nel DOM, prova con data attribute
    if not name_el:
        name = row.get("data-player") or row.get("data-name")
        if not name:
            return None
    else:
        name = name_el.get_text(strip=True)

    name = normalize_name(name)
    if not name:
        return None

    team = normalize_team(team_el.get_text(strip=True)) if team_el else ""

    # --- ruolo_fanta: leggere attribute data-filter-role-classic sul <tr>,
    # fallback data-value sul nested span.role
    ruolo_fanta: str | None = row.get("data-filter-role-classic")
    if not ruolo_fanta:
        nested_role_span = row.select_one("[data-value]")
        if nested_role_span is not None:
            ruolo_fanta = nested_role_span.get("data-value")
    # Normalizza: lowercase 'a' → 'A', 'd' → 'D', 'c' → 'C', 'p' → 'P'
    if ruolo_fanta:
        ruolo_fanta = ruolo_fanta.strip().upper()
        if ruolo_fanta not in ("P", "D", "C", "A"):
            # Se il sito ha role tipo "Por" "Dif" "Cen" "Att", prendi la 1a lettera
            first = ruolo_fanta[:1]
            if first in ("P", "D", "C", "A"):
                ruolo_fanta = first
            else:
                ruolo_fanta = None

    # --- ruolo_reale: attr title del nested span (es. "Punta Centravanti")
    ruolo_reale = ""
    if role_real_el is not None:
        ruolo_reale = (role_real_el.get("title") or "").strip()

    fvm = _parse_int(fvm_el.get_text(strip=True) if fvm_el else "")
    quotazione = _parse_int(quot_el.get_text(strip=True) if quot_el else "")

    return {
        "name": name,
        "team": team or None,
        "ruolo_fanta": ruolo_fanta,
        "ruolo_reale": ruolo_reale or None,
        "fvm": fvm,
        "quotazione_ufficiale": quotazione,
    }


def _parse_int(text: str) -> int | None:
    """Estrae un intero da testo tipo '38 f₥' o '38' o ''."""
    if not text:
        return None
    digits = re.search(r"-?\d+", text.replace("\u20a5", "").replace(",", ""))
    if not digits:
        return None
    try:
        return int(digits.group(0))
    except ValueError:
        return None


def parse_html(html: str) -> list[dict]:
    """Parsa l'HTML e ritorna la lista di giocatori.

    Strategia a cascata (M3.1):
      1. Selettori primari (HTML reale fantacalcio.it ottobre 2026):
         - tr.player-row (con attr data-filter-role-classic)
         - table.quotazioni tbody tr (legacy)
         - tbody tr[data-player] (legacy)
      2. Se <10 match, fallback su layout card-based.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strategia 1: tabella quotazioni + tr.player-row (HTML reale fantacalcio.it)
    rows = soup.select(
        "tr.player-row, "
        "table.quotazioni tbody tr, "
        "div.player-list tr.player-row, "
        "table tbody tr[data-player]"
    )

    # Strategia 2: card-based layout (fallback)
    if len(rows) < MIN_PLAYERS:
        rows = soup.select(
            "div.player-card, "
            "div.player-list div.row, "
            "tbody tr"
        )

    players: list[dict] = []
    seen_names: set[str] = set()
    for row in rows:
        p = parse_player_row(row)
        if not p:
            continue
        # Dedup per (name, team) lowercase — il sito occasionalmente duplica
        key = (p["name"].lower(), (p["team"] or "").lower())
        if key in seen_names:
            continue
        seen_names.add(key)
        players.append(p)

    return players


def assert_valid(players: list[dict]) -> None:
    """Assertion del design doc: 400-800 giocatori, 20 squadre, FVM sensato.

    Aggiuntivo M3.1 (FIX A3 — bug B1 ispezione M3): assertion separata sul
    coverage FVM — se <90% dei giocatori ha fvm non-None, i selettori HTML
    sono probabilmente rotti e il parser va aggiornato.
    """
    if not (MIN_PLAYERS <= len(players) <= MAX_PLAYERS):
        raise AssertionError(
            f"Numero giocatori {len(players)} fuori range "
            f"[{MIN_PLAYERS}, {MAX_PLAYERS}]. "
            "Struttura HTML di fantacalcio.it probabilmente cambiata — "
            "aggiornare i selettori in scraper/scrape_fantacalcio.py."
        )

    teams = {p["team"] for p in players if p["team"]}
    if len(teams) != EXPECTED_TEAMS:
        raise AssertionError(
            f"Squadre trovate: {len(teams)} (attese {EXPECTED_TEAMS}). "
            f"Squadre: {sorted(teams)}."
        )

    # FIX A3 — assertion coverage FVM (bug B1 ispezione M3): se la quota di
    # giocatori con fvm non-None è < 90%, i selettori sono rotti. Precedentemente
    # l'assertion saltava il check FVM se la lista era vuota, mascherando il bug.
    fvm_count = sum(1 for p in players if p.get("fvm") is not None)
    if fvm_count < len(players) * 0.9:
        raise AssertionError(
            f"Solo {fvm_count}/{len(players)} giocatori con FVM non-None. "
            f"Selettori HTML probabilmente rotti — aggiornare "
            f"scraper/scrape_fantacalcio.py (funzione parse_player_row)."
        )

    fvms = [p["fvm"] for p in players if p["fvm"] is not None]
    if fvms:
        fvm_min, fvm_max = min(fvms), max(fvms)
        if not (FVM_MIN <= fvm_min <= FVM_MAX) or not (FVM_MIN <= fvm_max <= FVM_MAX):
            raise AssertionError(
                f"Range FVM non sensato: min={fvm_min}, max={fvm_max}. "
                f"Atteso {FVM_MIN}-{FVM_MAX}."
            )

    # Campi obbligatori: name sempre presente (verificato in parse_player_row)
    missing_name = [p for p in players if not p.get("name")]
    if missing_name:
        raise AssertionError(
            f"Trovati {len(missing_name)} giocatori senza name obbligatorio."
        )


def build_output(players: list[dict]) -> dict:
    """Costruisce il dict JSON finale con metadata."""
    today = _dt.date.today().isoformat()
    scraped_at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    fvms = [p["fvm"] for p in players if p["fvm"] is not None]
    teams = sorted({p["team"] for p in players if p["team"]})
    fvm_min = min(fvms) if fvms else None
    fvm_max = max(fvms) if fvms else None
    return {
        "version": today,
        "source": "fantacalcio.it",
        "scraped_at": scraped_at,
        "stats": {
            "players_count": len(players),
            "teams_count": len(teams),
            "fvm_min": fvm_min,
            "fvm_max": fvm_max,
        },
        "players": players,
    }


def write_output(data: dict, path: Path = OUTPUT_PATH) -> Path:
    """Scrive JSON formattato su path (default: players.json in root repo)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def log_summary(data: dict) -> None:
    """Log human-readable a stdout (GitHub Actions log)."""
    stats = data.get("stats", {})
    print("=" * 60, file=sys.stdout)
    print(f"[DossierFanta] scraping fantacalcio.it — versione {data['version']}")
    print(f"  giocatori:  {stats.get('players_count', 0)}")
    print(f"  squadre:    {stats.get('teams_count', 0)}")
    print(f"  range FVM:  {stats.get('fvm_min')} - {stats.get('fvm_max')}")
    print(f"  scraped_at: {data['scraped_at']}")
    print("=" * 60, file=sys.stdout)


def main() -> int:
    try:
        html = fetch_html()
    except Exception as e:
        print(f"[FATAL] fetch fantacalcio.it fallita: {e}", file=sys.stderr)
        return 2

    try:
        players = parse_html(html)
        assert_valid(players)
    except AssertionError as e:
        print(f"[ASSERT] {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"[FATAL] parse HTML fallito: {e}", file=sys.stderr)
        return 4

    data = build_output(players)
    out = write_output(data)
    log_summary(data)
    print(f"[OK] scritto {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
