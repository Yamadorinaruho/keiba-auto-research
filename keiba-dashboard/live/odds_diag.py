#!/usr/bin/env python3
"""【診断・一時】GAランナーからnetkeibaオッズAJAXが取れるか確認。
当日全レースの単勝オッズAJAXを叩き、status/取得件数を集計して出力する。
使い方: python3 -m live.odds_diag [YYYYMMDD]
"""
import sys, os, json, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import get_race_ids_for_date, fetch, live_odds

def main():
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    rids = get_race_ids_for_date(date)
    print(f"[diag] {date} race_ids={len(rids)}")
    ok = empty = 0
    samples = []
    for rid in rids:
        url = f"https://race.netkeiba.com/api/api_get_jra_odds.html?race_id={rid}&type=1"
        try:
            raw = fetch(url, cache_key=None, force=True)
        except Exception as e:
            print(f"[diag] {rid} fetch例外 {type(e).__name__}: {e}")
            empty += 1
            continue
        try:
            j = json.loads(raw)
            status = j.get("status")
        except Exception:
            status = f"非JSON(len={len(raw)} head={raw[:80]!r})"
            j = {}
        _, omap = live_odds(rid)
        if omap:
            ok += 1
        else:
            empty += 1
        if len(samples) < 3:
            samples.append((rid, status, len(omap)))
    print(f"[diag] オッズ取得OK={ok} / 空={empty} / 全{len(rids)}")
    for rid, status, n in samples:
        print(f"[diag] sample {rid}: status={status} 馬番数={n}")

if __name__ == "__main__":
    main()
