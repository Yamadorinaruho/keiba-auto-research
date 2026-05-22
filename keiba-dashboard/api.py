"""
FastAPI backend for keiba-dashboard.
Serves horse racing data from SQLite DB (keiba.db).
Start: uvicorn api:app --reload --port 8001
"""

import sqlite3
import os
import math
from functools import lru_cache
from typing import Dict, List, Optional, Union

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# App & CORS
# ---------------------------------------------------------------------------
app = FastAPI(title="Keiba Dashboard API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keiba.db")

import threading
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA cache_size=-64000")
        _local.conn = conn
    return conn


def query(sql: str, params: tuple = ()) -> List[Dict]:
    cur = get_conn().execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> Optional[Dict]:
    rows = query(sql, params)
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def distance_band(d: Optional[int]) -> str:
    if d is None:
        return "不明"
    if d <= 1200:
        return "~1200"
    if d <= 1600:
        return "~1600"
    if d <= 2000:
        return "~2000"
    if d <= 2400:
        return "~2400"
    return "2401~"


def distance_band_sql() -> str:
    return """
        CASE
            WHEN distance <= 1200 THEN '~1200'
            WHEN distance <= 1600 THEN '~1600'
            WHEN distance <= 2000 THEN '~2000'
            WHEN distance <= 2400 THEN '~2400'
            ELSE '2401~'
        END
    """


def pop_band(p: Optional[int]) -> str:
    if p is None:
        return "不明"
    if p == 1:
        return "1番人気"
    if p <= 3:
        return "2-3番人気"
    if p <= 6:
        return "4-6番人気"
    return "7番以降"


def pop_band_sql() -> str:
    return """
        CASE
            WHEN popularity = 1 THEN '1番人気'
            WHEN popularity <= 3 THEN '2-3番人気'
            WHEN popularity <= 6 THEN '4-6番人気'
            ELSE '7番以降'
        END
    """


def safe(v, default=0):
    """Return v if not None, else default."""
    return v if v is not None else default


def rnd(v, digits=2):
    if v is None:
        return None
    return round(v, digits)

def pct(v, digits=1):
    """0-1 の比率を % に変換 (0.333 -> 33.3)"""
    if v is None:
        return None
    return round(v * 100, digits)


def build_where(filters: dict, params: list) -> str:
    """Build WHERE clause from filter dict. Values can be comma-separated."""
    clauses = []
    for col, val in filters.items():
        if val is None or val == "":
            continue
        parts = [v.strip() for v in val.split(",") if v.strip()]
        if not parts:
            continue
        placeholders = ",".join(["?"] * len(parts))
        clauses.append(f"{col} IN ({placeholders})")
        params.extend(parts)
    if not clauses:
        return ""
    return " AND " + " AND ".join(clauses)


# ---------------------------------------------------------------------------
# 1. GET /api/meta
# ---------------------------------------------------------------------------
_meta_cache: Optional[Dict] = None


@app.get("/api/meta")
def get_meta():
    global _meta_cache
    if _meta_cache is not None:
        return _meta_cache

    def uniq(col):
        rows = query(f"SELECT DISTINCT {col} FROM entries WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}")
        return [r[col] for r in rows]

    years = query("SELECT DISTINCT year FROM entries WHERE year IS NOT NULL ORDER BY year")
    distances = query("SELECT DISTINCT distance FROM entries WHERE distance IS NOT NULL ORDER BY distance")
    graded = query(
        "SELECT DISTINCT race_name FROM entries WHERE class LIKE '%Ｇ%' OR class LIKE '%ＪＧ%' ORDER BY race_name"
    )

    _meta_cache = {
        "venues": uniq("venue"),
        "classes": uniq("class"),
        "surfaces": uniq("surface"),
        "conditions": uniq("condition"),
        "weathers": uniq("weather"),
        "genders": uniq("gender"),
        "years": [r["year"] for r in years],
        "distances": [r["distance"] for r in distances],
        "race_names_graded": [r["race_name"] for r in graded],
        "total_rows": query_one("SELECT COUNT(*) AS cnt FROM entries")["cnt"],
    }
    return _meta_cache


# ---------------------------------------------------------------------------
# 2. GET /api/summary
# ---------------------------------------------------------------------------
@app.get("/api/summary")
def get_summary(
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
    condition: Optional[str] = None,
    year: Optional[str] = None,
    gender: Optional[str] = None,
):
    params: list = []
    where = build_where(
        {"venue": venue, "surface": surface, "class": class_,
         "condition": condition, "year": year, "gender": gender},
        params,
    )

    sql = f"""
        SELECT
            COUNT(DISTINCT race_id)           AS total_races,
            COUNT(*)                          AS total_entries,
            AVG(win_odds)                     AS avg_win_odds,
            AVG(CASE WHEN popularity = 1 AND finish = 1 THEN 1.0
                     WHEN popularity = 1 THEN 0.0 END) AS fav_win_rate,
            AVG(CASE WHEN popularity = 1 AND finish <= 3 THEN 1.0
                     WHEN popularity = 1 THEN 0.0 END) AS fav_place_rate,
            AVG(CASE WHEN finish = 1 THEN win_payout END) AS avg_win_payout,
            AVG(CASE WHEN finish = 1 THEN trifecta END)  AS avg_trifecta,
            AVG(runners)                      AS avg_runners,
            AVG(CASE WHEN finish = 1 THEN last_3f END) AS avg_last_3f_winner
        FROM entries
        WHERE 1=1 {where}
    """
    row = query_one(sql, tuple(params))
    if row is None:
        return {}
    return {
        "total_races": safe(row["total_races"]),
        "total_entries": safe(row["total_entries"]),
        "avg_win_odds": rnd(row["avg_win_odds"]),
        "fav_win_rate": pct(row["fav_win_rate"]),
        "fav_place_rate": pct(row["fav_place_rate"]),
        "avg_win_payout": rnd(row["avg_win_payout"]),
        "avg_trifecta": rnd(row["avg_trifecta"]),
        "avg_runners": rnd(row["avg_runners"], 1),
        "avg_last_3f_winner": rnd(row["avg_last_3f_winner"], 2),
    }


# ---------------------------------------------------------------------------
# 3. GET /api/roi-heatmap
# ---------------------------------------------------------------------------
AXIS_SQL = {
    "venue": "venue",
    "surface": "surface",
    "distance_band": distance_band_sql(),
    "condition": "condition",
    "weather": "weather",
}


