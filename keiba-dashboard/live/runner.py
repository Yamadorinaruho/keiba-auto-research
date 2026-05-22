"""
本番運用 CLI — 2026-05-09 開始
バックテストとは完全分離。state/ 配下のJSONで状態管理。

Usage:
  python live/runner.py picks  [--from YYYY-MM-DD] [--to YYYY-MM-DD]
  python live/runner.py settle [--date YYYY-MM-DD]
  python live/runner.py status

picks: 指定期間の未開催レース(finish IS NULL)から両ポートフォリオ用の picks を生成
settle: 確定済みレースの結果で pending_picks を精算 → portfolio 更新
status: 現状サマリー出力
"""
import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path

# plot_wealth_data から persona system を再利用
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from plot_wealth_data import (
    DB, INITIAL_CAPITAL, TOP, _make_personas, get_picks, merge_portfolio_bets
)
from live import notify

STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "portfolio.json"
PICKS_DIR = STATE_DIR / "picks"
RESULTS_DIR = STATE_DIR / "results"


def load_state():
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def get_upcoming_races(conn, date_from, date_to, classes=("Ｇ３", "ｵｰﾌﾟﾝ")):
    """finish IS NULL のレースを取得"""
    placeholders = ",".join(["?"] * len(classes))
    rids = [r[0] for r in conn.execute(
        f"SELECT DISTINCT race_id FROM entries "
        f"WHERE class IN ({placeholders}) AND date >= ? AND date <= ? "
        f"AND finish IS NULL ORDER BY date, race_id",
        list(classes) + [date_from, date_to]
    ).fetchall()]
    return rids


YOUNG_UNRACED_CELLS = {(2,'A'), (3,'B'), (3,'C'), (4,'B')}  # (出走月, days帯)


def _days_label(d):
    """days_since_birth による出走時の早熟度ラベル"""
    if d is None: return None
    if d < 1080: return 'A'
    if d < 1110: return 'B'
    if d < 1140: return 'C'
    return None


def build_young_unraced_picks(conn, date_from, date_to, state):
    """3歳未勝利初出走 早生まれ4セル × 10-50倍 単勝戦略の picks 生成。
    Returns: list of {race_id, date, picks=[馬番,...], horse_names, bet_per, total, ...}
    """
    pf = state["portfolios"]["young_unraced"]
    cap = pf["current_cap"]; pct = pf["pct"]
    rids = [r[0] for r in conn.execute(
        "SELECT DISTINCT race_id FROM entries WHERE class='未勝利' AND age=3 "
        "AND date >= ? AND date <= ? AND finish IS NULL ORDER BY date, race_id",
        [date_from, date_to]
    ).fetchall()]
    out = []
    for rid in rids:
        rows = conn.execute(
            "SELECT * FROM entries WHERE race_id=? AND number IS NOT NULL ORDER BY number",
            [rid]
        ).fetchall()
        if not rows: continue
        race_month = int(rows[0]["date"][5:7])
        # 候補馬抽出
        picks = []
        names = {}
        for r in rows:
            if r["prev_race"]: continue  # 初出走のみ
            wo = r["win_odds"]
            if wo is None or not (10 <= wo < 50): continue
            lbl = _days_label(r["days_since_birth"])
            if lbl is None: continue
            if (race_month, lbl) not in YOUNG_UNRACED_CELLS: continue
            picks.append(r["number"])
            names[r["number"]] = r["horse"]
        if not picks: continue
        # bet_per: cap × pct (¥100単位、最低¥100)
        bet_per = int(cap * pct / 100) * 100
        if bet_per < 100: bet_per = 100
        out.append({
            "race_id": rid, "date": rows[0]["date"], "picks": sorted(picks),
            "horse_names": {n: names[n] for n in picks},
            "bet_per": bet_per, "total": bet_per * len(picks),
            "race_name": rows[0]["race_name"] if "race_name" in rows[0].keys() else "",
            "venue": rows[0]["venue"], "race_num": rows[0]["race_num"] if "race_num" in rows[0].keys() else None,
            "surface": rows[0]["surface"], "distance": rows[0]["distance"],
        })
    return sorted(out, key=lambda x: (x["date"], x["race_id"]))


