#!/usr/bin/env python3
"""
tableau_complexity.py

Parse a Tableau workbook (.twb or .twbx) to extract per-worksheet visualization
metadata (e.g., mark/chart type, pills, filters, calcs) and compute a simple
"complexity score" you can customize.

Usage (CLI):
    python tableau_complexity.py /path/to/workbook.twbx --out summary.csv
    python tableau_complexity.py /path/to/workbook.twb --out summary.json

Library usage:
    from tableau_complexity import analyze_workbook
    results = analyze_workbook("/path/file.twbx")
    # results is a list of dicts (one per worksheet)

Notes:
- Tableau's XML schema is large and evolves over time. This utility uses
  robust heuristics rather than a strict schema. It should work "well enough"
  across most modern TWB/TWBX files but may need tuning for your environment.
- Chart/mark type detection is based on common XML patterns:
    - <mark type="bar|line|area|shape|text|..."> (most common)
    - Some worksheets contain multiple <mark> nodes (e.g., dual axis). We'll list unique types.
- Pills/fields used are inferred from <column> elements under worksheet/view zones.
- Calculations are detected via <calculation> nodes and by inspecting expressions for
  LOD braces { FIXED/INCLUDE/EXCLUDE } and common table calc functions (WINDOW_*, INDEX, RUNNING_*, etc.).
- Filters are inferred via <filter> nodes under each worksheet.
- Parameters are pulled from <parameters> and references in calculations.
"""

from __future__ import annotations
import argparse
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
import re
import json
import csv
from typing import Dict, Any, List, Optional, Tuple, Set


# -----------------------------
# XML helpers
# -----------------------------

def _read_twb_from_twbx(twbx_path: Path) -> str:
    """Extract and return the first .twb file contents inside a .twbx archive."""
    with zipfile.ZipFile(twbx_path, 'r') as zf:
        # pick the first .twb we find (there's usually only one)
        twb_names = [n for n in zf.namelist() if n.lower().endswith(".twb")]
        if not twb_names:
            raise ValueError("No .twb found inside the .twbx archive.")
        with zf.open(twb_names[0], 'r') as f:
            return f.read().decode("utf-8", errors="replace")


def _load_xml(path: Path) -> ET.Element:
    """Load the Tableau XML root element from .twb or .twbx"""
    if path.suffix.lower() == ".twbx":
        xml_text = _read_twb_from_twbx(path)
    elif path.suffix.lower() == ".twb":
        xml_text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError("Unsupported file type. Use .twb or .twbx")
    return ET.fromstring(xml_text)


# -----------------------------
# Parsing heuristics
# -----------------------------

TABLE_CALC_FUNCS = [
    "WINDOW_", "RUNNING_", "INDEX(", "RANK(", "RANK_DENSE(", "PERCENTILE(",
    "TOTAL(", "LOOKUP(", "FIRST(", "LAST(", "PREVIOUS_VALUE(", "MOVING_",
]

LOD_KEYWORDS = ["{FIXED", "{INCLUDE", "{EXCLUDE"]

def _text_contains_any(text: str, needles: List[str]) -> bool:
    t = text.upper()
    return any(n in t for n in needles)

def _collect_calculation_expressions(root: ET.Element) -> List[str]:
    exprs = []
    for calc in root.findall(".//calculation"):
        # Tableau often stores calc expression in the 'formula' attribute or text
        formula = calc.get("formula") or (calc.text or "")
        if formula:
            exprs.append(formula)
    return exprs

def _detect_has_table_calcs(exprs: List[str]) -> bool:
    return any(_text_contains_any(e, TABLE_CALC_FUNCS) for e in exprs)

def _detect_has_lod(exprs: List[str]) -> bool:
    return any(_text_contains_any(e, LOD_KEYWORDS) for e in exprs)

def _gather_parameters(root: ET.Element) -> List[str]:
    # Parameters often appear under a <parameters> container with <parameter name="...">
    params = []
    for p in root.findall(".//parameters/parameter"):
        name = p.get("name")
        if name:
            params.append(name)
    # Also try to capture parameters referenced in calculations as [<Param Name>]
    # This is best-effort; we do not attempt to disambiguate fields vs params here.
    return sorted(set(params))