@app.get("/api/roi-heatmap")
@app.get("/api/accuracy-heatmap")
def get_accuracy_heatmap(
    row_axis: str = "venue",
    col_axis: str = "surface",
    pop_min: int = 1,
    pop_max: int = 18,
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
    condition: Optional[str] = None,
    year: Optional[str] = None,
):
    row_expr = AXIS_SQL.get(row_axis, "venue")
    col_expr = AXIS_SQL.get(col_axis, "surface")

    params: list = [pop_min, pop_max]
    where = build_where(
        {"venue": venue, "surface": surface, "class": class_,
         "condition": condition, "year": year},
        params,
    )

    sql = f"""
        SELECT
            {row_expr} AS row_val,
            {col_expr} AS col_val,
            COUNT(*) AS n,
            SUM(CASE WHEN finish = 1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN finish <= 3 THEN 1 ELSE 0 END) AS places,
            AVG(finish) AS avg_finish,
            SUM(CASE WHEN finish = 1 THEN win_payout ELSE 0 END) AS total_payout
        FROM entries
        WHERE popularity >= ? AND popularity <= ? {where}
          AND {row_expr} IS NOT NULL AND {col_expr} IS NOT NULL
        GROUP BY row_val, col_val
    """
    rows = query(sql, tuple(params))

    cells = []
    row_totals: dict = {}
    col_totals: dict = {}
    grand_n = 0
    grand_payout = 0
    grand_wins = 0
    grand_places = 0
    grand_finish_sum = 0

    for r in rows:
        n = r["n"]
        wins = r["wins"]
        places = r["places"]
        avg_finish = r["avg_finish"]
        total_payout = safe(r["total_payout"])
        roi = rnd((total_payout / (n * 100)) * 100, 1) if n > 0 else 0
        cell = {
            "row": r["row_val"], "col": r["col_val"], "n": n,
            "win_rate": rnd(wins / n * 100, 1) if n > 0 else 0,
            "place_rate": rnd(places / n * 100, 1) if n > 0 else 0,
            "avg_finish": rnd(avg_finish, 1),
            "roi": roi,
        }
        cells.append(cell)

        # row totals
        if r["row_val"] not in row_totals:
            row_totals[r["row_val"]] = {"n": 0, "payout": 0, "wins": 0, "places": 0, "finish_sum": 0}
        row_totals[r["row_val"]]["n"] += n
        row_totals[r["row_val"]]["payout"] += total_payout
        row_totals[r["row_val"]]["wins"] += wins
        row_totals[r["row_val"]]["places"] += places
        row_totals[r["row_val"]]["finish_sum"] += avg_finish * n if avg_finish else 0

        # col totals
        if r["col_val"] not in col_totals:
            col_totals[r["col_val"]] = {"n": 0, "payout": 0, "wins": 0, "places": 0, "finish_sum": 0}
        col_totals[r["col_val"]]["n"] += n
        col_totals[r["col_val"]]["payout"] += total_payout
        col_totals[r["col_val"]]["wins"] += wins
        col_totals[r["col_val"]]["places"] += places
        col_totals[r["col_val"]]["finish_sum"] += avg_finish * n if avg_finish else 0

        grand_n += n
        grand_payout += total_payout
        grand_wins += wins
        grand_places += places
        grand_finish_sum += avg_finish * n if avg_finish else 0

    def totals_dict(d):
        return {
            k: {
                "n": v["n"],
                "win_rate": rnd(v["wins"] / v["n"] * 100, 1) if v["n"] > 0 else 0,
                "place_rate": rnd(v["places"] / v["n"] * 100, 1) if v["n"] > 0 else 0,
                "avg_finish": rnd(v["finish_sum"] / v["n"], 1) if v["n"] > 0 else None,
                "roi": rnd((v["payout"] / (v["n"] * 100)) * 100, 1) if v["n"] > 0 else 0,
            }
            for k, v in d.items()
        }

    return {
        "cells": cells,
        "row_totals": totals_dict(row_totals),
        "col_totals": totals_dict(col_totals),
        "grand_total": {
            "n": grand_n,
            "win_rate": rnd(grand_wins / grand_n * 100, 1) if grand_n > 0 else 0,
            "place_rate": rnd(grand_places / grand_n * 100, 1) if grand_n > 0 else 0,
            "avg_finish": rnd(grand_finish_sum / grand_n, 1) if grand_n > 0 else None,
            "roi": rnd((grand_payout / (grand_n * 100)) * 100, 1) if grand_n > 0 else 0,
        },
    }


# ---------------------------------------------------------------------------
# 4. GET /api/rotation
# ---------------------------------------------------------------------------
@app.get("/api/rotation")
def get_rotation(race_name: str):
    # Summary
    summary_sql = """
        SELECT
            COUNT(DISTINCT race_id) AS race_count,
            AVG(CASE WHEN popularity = 1 AND finish = 1 THEN 1.0
                     WHEN popularity = 1 THEN 0.0 END) AS fav_win_rate,
            AVG(CASE WHEN finish = 1 THEN win_payout END) AS avg_win_payout
        FROM entries
        WHERE race_name = ?
    """
    summary = query_one(summary_sql, (race_name,))

    # Top prev_race for winners
    top_prev_sql = """
        SELECT prev_race, COUNT(*) AS cnt
        FROM entries
        WHERE race_name = ? AND finish = 1 AND prev_race IS NOT NULL AND prev_race != ''
        GROUP BY prev_race ORDER BY cnt DESC LIMIT 1
    """
    top_prev = query_one(top_prev_sql, (race_name,))

    # prev_races stats
    prev_races_sql = """
        SELECT
            prev_race,
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 2 THEN 1.0 ELSE 0.0 END) AS top2_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS top3_rate,
            AVG(finish) AS avg_finish,
            AVG(popularity) AS avg_pop,
            SUM(CASE WHEN finish = 1 THEN win_payout ELSE 0 END) * 1.0 / (COUNT(*) * 100) * 100 AS roi
        FROM entries
        WHERE race_name = ? AND prev_race IS NOT NULL AND prev_race != ''
        GROUP BY prev_race
        HAVING COUNT(*) >= 3
        ORDER BY win_rate DESC, n DESC
    """
    prev_races = query(prev_races_sql, (race_name,))
    prev_races = [
        {**r, "win_rate": pct(r["win_rate"]), "top2_rate": pct(r["top2_rate"]),
         "top3_rate": pct(r["top3_rate"]), "avg_finish": rnd(r["avg_finish"], 1),
         "avg_pop": rnd(r["avg_pop"], 1), "roi": rnd(r["roi"], 1)}
        for r in prev_races
    ]

    # prev_finish_detail
    detail_sql = """
        SELECT
            prev_race,
            CAST(prev_finish AS INTEGER) AS prev_finish,
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS top3_rate,
            AVG(finish) AS avg_finish
        FROM entries
        WHERE race_name = ? AND prev_race IS NOT NULL AND prev_race != ''
              AND prev_finish IS NOT NULL
        GROUP BY prev_race, CAST(prev_finish AS INTEGER)
        HAVING COUNT(*) >= 2
        ORDER BY prev_race, prev_finish
    """
    detail = query(detail_sql, (race_name,))
    detail = [
        {**r, "win_rate": pct(r["win_rate"]), "top3_rate": pct(r["top3_rate"]),
         "avg_finish": rnd(r["avg_finish"], 1)}
        for r in detail
    ]

    return {
        "summary": {
            "race_count": safe(summary["race_count"]) if summary else 0,
            "fav_win_rate": pct(summary["fav_win_rate"]) if summary else None,
            "avg_win_payout": rnd(summary["avg_win_payout"]) if summary else None,
            "top_prev_race": top_prev["prev_race"] if top_prev else None,
        },
        "prev_races": prev_races,
        "prev_finish_detail": detail,
    }


