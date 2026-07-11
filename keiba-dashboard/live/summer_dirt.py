#!/usr/bin/env python3
"""【ダート第2戦略】夏・牝・ダート短距離(≤1400m)の本命をSlack通知。
芝の本命戦略(summer_notify)と独立した第2戦略。構造は芝と裏返し:
  芝 = 差し×ディープ系×負け巻き返し / ダート = 前付け×米国系×好走再現。

母集団(v2 decision 182ベース): 夏 × 3歳牝 × ダート≤1400m × 未勝利〜OP × 全会場
  × 帯内オッズ × 3走目以上 × 父=米国系 を全頭買い(条件の実数値は live/strategy_spec.py 参照)。
  4歳以上は赤字(ROI96%)のため除外し3歳牝に。3歳牝化に伴い帯上限を50→80倍へ拡大。
  in-sample(2010-25): 55点/年 ROI144% 全+2,432円/年 +9/16。

使い方: python3 -m live.summer_dirt [YYYYMMDD]
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba, live_odds, parse_horse
from live.summer_notify import prev_run, get_weight, BET_PER, MIN_SCORE
from live import strategy_spec as spec
from live.sire_lineage_map import LINEAGE, lineage_of, lineage_of_line
from live import notify

US = spec.DIRT_BLOOD      # ダートの妙味血統(芝のディープ/サンデーとは逆)
CLS_DIRT = spec.DIRT_CLS  # ダート短距離の対象クラス(未勝利〜OP、旧クラス表記含む)
MAX_DIST = spec.DIRT_MAX_DIST


def front(rel):   # 前付け(逃げ・先行) ≒ 前走4角が前1/3 (rel<=0.33)
    return rel is not None and rel <= 0.33


def build_dirt_pick(race_id, feats, date_iso):
    """v2(血統フィルタ): 3歳牝×ダ≤1400×未勝利〜OP×帯内×3走目以上×父米国系を全頭買い(spec参照)。
    score機構は撤廃(2026-06 decision 182)。feats: 朝に計算した不変特徴 {馬番:{lin,n_prev,...}}。"""
    s = parse_shutuba(race_id)
    if s["surface"] != "ダ" or s["class"] not in CLS_DIRT or s["distance"] > MAX_DIST:
        return None
    _, omap = live_odds(race_id)   # 最新の単勝オッズ・人気(AJAX=リロード相当)
    fmap = {f["umaban"]: f for f in (feats or [])}
    buys = []
    for h in s["horses"]:
        sa = h.get("性齢", "")
        if not (sa.startswith("牝") and sa.endswith("3")):   # 3歳牝(4歳以上は赤字のため除外 decision 182)
            continue
        lo = omap.get(h["馬番"])
        pop = lo["pop"] if lo else h.get("人気")
        odds = lo["odds"] if lo else h.get("単勝オッズ")
        if odds is None or not (spec.DIRT_BAND[0] <= odds < spec.DIRT_BAND[1]):   # 人気は不問
            continue
        f = fmap.get(h["馬番"])
        if f is not None:
            lin, n_prev = f["lin"], f["n_prev"]
        else:
            _, _, sire, n_prev, _ = prev_run(h["馬ID"], date_iso) if h.get("馬ID") else (None, None, None, 0, None)
            lin = lineage_of(sire)
            if lin is None and sire and h.get("馬ID"):   # 男系ライン自動判定(2026-07-11, scheduleと同ロジック)
                try:
                    lin, _ = lineage_of_line(sire, parse_horse(h["馬ID"]).get("sire_line") or [])
                except Exception:
                    pass
        if n_prev < spec.MIN_CAREER:   # 3走目以上
            continue
        if lin not in US:   # 米国系のみ
            continue
        buys.append({"馬番": h["馬番"], "馬名": h["馬名"], "人気": pop, "odds": odds, "lin": lin})
    if not buys:
        return None
    buys.sort(key=lambda x: -x["odds"])
    return {"race_name": s["race_name"], "distance": s["distance"], "buys": buys}


def format_notify(venue, rno, post, lead_i, p, bet=BET_PER):
    """発走15分前以内のダート買い目通知文(v2血統フィルタ)。"""
    buys = p["buys"]
    head = (f"🏜 *[ダート] {venue}{rno}R* {p['race_name']} (ダ{p['distance']}m)\n"
            f"⏱ 発走 {post} → *発走{lead_i}分前*")
    lines = [head, "━━━━━━━━━━━━━━",
             f"🎯 *買い目: 単勝 各¥{bet:,} (計¥{bet*len(buys):,})*"]
    for c in buys:
        lines.append(f"  ▶ *{c['馬番']}番 {c['馬名']}* ({c['人気']}人気 {c['odds']}倍 / 父系{c['lin']})")
    lines += ["━━━━━━━━━━━━━━",
              f"_米国系×単勝{spec.band_str(spec.DIRT_BAND)}×3走目以上を全頭。オッズは発走{lead_i}分前時点（変動）_"]
    return "\n".join(lines)


def is_target_race(s):
    """出馬表sがダート第2戦略の対象レースか(3歳牝が1頭でもいる ダ≤1400 未勝利〜OP)。"""
    if s["surface"] != "ダ" or s["class"] not in CLS_DIRT or s["distance"] > MAX_DIST:
        return False
    return any(target_horse(h) for h in s["horses"])


def target_horse(h):
    """ダート戦略の事前計算対象馬(3歳牝)。"""
    sa = h.get("性齢", "")
    return sa.startswith("牝") and sa.endswith("3")


def process_race(r, date_iso, lead_i, bet=BET_PER):
    """巡回から呼ぶ。(通知文 or None, state保存用picks) を返す。"""
    p = build_dirt_pick(r["race_id"], r.get("cands"), date_iso)
    if not p:
        return None, []
    buys = p["buys"]
    text = format_notify(r["venue"], r["rno"], r["post"], lead_i, p, bet)
    picks = [{"umaban": c["馬番"], "horse": c["馬名"], "odds_pre": c["odds"], "lin": c["lin"]} for c in buys]
    return text, picks


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    print(f"summer_dirt module loaded. date={date} (本番はworkflowから巡回)")