def cmd_picks(args):
    state = load_state()
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    print("[1/4] persona システム構築...", flush=True)
    personas = _make_personas(conn)

    date_from = args.date_from or state.get("last_processed_date") or state["start_date"]
    date_to = args.date_to or "2099-12-31"
    print(f"[2/4] 対象期間: {date_from} 〜 {date_to}")

    rids = get_upcoming_races(conn, date_from, date_to)
    if not rids:
        print(f"  対象レースなし (date >= {date_from})")
        notify.send(
            f"🤔 今週末のレースデータがまだ届いてないみたい\n"
            f"   ({date_from} 以降の出走表が DB にない)\n"
            f"   TARGETからCSV取り込んで `data/csv/` に置いてね"
        )
        return

    print(f"[3/4] 未開催レース {len(rids)}件 → 各戦略でpicks生成...")
    # レース毎の (rows, race_name, venue, race_num, surface, distance, horse_names)
    race_info = {}
    for rid in rids:
        rows = conn.execute(
            "SELECT * FROM entries WHERE race_id=? AND number IS NOT NULL ORDER BY number",
            [rid]
        ).fetchall()
        if not rows: continue
        race_info[rid] = {
            "rows": rows,
            "race_name": rows[0]["race_name"] if "race_name" in rows[0].keys() else "",
            "venue": rows[0]["venue"],
            "race_num": rows[0]["race_num"] if "race_num" in rows[0].keys() else None,
            "surface": rows[0]["surface"],
            "distance": rows[0]["distance"],
            "horse_names": {r["number"]: (r["horse"] if "horse" in r.keys() else "") for r in rows},
        }

    # 戦略ごとに bets を作る
    all_strategies_bets = []
    for label, cls, combo in TOP:
        bets = []
        for rid in rids:
            info = race_info.get(rid)
            if not info: continue
            rows = info["rows"]
            if len(rows) < 8: continue
            if rows[0]["class"] != cls: continue
            picks = get_picks(rows, combo, personas)
            if not picks: continue
            by_num = {r["number"]: (r["finish"], r["place_payout"], r["popularity"], r["win_odds"]) for r in rows}
            # 60+倍除外フィルタ (大穴帯 EV低い → 除外で性能向上、検証済バックテストで Sortino 1.66→2.24)
            picks = [n for n in picks if not (by_num[n][3] is not None and by_num[n][3] >= 60)]
            if not picks: continue
            bets.append((rows[0]["date"], picks, by_num, rid))
        all_strategies_bets.append((label, cls, combo, bets))
        print(f"    {label}: {len(bets)} picks")

    # 重複なし(union)
    union_map = {}  # race_id -> {date, picks_set, by_num}
    for label, cls, combo, bets in all_strategies_bets:
        for d_, picks, by_num, rid in bets:
            if rid not in union_map:
                union_map[rid] = {"date": d_, "picks": set(), "by_num": by_num, "race_id": rid}
            union_map[rid]["picks"].update(picks)
    merged_bets = sorted([
        {"race_id": v["race_id"], "date": v["date"], "picks": sorted(v["picks"])}
        for v in union_map.values()
    ], key=lambda x: (x["date"], x["race_id"]))

    # 重複あり(各戦略独立)
    dup_bets = []
    for label, cls, combo, bets in all_strategies_bets:
        for d_, picks, by_num, rid in bets:
            dup_bets.append({"strategy": label, "race_id": rid, "date": d_, "picks": sorted(picks)})
    dup_bets.sort(key=lambda x: (x["date"], x["race_id"], x["strategy"]))

    # bet額計算
    # - cap × pct を picks数で等分(100円単位切捨)
    # - 100円未満になる(picks≥3)場合は **picks毎に最低¥100保証**
    # 例: cap=10000, pct=0.02 (=¥200)
    #   1点: ¥200, 2点: ¥100×2=¥200, 3点: ¥100×3=¥300, 4点: ¥100×4=¥400
    def calc_bet(cap, pct, n_picks):
        if n_picks <= 0:
            return 0, 0
        bet_total = cap * pct
        bet_per = int(bet_total / n_picks / 100) * 100
        if bet_per < 100:
            bet_per = 100  # 最低保証
        return bet_per, bet_per * n_picks

    merged_pf = state["portfolios"]["merged"]
    dup_pf = state["portfolios"]["dup"]

    def enrich(b):
        info = race_info.get(b["race_id"], {})
        return {
            **b,
            "race_name": info.get("race_name", ""),
            "venue": info.get("venue", ""),
            "race_num": info.get("race_num"),
            "surface": info.get("surface", ""),
            "distance": info.get("distance"),
            "horse_names": {n: info.get("horse_names", {}).get(n, "") for n in b["picks"]},
        }

    merged_with_bet = []
    for b in merged_bets:
        bp, total = calc_bet(merged_pf["current_cap"], merged_pf["pct"], len(b["picks"]))
        merged_with_bet.append({**enrich(b), "bet_per": bp, "total": total})
    dup_with_bet = []
    for b in dup_bets:
        bp, total = calc_bet(dup_pf["current_cap"], dup_pf["pct"], len(b["picks"]))
        dup_with_bet.append({**enrich(b), "bet_per": bp, "total": total})

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "date_from": date_from, "date_to": date_to,
        "merged": {
            "current_cap": merged_pf["current_cap"],
            "pct": merged_pf["pct"],
            "races": merged_with_bet,
            "total_wagered": sum(b["total"] for b in merged_with_bet),
        },
        "dup": {
            "current_cap": dup_pf["current_cap"],
            "pct": dup_pf["pct"],
            "races": dup_with_bet,
            "total_wagered": sum(b["total"] for b in dup_with_bet),
        },
    }

    today = date.today().isoformat()
    out_path = PICKS_DIR / f"{today}.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\n[4/4] 出力: {out_path}")
    print(f"  重複なし: {len(merged_with_bet)}R / 合計¥{out['merged']['total_wagered']:,}")
    print(f"  重複あり: {len(dup_with_bet)}R / 合計¥{out['dup']['total_wagered']:,}")

    # ===== 投票指示 =====
    race_summary = {}
    for r in merged_with_bet:
        rid = r["race_id"]
        race_summary.setdefault(rid, {"race": r, "merged": [], "dup": []})
        for h in r["picks"]:
            race_summary[rid]["merged"].append({
                "horse": h,
                "name": r["horse_names"].get(h, ""),
                "amount": r["bet_per"],
            })
    for r in dup_with_bet:
        rid = r["race_id"]
        race_summary.setdefault(rid, {"race": r, "merged": [], "dup": []})
        for h in r["picks"]:
            race_summary[rid]["dup"].append({
                "horse": h,
                "name": r["horse_names"].get(h, ""),
                "amount": r["bet_per"],
                "strategy": r.get("strategy", ""),
            })

    print("\n" + "=" * 50)
    print("📋 投票指示")
    print("=" * 50)
    if not race_summary:
        print("  本日の投票対象なし")
    else:
        for rid in sorted(race_summary.keys(), key=lambda k: (race_summary[k]["race"]["date"], k)):
            info = race_summary[rid]
            race = info["race"]
            md = race["date"][5:].replace("-", "/")  # YYYY-MM-DD → MM/DD
            print(f"\n🏇 {md} {race['venue']}{race['race_num']}R {race['race_name']}（{race['surface']}{race['distance']}m）")
            print(f"  馬券種: 複勝")

            # 馬リスト (merged優先、なければdup)
            horses_src = info["merged"] or info["dup"]
            seen = set()
            horses_str_list = []
            for b in horses_src:
                key = b["horse"]
                if key in seen: continue
                seen.add(key)
                horses_str_list.append(f"{b['horse']}番 {b['name']}")
            print(f"  馬: {', '.join(horses_str_list)}")

            print(f"  金額:")
            # merged 合算
            if info["merged"]:
                merged_total = sum(b["amount"] for b in info["merged"])
                if len(info["merged"]) > 1:
                    breakdown = " + ".join(f"{b['horse']}番¥{b['amount']:,}" for b in info["merged"])
                    print(f"    安全運用 (merged): ¥{merged_total:,}  ({breakdown})")
                else:
                    print(f"    安全運用 (merged): ¥{merged_total:,}")
            else:
                print(f"    安全運用 (merged): なし")

            # dup 合算
            if info["dup"]:
                dup_total = sum(b["amount"] for b in info["dup"])
                horses_in_dup = {b["horse"] for b in info["dup"]}
                n_strat = len(info["dup"])
                if len(horses_in_dup) == 1 and n_strat > 1:
                    note = f"  ({n_strat}戦略が同じ馬を推奨で合算)"
                elif len(horses_in_dup) > 1:
                    breakdown = " + ".join(f"{b['horse']}番¥{b['amount']:,}" for b in info["dup"])
                    note = f"  ({breakdown})"
                else:
                    note = ""
                print(f"    攻め運用 (dup):    ¥{dup_total:,}{note}")
            else:
                print(f"    攻め運用 (dup):    なし")

        # 合計
        total_merged = out["merged"]["total_wagered"]
        total_dup = out["dup"]["total_wagered"]
        print(f"\n  💰 全レース合計: 安全¥{total_merged:,} / 攻め¥{total_dup:,} (両方やる場合 ¥{total_merged + total_dup:,})")
    print("=" * 50)

    # state に pending を記録
    state["pending_picks"] = {
        "picks_file": str(out_path.relative_to(ROOT)),
        "merged_races": [b["race_id"] for b in merged_with_bet],
        "dup_entries": [{"race_id": b["race_id"], "strategy": b["strategy"]} for b in dup_with_bet],
        "date_from": date_from, "date_to": date_to,
    }
    save_state(state)
    notify.picks_summary(out, state)
    print("\n💡 次の手順:")
    print("  1. このpicksに従って即PAT で複勝を投票")
    print(f"  2. レース後、TARGETで結果CSV出力 → enrich_db.py で取込")
    print(f"  3. python live/runner.py settle で精算")


