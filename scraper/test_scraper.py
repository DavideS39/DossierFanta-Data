"""
scraper/test_scraper.py — Test di regressione del parser.

NON fa richieste HTTP reali: usa fixture HTML salvate in fixtures/.
Verifica:
  - Il parser estrae correttamente nome/squadra/ruolo/fvm/quotazione
  - L'assertion su numero giocatori fallisce se <400 o >800
  - La normalizzazione dei nomi (capitalize, trim, spazi multipli)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Permetti `python test_scraper.py` dalla root del repo senza installare il pkg
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from scrape_fantacalcio import (  # noqa: E402
    MIN_PLAYERS,
    MAX_PLAYERS,
    assert_valid,
    build_output,
    normalize_name,
    normalize_team,
    parse_html,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> str:
    p = FIXTURES_DIR / name
    if not p.exists():
        # Fallback: fixture minimale inline (per smoke test senza fixture file)
        return _minimal_fixture()
    return p.read_text(encoding="utf-8")


def _minimal_fixture() -> str:
    """Fixture minimale con 5 giocatori, 2 squadre — per smoke test base.

    M3.1 (FIX A4): struttura HTML allineata al layout reale di fantacalcio.it
    verificato a ottobre 2026 (vedi fixtures/fanta_real_row.html). Mantiene
    anche i td.name/td.team legacy come fallback per verificare la backward
    compat del parser con fixture vecchie.
    """
    return """
    <html><body>
    <table class="quotazioni"><tbody>
      <tr class="player-row" data-player="Lautaro Martinez" data-filter-role-classic="a">
        <th class="player-role player-role-classic"><span class="role" data-value="a"></span></th>
        <th class="player-role player-role-mantra">
          <div class="pill role-pill"><span class="role role-mantra" data-value="pc" title="Punta"></span></div>
        </th>
        <th class="player-name"><a class="player-link"><span>Lautaro Martinez</span></a></th>
        <td class="player-team">Inter</td>
        <td class="player-classic-current-price">65</td>
        <td class="player-classic-fvm">38</td>
        <td class="player-mantra-fvm">38</td>
        <td class="name">Lautaro Martinez</td>
        <td class="team">Inter</td>
        <td class="role">A</td>
        <td class="fvm">38</td>
        <td class="quotation">65</td>
      </tr>
      <tr class="player-row" data-filter-role-classic="a">
        <th class="player-role player-role-classic"><span class="role" data-value="a"></span></th>
        <th class="player-role player-role-mantra">
          <span class="role role-mantra" data-value="pc" title="Punta"></span>
        </th>
        <th class="player-name"><span>  Marcus   Thuram  </span></th>
        <td class="player-team">inter</td>
        <td class="player-classic-current-price">58</td>
        <td class="player-classic-fvm">34</td>
      </tr>
      <tr class="player-row" data-filter-role-classic="c">
        <th class="player-role player-role-classic"><span class="role" data-value="c"></span></th>
        <th class="player-role player-role-mantra">
          <span class="role role-mantra" data-value="m" title="Centrocampista"></span>
        </th>
        <th class="player-name"><span>Nicolò Barella</span></th>
        <td class="player-team">Inter</td>
        <td class="player-classic-current-price">52</td>
        <td class="player-classic-fvm">30</td>
      </tr>
      <tr class="player-row" data-filter-role-classic="a">
        <th class="player-role player-role-classic"><span class="role" data-value="a"></span></th>
        <th class="player-role player-role-mantra">
          <span class="role role-mantra" data-value="pc" title="Punta"></span>
        </th>
        <th class="player-name"><span>Dušan Vlahović</span></th>
        <td class="player-team">juventus</td>
        <td class="player-classic-current-price">48</td>
        <td class="player-classic-fvm">28</td>
      </tr>
      <tr class="player-row" data-filter-role-classic="a">
        <th class="player-role player-role-classic"><span class="role" data-value="a"></span></th>
        <th class="player-role player-role-mantra">
          <span class="role role-mantra" data-value="a" title="Ala"></span>
        </th>
        <th class="player-name"><span>Federico Chiesa</span></th>
        <td class="player-team">juventus</td>
        <td class="player-classic-current-price">42</td>
        <td class="player-classic-fvm">26</td>
      </tr>
    </tbody></table>
    </body></html>
    """


# ----------------------------------------------------------------------
# Test 1 — Parser estrae correttamente i campi
# ----------------------------------------------------------------------


def test_parse_extracts_fields():
    html = _minimal_fixture()
    players = parse_html(html)
    assert len(players) == 5

    by_name = {p["name"]: p for p in players}
    assert "Lautaro Martinez" in by_name
    lautaro = by_name["Lautaro Martinez"]
    assert lautaro["team"] == "Inter"
    assert lautaro["ruolo_fanta"] == "A"
    assert lautaro["ruolo_reale"] == "Punta"
    assert lautaro["fvm"] == 38
    assert lautaro["quotazione_ufficiale"] == 65


def test_parse_normalizes_name_with_extra_spaces():
    html = _minimal_fixture()
    players = parse_html(html)
    names = [p["name"] for p in players]
    # "  Marcus   Thuram  " → "Marcus Thuram"
    assert "Marcus Thuram" in names


def test_parse_normalizes_team_lowercase():
    html = _minimal_fixture()
    players = parse_html(html)
    teams = {p["team"] for p in players}
    assert "Inter" in teams
    assert "Juventus" in teams


def test_parse_dedup_by_name_team():
    """Se la pagina duplica un giocatore, il parser deduplica."""
    html = _minimal_fixture()
    html = html.replace(
        "</tbody>",
        """<tr class="player-row" data-filter-role-classic="a">
            <th class="player-name"><span>Lautaro Martinez</span></th>
            <td class="player-team">Inter</td>
            <td class="player-classic-fvm">38</td>
            <td class="player-classic-current-price">65</td>
        </tr>
        </tbody>""",
    )
    players = parse_html(html)
    lautaro = [p for p in players if p["name"] == "Lautaro Martinez"]
    assert len(lautaro) == 1, "duplicate entries should be deduped"


# ----------------------------------------------------------------------
# Test 1b — Fixture reale fantacalcio.it (FIX A4)
# ----------------------------------------------------------------------


def test_parse_real_fixture():
    """Gate principale M3.1: parsing della fixture reale fantacalcio.it.

    La fixture è estratta direttamente dal HTML live di
    https://www.fantacalcio.it/quotazioni-fantacalcio (vedi script
    extract_fixture.py in this dir, o rigenerabile con curl).
    """
    p = FIXTURES_DIR / "fanta_real_row.html"
    if not p.exists():
        pytest.skip("fixture fanta_real_row.html non presente — scarica con curl")
    html = p.read_text(encoding="utf-8")
    players = parse_html(html)
    assert len(players) >= 1, "fixture reale deve produrre almeno 1 giocatore"
    p0 = players[0]
    # Campi obbligatori (TUTTI devono essere non-None — è il gate del bug B1)
    assert p0.get("name"), f"name None/empty: {p0}"
    assert p0.get("team"), f"team None/empty: {p0}"
    assert p0.get("ruolo_fanta") in ("P", "D", "C", "A"), (
        f"ruolo_fanta non in P|D|C|A: {p0}"
    )
    assert p0.get("fvm") is not None, f"fvm None: {p0}"
    assert p0.get("quotazione_ufficiale") is not None, (
        f"quotazione None: {p0}"
    )


def test_assert_valid_fvm_coverage():
    """FIX A3 — se <90% ha fvm non-None, l'assertion deve sollevare."""
    # 500 giocatori, 20 squadre, ma solo 50% con fvm → deve fallire
    teams = [f"Team{i:02d}" for i in range(20)]
    players = []
    for i in range(500):
        fvm = 20 if i % 2 == 0 else None  # solo 50% ha fvm
        players.append({
            "name": f"Player {i}", "team": teams[i % 20],
            "ruolo_fanta": "A", "ruolo_reale": "Punta",
            "fvm": fvm, "quotazione_ufficiale": 30,
        })
    with pytest.raises(AssertionError, match="FVM non-None"):
        assert_valid(players)