# ---------------------------------------------------------------------------
# 5. GET /api/rotation-condition
# ---------------------------------------------------------------------------
@app.get("/api/rotation-condition")
def get_rotation_condition(
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    distance: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
):
    params: list = []
    where = build_where(
        {"venue": venue, "surface": surface, "distance": distance, "class": class_},
        params,
    )

    summary_sql = f"""
        SELECT
            COUNT(DISTINCT race_id) AS race_count,
            AVG(CASE WHEN popularity = 1 AND finish = 1 THEN 1.0
                     WHEN popularity = 1 THEN 0.0 END) AS fav_win_rate,
            AVG(CASE WHEN finish = 1 THEN win_payout END) AS avg_win_payout
        FROM entries
        WHERE 1=1 {where}
    """
    summary = query_one(summary_sql, tuple(params))

    top_prev_sql = f"""
        SELECT prev_race, COUNT(*) AS cnt
        FROM entries
        WHERE finish = 1 AND prev_race IS NOT NULL AND prev_race != '' {where}
        GROUP BY prev_race ORDER BY cnt DESC LIMIT 1
    """
    top_prev = query_one(top_prev_sql, tuple(params))

    prev_races_sql = f"""
        SELECT
            prev_race,
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 2 THEN 1.0 ELSE 0.0 END) AS top2_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS top3_rate,
            AVG(finish) AS avg_finish,
            AVG(popularity) AS avg_pop,
            SUM(CASE WHEN finish = 1 THEN win_payout ELSE 0 END) * 1.0 / (COUNT(*) * 100) * 100 AS roi
        FROM entries
        WHERE prev_race IS NOT NULL AND prev_race != '' {where}
        GROUP BY prev_race
        HAVING COUNT(*) >= 3
        ORDER BY win_rate DESC, n DESC
    """
    prev_races = query(prev_races_sql, tuple(params))
    prev_races = [
        {**r, "win_rate": pct(r["win_rate"]), "top2_rate": pct(r["top2_rate"]),
         "top3_rate": pct(r["top3_rate"]), "avg_finish": rnd(r["avg_finish"], 1),
         "avg_pop": rnd(r["avg_pop"], 1), "roi": rnd(r["roi"], 1)}
        for r in prev_races
    ]

    detail_sql = f"""
        SELECT
            prev_race,
            CAST(prev_finish AS INTEGER) AS prev_finish,
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS top3_rate,
            AVG(finish) AS avg_finish
        FROM entries
        WHERE prev_race IS NOT NULL AND prev_race != '' AND prev_finish IS NOT NULL {where}
        GROUP BY prev_race, CAST(prev_finish AS INTEGER)
        HAVING COUNT(*) >= 2
        ORDER BY prev_race, prev_finish
    """
    detail = query(detail_sql, tuple(params))
    detail = [
        {**r, "win_rate": pct(r["win_rate"]), "top3_rate": pct(r["top3_rate"]),
         "avg_finish": rnd(r["avg_finish"], 1)}
        for r in detail
    ]

    return {
        "summary": {
            "race_count": safe(summary["race_count"]) if summary else 0,
            "fav_win_rate": pct(summary["fav_win_rate"]) if summary else None,
            "avg_win_payout": rnd(summary["avg_win_payout"]) if summary else None,
            "top_prev_race": top_prev["prev_race"] if top_prev else None,
        },
        "prev_races": prev_races,
        "prev_finish_detail": detail,
    }


# ---------------------------------------------------------------------------
# 6. GET /api/profit-scan
# ---------------------------------------------------------------------------
_strength_cache: dict = {}

@app.get("/api/profit-scan")
@app.get("/api/strength-scan")
def get_strength_scan(
    min_n: int = 30,
    min_place_rate: float = 30,
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
    condition: Optional[str] = None,
    year: Optional[str] = None,
    gender: Optional[str] = None,
):
    cache_key = f"ss|{min_n}|{min_place_rate}|{venue}|{surface}|{class_}|{condition}|{year}|{gender}"
    if cache_key in _strength_cache:
        return _strength_cache[cache_key]

    db_band = distance_band_sql()
    db_pop_band = pop_band_sql()

    base_params: list = []
    base_where = build_where(
        {"venue": venue, "surface": surface, "class": class_,
         "condition": condition, "year": year, "gender": gender},
        base_params,
    )

    # Build one big UNION ALL query
    def block(label_expr, group_expr, extra_where=""):
        return f"""
            SELECT {label_expr} AS condition_label, COUNT(*) AS n,
                AVG(CASE WHEN finish=1 THEN 1.0 ELSE 0.0 END) AS win_rate,
                AVG(CASE WHEN finish<=3 THEN 1.0 ELSE 0.0 END) AS place_rate,
                AVG(finish) AS avg_finish,
                SUM(CASE WHEN finish=1 THEN win_payout ELSE 0 END)*1.0/(COUNT(*)*100)*100 AS roi,
                AVG(CASE WHEN finish=1 THEN win_payout END) AS avg_payout
            FROM entries WHERE {group_expr} IS NOT NULL {base_where} {extra_where}
            GROUP BY condition_label"""

    parts = [
        block("'場所:'||venue", "venue"),
        block("'馬場:'||surface", "surface"),
        block(f"'距離:'||{db_band}", db_band),
        block("'状態:'||condition", "condition"),
        block("'人気:'||CAST(popularity AS TEXT)", "popularity"),
        block("venue||'×'||surface", "venue"),
        block(f"surface||'×'||{db_band}", "surface"),
        block("surface||'×'||condition", "surface"),
        block(f"venue||'×'||{db_pop_band}", "venue"),
        block("'騎手:'||jockey", "jockey", "AND jockey!=''"),
        block("'種牡馬:'||sire", "sire", "AND sire!=''"),
    ]

    # Each block gets its own copy of base_params
    all_params = []
    for _ in parts:
        all_params.extend(base_params)

    sql = " UNION ALL ".join(parts)
    rows = query(sql, tuple(all_params))

    results = []
    for r in rows:
        n = r["n"]
        if n < min_n:
            continue
        place_rate_val = pct(r["place_rate"])
        if place_rate_val is None or place_rate_val < min_place_rate:
            continue

        # Confidence: lower bound of 95% CI for place_rate (binomial)
        p = (r["place_rate"] or 0)  # 0-1 scale
        se = math.sqrt(p * (1 - p) / n) if n > 0 and 0 < p < 1 else 0
        lower_bound = p - 1.96 * se
        confidence = rnd(lower_bound * 100, 1)  # conservative estimate of true place rate %

        results.append({
            "condition_label": r["condition_label"],
            "n": n,
            "win_rate": pct(r["win_rate"]),
            "place_rate": place_rate_val,
            "avg_finish": rnd(r["avg_finish"], 1),
            "roi": rnd(r["roi"], 1),
            "avg_payout": rnd(r["avg_payout"]),
            "confidence": confidence,  # 95% CI lower bound of place_rate (%)
        })

    results.sort(key=lambda x: x["place_rate"] or 0, reverse=True)
    _strength_cache[cache_key] = results
    return results