def cmd_settle(args):
    state = load_state()
    pending = state.get("pending_picks")
    if not pending:
        print("精算対象なし")
        notify.send("💤 今回は精算するレースはなさそう")
        return

    picks_file = ROOT / pending["picks_file"]
    if not picks_file.exists():
        print(f"picks_file 不存在: {picks_file}")
        notify.error(f"picks ファイルが見つからないよ: {picks_file}")
        return
    picks_data = json.loads(picks_file.read_text())

    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row

    def get_race_result(rid):
        rows = conn.execute(
            "SELECT number, finish, place_payout, win_payout FROM entries WHERE race_id=?",
            [rid]
        ).fetchall()
        if not rows or any(r["finish"] is None for r in rows):
            return None  # まだ結果未確定
        return {r["number"]: (r["finish"], r["place_payout"], r["win_payout"]) for r in rows}

    # 重複なし精算 (既に履歴にあるレースは二重精算しない)
    merged_pf = state["portfolios"]["merged"]
    settled_merged_ids = {h["race_id"] for h in merged_pf["history"]}
    settled_merged = []
    unsettled_merged = []
    for race in picks_data["merged"]["races"]:
        if race["race_id"] in settled_merged_ids:
            continue
        result = get_race_result(race["race_id"])
        if result is None:
            unsettled_merged.append(race)
            continue
        wagered = race["total"]
        returned = 0
        hits = []
        for h in race["picks"]:
            if h not in result: continue
            fin, pp, _ = result[h]
            if fin and fin <= 3 and pp:
                returned += pp * race["bet_per"] / 100
                hits.append({"horse": h, "finish": fin, "payout": pp})
        merged_pf["current_cap"] -= wagered
        merged_pf["current_cap"] += returned
        merged_pf["history"].append({
            "date": race["date"], "race_id": race["race_id"],
            "picks": race["picks"], "bet_per": race["bet_per"],
            "wagered": wagered, "returned": int(returned),
            "hit": returned > 0, "hits": hits,
            "cap_after": round(merged_pf["current_cap"]),
        })
        settled_merged.append(race["race_id"])

    # 重複あり精算 (race_id+strategy で重複チェック)
    dup_pf = state["portfolios"]["dup"]
    settled_dup_keys = {(h["race_id"], h.get("strategy")) for h in dup_pf["history"]}
    settled_dup = []
    unsettled_dup = []
    for race in picks_data["dup"]["races"]:
        key = (race["race_id"], race.get("strategy"))
        if key in settled_dup_keys:
            continue
        result = get_race_result(race["race_id"])
        if result is None:
            unsettled_dup.append(race)
            continue
        wagered = race["total"]
        returned = 0
        hits = []
        for h in race["picks"]:
            if h not in result: continue
            fin, pp, _ = result[h]
            if fin and fin <= 3 and pp:
                returned += pp * race["bet_per"] / 100
                hits.append({"horse": h, "finish": fin, "payout": pp})
        dup_pf["current_cap"] -= wagered
        dup_pf["current_cap"] += returned
        dup_pf["history"].append({
            "date": race["date"], "race_id": race["race_id"], "strategy": race["strategy"],
            "picks": race["picks"], "bet_per": race["bet_per"],
            "wagered": wagered, "returned": int(returned),
            "hit": returned > 0, "hits": hits,
            "cap_after": round(dup_pf["current_cap"]),
        })
        settled_dup.append({"race_id": race["race_id"], "strategy": race["strategy"]})

    # 結果ファイル保存
    today = date.today().isoformat()
    settle_path = RESULTS_DIR / f"{today}.json"
    settle_path.write_text(json.dumps({
        "settled_at": datetime.now().isoformat(timespec="seconds"),
        "merged_settled": len(settled_merged),
        "dup_settled": len(settled_dup),
        "merged_history_added": [h for h in merged_pf["history"][-len(settled_merged):]] if settled_merged else [],
        "dup_history_added": [h for h in dup_pf["history"][-len(settled_dup):]] if settled_dup else [],
    }, ensure_ascii=False, indent=2))

    # 部分精算対応: 未確定分が残っていれば pending を維持、なければクリア
    if unsettled_merged or unsettled_dup:
        print(f"📌 一部精算: 未確定 {len(unsettled_merged)}R(merged) / {len(unsettled_dup)}R(dup) — 残レース後に再実行してください")
        # pending は維持(picks_file はそのまま、settle 再実行で残り分処理)
        # 翌日 recalc コマンドで cap反映済みのbet額に更新可能
    else:
        print(f"✅ 全精算完了")
        state["last_processed_date"] = pending.get("date_to") or date.today().isoformat()
        state["pending_picks"] = []

    save_state(state)
    if settled_merged or settled_dup:
        notify.settle_summary(settled_merged, settled_dup, state)
    print(f"\n精算結果保存: {settle_path}")
    print(f"  重複なし: 精算 {len(settled_merged)}R / 残¥{merged_pf['current_cap']:,.0f} / 損益 {merged_pf['current_cap']-merged_pf['initial']:+,.0f}")
    print(f"  重複あり: 精算 {len(settled_dup)}R / 残¥{dup_pf['current_cap']:,.0f} / 損益 {dup_pf['current_cap']-dup_pf['initial']:+,.0f}")


