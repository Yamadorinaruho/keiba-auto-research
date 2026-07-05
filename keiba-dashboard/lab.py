#!/usr/bin/env python3
"""夏戦略バックテストの実験ハーネス。

毎回書いていた前処理(履歴からのキャリア/前走頭数再構築・血統・母集団フィルタ・score・ROI集計)を
再利用可能にまとめたもの。3戦略(芝/ダ/新馬)の確定条件をデフォルトに持ち、
パラメータ上書きで「もし帯を変えたら」「もしscore閾値を変えたら」を1行で試せる。

前提: cwd=keiba-dashboard で実行(keiba.db と live/ を相対参照)。
履歴再構築(795k行)は .lab_hist_cache.pkl にキャッシュ(DB行数が変わると自動再生成)。

使い方:
    import lab
    lab.report('shiba')                       # 現行v2条件(strategy_spec)で集計
    lab.report('shiba', band=(8, 30))         # 帯だけ8-30に変えて比較
    lab.report('shiba', blood={'ディープ系'})  # 血統をディープ系のみに
    lab.report('shiba', **lab.V1['shiba'])    # 旧v1(score版)を再現(芝=146%)
    sel = lab.select('shiba', band=None)      # 帯なしの買い目リストを取得
    print(lab.roi(sel), lab.yearly(sel))

デフォルト条件は live/strategy_spec.py(仕様の単一情報源)から取る=本番v2と常に一致。
旧v1仕様は V1 プリセットで再現できる。数値の解説は SUMMER_STRATEGIES.md 参照。
"""
import os
import re
import sqlite3
import pickle
from collections import defaultdict

from live import strategy_spec as spec
from live.sire_lineage_map import lineage_of

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "keiba.db")
_HIST_CACHE = os.path.join(DIR, ".lab_hist_cache.pkl")

GOOD2 = spec.GOOD2                     # 芝 血統 +2 (v1 score用)
GOOD1 = spec.GOOD1                     # 芝 血統 +1
US = spec.DIRT_BLOOD                   # ダ 血統
EPI = spec.SHINBA_SIRES                # 新馬 対象父
LOCAL5 = ("函館", "札幌", "福島", "新潟", "小倉")   # v1芝の対象5場(v2は全会場)
CLS_DIRT = spec.DIRT_CLS

# 各戦略のデフォルト条件=現行v2(strategy_spec準拠)。report/selectで個別に上書き可能。
# Noneはフィルタ無効。age=馬齢(ダのみ有効: v2=3歳牝, v1=牝全年齢)。
DEFAULTS = {
    "shiba":  {"pop": None, "band": spec.SHIBA_BAND, "min_score": None,
               "min_career": spec.MIN_CAREER, "blood": spec.SHIBA_BLOOD, "venues": None, "age": None},
    "dirt":   {"pop": None, "band": spec.DIRT_BAND, "min_score": None,
               "min_career": spec.MIN_CAREER, "blood": spec.DIRT_BLOOD, "venues": None, "age": 3},
    "shinba": {"pop": None, "band": None, "min_score": None,
               "min_career": None, "blood": None, "venues": None, "age": None},
}

# 旧v1(score版, 〜2026-06-27稼働)の再現プリセット: lab.report('shiba', **lab.V1['shiba'])
V1 = {
    "shiba":  {"pop": (4, 12), "band": (10, 80), "min_score": 3,
               "min_career": 2, "blood": None, "venues": LOCAL5, "age": None},
    "dirt":   {"pop": (4, 12), "band": (10, 50), "min_score": 3,
               "min_career": 2, "blood": None, "venues": None, "age": None},
    "shinba": dict(DEFAULTS["shinba"]),
}


def connect():
    return sqlite3.connect(DB)


def lin_bonus(lin):
    return 2 if lin in GOOD2 else (1 if lin in GOOD1 else 0)


def summer(mm, dd):
    """夏季(芝/ダ=6/16-8末)。新馬は6/1-だが month in (6,7,8) で別途扱う。"""
    return (mm == 6 and dd >= 16) or mm in (7, 8)


def _is_epi(sire):
    sn = sire or ""
    if sn in EPI:
        return True
    m = re.match(r"^[^\x00-\x7f]+", sn)
    return (m.group(0) if m else sn) in EPI


