"""Slack Incoming Webhook 通知"""
import os
import json
import urllib.request
import urllib.error

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def send(text, blocks=None, silent=False):
    """Slack に投稿。WEBHOOK未設定なら何もしない"""
    if not WEBHOOK_URL:
        if not silent:
            print(f"[notify] SLACK_WEBHOOK_URL 未設定 (skip): {text[:80]}")
        return False
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    try:
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()
        return True
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[notify] Slack送信失敗: {e}")
        return False


def _format_race(r, with_amount=True):
    rn = r.get("race_num", "")
    course = f"{r.get('surface','')}{r.get('distance','')}m" if r.get("distance") else ""
    rname = r.get("race_name") or ""
    head = f"📍 *{r['date'][5:].replace('-','/')} {r.get('venue','')} {rn}R* {rname} ({course})"
    horses = []
    horse_names = r.get("horse_names") or {}
    for num in r["picks"]:
        name = horse_names.get(num) or horse_names.get(str(num)) or "?"
        amount_str = f"  ¥{r['bet_per']:,}" if with_amount else ""
        horses.append(f"   ▸ {num}番 *{name}*{amount_str}")
    if with_amount:
        n = len(r["picks"])
        if n == 1:
            horses.append(f"   この1点に ¥{r['total']:,}")
        else:
            horses.append(f"   {n}点まとめて ¥{r['total']:,} (1点 ¥{r['bet_per']:,})")
    return "\n".join([head] + horses)


def picks_summary(picks_data, state):
    m = picks_data["merged"]; d = picks_data["dup"]
    period = f"{picks_data['date_from'].replace('-','/')[5:]}〜{picks_data['date_to'].replace('-','/')[5:]}" if picks_data.get('date_to', '2099-12-31') < '2099' else f"{picks_data['date_from'].replace('-','/')[5:]}以降"
    lines = [
        f"🏇 *今週末のピックが揃いました* ({period})",
        "",
        f"━━━ *重複なし(2%)* ━━━",
        f"資金 ¥{m['current_cap']:,} → {len(m['races'])}レースに合計 *¥{m['total_wagered']:,}* 投資",
    ]
    for r in m["races"]:
        lines.append("")
        lines.append(_format_race(r))
    lines.append("")
    lines.append(f"━━━ *重複あり(2%)* ━━━")
    lines.append(f"資金 ¥{d['current_cap']:,} → {len(d['races'])}件で合計 *¥{d['total_wagered']:,}* 投資")
    for r in d["races"]:
        rn = r.get("race_num", "")
        rname = r.get("race_name") or ""
        horse_names = r.get("horse_names") or {}
        names = "/".join(f"{n}番{horse_names.get(n) or horse_names.get(str(n)) or ''}" for n in r["picks"])
        lines.append(f"   ▸ {r['date'][5:].replace('-','/')} {r.get('venue','')}{rn}R {rname} [{r.get('strategy','')}] {names} ¥{r['total']:,}")
    lines.append("")
    lines.append("即PATで複勝を投票してください 🎯")
    return send("\n".join(lines))


def morning_summary(picks_data, label="今日のpicks"):
    if not picks_data:
        return send(f"☀️ おはようございます\n{label}")
    m = picks_data["merged"]; d = picks_data["dup"]
    lines = [
        f"☀️ *{label}*",
        "",
        f"━━━ *重複なし* ━━━",
        f"{len(m['races'])}レース 合計 ¥{m['total_wagered']:,}",
    ]
    for r in m["races"]:
        lines.append("")
        lines.append(_format_race(r))
    lines.append("")
    lines.append(f"━━━ *重複あり* ━━━")
    lines.append(f"{len(d['races'])}件 合計 ¥{d['total_wagered']:,}")
    for r in d["races"]:
        rn = r.get("race_num", "")
        rname = r.get("race_name") or ""
        horse_names = r.get("horse_names") or {}
        names = "/".join(f"{n}番{horse_names.get(n) or horse_names.get(str(n)) or ''}" for n in r["picks"])
        lines.append(f"   ▸ {r['date'][5:].replace('-','/')} {r.get('venue','')}{rn}R {rname} [{r.get('strategy','')}] {names} ¥{r['total']:,}")
    lines.append("")
    lines.append("買い忘れに注意 🏇")
    return send("\n".join(lines))


def settle_summary(settled_merged, settled_dup, state):
    m = state["portfolios"]["merged"]; d = state["portfolios"]["dup"]
    m_recent = m["history"][-len(settled_merged):] if settled_merged else []
    d_recent = d["history"][-len(settled_dup):] if settled_dup else []
    m_won = sum(h["returned"] for h in m_recent); m_bet = sum(h["wagered"] for h in m_recent); m_hits = sum(1 for h in m_recent if h["hit"])
    d_won = sum(h["returned"] for h in d_recent); d_bet = sum(h["wagered"] for h in d_recent); d_hits = sum(1 for h in d_recent if h["hit"])

    m_diff = m_won - m_bet
    d_diff = d_won - d_bet
    m_emoji = "🎉" if m_diff > 0 else "💔" if m_diff < 0 else "😐"
    d_emoji = "🎉" if d_diff > 0 else "💔" if d_diff < 0 else "😐"

    m_total = m["current_cap"] - m["initial"]
    d_total = d["current_cap"] - d["initial"]

    lines = [
        f"🏁 *今週の結果が出ました*",
        "",
        f"━━━ {m_emoji} *重複なし* ━━━",
        f"   {len(settled_merged)}レース勝負 → {m_hits}的中",
        f"   ¥{m_bet:,} 賭けて ¥{m_won:,} 戻り → *{m_diff:+,}円*",
        f"   今の資金 ¥{m['current_cap']:,} (累計 *{m_total:+,}円*)",
        "",
        f"━━━ {d_emoji} *重複あり* ━━━",
        f"   {len(settled_dup)}件勝負 → {d_hits}的中",
        f"   ¥{d_bet:,} 賭けて ¥{d_won:,} 戻り → *{d_diff:+,}円*",
        f"   今の資金 ¥{d['current_cap']:,} (累計 *{d_total:+,}円*)",
    ]
    return send("\n".join(lines))


def recalc_notice(changes, merged_cap, dup_cap):
    return send(
        f"🔄 来週の賭け金額を更新しました ({changes}件)\n"
        f"   重複なし: ¥{merged_cap:,}  /  重複あり: ¥{dup_cap:,}"
    )


def error(msg):
    return send(f"💥 *エラー*\n```{msg}```")
