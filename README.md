# AI Oversight QA Dashboard

Interactive dashboard for the capstone on human oversight of AI in wealth management. 
**All numbers other than the severity, likelihood and detectability scores are synthesized and
illustrative.** They will be replaced once the experiment has run.

## Run it

- **On GitHub Pages:** Settings → Pages → Deploy from a branch → `main` / root. The site appears at `https://<your-username>.github.io/<repo>/`.
- **Locally:** the page loads its data with `fetch`, so serve the folder rather than double-clicking
  the file: `python3 -m http.server`, then open http://localhost:8000.

## What is where

| Path | What it is |
|---|---|
| `index.html` | Page structure and all fixed text (titles, notes, table headings) |
| `styles.css` | Theme colors (light and dark, including the four risk-tier colors) and layout |
| `js/app.js` | Charts, tables, tabs, the RPN thresholds (`THRESHOLDS`) |
| `js/dash-shim.js` | Small helper that loads `data/*.json` and redraws charts when the tab or window changes |
| `data/*.json` | The data behind every number and chart. Edit these to change the dashboard |
| `lib/d3.min.js` | d3 v7.9.0, included so the site works offline |
| `tools/generate_sample_data.py` | Rebuilds `data/` from the test-suite workbook (repeatable, fixed random seed) |

## Editing

- **Change a score, result or evidence text:** edit `data/risks.json`. Keep `rpn` and `tier` in step
  with the scores: RPN = severity × likelihood × detectability; tiers are Critical ≥ 60, High ≥ 36,
  Medium ≥ 18, Low below that. The GitHub web editor (press `.` on the repo page) is enough.
- **Change thresholds or the max RPN:** `THRESHOLDS` and `MAX_RPN` near the top of the helpers
  section in `js/app.js`.
- **Change wording:** `index.html`.
- **Regenerate the sample data:** `pip install openpyxl`, then
  `python3 tools/generate_sample_data.py path/to/Sample_Test_Suite.xlsx data/`.

`data/oversight.json` holds the reviewer-behaviour experiment sample. No tab shows it right now; it is
kept in case that tab comes back.

