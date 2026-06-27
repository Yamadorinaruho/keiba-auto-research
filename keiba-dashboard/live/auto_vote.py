"""【自動投票・プラン層】夏3戦略(芝/ダ/新馬エピ系)のpicksを集約し、単勝の投票プランを作る。

このモジュールは「何をいくら買うか」を決めるだけ(=純ロジック、テスト可能)。
実際の即PAT投票は ipat_vote.py が担当し、本モジュールが渡すプランを実行する。

安全装置:
  - 二重投票ガード: state/bet_log_<date>.json に投票済み(race_id,馬番)を記録し再投票しない。
  - DRY_RUN(既定): プランを表示するだけで投票しない。--live で初めて ipat_vote を呼ぶ。
  - 初回テスト: env AUTOVOTE_FORCE_AMOUNT(例100)で全点を最小額に、AUTOVOTE_MAX_RACES でレース数制限。
"""
import os, sys, json, datetime
from live import bankroll

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state")
JST = datetime.timezone(datetime.timedelta(hours=9))

# 投票する発走前ウィンドウ(分)。発走 BET_LEAD_MAX〜BET_LEAD_MIN 分前のレースを対象にする。
# 発走3分前狙い: オッズがほぼ確定してから買う(妙味帯フィルタを最終オッズに近い値で判定)。
# 2〜5分前の窓=3分毎巡回で確実に1回ヒットし、自動操作(約1分)も締切前に完了する。
BET_LEAD_MAX = 5.0    # 5分前から試行(これより早い=オッズ未確定なので待つ)
BET_LEAD_MIN = 2.0    # 締切ガード: 2分前を切ったら投票しない(間に合わないリスク回避)

# 1点額(円)は bankroll.daily_unit(=残高0.5%/100円単位)を使い、通知・収支・bankrollと一致させる。
# AUTOVOTE_FORCE_AMOUNT を設定すると全点をその額に上書き(初回テスト/緊急用)。


def _now():
    return datetime.datetime.now(JST)


def sched_path(date):
    return os.path.join(STATE_DIR, f"summer_sched_{date}.json")


def betlog_path(date):
    return os.path.join(STATE_DIR, f"bet_log_{date}.json")


def load_betlog(date):
    p = betlog_path(date)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return {}
    return {}


def record_bet(date, race_id, umaban, amount, horse=""):
    """投票確定後に呼ぶ。二重投票ガードの記録。"""
    p = betlog_path(date)
    log = load_betlog(date)
    rec = log.setdefault(race_id, [])
    if any(x["umaban"] == umaban for x in rec):
        return
    rec.append({"umaban": umaban, "horse": horse, "amount": amount,
                "ts": _now().strftime("%H:%M:%S")})
    json.dump(log, open(p, "w"), ensure_ascii=False, indent=1)


def _lead_min(post, now):
    hh, mm = map(int, post.split(":"))
    pdt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (pdt - now).total_seconds() / 60.0


def plan_bets(date, now=None, all_races=False):
    """投票プランを返す: [{race_id,venue,rno,post,strat,umaban,horse,amount,score,lead}]。
    all_races=True なら発走ウィンドウ無視で picks のある全レースを対象(テスト/手動用)。
    二重投票ガード適用済み・各点 amount=当日の1点額(=残高0.5%/100円単位)。
    """
    date_iso = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    now = now or _now()
    if not os.path.exists(sched_path(date)):
        return []
    sched = json.load(open(sched_path(date)))
    betlog = load_betlog(date)
    force = os.environ.get("AUTOVOTE_FORCE_AMOUNT")   # 設定時は全点をその額に上書き
    max_races = os.environ.get("AUTOVOTE_MAX_RACES")
    unit = int(force) if force else bankroll.daily_unit(date_iso)   # 当日の1点額(通知・収支と同一)
    plan, used_races = [], set()
    for r in sched.get("races", []):
        picks = r.get("picks") or []
        if not picks:
            continue
        lead = _lead_min(r["post"], now)
        if not all_races and not (BET_LEAD_MIN <= lead <= BET_LEAD_MAX):
            continue
        strat = r.get("strat")
        amount = unit   # 全戦略・全点 当日の1点額で統一(残高0.5%)
        already = {x["umaban"] for x in betlog.get(r["race_id"], [])}
        for pk in picks:
            if pk["umaban"] in already:
                continue   # 二重投票ガード
            plan.append({"race_id": r["race_id"], "venue": r["venue"], "rno": r["rno"],
                         "post": r["post"], "strat": strat, "umaban": pk["umaban"],
                         "horse": pk.get("horse", ""), "amount": amount,
                         "info": pk.get("lin") or pk.get("score"), "lead": round(lead, 1)})
            used_races.add(r["race_id"])
        if max_races and len(used_races) >= int(max_races):
            break
    return plan


def format_plan(plan):
    if not plan:
        return "投票プランなし(対象レース/picksなし or 全て投票済み)"
    lines, total = ["━━━ 自動投票プラン(単勝) ━━━"], 0
    cur = None
    for b in plan:
        if b["race_id"] != cur:
            cur = b["race_id"]
            lines.append(f"\n{b['post']} {b['venue']}{b['rno']}R [{b['strat']}] (発走{b['lead']}分前)")
        total += b["amount"]
        lines.append(f"  単勝 {b['umaban']}番 {b['horse']} ¥{b['amount']:,} ({b.get('info') or '-'})")
    lines.append(f"\n合計 {len(plan)}点 / ¥{total:,}")
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    date = next((a for a in args if a.isdigit() and len(a) == 8), _now().strftime("%Y%m%d"))
    all_races = "--all" in args
    live = "--live" in args
    plan = plan_bets(date, all_races=all_races)
    print(format_plan(plan))
    if not live:
        print("\n[DRY_RUN] 投票はしていません。実投票は --live を付けてください。")
        return
    if not plan:
        return
    from live import ipat_vote
    ipat_vote.place_bets(plan, date)


if __name__ == "__main__":
    main()