def build_history(force=False):
    """各馬の自己履歴から prev_runners[(horse,race_id)]=前走頭数, career[(horse,race_id)]=過去出走数 を作る。
    DB行数が変わらなければ .lab_hist_cache.pkl から即ロード。"""
    con = connect()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM entries")
    nrow = cur.fetchone()[0]
    if not force and os.path.exists(_HIST_CACHE):
        try:
            with open(_HIST_CACHE, "rb") as f:
                c = pickle.load(f)
            if c.get("nrow") == nrow:
                return c["prev_runners"], c["career"]
        except Exception:
            pass
    cur.execute("SELECT horse,date,race_id,runners FROM entries WHERE horse IS NOT NULL ORDER BY horse,date")
    hist = defaultdict(list)
    for h, d, rid, run in cur.fetchall():
        hist[h].append((d, rid, run))
    prev_runners, career = {}, {}
    for h, rows in hist.items():
        for i, (d, rid, run) in enumerate(rows):
            career[(h, rid)] = i
            if i >= 1:
                prev_runners[(h, rid)] = rows[i - 1][2]
    try:
        with open(_HIST_CACHE, "wb") as f:
            pickle.dump({"nrow": nrow, "prev_runners": prev_runners, "career": career}, f)
    except Exception:
        pass
    return prev_runners, career


_POP_CACHE = {}


def population(strat):
    """戦略の母集団(オッズ帯/score閾値を掛ける前)を返す。
    各行: {fin,pay,ppay,yr,pop,odds,score,career,strat}。"""
    if strat in _POP_CACHE:
        return _POP_CACHE[strat]
    pr, career = build_history()
    con = connect()
    cur = con.cursor()
    out = []
    if strat == "shiba":
        cur.execute("""SELECT horse,race_id,popularity,win_odds,finish,win_payout,place_payout,
            prev_corner4,prev_finish,horse_weight,sire,prev_margin,venue,
            CAST(substr(date,6,2) AS INT),CAST(substr(date,9,2) AS INT),substr(date,1,4)
            FROM entries WHERE surface='芝' AND class='未勝利' AND age=3 AND gender='牝'
            AND win_odds IS NOT NULL AND finish IS NOT NULL""")
        for horse, rid, pop, odds, fin, pay, ppay, pc4, pfin, wt, sire, pmargin, venue, mm, dd, yr in cur.fetchall():
            if not summer(mm, dd):
                continue
            nrun = pr.get((horse, rid))
            rel = (pc4 / nrun) if (pc4 and nrun) else None
            lin = lineage_of(sire)
            comp = {"rel": int(rel is not None and rel > 0.33), "fin": int(pfin is not None and pfin >= 6),
                    "wt": int(wt is not None and 420 <= wt <= 470), "blood": lin_bonus(lin)}
            score = comp["rel"] + comp["fin"] + comp["wt"] + comp["blood"]
            out.append({"fin": fin, "pay": pay or 0, "ppay": ppay, "yr": yr, "pop": pop, "odds": odds,
                        "score": score, "comp": comp, "career": career.get((horse, rid), 0),
                        "pmargin": pmargin, "pfin": pfin, "lin": lin, "venue": venue,
                        "age": 3, "strat": strat})
    elif strat == "dirt":
        cur.execute("""SELECT horse,race_id,popularity,win_odds,finish,win_payout,place_payout,
            prev_corner4,prev_finish,horse_weight,sire,prev_margin,venue,age,
            CAST(substr(date,6,2) AS INT),CAST(substr(date,9,2) AS INT),substr(date,1,4),class,distance
            FROM entries WHERE surface='ダ' AND gender='牝'
            AND win_odds IS NOT NULL AND finish IS NOT NULL""")
        for horse, rid, pop, odds, fin, pay, ppay, pc4, pfin, wt, sire, pmargin, venue, age, mm, dd, yr, cls, dist in cur.fetchall():
            if not summer(mm, dd) or cls not in CLS_DIRT or dist is None or dist > spec.DIRT_MAX_DIST:
                continue
            nrun = pr.get((horse, rid))
            rel = (pc4 / nrun) if (pc4 and nrun) else None
            lin = lineage_of(sire)
            comp = {"rel": int(rel is not None and rel <= 0.33), "blood": int(lin in US),
                    "wt": int(wt is not None and 450 <= wt <= 490), "fin": int(pfin is not None and pfin <= 9)}
            score = comp["rel"] + comp["blood"] + comp["wt"] + comp["fin"]
            out.append({"fin": fin, "pay": pay or 0, "ppay": ppay, "yr": yr, "pop": pop, "odds": odds,
                        "score": score, "comp": comp, "career": career.get((horse, rid), 0),
                        "pmargin": pmargin, "pfin": pfin, "lin": lin, "venue": venue,
                        "age": age, "strat": strat})
    elif strat == "shinba":
        cur.execute("""SELECT win_odds,finish,win_payout,place_payout,sire,popularity,
            CAST(substr(date,6,2) AS INT),substr(date,1,4)
            FROM entries WHERE surface='芝' AND class='新馬' AND age=2
            AND win_odds IS NOT NULL AND finish IS NOT NULL""")
        for odds, fin, pay, ppay, sire, pop, mm, yr in cur.fetchall():
            if mm not in (6, 7, 8) or not _is_epi(sire):
                continue
            out.append({"fin": fin, "pay": pay or 0, "ppay": ppay, "yr": yr, "pop": pop,
                        "odds": odds, "score": None, "career": None, "strat": strat})
    else:
        raise ValueError(f"unknown strat: {strat}")
    _POP_CACHE[strat] = out
    return out