# ---------------------------------------------------------------------------
# 7. GET /api/upset-ranking
# ---------------------------------------------------------------------------
@app.get("/api/upset-ranking")
def get_upset_ranking(
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
    condition: Optional[str] = None,
    year: Optional[str] = None,
    gender: Optional[str] = None,
):
    db_band = distance_band_sql()

    base_params: list = []
    base_where = build_where(
        {"venue": venue, "surface": surface, "class": class_,
         "condition": condition, "year": year, "gender": gender},
        base_params,
    )

    groupings = [
        ("venue", "venue"),
        ("surface_distance", f"surface || '×' || {db_band}"),
        ("class", "class"),
    ]

    all_rows = []
    for name, expr in groupings:
        sql = f"""
            SELECT
                {expr} AS label,
                '{name}' AS group_type,
                COUNT(DISTINCT race_id) AS race_count,
                AVG(CASE WHEN popularity = 1 AND finish = 1 THEN 1.0
                         WHEN popularity = 1 THEN 0.0 END) AS fav_win_rate,
                AVG(CASE WHEN finish = 1 THEN win_payout END) AS avg_win_payout,
                (1.0 - AVG(CASE WHEN popularity = 1 AND finish = 1 THEN 1.0
                                WHEN popularity = 1 THEN 0.0 END))
                * AVG(CASE WHEN finish = 1 THEN win_payout END) / 1000.0 AS upset_score
            FROM entries
            WHERE {expr} IS NOT NULL {base_where}
            GROUP BY label
            HAVING COUNT(DISTINCT race_id) >= 30
        """
        rows = query(sql, tuple(base_params))
        all_rows.extend(rows)

    for r in all_rows:
        r["fav_win_rate"] = pct(r["fav_win_rate"])
        r["avg_win_payout"] = rnd(r["avg_win_payout"])
        r["upset_score"] = rnd(r["upset_score"], 3)

    # Sort by upset_score desc for upset-prone
    upset_prone = sorted(all_rows, key=lambda x: x["upset_score"] or 0, reverse=True)[:20]
    # Sort by upset_score asc for predictable
    predictable = sorted(all_rows, key=lambda x: x["upset_score"] or 999)[:20]

    return {
        "upset_prone": upset_prone,
        "predictable": predictable,
    }


# ---------------------------------------------------------------------------
# 8. GET /api/search
# ---------------------------------------------------------------------------
@app.get("/api/search")
def search_entries(
    type: str = "horse",
    q: str = "",
):
    if not q or not q.strip():
        return {"stats": None, "records": []}

    col_map = {"horse": "horse", "jockey": "jockey", "sire": "sire", "trainer": "trainer"}
    col = col_map.get(type, "horse")

    # Stats
    stats_sql = f"""
        SELECT
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS top3_rate,
            AVG(finish) AS avg_finish,
            AVG(popularity) AS avg_pop,
            AVG(win_odds) AS avg_odds,
            SUM(CASE WHEN finish = 1 THEN win_payout ELSE 0 END) * 1.0 / (COUNT(*) * 100) * 100 AS roi,
            SUM(CASE WHEN finish = 1 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN finish <= 2 THEN 1 ELSE 0 END) AS top2,
            SUM(CASE WHEN finish <= 3 THEN 1 ELSE 0 END) AS top3
        FROM entries
        WHERE {col} LIKE ?
    """
    stats = query_one(stats_sql, (f"%{q}%",))
    if stats:
        stats["win_rate"] = pct(stats["win_rate"])
        stats["top3_rate"] = pct(stats["top3_rate"])
        stats["avg_finish"] = rnd(stats["avg_finish"], 1)
        stats["avg_pop"] = rnd(stats["avg_pop"], 1)
        stats["avg_odds"] = rnd(stats["avg_odds"], 1)
        stats["roi"] = rnd(stats["roi"], 1)

    # Recent records
    records_sql = f"""
        SELECT date, venue, race_num, race_name, class, horse, jockey, sire, trainer,
               popularity, win_odds, finish, surface, distance, condition, runners, last_3f
        FROM entries
        WHERE {col} LIKE ?
        ORDER BY date DESC, race_num DESC
        LIMIT 50
    """
    records = query(records_sql, (f"%{q}%",))

    return {"stats": stats, "records": records}


# ---------------------------------------------------------------------------
# 9. GET /api/entries
# ---------------------------------------------------------------------------
SORTABLE_COLS = {
    "date", "venue", "race_num", "race_name", "class", "horse", "jockey",
    "popularity", "win_odds", "finish", "surface", "distance", "condition",
    "runners", "last_3f", "prize", "sire", "trainer", "win_payout", "trifecta",
}


@app.get("/api/entries")
def list_entries(
    page: int = 1,
    per_page: int = 200,
    sort: str = "date",
    order: str = "desc",
    q: Optional[str] = None,
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
    condition: Optional[str] = None,
    year: Optional[str] = None,
    gender: Optional[str] = None,
    distance: Optional[str] = None,
    race_name: Optional[str] = None,
):
    params: list = []
    where = build_where(
        {"venue": venue, "surface": surface, "class": class_,
         "condition": condition, "year": year, "gender": gender,
         "distance": distance, "race_name": race_name},
        params,
    )

    # Free-text search
    if q and q.strip():
        where += " AND (horse LIKE ? OR jockey LIKE ? OR sire LIKE ? OR trainer LIKE ? OR race_name LIKE ?)"
        pq = f"%{q.strip()}%"
        params.extend([pq, pq, pq, pq, pq])

    sort_col = sort if sort in SORTABLE_COLS else "date"
    sort_order = "ASC" if order.lower() == "asc" else "DESC"

    # Total count
    count_sql = f"SELECT COUNT(*) AS cnt FROM entries WHERE 1=1 {where}"
    total = query_one(count_sql, tuple(params))
    total_count = total["cnt"] if total else 0

    # Data
    offset = (max(page, 1) - 1) * per_page
    data_sql = f"""
        SELECT date, venue, race_num, race_name, class, race_id, horse, gender, age,
               jockey, trainer, sire, runners, gate, number, popularity, win_odds,
               finish, surface, distance, condition, weather, last_3f, prize, weight,
               win_payout, trifecta, prev_race, prev_finish, prev_pop, prev_class, interval
        FROM entries
        WHERE 1=1 {where}
        ORDER BY {sort_col} {sort_order}, race_num {"DESC" if sort_order == "DESC" else "ASC"}, number ASC
        LIMIT ? OFFSET ?
    """
    params.extend([per_page, offset])
    rows = query(data_sql, tuple(params))

    return {
        "total": total_count,
        "page": page,
        "per_page": per_page,
        "pages": math.ceil(total_count / per_page) if per_page > 0 else 0,
        "data": rows,
    }


