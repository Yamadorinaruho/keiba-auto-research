#!/bin/zsh
# 夏3戦略 自動投票ランナー(Mac/launchdから3分毎に起動)
# 役割: 週末の発売時間中に picks を計算し、発走15分前以内の単勝買い目を即PATへ自動投票する。
#   GAが頭脳(Slack通知)、本スクリプトは手(投票)。Slackは送らない(SLACK_WEBHOOK_URL="")。
# 安全: 既定は ¥100/点(AUTOVOTE_FORCE_AMOUNT=100)。本来の0.5%額にするには下のFORCE行を消す。
#   二重投票ガード(state/bet_log_<date>.json)で同一馬の再投票はしない。
set -e
DIR=/Users/yamadori/keiba-auto-research/keiba-dashboard
cd "$DIR"
PY=/usr/bin/python3

DATE=$(TZ=Asia/Tokyo date +%Y%m%d)
DOW=$(TZ=Asia/Tokyo date +%u)     # 1=月..6=土,7=日
H=$(TZ=Asia/Tokyo date +%H)
# 稼働窓: 土(6)・日(7) の 9〜16時台のみ
[ "$DOW" -lt 6 ] && exit 0
[ "$H" -lt 9 ] && exit 0
[ "$H" -gt 16 ] && exit 0

# 二重起動防止(前のtickが走行中ならスキップ)
LOCK=/tmp/keiba_autovote.lock
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then exit 0; fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

caffeinate -i -t 200 &            # このtick中スリープ抑止(~3分)

# picks計算系はSlack抑止(picks通知はGAが送る)。SLACK_WEBHOOK_URL="" を各呼び出しに付与。
[ -f "state/summer_sched_${DATE}.json" ] || SLACK_WEBHOOK_URL="" "$PY" -m live.summer_schedule "$DATE" || true
SLACK_WEBHOOK_URL="" "$PY" -m live.summer_notify "$DATE" || true
# 投票はSlack有効(.envのSLACK_WEBHOOK_URLを使用)→「買えたら通知」が飛ぶ。金額は戦略別(STRAT_AMOUNT,現在全¥1000)。
DRY_RUN=0 CONFIRM_PURCHASE=1 "$PY" -m live.auto_vote "$DATE" --live || true
echo "[runner] $(TZ=Asia/Tokyo date '+%F %H:%M:%S') done"