def _worksheet_nodes(root: ET.Element) -> List[ET.Element]:
    return root.findall(".//worksheet")

def _worksheet_name(ws: ET.Element) -> str:
    return ws.get("name") or ws.get("caption") or "(unnamed)"


def _worksheet_mark_types(ws: ET.Element) -> List[str]:
    """Return unique mark types using robust, namespace-agnostic heuristics.
    Looks for:
      - <mark type="...">
      - any element with attribute mark="..."
      - <style mark="...">
      - <view mark="...">
      - presence of map-related elements -> 'map'
    """
    types: Set[str] = set()

    def _local(tag: str) -> str:
        # strip namespace if present: {ns}tag -> tag
        if tag and "}" in tag:
            return tag.split("}", 1)[1]
        return tag or ""

    # Walk all descendants once
    for el in ws.iter():
        tag = _local(el.tag).lower()
        # direct mark element with type attr
        t_attr = el.get("type") or el.get("mark") or ""
        if tag in ("mark", "marks", "style", "view") and t_attr:
            types.add(t_attr.strip().lower())
        # any element with a 'mark' attribute
        m = el.get("mark")
        if m:
            types.add(m.strip().lower())
        # map detection
        if tag in ("map", "layers") or el.get("map") is not None:
            types.add("map")

    # Normalize synonyms
    synonyms = {
        "bar": "bar",
        "line": "line",
        "area": "area",
        "shape": "shape",
        "text": "text",
        "gantt": "gantt",
        "polygon": "polygon",
        "circle": "scatter",
        "square": "scatter",
        "pie": "pie",
        "heatmap": "heatmap",
        "density": "density",
        "boxandwhisker": "box-and-whisker",
        "box-and-whisker": "box-and-whisker",
        "box": "box-and-whisker",
        "map": "map",
        "automatic": "automatic",
    }
    normalized: Set[str] = set()
    for t in list(types):
        base = t.replace("_", "-").replace(" ", "-")
        base = re.sub(r"[^a-z\-]", "", base)
        normalized.add(synonyms.get(base, base))

    
    # Fallback inference if still empty
    if not normalized:
        # Shelf-based inference
        def count_cols(node_name: str) -> int:
            node = ws.find(f".//{node_name}")
            return len(node.findall(".//column")) if node is not None else 0
        n_rows = count_cols("rows")
        n_cols = count_cols("cols")
        name = (_worksheet_name(ws) or "").lower()

        if n_rows == 0 and n_cols == 0:
            has_color = ws.find(".//color") is not None
            has_size = ws.find(".//size") is not None
            has_shape = ws.find(".//shape") is not None
            if "text" in name:
                normalized.add("text")
            elif has_shape:
                normalized.add("shape")
            elif has_color and has_size:
                normalized.add("scatter")
            else:
                # Treat empty-row/col worksheets as KPI/text tables by default
                normalized.add("text")
        else:
            # With axes, make a weak guess based on field roles and names
            fields = _worksheet_field_refs(ws)
            tokens = " ".join(f.lower() for f in fields)
            if any(k in tokens for k in ["bin(", "hist", "bucket"]):
                normalized.add("histogram")
            elif any(k in tokens for k in ["lat", "longitude", "latitude"]):
                normalized.add("map")
            elif any(k in tokens for k in ["path", "index(", "running_", "window_"]):
                normalized.add("line")
            else:
                normalized.add("bar")

    return sorted(normalized) if normalized else ["unknown"]


def _worksheet_filter_count(ws: ET.Element) -> int:
    return len(ws.findall(".//filter"))

def _worksheet_field_refs(ws: ET.Element) -> Set[str]:
    """Collect unique field names referenced by the worksheet pills/columns."""
    fields: Set[str] = set()
    # Many worksheets list fields under <view><columns><column field="[Field Name]">
    for col in ws.findall(".//column"):
        field = col.get("field") or col.get("name")
        if field:
            fields.add(field.strip())
    # Also catch <shelf><column> forms
    for col in ws.findall(".//shelf//column"):
        field = col.get("field") or col.get("name")
        if field:
            fields.add(field.strip())
    return fields