# ---------------------------------------------------------------------------
# 10. GET /api/condition-strength
# ---------------------------------------------------------------------------
@app.get("/api/condition-strength")
def get_condition_strength(
    jockey: Optional[str] = None,
    sire: Optional[str] = None,
    trainer: Optional[str] = None,
    venue: Optional[str] = None,
    surface: Optional[str] = None,
    distance: Optional[str] = None,
):
    filters = {
        "jockey": jockey, "sire": sire, "trainer": trainer,
        "venue": venue, "surface": surface, "distance": distance,
    }
    # At least one filter required
    if not any(v for v in filters.values()):
        return {"error": "At least one filter parameter is required (jockey, sire, trainer, venue, surface, distance)"}

    params: list = []
    where = build_where(filters, params)

    # Overall stats
    stats_sql = f"""
        SELECT
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS place_rate,
            AVG(CASE WHEN finish <= 2 THEN 1.0 ELSE 0.0 END) AS top2_rate,
            AVG(finish) AS avg_finish,
            AVG(popularity) AS avg_pop
        FROM entries
        WHERE 1=1 {where}
    """
    stats = query_one(stats_sql, tuple(params))

    if not stats or stats["n"] == 0:
        return {"n": 0, "win_rate": None, "place_rate": None, "top2_rate": None,
                "avg_finish": None, "avg_pop": None, "by_class": []}

    # By class breakdown
    by_class_sql = f"""
        SELECT
            class,
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS place_rate
        FROM entries
        WHERE 1=1 {where} AND class IS NOT NULL AND class != ''
        GROUP BY class
        ORDER BY n DESC
    """
    by_class = query(by_class_sql, tuple(params))
    by_class = [
        {"class": r["class"], "n": r["n"],
         "win_rate": pct(r["win_rate"]), "place_rate": pct(r["place_rate"])}
        for r in by_class
    ]

    return {
        "n": stats["n"],
        "win_rate": pct(stats["win_rate"]),
        "place_rate": pct(stats["place_rate"]),
        "top2_rate": pct(stats["top2_rate"]),
        "avg_finish": rnd(stats["avg_finish"], 1),
        "avg_pop": rnd(stats["avg_pop"], 1),
        "by_class": by_class,
    }


