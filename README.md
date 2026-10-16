# dossier-fanta-data

Data source pubblico per l'app desktop **DossierFanta** (v2.5+).

Pipeline notturna GitHub Actions che scarica il listone Serie A da
[fantacalcio.it](https://www.fantacalcio.it/quotazioni-fantacalcio),
lo parsa, valida il risultato e pubblica `players.json` come artefatto
di una **GitHub Release**. L'app DossierFanta all'avvio interpella
`/releases/latest` di questo repo per scaricare il listone aggiornato.

```
┌──────────────────────┐   cron 01:00 UTC    ┌──────────────────────┐
│  fantacalcio.it      │ ──────────────────► │  GitHub Actions       │
│  quotazioni          │                      │  scrape.yml           │
└──────────────────────┘                      │  ├─ Python scraper    │
                                              │  ├─ assertion 400-800 │
                                              │  └─ Release + JSON    │
                                              └──────────┬───────────┘
                                                         │
                                                         ▼
                                              ┌──────────────────────┐
                                              │  DossierFanta app    │
                                              │  check_latest → dl   │
                                              │  → apply_auto_fetch  │
                                              └──────────────────────┘
```

## Struttura del repo

```
dossier-fanta-data/
├── .github/workflows/scrape.yml     # workflow cron
├── scraper/
│   ├── requirements.txt              # dipendenze Python (httpx, bs4)
│   ├── scrape_fantacalcio.py         # scraper principale
│   └── test_scraper.py               # test di regressione (pytest)
├── schema/
│   └── players.schema.json           # JSON Schema dell'output
├── examples/
│   └── players.example.json          # esempio di output
└── README.md
```

## Formato `players.json`

```json
{
  "version": "2026-10-13",
  "source": "fantacalcio.it",
  "scraped_at": "2026-10-13T01:00:00Z",
  "stats": {
    "players_count": 500,
    "teams_count": 20,
    "fvm_min": 1,
    "fvm_max": 50
  },
  "players": [
    {
      "name": "Lautaro Martinez",
      "team": "Inter",
      "ruolo_fanta": "A",
      "ruolo_reale": "Punta",
      "fvm": 38,
      "quotazione_ufficiale": 65
    }
  ]
}
```

Campi:

| Campo                  | Tipo            | Note                                              |
|------------------------|-----------------|---------------------------------------------------|
| `version`              | string YYYY-MM-DD | data scraping                                   |
| `source`               | string          | sempre `"fantacalcio.it"`                          |
| `scraped_at`           | ISO 8601 UTC    | timestamp scraping                                 |
| `stats.players_count`  | int 400-800     | assertion: fuori range → workflow fallisce        |
| `stats.teams_count`    | int = 20        | 20 squadre Serie A obbligatorio                   |
| `players[].name`       | string          | capitalize + trim (obbligatorio)                  |
| `players[].team`       | string \| null  | squadra normalizzata                              |
| `players[].ruolo_fanta`| P/D/C/A \| null | ruolo fantacalcio                                 |
| `players[].ruolo_reale`| string \| null  | ruolo tattico reale (es. "Terzino sinistro")      |
| `players[].fvm`        | int 1-100       | FantaValoreMedio                                  |
| `players[].quotazione_ufficiale` | int    | prezzo ufficiale fantacalcio.it                  |

## Come lanciare lo scraper localmente

```bash
# Setup
python -m venv .venv
source .venv/bin/activate   # su Windows: .venv\Scripts\activate
pip install -r scraper/requirements.txt
pip install pytest           # per i test

# Run scraper (scrive players.json in root repo)
python scraper/scrape_fantacalcio.py

# Run test di regressione (no rete, usa fixture HTML)
python scraper/test_scraper.py -v
```

## Selettori HTML usati

Lo scraper usa `BeautifulSoup` con selettori CSS. Se fantacalcio.it cambia
layout, modificare `scraper/scrape_fantacalcio.py`:

```python
# Tabella principale
rows = soup.select(
    "table.quotazioni tbody tr, "
    "div.player-list tr.player-row, "
    "table tbody tr[data-player]"
)
# Fallback adattivo (card layout)
rows = soup.select(
    "div.player-card, "
    "div.player-list div.row, "
    "tbody tr"
)

# Campi per riga
name_el  = row.select_one("td.name, .player-name")
team_el  = row.select_one("td.team, .player-team")
role_el  = row.select_one("td.role, .player-role")
fvm_el   = row.select_one("td.fvm, .player-fvm")
quot_el  = row.select_one("td.quotation, .player-quotation")
```

Se i selettori primari non matchano (meno di 10 entry), lo scraper prova
automaticamente i selettori fallback adattivi. Se anche quelli falliscono,
l'assertion `assert_valid` solleva errore e il workflow GitHub Actions
fallisce (notifica via email al maintainer).

## Workflow GitHub Actions

`.github/workflows/scrape.yml`:

- **Trigger**: cron `0 1 * * *` (01:00 UTC, ~03:00 CET in inverno) +
  `workflow_dispatch` (manuale)
- **Steps**:
  1. Checkout
  2. Setup Python 3.12 con cache pip
  3. Installa dipendenze da `scraper/requirements.txt`
  4. `python scraper/scrape_fantacalcio.py` (scrive `players.json`)
  5. Valida output: JSON parseable, 400-800 giocatori
  6. `python scraper/test_scraper.py -v` (regression test)
  7. Compute tag: `data-YYYY-MM-DD-<sha256[:12]>`
  8. Crea release con `softprops/action-gh-release@v2`

L'app DossierFanta interpella:
```
GET https://api.github.com/repos/{owner}/dossier-fanta-data/releases/latest
```
e scarica l'asset `players.json` dalla release più recente.

## Perché GitHub Releases e non un branch

- **Versioning naturale**: ogni release ha un tag univoco `data-YYYY-MM-DD-<hash>`
  che l'app confronta con `cache/last_applied_tag.txt` per decidere se
  scaricare o skip.
- **Rate limit**: endpoint pubblico senza token → 60 req/h (più che sufficiente
  per un check all'avvio dell'app).
- **CDN-backed**: download veloce anche per file 1-2 MB.
- **Atomicità**: una release è immutabile, non c'è rischio di fetch parziale.

## Licenza

Codice scraper: MIT.
Dati (`players.json`): derivati da fantacalcio.it, utilizzo ai sensi dei
termini del sito source per uso personale e non commerciale nell'app
DossierFanta.