def _count_dimensions_measures(field_names: Set[str]) -> Tuple[int, int]:
    """
    Heuristic: dimensions often have a leading [dim:] or are non-aggregated.
    However, Tableau XML doesn't always expose data roles plainly.
    We use simple regex hints:
      - measure-like names often include SUM/AVG/MIN/MAX around [Field]
      - dimension-like are "raw" [Field] or date buckets.
    This is imperfect; tune if you can map field role metadata from your TWB.
    """
    dim, meas = 0, 0
    agg_pattern = re.compile(r"(SUM|AVG|MIN|MAX|COUNT|MEDIAN|STDEV|VAR)\s*\(", re.IGNORECASE)
    for f in field_names:
        # strip brackets if present
        inner = f.strip("[]")
        if agg_pattern.search(inner):
            meas += 1
        else:
            # If it looks like a numeric calc or has known measure tokens, count as measure
            if any(tok in inner.upper() for tok in ["#", "AMOUNT", "PRICE", "COST", "QUANTITY", "MEASURE"]):
                meas += 1
            else:
                dim += 1
    return dim, meas

def _worksheet_calc_counts(ws: ET.Element) -> Tuple[int, int]:
    """
    Count calculated fields referenced in this worksheet.
    Heuristic: look for columns with a <calculation> child, and inline calcs on columns.
    Returns (num_calculated_fields, num_table_calcs_detected_in_these)
    """
    calc_exprs = []
    for col in ws.findall(".//column"):
        # direct child <calculation>
        for calc in col.findall(".//calculation"):
            formula = calc.get("formula") or (calc.text or "")
            if formula:
                calc_exprs.append(formula)
        # formula attribute on column (less common)
        formula_attr = col.get("formula")
        if formula_attr:
            calc_exprs.append(formula_attr)

    num_calcs = len(calc_exprs)
    has_table_calc = _detect_has_table_calcs(calc_exprs)
    return num_calcs, (1 if has_table_calc else 0)

# -----------------------------
# Complexity scoring
# -----------------------------

def _score_complexity(
    dims: int,
    meas: int,
    num_filters: int,
    num_calcs: int,
    has_table_calc: bool,
    has_lod: bool,
    num_params: int,
    mark_types: List[str],
) -> float:
    """
    Simple weighted score. Tune weights for your environment.
    """
    weights = {
        "dims": 0.5,
        "meas": 0.7,
        "filters": 0.6,
        "calcs": 1.2,
        "table_calc": 2.0,
        "lod": 2.0,
        "params": 0.8,
        "mark_bonus": {
            # certain mark types tend to need more care (dual-axis blends not captured here)
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
            "unknown": 0.4,
        },
    }

    score = (
        dims * weights["dims"] +
        meas * weights["meas"] +
        num_filters * weights["filters"] +
        num_calcs * weights["calcs"] +
        (weights["table_calc"] if has_table_calc else 0.0) +
        (weights["lod"] if has_lod else 0.0) +
        num_params * weights["params"]
    )
    # add average mark bonus across detected types
    if mark_types:
        mb = sum(weights["mark_bonus"].get(m, 0.6) for m in mark_types) / len(mark_types)
        score += mb
    return round(score, 2)


# -----------------------------
# Public API
# -----------------------------