def cmd_recalc(args):
    """pending_picks の bet額を現在の cap で再計算"""
    state = load_state()
    pending = state.get("pending_picks")
    if not pending or not pending.get("picks_file"):
        print("再計算対象なし")
        return
    picks_file = ROOT / pending["picks_file"]
    picks_data = json.loads(picks_file.read_text())

    def calc_bet(cap, pct, n_picks):
        if n_picks <= 0: return 0, 0
        bet_total = cap * pct
        bet_per = int(bet_total / n_picks / 100) * 100
        if bet_per < 100: bet_per = 100
        return bet_per, bet_per * n_picks

    merged_pf = state["portfolios"]["merged"]
    dup_pf = state["portfolios"]["dup"]

    # 既に履歴に入ったレースは除外、未確定だけ再計算
    settled_merged_ids = {h["race_id"] for h in merged_pf["history"]}
    settled_dup_keys = {(h["race_id"], h.get("strategy")) for h in dup_pf["history"]}

    changes = 0
    for race in picks_data["merged"]["races"]:
        if race["race_id"] in settled_merged_ids: continue
        old = race["bet_per"], race["total"]
        bp, total = calc_bet(merged_pf["current_cap"], merged_pf["pct"], len(race["picks"]))
        race["bet_per"] = bp; race["total"] = total
        if old != (bp, total): changes += 1
    picks_data["merged"]["current_cap"] = merged_pf["current_cap"]
    picks_data["merged"]["total_wagered"] = sum(b["total"] for b in picks_data["merged"]["races"]
                                                  if b["race_id"] not in settled_merged_ids)

    for race in picks_data["dup"]["races"]:
        if (race["race_id"], race.get("strategy")) in settled_dup_keys: continue
        old = race["bet_per"], race["total"]
        bp, total = calc_bet(dup_pf["current_cap"], dup_pf["pct"], len(race["picks"]))
        race["bet_per"] = bp; race["total"] = total
        if old != (bp, total): changes += 1
    picks_data["dup"]["current_cap"] = dup_pf["current_cap"]
    picks_data["dup"]["total_wagered"] = sum(b["total"] for b in picks_data["dup"]["races"]
                                                if (b["race_id"], b.get("strategy")) not in settled_dup_keys)

    picks_data["recalculated_at"] = datetime.now().isoformat(timespec="seconds")
    picks_file.write_text(json.dumps(picks_data, ensure_ascii=False, indent=2))
    print(f"再計算完了: {changes} レースのbet額更新 → {picks_file}")
    print(f"  重複なし cap=¥{merged_pf['current_cap']:,}")
    print(f"  重複あり cap=¥{dup_pf['current_cap']:,}")
    if changes > 0:
        notify.recalc_notice(changes, merged_pf["current_cap"], dup_pf["current_cap"])