# ----------------------------------------------------------------------
# Test 2 — Assertion numero giocatori
# ----------------------------------------------------------------------


def test_assert_valid_fails_below_min():
    """5 giocatori < MIN_PLAYERS (400) → AssertionError."""
    players = [
        {"name": f"Player {i}", "team": "Inter", "ruolo_fanta": "A",
         "ruolo_reale": "Punta", "fvm": 20, "quotazione_ufficiale": 30}
        for i in range(5)
    ]
    with pytest.raises(AssertionError, match="fuori range"):
        assert_valid(players)


def test_assert_valid_passes_in_range():
    """500 giocatori, 20 squadre → OK."""
    teams = [f"Team{i:02d}" for i in range(20)]
    players = [
        {"name": f"Player {i}", "team": teams[i % 20], "ruolo_fanta": "A",
         "ruolo_reale": "Punta", "fvm": 20, "quotazione_ufficiale": 30}
        for i in range(500)
    ]
    # Non solleva
    assert_valid(players)


def test_assert_valid_fails_wrong_team_count():
    """500 giocatori ma solo 5 squadre → AssertionError."""
    teams = [f"Team{i}" for i in range(5)]
    players = [
        {"name": f"Player {i}", "team": teams[i % 5], "ruolo_fanta": "A",
         "ruolo_reale": "Punta", "fvm": 20, "quotazione_ufficiale": 30}
        for i in range(500)
    ]
    with pytest.raises(AssertionError, match="Squadre trovate"):
        assert_valid(players)