def select(strat, **ov):
    """戦略の買い目を返す。ov でデフォルト条件を上書き
    (pop/band/min_score/min_career/blood/venues/age, Noneで無効化)。"""
    p = {**DEFAULTS[strat], **ov}
    sel = []
    for r in population(strat):
        if p.get("venues") is not None and r.get("venue") not in p["venues"]:
            continue
        if p.get("age") is not None and r.get("age") != p["age"]:
            continue
        if p["min_career"] is not None and (r["career"] is None or r["career"] < p["min_career"]):
            continue
        if p["pop"] is not None and (r["pop"] is None or not (p["pop"][0] <= r["pop"] <= p["pop"][1])):
            continue
        if p["band"] is not None and not (p["band"][0] <= r["odds"] < p["band"][1]):
            continue
        if p["min_score"] is not None and (r["score"] is None or r["score"] < p["min_score"]):
            continue
        if p.get("blood") is not None and r.get("lin") not in p["blood"]:
            continue
        sel.append(r)
    return sel


def roi(rows):
    """単勝/複勝のROI等をまとめて返す dict。"""
    n = len(rows)
    if not n:
        return {"n": 0, "hits": 0, "win_rate": 0, "roi": 0, "place": 0, "place_rate": 0, "place_roi": 0}
    hits = sum(1 for r in rows if r["fin"] == 1)
    ret = sum(r["pay"] for r in rows if r["fin"] == 1)
    plc = sum(1 for r in rows if r["fin"] <= 3)
    plc_pay = sum((r["ppay"] or 0) for r in rows if r["fin"] <= 3)
    return {"n": n, "hits": hits, "win_rate": hits / n * 100, "roi": ret / (100 * n) * 100,
            "place": plc, "place_rate": plc / n * 100, "place_roi": plc_pay / (100 * n) * 100}


def yearly(rows):
    """年別ROIの dict {年: roi()} を返す。"""
    by = defaultdict(list)
    for r in rows:
        by[r["yr"]].append(r)
    return {yr: roi(by[yr]) for yr in sorted(by)}


def report(strat, **ov):
    """select→roi→yearly を整形表示。戻り値は (summary, yearly_dict)。"""
    sel = select(strat, **ov)
    s = roi(sel)
    yr = yearly(sel)
    plus = sum(1 for y in yr if yr[y]["roi"] >= 100)
    cond = {**DEFAULTS[strat], **ov}
    print(f"=== {strat} {cond} ===")
    print(f"  N={s['n']} 的中{s['hits']}({s['win_rate']:.1f}%) 単勝ROI={s['roi']:.0f}% "
          f"複勝{s['place']}({s['place_rate']:.0f}%) 複勝ROI={s['place_roi']:.0f}% プラス{plus}/{len(yr)}年")
    for y in sorted(yr):
        v = yr[y]
        print(f"    {y}: N={v['n']:<4} 的中{v['hits']:<3}({v['win_rate']:.1f}%) ROI={v['roi']:.0f}%")
    return s, yr


if __name__ == "__main__":
    import sys
    strat = sys.argv[1] if len(sys.argv) > 1 else "shiba"
    report(strat)
