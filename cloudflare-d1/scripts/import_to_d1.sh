#!/usr/bin/env bash
# Cloudflare D1 へ data/chunk_XXXX.sql を順次インポートする
#
# 前提:
#   - wrangler がインストール済 (npm i -g wrangler)
#   - wrangler login 済 / D1 DB 作成済
#   - 事前に migrations/0001_schema.sql, 0002_views.sql を投入済
#       wrangler d1 execute keiba-db --file=migrations/0001_schema.sql --remote
#       wrangler d1 execute keiba-db --file=migrations/0002_views.sql  --remote
#
# 使い方:
#   ./scripts/import_to_d1.sh                    # 最初から
#   ./scripts/import_to_d1.sh --start-chunk 123  # chunk_0123.sql から再開
#   DB_NAME=my-db ./scripts/import_to_d1.sh       # DB名を変更
#   LOCAL=1 ./scripts/import_to_d1.sh             # --remote ではなく --local

set -u
set -o pipefail

DB_NAME="${DB_NAME:-keiba-db}"
DATA_DIR="${DATA_DIR:-data}"
LOG_FILE="${LOG_FILE:-import.log}"
START_CHUNK=1
SLEEP_BETWEEN="${SLEEP_BETWEEN:-0}"   # rate limit 回避用に秒数を入れられる
REMOTE_FLAG="--remote"
if [[ "${LOCAL:-0}" == "1" ]]; then
  REMOTE_FLAG="--local"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start-chunk)
      START_CHUNK="$2"
      shift 2
      ;;
    --data-dir)
      DATA_DIR="$2"
      shift 2
      ;;
    --db-name)
      DB_NAME="$2"
      shift 2
      ;;
    --local)
      REMOTE_FLAG="--local"
      shift
      ;;
    --remote)
      REMOTE_FLAG="--remote"
      shift
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

cd "$(dirname "$0")/.."  # cloudflare-d1/ に移動

if [[ ! -d "$DATA_DIR" ]]; then
  echo "ERROR: $DATA_DIR not found. Run dump_data.py first." >&2
  exit 1
fi

mapfile -t CHUNKS < <(ls "$DATA_DIR"/chunk_*.sql 2>/dev/null | sort)
if [[ ${#CHUNKS[@]} -eq 0 ]]; then
  echo "ERROR: no chunk_*.sql in $DATA_DIR" >&2
  exit 1
fi

TOTAL=${#CHUNKS[@]}
echo "[$(date +%H:%M:%S)] DB=$DB_NAME FLAG=$REMOTE_FLAG total=$TOTAL start=$START_CHUNK" | tee -a "$LOG_FILE"

i=0
for f in "${CHUNKS[@]}"; do
  i=$((i+1))
  # chunk_0123.sql -> 123
  num=$(basename "$f" | sed -E 's/chunk_0*([0-9]+)\.sql/\1/')
  if [[ -z "$num" ]] || [[ "$num" -lt "$START_CHUNK" ]]; then
    continue
  fi

  echo "[$(date +%H:%M:%S)] ($i/$TOTAL) chunk=$num file=$f" | tee -a "$LOG_FILE"

  if ! wrangler d1 execute "$DB_NAME" "$REMOTE_FLAG" --file="$f" 2>&1 | tee -a "$LOG_FILE"; then
    echo "[$(date +%H:%M:%S)] FAILED at chunk $num. Resume with: $0 --start-chunk $num" | tee -a "$LOG_FILE"
    exit 1
  fi

  if [[ "$SLEEP_BETWEEN" != "0" ]]; then
    sleep "$SLEEP_BETWEEN"
  fi
done

echo "[$(date +%H:%M:%S)] all chunks imported OK ($TOTAL files)" | tee -a "$LOG_FILE"