# ----------------------------------------------------------------------
# Test 3 — Normalizzazione nomi
# ----------------------------------------------------------------------


def test_normalize_name_trims_whitespace():
    assert normalize_name("  Lautaro  Martinez  ") == "Lautaro Martinez"


def test_normalize_name_capitalizes_first_letter():
    assert normalize_name("lautaro martinez") == "Lautaro martinez"


def test_normalize_name_handles_empty():
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


def test_normalize_name_preserves_apostrophe():
    # Cognomi italiani tipo "D'Amico" non devono essere rotti
    assert normalize_name("d'amico") == "D'amico"


def test_normalize_team_capitalizes():
    assert normalize_team("inter") == "Inter"
    assert normalize_team("JUVENTUS") == "JUVENTUS"  # solo capitalize first


# ----------------------------------------------------------------------
# Test 4 — Output JSON valido
# ----------------------------------------------------------------------


def test_build_output_structure():
    players = [
        {"name": "Lautaro Martinez", "team": "Inter", "ruolo_fanta": "A",
         "ruolo_reale": "Punta", "fvm": 38, "quotazione_ufficiale": 65},
        {"name": "Marcus Thuram", "team": "Inter", "ruolo_fanta": "A",
         "ruolo_reale": "Punta", "fvm": 34, "quotazione_ufficiale": 58},
    ]
    data = build_output(players)
    assert data["source"] == "fantacalcio.it"
    assert "version" in data  # YYYY-MM-DD
    assert "scraped_at" in data  # ISO 8601
    assert "players" in data
    assert len(data["players"]) == 2
    assert data["stats"]["players_count"] == 2
    assert data["stats"]["teams_count"] == 1
    assert data["stats"]["fvm_min"] == 34
    assert data["stats"]["fvm_max"] == 38
    # JSON serializzabile
    json.dumps(data)


if __name__ == "__main__":
    # Permetti run senza pytest: `python test_scraper.py`
    sys.exit(pytest.main([__file__, "-v"]))