def cmd_morning(args):
    """土日朝の確認用 — 最新picksをSlackに再送"""
    state = load_state()
    pending = state.get("pending_picks") or {}
    picks_file = pending.get("picks_file")
    today = date.today().isoformat()
    label = f"{today} picks (土朝確認)" if datetime.today().weekday() == 5 else f"{today} picks (日朝確認)"

    if not picks_file:
        notify.morning_summary(None, label=f"{label} — picks未生成。CSV取込&picks実行を確認してください")
        print("picks未生成")
        return
    p = ROOT / picks_file
    if not p.exists():
        notify.morning_summary(None, label=f"{label} — picks_file 不存在: {picks_file}")
        print(f"picks_file 不存在: {p}")
        return
    data = json.loads(p.read_text())

    # 該当日のレースだけにフィルタ(merged/dup から)
    def filter_by_date(races):
        return [r for r in races if r.get("date") == today]
    filtered = {
        **data,
        "merged": {**data["merged"], "races": filter_by_date(data["merged"]["races"])},
        "dup": {**data["dup"], "races": filter_by_date(data["dup"]["races"])},
        "date_from": today, "date_to": today,
    }
    filtered["merged"]["total_wagered"] = sum(r["total"] for r in filtered["merged"]["races"])
    filtered["dup"]["total_wagered"] = sum(r["total"] for r in filtered["dup"]["races"])

    if not filtered["merged"]["races"] and not filtered["dup"]["races"]:
        notify.morning_summary(None, label=f"{label} — 本日対象レースなし")
        print("本日picks 0件")
        return

    notify.morning_summary(filtered, label=label)
    print(f"morning通知送信: merged {len(filtered['merged']['races'])}R / dup {len(filtered['dup']['races'])}件")


