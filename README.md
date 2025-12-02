# Tableau Workbook Complexity Analyzer

A Python utility for parsing Tableau workbooks (`.twb` / `.twbx`) to extract per-worksheet metadata (marks, shelves, filters, calcs, parameters, etc.) and compute a configurable complexity score.

## Features
- Parse `.twb` or `.twbx` (zip) files.
- Detect mark/chart types with robust fallbacks.
- Extract **shelves** per worksheet: Rows, Cols, Color, Size, Shape, Label, Tooltip, Detail, Path, Text, Angle, Opacity.
- Compute a **shelf_density** metric and include it in scoring.
- Extract each worksheet's **calculated field formulas**, score their complexity, and surface the formulas alongside per-worksheet metrics.
- Aggregate the **formula complexity** across a workbook and fold it into the overall workbook complexity score.
- Generate a **standalone HTML report** (with local CSS/JS assets) for a more readable deliverable.
- Flag **table calcs** and **LOD** usage.
- Compute a **workbook summary**, and in directory mode, a **corpus summary**.
- **Directory mode** (+ optional `--recursive`) to batch analyze many workbooks.
- **Configurable** weights and shelf channels via `config.json`.

## Install / Run
Python 3.9+ recommended.
```bash
# Per-file JSON
python tableau_complexity.py /path/to/workbook.twbx --out result.json

# Per-file CSV (+ summary sidecars for CSV)
python tableau_complexity.py /path/to/workbook.twbx --out result.csv

# Directory (non-recursive)
python tableau_complexity.py /path/to/folder --out all_results.json

# Directory (recursive)
python tableau_complexity.py /path/to/folder --recursive --out all_results.csv

# HTML report (copies local assets to the destination folder)
python tableau_complexity.py /path/to/workbook --report workbook_report.html
python tableau_complexity.py /path/to/folder --recursive --report corpus_report.html
```

The report generator writes a standalone HTML file plus a sibling `report_assets/` directory containing the CSS/JS used by the report. Share both together for an offline-ready experience.

## Config
You can provide a config JSON to tune scoring and the channels that count toward density:
```bash
python tableau_complexity.py /path/to/workbook.twbx --config config.json --out result.json
python tableau_complexity.py /path/to/folder --recursive --config config.json --out all_results.csv
```

### Config schema
```jsonc
{
  "weights": {
    "dims": 0.5,
    "meas": 0.7,
    "filters": 0.6,
      "calcs": 1.2,
      "table_calc": 2.0,
      "lod": 2.0,
      "params": 0.8,
      "shelf_density": 0.8,
      "calc_formula_complexity": 0.3,
      "mark_bonus": {
        "text": 0.2,
        "bar": 0.5,
        "line": 0.7,
      "area": 0.7,
      "shape": 0.8,
      "map": 1.0,
      "gantt": 1.0,
      "scatter": 1.2,
      "histogram": 0.6,
      "box-and-whisker": 1.3,
      "heatmap": 1.0,
      "density": 1.2,
      "unknown": 0.4
    }
  },
  "shelf_channels": ["rows","cols","color","size","shape","label","tooltip","detail","path","text","angle","opacity"]
}
```

- **weights**: control how the score is composed.
- **mark_bonus**: small per-mark adjustments (averaged across detected marks).
- **shelf_channels**: channels that count toward `shelf_density` (you can drop ones you don't care about).

## Outputs
- **JSON (per workbook)**: `{ "summary": {...}, "worksheets": [...] }`
- **CSV (per workbook)**: worksheets table; sidecars:
  - `_summary.json`, `_summary.csv`
 - **Directory CSV**: `all_results.csv` (all worksheets), `all_results_summaries.csv` (per-workbook), plus `all_results_corpus_summary.(json|csv)`.
- **HTML report**: cards + tables visualizing summary metrics, top worksheets, and detailed calculated fields; ships with local assets for offline sharing.

Each worksheet row now includes the resolved `calculated_fields` (field name, formula text, and formula complexity) as well as aggregate complexity metrics (`calc_formula_complexity_total`, `calc_formula_complexity_avg`). The workbook summaries include the total number of calculated fields plus the aggregated/average formula complexity so you can see how calculated logic contributes to overall workbook complexity.

## Notes
- This uses heuristics; Tableau XML varies by version. If a mark/shelf doesn't show up, share a sample and adjust the XPath/logic accordingly.
- Privacy: the tool only reads local files; it doesn't phone home.

## License
MIT (or adapt to your needs).


## Disclaimer
This utility was originally generated with the assistance of AI (ChatGPT).  
While care has been taken to make it functional and accurate, you should **review, test, and adapt** it for your specific use cases and data environments.  
No warranty is provided, and you assume responsibility for any use.