def analyze_workbook(path_str: str) -> List[Dict[str, Any]]:
    """
    Analyze a Tableau .twb or .twbx and return per-worksheet dictionaries.
    """
    path = Path(path_str)
    root = _load_xml(path)

    # workbook-level derived info
    all_exprs = _collect_calculation_expressions(root)
    has_lod = _detect_has_lod(all_exprs)
    has_table_calc_any = _detect_has_table_calcs(all_exprs)
    params = _gather_parameters(root)

    results: List[Dict[str, Any]] = []

    for ws in _worksheet_nodes(root):
        name = _worksheet_name(ws)
        mark_types = _worksheet_mark_types(ws)
        num_filters = _worksheet_filter_count(ws)
        fields = _worksheet_field_refs(ws)
        dims, meas = _count_dimensions_measures(fields)
        num_calcs, has_table_calc_ws = _worksheet_calc_counts(ws)

        score = _score_complexity(
            dims=dims,
            meas=meas,
            num_filters=num_filters,
            num_calcs=num_calcs,
            has_table_calc=bool(has_table_calc_ws),
            has_lod=has_lod,  # workbook-level signal
            num_params=len(params),
            mark_types=mark_types,
        )

        results.append({
            "worksheet": name,
            "mark_types": mark_types,
            "num_fields_used": len(fields),
            "num_dimensions_est": dims,
            "num_measures_est": meas,
            "num_filters": num_filters,
            "num_calculated_fields_est": num_calcs,
            "has_table_calc_ws": bool(has_table_calc_ws),
            "has_lod_anywhere": has_lod,
            "num_parameters": len(params),
            "complexity_score": score,
        })

    return results




# -----------------------------
# Summary helpers
# -----------------------------

def compute_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute an overall workbook summary from per-worksheet rows."""
    if not rows:
        return {
            "overall_score": 0.0,
            "num_worksheets": 0,
            "max_score": 0.0,
            "min_score": 0.0,
        }
    scores = [r.get("complexity_score", 0) for r in rows]
    return {
        "overall_score": round(sum(scores) / len(scores), 2),
        "num_worksheets": len(rows),
        "max_score": max(scores),
        "min_score": min(scores),
    }


def analyze_workbook_with_summary(path_str: str) -> Dict[str, Any]:
    """Analyze and return {'summary': {...}, 'worksheets': [...]}"""
    rows = analyze_workbook(path_str)
    summary = compute_summary(rows)
    return {"summary": summary, "worksheets": rows}



# -----------------------------
# Directory helpers
# -----------------------------

def analyze_directory(dir_path: str) -> List[Dict[str, Any]]:
    """
    Analyze all .twb and .twbx files in a directory (non-recursive).
    Returns a list of {"workbook": filename, "summary": {...}, "worksheets": [...]} dicts.
    """
    p = Path(dir_path)
    if not p.is_dir():
        raise ValueError(f"{dir_path} is not a directory")
    results = []
    for f in sorted(p.iterdir()):
        if f.suffix.lower() in (".twb", ".twbx"):
            try:
                wb_result = analyze_workbook_with_summary(str(f))
                wb_result["workbook"] = f.name
                results.append(wb_result)
            except Exception as e:
                results.append({
                    "workbook": f.name,
                    "error": str(e),
                })
    return results



# -----------------------------
# Directory analysis
# -----------------------------

def analyze_directory(dir_path_str: str, recursive: bool = False) -> List[Dict[str, Any]]:
    """
    Analyze all .twb and .twbx files in a directory.
    Returns a list of {'workbook': <name>, 'summary': {...}, 'worksheets': [...]}
    """
    base = Path(dir_path_str)
    if not base.is_dir():
        raise ValueError(f"Not a directory: {dir_path_str}")
    patterns = ["*.twb", "*.twbx"]
    files: List[Path] = []
    if recursive:
        for pat in patterns:
            files.extend(base.rglob(pat))
    else:
        for pat in patterns:
            files.extend(base.glob(pat))

    results: List[Dict[str, Any]] = []
    for f in sorted(files):
        try:
            data = analyze_workbook_with_summary(str(f))
            data["workbook"] = f.name
            results.append(data)
        except Exception as e:
            # Include a failure record so batch runs are robust
            results.append({
                "workbook": f.name,
                "summary": {"overall_score": 0.0, "num_worksheets": 0, "max_score": 0.0, "min_score": 0.0},
                "worksheets": [],
                "error": str(e),
            })
    return results



# -----------------------------
# Corpus summary (directory-level)
# -----------------------------

def compute_corpus_summary(dir_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate stats across a list of per-workbook results.
    """
    if not dir_results:
        return {
            "num_workbooks": 0,
            "total_worksheets": 0,
            "overall_score_avg": 0.0,
            "overall_score_min": 0.0,
            "overall_score_max": 0.0,
            "worksheet_complexity_avg": 0.0,
            "worksheets_with_table_calc_pct": 0.0,
            "worksheets_with_lod_pct": 0.0,
            "errors_count": 0,
            "top_mark_types": [],
        }
    import statistics as _stats

    num_workbooks = len(dir_results)
    errors_count = sum(1 for r in dir_results if r.get("error"))
    summaries = [r.get("summary", {}) for r in dir_results if r.get("summary")]
    overall_scores = [s.get("overall_score", 0.0) for s in summaries]

    all_ws = []
    for r in dir_results:
        all_ws.extend(r.get("worksheets", []))

    total_worksheets = len(all_ws)
    worksheet_scores = [ws.get("complexity_score", 0.0) for ws in all_ws]
    has_table_calc = [bool(ws.get("has_table_calc_ws")) for ws in all_ws]
    has_lod_anywhere = [bool(ws.get("has_lod_anywhere")) for ws in all_ws]

    # mark types
    from collections import Counter
    mt_counter = Counter()
    for ws in all_ws:
        mts = ws.get("mark_types", [])
        if isinstance(mts, list):
            mt_counter.update(mts)
        elif isinstance(mts, str) and mts:
            mt_counter.update(mts.split(";"))
    top_mark_types = mt_counter.most_common(10)

    def _safe_mean(vals):
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    corpus = {
        "num_workbooks": num_workbooks,
        "total_worksheets": total_worksheets,
        "overall_score_avg": _safe_mean(overall_scores),
        "overall_score_min": round(min(overall_scores), 2) if overall_scores else 0.0,
        "overall_score_max": round(max(overall_scores), 2) if overall_scores else 0.0,
        "worksheet_complexity_avg": _safe_mean(worksheet_scores),
        "worksheets_with_table_calc_pct": round((sum(has_table_calc) / total_worksheets) * 100, 1) if total_worksheets else 0.0,
        "worksheets_with_lod_pct": round((sum(has_lod_anywhere) / total_worksheets) * 100, 1) if total_worksheets else 0.0,
        "errors_count": errors_count,
        "top_mark_types": top_mark_types,
    }
    return corpus