def cmd_check_db(args):
    """DBの日付カバレッジを確認 → 欠損週末を Slack で通知"""
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row
    last = conn.execute("SELECT MAX(date) FROM entries").fetchone()[0]
    first = conn.execute("SELECT MIN(date) FROM entries").fetchone()[0]
    today = date.today()
    last_d = datetime.fromisoformat(last).date() if last else None
    print(f"DB: {first} 〜 {last}, 今日: {today.isoformat()}")
    if last_d:
        gap = (today - last_d).days
        print(f"最新DB日からの経過: {gap} 日")
        # 過去30日に欠損週末があるか確認
        weekend_dates = []
        d = today - timedelta(days=30)
        while d <= today:
            if d.weekday() in (5, 6):  # 土日
                weekend_dates.append(d.isoformat())
            d += timedelta(days=1)
        existing = {r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM entries WHERE date >= ?",
            [(today - timedelta(days=30)).isoformat()]
        ).fetchall()}
        missing = [d for d in weekend_dates if d not in existing and d <= today.isoformat()]
        if missing:
            dates_disp = ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")
            print(f"DB欠損: {dates_disp}")
            notify.send(
                f"📊 ちょっとデータが足りないかも\n"
                f"   {len(missing)}日分の週末データが未取込: {dates_disp}\n"
                f"   TARGETからCSV出して `data/csv/` に置いてね"
            )
        else:
            print(f"DB OK ({first} 〜 {last})")
            notify.send(f"✅ DB揃ってます ({first} 〜 {last})")


