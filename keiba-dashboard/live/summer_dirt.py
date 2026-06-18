#!/usr/bin/env python3
"""【ダート第2戦略】夏・牝・ダート短距離(≤1400m)の本命をSlack通知。
芝の本命戦略(summer_notify)と独立した第2戦略。構造は芝と裏返し:
  芝 = 差し×ディープ系×負け巻き返し / ダート = 前付け×米国系×好走再現。

母集団(decision 172): 夏 × 牝(全年齢) × ダート≤1400m × 未勝利〜OP × 全会場
  × 人気4-12 × 単勝10-50倍 × キャリア3戦目以上(過去出走2戦以上)
スコア(上限4・各+1): 前走前付け(rel≤0.33=逃げ・先行) + 米国系 + 馬体重450-490 + 前走9着以内
  → score≥3 の該当馬を全部・単勝1000円。
WF(2014-2025): ROI148% / 79頭/年 / 最悪年35% / +年9/12。
※多軸スライスの末の数字で芝(素直な母集団152%)よりフォワード目減りリスク高い第2候補。

使い方: python3 -m live.summer_dirt [YYYYMMDD]
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba
from live.summer_notify import prev_run, get_weight, BET_PER, MIN_SCORE
from live.sire_lineage_map import LINEAGE
from live import notify

US = {"米国系"}   # ダートの妙味血統(芝のディープ/サンデーとは逆)
# ダート短距離の対象クラス(未勝利〜OP)。旧クラス表記も含める。
CLS_DIRT = {"未勝利", "1勝", "500万", "2勝", "1000万", "3勝", "1600万", "ｵｰﾌﾟﾝ", "OP(L)"}
MAX_DIST = 1400


def front(rel):   # 前付け(逃げ・先行) ≒ 前走4角が前1/3 (rel<=0.33)
    return rel is not None and rel <= 0.33


def dirt_axes(c):
    rel = c["rel"]
    return [("前付け(逃げ先行)", front(rel), f"4角{rel:.0%}" if rel is not None else "前走不明"),
            ("米国系", c.get("lin") in US, c.get("lin") or "血統不明"),
            ("馬体重450-490", c["体重"] is not None and 450 <= c["体重"] <= 490, f"{c['体重']}kg" if c["体重"] is not None else "体重不明"),
            ("前走9着以内", c["前着"] is not None and c["前着"] <= 9, f"前走{c['前着']}着" if c["前着"] is not None else "前走不明")]


def dirt_reason(c):
    lbl = {"前付け(逃げ先行)": "前で運べる(砂向き)", "米国系": "米国系(ダート速力)",
           "馬体重450-490": "好適馬体重", "前走9着以内": "前走で力を出せている"}
    ok = [lbl[n] for n, hit, _ in dirt_axes(c) if hit]
    return " / ".join(ok) if ok else "母集団該当のみ"


def build_dirt_pick(race_id, feats, date_iso):
    """feats: 朝(summer_dirt_schedule等)が計算した不変特徴 {馬番:{rel,fin,lin,n_prev}}。無ければ直前取得。"""
    s = parse_shutuba(race_id)
    if s["surface"] != "ダ" or s["class"] not in CLS_DIRT or s["distance"] > MAX_DIST:
        return None
    wmap = get_weight(race_id)
    fmap = {f["umaban"]: f for f in (feats or [])}
    cands = []
    for h in s["horses"]:
        if not h.get("性齢", "").startswith("牝"):   # 牝のみ(年齢不問)
            continue
        pop, odds = h.get("人気"), h.get("単勝オッズ")
        if pop is None or odds is None or not (4 <= pop <= 12) or not (10 <= odds < 50):
            continue
        wt = wmap.get(h["馬番"])
        f = fmap.get(h["馬番"])
        if f is not None:
            rel, fin, lin, n_prev = f["rel"], f["fin"], f["lin"], f["n_prev"]
        else:
            rel, fin, sire, n_prev = prev_run(h["馬ID"], date_iso) if h.get("馬ID") else (None, None, None, 0)
            lin = LINEAGE.get(sire) if sire else None
        if n_prev < 2:   # キャリア3戦目以上
            continue
        sc = (int(front(rel)) + int(lin in US)
              + int(wt is not None and 450 <= wt <= 490) + int(fin is not None and fin <= 9))
        cands.append({"馬番": h["馬番"], "馬名": h["馬名"], "人気": pop, "odds": odds,
                      "rel": rel, "前着": fin, "体重": wt, "lin": lin, "score": sc})
    if not cands:
        return None
    cands.sort(key=lambda x: (-x["score"], -x["odds"]))
    return {"race_name": s["race_name"], "distance": s["distance"],
            "honmei": cands[0], "others": cands[1:]}


def format_notify(venue, rno, post, lead_i, p):
    """発走15分前以内のダート買い目通知文(本番フォーマット)。"""
    allc = [p["honmei"]] + p["others"]
    buys = [c for c in allc if c["score"] >= MIN_SCORE]
    if not buys:
        return None
    head = (f"🏜 *[ダート] {venue}{rno}R* {p['race_name']} (ダ{p['distance']}m)\n"
            f"⏱ 発走 {post} → *発走{lead_i}分前*")
    lines = [head, "━━━━━━━━━━━━━━",
             f"🎯 *買い目: 単勝 各¥{BET_PER:,} (計¥{BET_PER*len(buys):,})*"]
    for c in buys:
        lines.append(f"  ▶ *{c['馬番']}番 {c['馬名']}*")
    lines += ["━━━━━━━━━━━━━━",
              f"_オッズ・人気は発走{lead_i}分前時点（締切まで変動します）_", "▼内訳"]
    for c in buys:
        ax = " ".join(f"{'✅' if hit else '⬜'}{n}({v})" for n, hit, v in dirt_axes(c))
        lines.append(f"・{c['馬番']}番 *{c['馬名']}* {c['人気']}人気 *{c['odds']}倍* (score {c['score']}/4)")
        lines.append(f"   {ax} → {dirt_reason(c)}")
    return "\n".join(lines)


def is_target_race(s):
    """出馬表sがダート第2戦略の対象レースか(牝が1頭でもいる ダ≤1400 未勝利〜OP)。"""
    if s["surface"] != "ダ" or s["class"] not in CLS_DIRT or s["distance"] > MAX_DIST:
        return False
    return any(h.get("性齢", "").startswith("牝") for h in s["horses"])


def target_horse(h):
    """ダート戦略の事前計算対象馬(牝・年齢不問)。"""
    return h.get("性齢", "").startswith("牝")


def process_race(r, date_iso, lead_i):
    """巡回から呼ぶ。(通知文 or None, state保存用picks) を返す。"""
    p = build_dirt_pick(r["race_id"], r.get("cands"), date_iso)
    if not p:
        return None, []
    allc = [p["honmei"]] + p["others"]
    buys = [c for c in allc if c["score"] >= MIN_SCORE]
    text = format_notify(r["venue"], r["rno"], r["post"], lead_i, p) if buys else None
    picks = [{"umaban": c["馬番"], "horse": c["馬名"], "odds_pre": c["odds"], "score": c["score"]} for c in buys]
    return text, picks


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    print(f"summer_dirt module loaded. date={date} (本番はworkflowから巡回)")