# ---------------------------------------------------------------------------
# 11. GET /api/horse-form
# ---------------------------------------------------------------------------
@app.get("/api/horse-form")
def get_horse_form(horse: str):
    if not horse or not horse.strip():
        return {"error": "horse parameter is required"}

    horse = horse.strip()

    # Overall stats
    stats_sql = """
        SELECT
            COUNT(*) AS total_runs,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS place_rate,
            AVG(finish) AS avg_finish,
            AVG(popularity) AS avg_pop
        FROM entries
        WHERE horse = ?
    """
    stats = query_one(stats_sql, (horse,))

    if not stats or stats["total_runs"] == 0:
        return {"total_runs": 0, "win_rate": None, "place_rate": None,
                "avg_finish": None, "avg_pop": None, "recent": [], "condition_breakdown": []}

    # Recent races
    recent_sql = """
        SELECT date, venue, race_name, distance, surface, finish, popularity,
               win_odds, prev_race
        FROM entries
        WHERE horse = ?
        ORDER BY date DESC, race_num DESC
        LIMIT 20
    """
    recent = query(recent_sql, (horse,))

    # Condition breakdown: surface x distance_band
    db_band = distance_band_sql()
    breakdown_sql = f"""
        SELECT
            surface || '×' || {db_band} AS surface_distance,
            COUNT(*) AS n,
            AVG(CASE WHEN finish = 1 THEN 1.0 ELSE 0.0 END) AS win_rate,
            AVG(CASE WHEN finish <= 3 THEN 1.0 ELSE 0.0 END) AS place_rate,
            AVG(finish) AS avg_finish
        FROM entries
        WHERE horse = ? AND surface IS NOT NULL
        GROUP BY surface_distance
        ORDER BY n DESC
    """
    breakdown = query(breakdown_sql, (horse,))
    breakdown = [
        {"surface_distance": r["surface_distance"], "n": r["n"],
         "win_rate": pct(r["win_rate"]), "place_rate": pct(r["place_rate"]),
         "avg_finish": rnd(r["avg_finish"], 1)}
        for r in breakdown
    ]

    return {
        "total_runs": stats["total_runs"],
        "win_rate": pct(stats["win_rate"]),
        "place_rate": pct(stats["place_rate"]),
        "avg_finish": rnd(stats["avg_finish"], 1),
        "avg_pop": rnd(stats["avg_pop"], 1),
        "recent": recent,
        "condition_breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# 12. GET /api/race-entries
# ---------------------------------------------------------------------------
@app.get("/api/race-entries")
def get_race_entries(race_name: str, year: Optional[int] = None):
    if not race_name or not race_name.strip():
        return {"error": "race_name parameter is required"}

    like_pat = f"%{race_name.strip()}%"

    # Available years for this race
    year_rows = query(
        "SELECT DISTINCT year FROM entries WHERE race_name LIKE ? ORDER BY year",
        (like_pat,),
    )
    available_years = [r["year"] for r in year_rows]

    if not available_years:
        return {"error": "No entries found for this race", "race_name": race_name}

    # If year not specified, use the most recent
    target_year = year if year is not None and year in available_years else available_years[-1]

    # Fetch entries
    rows = query(
        """SELECT horse, jockey, sire, prev_race, prev_finish,
                  number, popularity, finish, win_odds,
                  venue, surface, distance, condition
           FROM entries
           WHERE race_name LIKE ? AND year = ?
           ORDER BY popularity ASC""",
        (like_pat, target_year),
    )

    if not rows:
        return {"error": "No entries found for this race/year",
                "race_name": race_name, "year": target_year}

    # Race-level info from the first row
    first = rows[0]
    horses = [
        {
            "name": r["horse"],
            "jockey": r["jockey"],
            "sire": r["sire"],
            "prev_race": r["prev_race"],
            "prev_finish": r["prev_finish"],
            "number": r["number"],
            "popularity": r["popularity"],
            "finish": r["finish"],
            "win_odds": r["win_odds"],
        }
        for r in rows
    ]

    return {
        "race_name": race_name.strip(),
        "year": target_year,
        "venue": first["venue"],
        "surface": first["surface"],
        "distance": first["distance"],
        "condition": first["condition"],
        "available_years": available_years,
        "horses": horses,
    }


# ---------------------------------------------------------------------------
# POST /api/race-analysis
# ---------------------------------------------------------------------------

class HorseEntry(BaseModel):
    name: str
    jockey: Optional[str] = None
    sire: Optional[str] = None
    prev_race: Optional[str] = None
    prev_finish: Optional[int] = None


class RaceAnalysisRequest(BaseModel):
    race_name: Optional[str] = None
    venue: str
    surface: str
    distance: int
    condition: Optional[str] = None
    horses: List[HorseEntry]


def _rate_stats(rows: List[Dict]) -> Dict:
    """Compute win_rate, place_rate, avg_finish from a list of entry rows."""
    n = len(rows)
    if n == 0:
        return {"n": 0, "win_rate": None, "place_rate": None, "avg_finish": None}
    finishes = [r["finish"] for r in rows if r.get("finish") is not None]
    if not finishes:
        return {"n": n, "win_rate": None, "place_rate": None, "avg_finish": None}
    wins = sum(1 for f in finishes if f == 1)
    places = sum(1 for f in finishes if f <= 3)
    avg_f = sum(finishes) / len(finishes)
    return {
        "n": n,
        "win_rate": rnd(wins / n * 100, 1),
        "place_rate": rnd(places / n * 100, 1),
        "avg_finish": rnd(avg_f, 1),
    }


@app.post("/api/race-analysis")
def race_analysis(req: RaceAnalysisRequest):
    results = []

    for h in req.horses:
        entry: Dict = {"name": h.name, "jockey": h.jockey, "sire": h.sire}

        # ------------------------------------------------------------------
        # 1. Rotation score
        # ------------------------------------------------------------------
        rotation: Dict = {"n": 0, "win_rate": None, "place_rate": None, "avg_finish": None}
        rotation_by_finish: Optional[Dict] = None

        if req.race_name and h.prev_race:
            rot_rows = query(
                "SELECT finish FROM entries WHERE race_name LIKE ? AND prev_race LIKE ?",
                (f"%{req.race_name}%", f"%{h.prev_race}%"),
            )
            rotation = _rate_stats(rot_rows)

            if h.prev_finish is not None:
                rot_finish_rows = query(
                    "SELECT finish FROM entries WHERE race_name LIKE ? AND prev_race LIKE ? AND prev_finish = ?",
                    (f"%{req.race_name}%", f"%{h.prev_race}%", h.prev_finish),
                )
                rotation_by_finish = _rate_stats(rot_finish_rows)

        entry["rotation"] = {
            "n": rotation["n"],
            "win_rate": rotation["win_rate"],
            "place_rate": rotation["place_rate"],
            "avg_finish": rotation["avg_finish"],
        }
        if rotation_by_finish is not None:
            entry["rotation"]["by_finish"] = {
                "prev_finish": h.prev_finish,
                "n": rotation_by_finish["n"],
                "place_rate": rotation_by_finish["place_rate"],
                "avg_finish": rotation_by_finish["avg_finish"],
            }

        # ------------------------------------------------------------------
        # 2. Sire score
        # ------------------------------------------------------------------
        sire_stats: Dict = {"n": 0, "win_rate": None, "place_rate": None, "avg_finish": None}
        if h.sire:
            sire_rows = query(
                "SELECT finish FROM entries WHERE sire LIKE ? AND venue = ? AND surface = ? AND distance BETWEEN ? AND ?",
                (f"%{h.sire}%", req.venue, req.surface, req.distance - 200, req.distance + 200),
            )
            sire_stats = _rate_stats(sire_rows)
        entry["sire_fit"] = {
            "n": sire_stats["n"],
            "win_rate": sire_stats["win_rate"],
            "place_rate": sire_stats["place_rate"],
            "avg_finish": sire_stats["avg_finish"],
        }

        # ------------------------------------------------------------------
        # 3. Jockey score
        # ------------------------------------------------------------------
        jockey_stats: Dict = {"n": 0, "win_rate": None, "place_rate": None, "avg_finish": None}
        if h.jockey:
            jockey_rows = query(
                "SELECT finish FROM entries WHERE jockey LIKE ? AND venue = ?",
                (f"%{h.jockey}%", req.venue),
            )
            jockey_stats = _rate_stats(jockey_rows)
        entry["jockey_fit"] = {
            "n": jockey_stats["n"],
            "win_rate": jockey_stats["win_rate"],
            "place_rate": jockey_stats["place_rate"],
            "avg_finish": jockey_stats["avg_finish"],
        }

        # ------------------------------------------------------------------
        # 4. Horse form
        # ------------------------------------------------------------------
        horse_rows = query(
            "SELECT finish, surface, distance FROM entries WHERE horse LIKE ?",
            (f"%{h.name}%",),
        )
        h_stats = _rate_stats(horse_rows)
        # same condition subset
        sc_rows = [
            r for r in horse_rows
            if r.get("surface") == req.surface
            and r.get("distance") is not None
            and abs(r["distance"] - req.distance) <= 200
        ]
        sc_stats = _rate_stats(sc_rows)

        entry["horse_form"] = {
            "total": h_stats["n"],
            "wins": sum(1 for r in horse_rows if r.get("finish") == 1),
            "places": sum(1 for r in horse_rows if r.get("finish") is not None and r["finish"] <= 3),
            "win_rate": h_stats["win_rate"],
            "place_rate": h_stats["place_rate"],
            "avg_finish": h_stats["avg_finish"],
            "same_cond": {
                "n": sc_stats["n"],
                "place_rate": sc_stats["place_rate"],
            },
        }

        # ------------------------------------------------------------------
        # 5. Composite score
        # ------------------------------------------------------------------
        rot_pr = rotation["place_rate"] or 0
        sire_pr = sire_stats["place_rate"] or 0
        jockey_pr = jockey_stats["place_rate"] or 0
        horse_pr = h_stats["place_rate"] or 0

        composite = rnd(rot_pr * 0.35 + sire_pr * 0.20 + jockey_pr * 0.20 + horse_pr * 0.25, 1)
        entry["composite_score"] = composite

        # ------------------------------------------------------------------
        # 6. Factors
        # ------------------------------------------------------------------
        factors = []
        if rotation["place_rate"] is not None and rotation["place_rate"] >= 40:
            factors.append("rotation_strong")
        if (rotation_by_finish is not None
                and rotation_by_finish["place_rate"] is not None
                and rotation_by_finish["place_rate"] >= 50):
            factors.append("rotation_by_finish_strong")
        if sire_stats["place_rate"] is not None and sire_stats["place_rate"] >= 30:
            factors.append("sire_fit")
        if jockey_stats["place_rate"] is not None and jockey_stats["place_rate"] >= 35:
            factors.append("jockey_fit")
        if h_stats["place_rate"] is not None and h_stats["place_rate"] >= 50:
            factors.append("horse_proven")
        if sc_stats["place_rate"] is not None and sc_stats["place_rate"] >= 50:
            factors.append("same_cond_proven")
        entry["factors"] = factors

        results.append(entry)

    # Sort by composite_score descending
    results.sort(key=lambda x: x.get("composite_score") or 0, reverse=True)

    return {
        "race_info": {
            "venue": req.venue,
            "surface": req.surface,
            "distance": req.distance,
            "race_name": req.race_name,
        },
        "horses": results,
    }


# ---------------------------------------------------------------------------
# GET /api/backtest-g1
# ---------------------------------------------------------------------------
_backtest_g1_cache: Optional[Dict] = None


def _compute_composite(race_name: str, venue: str, surface: str, distance: int,
                       horse_name: str, jockey: Optional[str], sire: Optional[str],
                       prev_race: Optional[str], prev_finish: Optional[float],
                       date_cutoff: Optional[str] = None) -> float:
    """Compute composite_score for a single horse.
    date_cutoff: if set, only use data before this date (YYYY-MM-DD) to prevent future leakage."""
    date_filter = " AND date < ?" if date_cutoff else ""
    date_params = (date_cutoff,) if date_cutoff else ()

    # 1. Rotation
    rot_pr = 0.0
    if race_name and prev_race:
        rot_rows = query(
            f"SELECT finish FROM entries WHERE race_name LIKE ? AND prev_race LIKE ?{date_filter}",
            (f"%{race_name}%", f"%{prev_race}%") + date_params,
        )
        rot_stats = _rate_stats(rot_rows)
        rot_pr = rot_stats["place_rate"] or 0

    # 2. Sire
    sire_pr = 0.0
    if sire:
        sire_rows = query(
            f"SELECT finish FROM entries WHERE sire LIKE ? AND venue = ? AND surface = ? AND distance BETWEEN ? AND ?{date_filter}",
            (f"%{sire}%", venue, surface, distance - 200, distance + 200) + date_params,
        )
        sire_pr = (_rate_stats(sire_rows)["place_rate"] or 0)

    # 3. Jockey
    jockey_pr = 0.0
    if jockey:
        jockey_rows = query(
            f"SELECT finish FROM entries WHERE jockey LIKE ? AND venue = ?{date_filter}",
            (f"%{jockey}%", venue) + date_params,
        )
        jockey_pr = (_rate_stats(jockey_rows)["place_rate"] or 0)

    # 4. Horse form
    horse_rows = query(
        f"SELECT finish, surface, distance FROM entries WHERE horse LIKE ?{date_filter}",
        (f"%{horse_name}%",) + date_params,
    )
    horse_pr = (_rate_stats(horse_rows)["place_rate"] or 0)

    composite = rot_pr * 0.35 + sire_pr * 0.20 + jockey_pr * 0.20 + horse_pr * 0.25
    return round(composite, 1)


@app.get("/api/backtest-g1")
def backtest_g1():
    global _backtest_g1_cache
    if _backtest_g1_cache is not None:
        return _backtest_g1_cache

    # Get all distinct G1 races with date for cutoff
    g1_races = query(
        "SELECT DISTINCT race_name, year, date, venue, surface, distance, condition "
        "FROM entries WHERE class LIKE '%Ｇ1%' ORDER BY year, race_name"
    )

    by_race = []
    top1_hits = 0
    top3_hits = 0
    top5_hits = 0
    all_top3_ranks = []

    # ROI tracking: bet top-N by score, check payout
    roi_top1_bet = 0; roi_top1_payout = 0  # 単勝: スコア1位に100円
    roi_top3_bet = 0; roi_top3_payout = 0  # 複勝的: スコア上位3頭に各100円(的中=着順3位以内でオッズ×100)
    roi_trio_bet = 0; roi_trio_payout = 0  # 三連単: スコア上位3頭ボックス(6点×100円)

    for race in g1_races:
        rn = race["race_name"]
        yr = race["year"]
        race_date = race["date"]  # YYYY-MM-DD
        ven = race["venue"]
        sf = race["surface"]
        dist = race["distance"]

        # Get horses in this race
        horses = query(
            "SELECT horse, jockey, sire, prev_race, prev_finish, finish, win_odds, win_payout, trifecta "
            "FROM entries WHERE race_name = ? AND year = ? AND venue = ? "
            "AND surface = ? AND distance = ? ORDER BY horse",
            (rn, yr, ven, sf, dist),
        )
        if len(horses) < 3:
            continue

        # Compute composite scores
        scored = []
        for h in horses:
            score = _compute_composite(
                rn, ven, sf, dist,
                h["horse"], h["jockey"], h["sire"],
                h.get("prev_race"), h.get("prev_finish"),
                date_cutoff=race_date,
            )
            scored.append({
                "name": h["horse"],
                "actual_finish": int(h["finish"]) if h["finish"] is not None else None,
                "composite_score": score,
                "win_odds": h.get("win_odds"),
                "win_payout": h.get("win_payout"),
                "trifecta": h.get("trifecta"),
            })

        # Rank by composite_score descending
        scored.sort(key=lambda x: x["composite_score"], reverse=True)
        for rank_idx, s in enumerate(scored):
            s["score_rank"] = rank_idx + 1

        # Actual top 3 finishers
        actual_top3 = {s["name"] for s in scored if s["actual_finish"] is not None and s["actual_finish"] <= 3}

        # Score-ranked top N
        score_top1_names = {scored[0]["name"]} if scored else set()
        score_top3_names = {s["name"] for s in scored[:3]}
        score_top5_names = {s["name"] for s in scored[:5]}

        # top1_hit: score rank 1 finished in actual top 3
        t1_hit = bool(score_top1_names & actual_top3)
        # top3_hit: at least 2 of score top 3 in actual top 3
        t3_hit = len(score_top3_names & actual_top3) >= 2
        # top5_hit: at least 2 of score top 5 in actual top 3
        t5_hit = len(score_top5_names & actual_top3) >= 2

        if t1_hit:
            top1_hits += 1
        if t3_hit:
            top3_hits += 1
        if t5_hit:
            top5_hits += 1

        # avg rank of actual top-3 finishers
        for s in scored:
            if s["name"] in actual_top3:
                all_top3_ranks.append(s["score_rank"])

        # ROI: 単勝 — スコア1位に100円賭け
        roi_top1_bet += 100
        if scored[0]["actual_finish"] == 1 and scored[0]["win_payout"]:
            roi_top1_payout += scored[0]["win_payout"]

        # ROI: 複勝的 — スコア上位3頭に各100円(3着以内ならオッズ×100近似)
        for s in scored[:3]:
            roi_top3_bet += 100
            if s["actual_finish"] is not None and s["actual_finish"] <= 3 and s["win_odds"]:
                # 複勝配当の近似: 単勝オッズ / 3 * 100 (rough)
                roi_top3_payout += max(s["win_odds"] / 3 * 100, 100)

        # ROI: 三連単ボックス — スコア上位3頭(6点×100円=600円)
        roi_trio_bet += 600
        top3_names = {s["name"] for s in scored[:3]}
        if actual_top3 == top3_names:
            # 全員的中 = 三連単GET
            tf = scored[0].get("trifecta")
            if tf:
                roi_trio_payout += tf

        # Build top3 horses info for display
        top3_display = [s for s in scored if s["actual_finish"] is not None and s["actual_finish"] <= 3]
        top3_display.sort(key=lambda x: x["actual_finish"])

        by_race.append({
            "race_name": rn,
            "year": yr,
            "top3_horses": [
                {"name": s["name"], "score_rank": s["score_rank"], "actual_finish": s["actual_finish"]}
                for s in top3_display
            ],
            "hit": t5_hit,
        })

    total = len(by_race)
    avg_rank = round(sum(all_top3_ranks) / len(all_top3_ranks), 1) if all_top3_ranks else None

    result = {
        "total_races": total,
        "top1_hit": round(top1_hits / total * 100, 1) if total else 0,
        "top3_hit": round(top3_hits / total * 100, 1) if total else 0,
        "top5_hit": round(top5_hits / total * 100, 1) if total else 0,
        "avg_top3_rank": avg_rank,
        "roi": {
            "top1_win": round(roi_top1_payout / roi_top1_bet * 100, 1) if roi_top1_bet else 0,
            "top3_place": round(roi_top3_payout / roi_top3_bet * 100, 1) if roi_top3_bet else 0,
            "trio_box": round(roi_trio_payout / roi_trio_bet * 100, 1) if roi_trio_bet else 0,
            "top1_bet": roi_top1_bet, "top1_payout": round(roi_top1_payout),
            "top3_bet": roi_top3_bet, "top3_payout": round(roi_top3_payout),
            "trio_bet": roi_trio_bet, "trio_payout": round(roi_trio_payout),
        },
        "by_race": by_race,
    }

    _backtest_g1_cache = result
    return result


# ---------------------------------------------------------------------------
# Strategy results (v3-v6 + simulations)
# ---------------------------------------------------------------------------
import json as _json

_strategies_cache = None

@app.get("/api/strategies")
def get_strategies():
    """戦略結果ファイルを返す (v3-v6, OOS, ポートフォリオ等)"""
    global _strategies_cache
    if _strategies_cache: return _strategies_cache
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_results.json")
    if not os.path.exists(p): return {"records": []}
    with open(p) as f:
        records = _json.load(f)
    _strategies_cache = {"records": records}
    return _strategies_cache


@app.get("/api/wealth-curve")
def get_wealth_curve():
    """資産推移データを返す (戦略別+ポートフォリオ、ベットサイズ複数モード)"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wealth_data.json")
    if not os.path.exists(p):
        return {"error": "wealth_data.json not found. Run plot_wealth_data.py first."}
    with open(p) as f:
        return _json.load(f)


@app.get("/api/edge-validation")
def get_edge_validation():
    """エッジ検証データを返す (人気帯/オッズ帯/DD)"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "edge_data.json")
    if not os.path.exists(p):
        return {"error": "edge_data.json not found. Run edge_validation.py first."}
    with open(p) as f:
        return _json.load(f)


@app.get("/api/analytics")
def get_analytics():
    """8つの追加分析(walk-forward / yearly / underwater / MC / benchmark / p-value / frequency)"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analytics_data.json")
    if not os.path.exists(p):
        return {"error": "analytics_data.json not found. Run analytics.py first."}
    with open(p) as f:
        return _json.load(f)


@app.get("/api/live-state")
def get_live_state():
    """本番ポートフォリオ状態 + 直近picks"""
    base = os.path.dirname(os.path.abspath(__file__))
    state_p = os.path.join(base, "state", "portfolio.json")
    if not os.path.exists(state_p):
        return {"error": "state/portfolio.json not found"}
    with open(state_p) as f:
        state = _json.load(f)

    picks_dir = os.path.join(base, "state", "picks")
    latest_picks = None
    if os.path.exists(picks_dir):
        files = sorted([f for f in os.listdir(picks_dir) if f.endswith(".json")])
        if files:
            with open(os.path.join(picks_dir, files[-1])) as f:
                latest_picks = _json.load(f)
                latest_picks["_filename"] = files[-1]

    return {"state": state, "latest_picks": latest_picks}


# ---------------------------------------------------------------------------
# POST /api/generate-picks
# スマホアプリから日付指定で scrape+picks 生成を実行するエンドポイント
#
# 動作確認:
#   curl -X POST http://localhost:8001/api/generate-picks \
#     -H 'Content-Type: application/json' \
#     -d '{"date_from": "2026-05-23", "date_to": "2026-05-23"}'
#
# 注意:
#   - scrape は 30〜60 秒かかる。FastAPI 自体は問題ないが、
#     Cloudflare Tunnel / リバースプロキシのアイドルタイムアウトに注意。
#   - 既存 uvicorn プロセスは --reload なしで起動しているので、
#     このファイル更新後は手動再起動が必要。
# ---------------------------------------------------------------------------
import sys as _sys
import time as _time
import argparse as _argparse
from datetime import date as _date
from pathlib import Path as _Path

from fastapi import HTTPException
from fastapi import Body


class GeneratePicksRequest(BaseModel):
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD


def _ymd_to_yyyymmdd_range(date_from: str, date_to: str) -> List[str]:
    """YYYY-MM-DD 〜 YYYY-MM-DD の日付範囲を YYYYMMDD のリストに変換"""
    from datetime import datetime, timedelta
    try:
        d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date format (expected YYYY-MM-DD): {e}")
    if d_to < d_from:
        raise HTTPException(status_code=400, detail="date_to must be >= date_from")
    days = []
    d = d_from
    while d <= d_to:
        days.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return days


@app.post("/api/generate-picks")
def generate_picks(req: GeneratePicksRequest):
    """指定日付範囲で scrape+picks 生成を一気通貫で実行し、生成された picks.json を返す。"""
    t_start = _time.time()
    # live/ 配下のスクリプトを直接 import するため sys.path を通す
    base = _Path(__file__).resolve().parent
    if str(base) not in _sys.path:
        _sys.path.insert(0, str(base))

    # 日付変換
    dates_yyyymmdd = _ymd_to_yyyymmdd_range(req.date_from, req.date_to)

    # 1. scrape (関数直接呼び出し)
    try:
        from live.scrape_all import run_scrape
        scrape_summary = run_scrape(dates_yyyymmdd, notify_slack=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"scrape failed: {type(e).__name__}: {e}")

    # 2. picks 生成 (runner.cmd_picks に Namespace を渡す)
    try:
        from live.runner import cmd_picks
        ns = _argparse.Namespace(date_from=req.date_from, date_to=req.date_to)
        cmd_picks(ns)
    except SystemExit:
        # argparse 経路ではないので通常出ないが、念のため
        raise HTTPException(status_code=500, detail="cmd_picks called sys.exit")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"picks generation failed: {type(e).__name__}: {e}")

    # 3. 生成された picks.json を読み込む
    today = _date.today().isoformat()
    picks_path = base / "state" / "picks" / f"{today}.json"
    if not picks_path.exists():
        # 対象レース 0 件等で picks.json が作られなかった場合
        return {
            "picks": None,
            "message": "picks ファイルが生成されませんでした (対象レースなしの可能性)",
            "scrape_summary": scrape_summary,
            "elapsed_sec": round(_time.time() - t_start, 1),
        }

    with open(picks_path) as f:
        picks = _json.load(f)

    return {
        "picks": picks,
        "picks_filename": picks_path.name,
        "scrape_summary": scrape_summary,
        "elapsed_sec": round(_time.time() - t_start, 1),
    }