def cmd_status(args):
    state = load_state()
    print(f"=== 本番運用 状態 ({state['start_date']} 開始) ===\n")
    for key in ("merged", "dup"):
        p = state["portfolios"][key]
        n_h = len(p["history"])
        n_hit = sum(1 for h in p["history"] if h.get("hit"))
        cap = p["current_cap"]
        roi = cap / p["initial"] * 100
        profit = cap - p["initial"]
        print(f"--- {p['label']} ---")
        print(f"  資金: ¥{cap:,.0f} (初期 ¥{p['initial']:,} / 損益 {profit:+,.0f} / 倍率 {cap/p['initial']:.3f}x)")
        print(f"  賭け: {n_h}R / 命中 {n_hit}R ({n_hit/n_h*100 if n_h else 0:.1f}%)")
        print()
    pending = state.get("pending_picks")
    if pending:
        print(f"⏳ 精算待ち: {pending.get('picks_file')}")
    else:
        print("✅ pending なし")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("picks", help="未開催レースのpicks生成")
    sp.add_argument("--from", dest="date_from", help="開始日 YYYY-MM-DD (省略時は last_processed_date or start_date)")
    sp.add_argument("--to", dest="date_to", help="終了日 YYYY-MM-DD (省略時は2099-12-31)")
    ss = sub.add_parser("settle", help="結果反映")
    ss.add_argument("--date", help="精算日 YYYY-MM-DD (省略時=今日)")
    sub.add_parser("recalc", help="pending picks の bet額を現在capで再計算")
    sub.add_parser("morning", help="土日朝: 当日picksをSlackに再送")
    sub.add_parser("check-db", help="DB日付カバレッジ確認(欠損週末をSlack通知)")
    sub.add_parser("status", help="現状表示")
    args = p.parse_args()
    {"picks": cmd_picks, "settle": cmd_settle, "recalc": cmd_recalc,
     "morning": cmd_morning, "check-db": cmd_check_db, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()