# -----------------------------
# CLI
# -----------------------------

def _write_output(data: Any, out_path: Path) -> None:
    if out_path.suffix.lower() == ".json":
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    elif out_path.suffix.lower() in (".csv", ".tsv"):
        delim = "," if out_path.suffix.lower() == ".csv" else "\t"

        # Directory case: list of workbook dicts
        if isinstance(data, list):
            # Worksheets CSV
            ws_rows = []
            sm_rows = []
            for item in data:
                wb = item.get("workbook")
                summary = item.get("summary", {})
                worksheets = item.get("worksheets", [])
                if summary:
                    sm = dict(summary)
                    sm["workbook"] = wb
                    sm_rows.append(sm)
                for r in worksheets:
                    rr = dict(r)
                    rr["workbook"] = wb
                    if isinstance(rr.get("mark_types"), list):
                        rr["mark_types"] = ";".join(rr["mark_types"])
                    ws_rows.append(rr)

            # Write worksheets CSV to the requested path
            ws_fields = list({k for r in ws_rows for k in r.keys()}) if ws_rows else ["workbook"]
            with out_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=ws_fields, delimiter=delim)
                w.writeheader()
                for r in ws_rows:
                    w.writerow(r)

            # Write summaries CSV as sidecar
            sm_path = out_path.with_name(out_path.stem + "_summaries" + out_path.suffix)
            if sm_rows:
                sm_fields = list({k for r in sm_rows for k in r.keys()})
            else:
                sm_fields = ["workbook","overall_score","num_worksheets","max_score","min_score"]
            with sm_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=sm_fields, delimiter=delim)
                w.writeheader()
                for r in sm_rows:
                    w.writerow(r)
            return

        delim = "," if out_path.suffix.lower() == ".csv" else "\t"
        if isinstance(data, dict) and "worksheets" in data:
            rows = data.get("worksheets", [])
            summary = data.get("summary", {})
        else:
            rows = data
            summary = None

        # Worksheet CSV
        if rows:
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = [
                "worksheet", "mark_types", "num_fields_used", "num_dimensions_est",
                "num_measures_est", "num_filters", "num_calculated_fields_est",
                "has_table_calc_ws", "has_lod_anywhere", "num_parameters",
                "complexity_score"
            ]
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=delim)
            writer.writeheader()
            for r in rows or []:
                r = dict(r)
                if isinstance(r.get("mark_types"), list):
                    r["mark_types"] = ";".join(r["mark_types"])
                writer.writerow(r)

        # Summary sidecar outputs
        if summary is not None:
            # JSON sidecar
            out_json = out_path.with_name(out_path.stem + "_summary.json")
            out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            # CSV sidecar (one row)
            out_csv = out_path.with_name(out_path.stem + "_summary" + out_path.suffix)
            with out_csv.open("w", newline="", encoding="utf-8") as f:
                s_writer = csv.DictWriter(f, fieldnames=list(summary.keys()), delimiter=delim)
                s_writer.writeheader()
                s_writer.writerow(summary)
    else:
        raise ValueError("Unsupported output extension. Use .json, .csv, or .tsv")

