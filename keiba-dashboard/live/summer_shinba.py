#!/usr/bin/env python3
"""【新馬第3戦略】夏・エピファネイア系・芝・2歳新馬の産駒を全頭買い(単勝)。
芝/ダートの本命型(summer_notify/summer_dirt)とは性質が異なる第3戦略。
新馬は前走データが無く本命を絞る材料がないため、母集団自体をエッジとして全頭買う。

母集団(検証): 夏(6-8月) × 芝 × 2歳新馬 × エピファネイア系産駒(エピファネイア/エフフォーリア)
  → WF: 単勝ROI175% / 複勝ROI114% / 勝率19.6% (n=255, ゼロ年なし)。
  エピ産駒は早熟・芝適性で初戦から動けるのが因果。人気種牡馬で唯一オッズに織り込まれ切らない。
  ※エフフォーリア(エピ産駒・社台SS)は2026年から初年度産駒がデビュー。エピ直仔は逓減するため両父を対象。
使い方: python3 -m live.summer_shinba [YYYYMMDD]
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from live.netkeiba_scraper import parse_shutuba, parse_horse
from live.summer_notify import BET_PER

# エピファネイア系後継種牡馬(現状の国内大黒柱はエフフォーリア。直仔エピも対象に残す)
SIRES = {"エピファネイア", "エフフォーリア"}


def horse_sire(horse_id):
    try:
        return parse_horse(horse_id).get("sire") or None
    except Exception:
        return None


def is_target_race(s):
    """芝の2歳新馬か(エピ系在籍は cands_for で別途判定)。"""
    return s["surface"] == "芝" and s["class"] == "新馬"


def cands_for(s, date_iso):
    """対象馬=エピ系産駒のみを抽出(各馬の父を引く)。新馬は前走特徴が無いので父のみ記録。"""
    out = []
    for h in s["horses"]:
        if not h.get("馬ID"):
            continue
        sire = horse_sire(h["馬ID"])
        if sire in SIRES:
            out.append({"umaban": h["馬番"], "horse": h["馬名"], "sire": sire})
    return out


def build_pick(race_id, feats, date_iso):
    """feats: 朝に計算したエピ系候補 [{umaban,horse,sire}]。無ければ直前に父を引いて判定。
    エピ系の芝新馬は母集団自体がエッジのため、オッズ不問で全頭買う。"""
    s = parse_shutuba(race_id)
    if s["surface"] != "芝" or s["class"] != "新馬":
        return None
    fmap = {f["umaban"]: f for f in (feats or [])}
    cands = []
    for h in s["horses"]:
        f = fmap.get(h["馬番"])
        if f is not None:
            sire = f["sire"]
        else:   # フォールバック: 朝に未計算なら直前に父を取得
            sire = horse_sire(h["馬ID"]) if h.get("馬ID") else None
            if sire not in SIRES:
                continue
        cands.append({"馬番": h["馬番"], "馬名": h["馬名"],
                      "人気": h.get("人気"), "odds": h.get("単勝オッズ"), "sire": sire})
    if not cands:
        return None
    cands.sort(key=lambda x: x["馬番"])
    return {"race_name": s["race_name"], "distance": s["distance"], "buys": cands}


def format_notify(venue, rno, post, lead_i, p):
    buys = p["buys"]
    head = (f"🌱 *[新馬·エピ系] {venue}{rno}R* {p['race_name']} (芝{p['distance']}m)\n"
            f"⏱ 発走 {post} → *発走{lead_i}分前*")
    lines = [head, "━━━━━━━━━━━━━━",
             f"🎯 *買い目: 単勝 各¥{BET_PER:,} (計¥{BET_PER*len(buys):,})*"]
    for c in buys:
        lines.append(f"  ▶ *{c['馬番']}番 {c['馬名']}* (父{c['sire']})")
    lines += ["━━━━━━━━━━━━━━",
              f"_オッズ・人気は発走{lead_i}分前時点（締切まで変動します）_",
              "_新馬は母集団自体が妙味(エピ系の芝新馬=単複プラス・早熟芝型)。オッズ不問で全頭買い_"]
    for c in buys:
        pop = f"{c['人気']}人気" if c['人気'] is not None else "人気?"
        od = f"{c['odds']}倍" if c['odds'] is not None else "オッズ?"
        lines.append(f"・{c['馬番']}番 *{c['馬名']}* {pop} *{od}* 父{c['sire']}")
    return "\n".join(lines)


def process_race(r, date_iso, lead_i):
    """巡回から呼ぶ。(通知文 or None, state保存用picks) を返す。"""
    p = build_pick(r["race_id"], r.get("cands"), date_iso)
    if not p:
        return None, []
    text = format_notify(r["venue"], r["rno"], r["post"], lead_i, p)
    picks = [{"umaban": c["馬番"], "horse": c["馬名"], "odds_pre": c["odds"], "sire": c["sire"]}
             for c in p["buys"]]
    return text, picks


if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().strftime("%Y%m%d")
    print(f"summer_shinba module loaded. date={date} (本番はworkflowから巡回)")