def main():
    parser = argparse.ArgumentParser(description="Analyze Tableau workbook complexity.")
    parser.add_argument("workbook", help="Path to .twb/.twbx or a directory containing them")
    parser.add_argument("--out", help="Output file (.json, .csv, or .tsv). If omitted, prints JSON to stdout.")
    parser.add_argument("--recursive", action="store_true", help="When INPUT is a directory, recurse into subfolders.")
    args = parser.parse_args()

    target = Path(args.workbook)
    if target.is_dir():
        data = analyze_directory(str(target))
        if args.out:
            out_path = Path(args.out)
            if out_path.suffix.lower() == ".json":
                out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                print(f"Wrote {len(data)} workbook results to {out_path}")
            elif out_path.suffix.lower() in (".csv", ".tsv"):
                # Write worksheets CSV and summaries CSV
                delim = "," if out_path.suffix.lower() == ".csv" else "\t"
                all_rows = []
                summaries = []
                for d in data:
                    if "worksheets" in d:
                        for ws in d["worksheets"]:
                            row = dict(ws)
                            row["workbook"] = d.get("workbook")
                            all_rows.append(row)
                        summaries.append({
                            "workbook": d.get("workbook"),
                            **d.get("summary", {})
                        })
                # Worksheets
                if all_rows:
                    ws_fields = list(all_rows[0].keys())
                    with out_path.open("w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=ws_fields, delimiter=delim)
                        w.writeheader()
                        for r in all_rows:
                            if isinstance(r.get("mark_types"), list):
                                r["mark_types"] = ";".join(r["mark_types"])
                            w.writerow(r)
                # Summaries
                sum_path = out_path.with_name(out_path.stem + "_summaries" + out_path.suffix)
                if summaries:
                    sum_fields = list(summaries[0].keys())
                    with sum_path.open("w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=sum_fields, delimiter=delim)
                        w.writeheader()
                        for r in summaries:
                            w.writerow(r)
                print(f"Wrote worksheets to {out_path} and summaries to {sum_path}")
            else:
                raise ValueError("Unsupported output extension. Use .json, .csv, or .tsv")
        else:
            print(json.dumps(data, indent=2))
    else:
        data = analyze_workbook_with_summary(str(target))
        if args.out:
            out_path = Path(args.out)
            _write_output(data, out_path)
            n = len(data.get("worksheets", []))
            print(f"Wrote {n} worksheet rows + summary to {out_path}")
            if out_path.suffix.lower() in (".csv", ".tsv"):
                side_json = out_path.with_name(out_path.stem + "_summary.json")
                side_csv = out_path.with_name(out_path.stem + "_summary" + out_path.suffix)
                print(f"Summary also saved to {side_json.name} and {side_csv.name}")
        else:
            print(json.dumps(data, indent=2))

if __name__ == "__main__":
    main()
