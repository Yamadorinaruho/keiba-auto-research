# Cloudflare D1 移行 (keiba.db)

`keiba-dashboard/keiba.db` (SQLite, 1.1GB / 795,454行 / 56,267レース) を Cloudflare D1 に移行するためのスキーマ・ダンプ・インポートスクリプト一式。

## ディレクトリ構成

```
cloudflare-d1/
├── migrations/
│   ├── 0001_schema.sql      # CREATE TABLE + CREATE INDEX (324列 + 22インデックス)
│   └── 0002_views.sql       # CREATE VIEW races
├── scripts/
│   ├── dump_data.py         # entries -> data/chunk_XXXX.sql に分割
│   └── import_to_d1.sh      # 分割 SQL を wrangler で順次投入 (再開対応)
├── data/                    # ダンプ出力先 (gitignore 推奨)
└── README.md
```

## 移行手順

### 1. D1 DB 作成

```bash
wrangler d1 create keiba-db
# wrangler.toml に出力される database_id をメモ
```

### 2. スキーマ投入

```bash
cd cloudflare-d1/
wrangler d1 execute keiba-db --remote --file=migrations/0001_schema.sql
wrangler d1 execute keiba-db --remote --file=migrations/0002_views.sql
```

### 3. データダンプ生成

```bash
python3 scripts/dump_data.py \
  --db ../keiba-dashboard/keiba.db \
  --out data \
  --rows-per-stmt 50 \
  --max-bytes 90000
```

- `--rows-per-stmt`: 1ステートメントに何行入れるか。デフォルト50。
- `--max-bytes`: 1チャンクファイルの最大バイト数。D1 ステートメント上限 100KB に対して安全マージンを取って 90,000B (約88KB) に。
- 出力先 `data/chunk_0001.sql ... chunk_NNNN.sql`。
- 実測 (デフォルト設定): **21,902 チャンク** / 各ファイル 11KB〜85KB / 全て 90KB 以下に収まる。

### 4. インポート

```bash
./scripts/import_to_d1.sh
# 途中で失敗したらログを見て再開
./scripts/import_to_d1.sh --start-chunk 123
```

ローカル確認用 (D1 リモートを汚さない):

```bash
LOCAL=1 ./scripts/import_to_d1.sh
```

## 制約検証

### 容量: 1.1GB → D1 無料枠 5GB/DB → **OK**

`dbstat` 仮想テーブル実測:

| 内訳 | サイズ |
|---|---|
| `entries` テーブル本体 | 561.2 MB |
| インデックス 22個 合計 | 581.2 MB |
| `sqlite_stat1` ほか | < 1 MB |
| **合計** | **約 1.14 GB** |

主要インデックスの内訳:

| インデックス | サイズ |
|---|---|
| `idx_sire_exact` (sire+venue+surface+distance+condition+date+runners+finish) | 49.9 MB |
| `idx_bms_exact` (broodmare_sire+...) | 48.1 MB |
| `idx_horse_form` (horse+surface+distance+date+runners+finish) | 41.6 MB |
| `idx_rotation2` (race_name+prev_race+prev_finish+date+...) | 38.4 MB |
| `idx_jockey_dist` (jockey+venue+surface+distance+date+...) | 38.3 MB |
| ... (他 17 個) | 計 365 MB |

実データ 561MB に対しインデックスが 581MB と同等以上に膨らんでいるため、もし D1 容量がきつくなる場合は **使用頻度の低い複合インデックスから削る** のが第一手。
- `idx_runners` (8MB), `idx_year` (8.4MB), `idx_prev_dist` (8.3MB) はカーディナリティが低く、削除候補としても優先順位は高くない (元から小さい)
- 一方 `idx_sire_exact` / `idx_bms_exact` / `idx_horse_form` の8列複合インデックスは合計 140MB → 不要なら大幅削減可能

5GB 無料枠に対して 1.14GB なので **約 23% 使用** → 余裕あり。

### 書き込みレート: 79万行 → 無料枠で8日 → **有料 Workers Paid プラン推奨**

D1 の課金は **rows_written / rows_read** ベース:

| プラン | 1日あたり rows_written 無料枠 | 月額 |
|---|---|---|
| Free | 100,000 行/日 | $0 |
| Workers Paid | 25,000,000 行/日 (込み) + 超過は $1.00 / 100万行 | $5 |

計算:
- 79.5万行 ÷ 10万行/日 = **約 8日** (Free)
- 79.5万行 ÷ 2,500万行/日 = **当日中に完了** (Workers Paid)
- Workers Paid で 79.5万行は無料枠の 3% 未満 → 実質追加コスト $0

**推奨**: 一括投入時のみ Workers Paid ($5) に上げて流し込む。完了後はトラフィック次第で Free に戻すことも可能。

無料のまま回避するなら:
1. `import_to_d1.sh` を `SLEEP_BETWEEN=86 ./scripts/import_to_d1.sh` 等で **1日100K行ペースに律速** して 8日かけて流す
2. 各チャンクファイル ≒ 50行/stmt × 数stmt = 数百行なので、1日あたり 200チャンク前後で打ち切るバッチ運用
3. `--start-chunk` で翌日から再開

### ステートメント上限: 1 SQL 100KB

1行あたりの INSERT VALUES 部分の実測 (SQLite `.mode insert` で計測):

| metric | 値 |
|---|---|
| 1行あたり平均 INSERT 行 | 約 **1,900 B** |
| `INSERT INTO entries (324列) VALUES ` プレフィックス | 約 **4,500 B** (324列の引用識別子列挙) |
| 100KB / 1900B - プレフィックス分 | **約 50 行/stmt** |

→ `--rows-per-stmt 50` がほぼ理論上限近く、デフォルト値はこの計算に基づく。
- 余裕を持つなら 30〜40 に下げる
- 全列 NULL が多い軽い行なら 80〜100 まで詰められるが、列値が長い行に当たると `--max-stmt-bytes 95000` の安全弁で自動的にステートメント分割される

1チャンクファイル 90KB に対し、1ステートメント約 95KB を許容してしまうとファイルに乗らないので、`max-bytes` < `max-stmt-bytes` で運用する設計。

### インデックス: D1 でも作成可能 (注意点あり)

- **OK**: `CREATE INDEX`, `CREATE UNIQUE INDEX`, 複合インデックス, `IF NOT EXISTS`
- **NG**:
  - `PRAGMA` 全般 (journal_mode, foreign_keys 等は D1 側で固定)
  - `ATTACH DATABASE`
  - `CREATE TRIGGER` は v1 D1 で制限あり (現状サポートされていない/今回は元 DB にトリガなし)
  - `WITHOUT ROWID` は未確認、避けるのが無難
  - `CREATE VIRTUAL TABLE` (FTS5 含む) は限定的サポート、今回は不要

**運用上の注意**:
1. インデックスは **データ投入後にまとめて作る** 方がインポート高速 (ただし今回はスキーマ先行で作っているのでそのまま動く)
2. インデックス作成自体も rows_written にカウントされる場合があるので、有料プランでの実行が無難
3. `sqlite_stat1` (ANALYZE 結果) はマイグレーションから除外済 — 投入後に `ANALYZE` を実行してプランナを賢く

```bash
wrangler d1 execute keiba-db --remote --command="ANALYZE entries;"
```

## 文字エンコーディング

- 元 DB / CSV ヘッダに「Ｍ」「・」「３Ｆ」等の全角文字を含むが、SQLite は UTF-8 ストレージなので **D1 でもそのまま透過的に通る**
- `dump_data.py` は `open(..., encoding="utf-8")` で書き出し、wrangler も UTF-8 で読み込むため問題なし
- 列名にも全角文字 (例: `col_発走時刻`, `col_馬印`) が含まれるが、`"..."` 引用で識別子化済なので D1 で問題なく作成可能

## トラブルシューティング

- **`Error 1101: too many SQL variables`** → `--rows-per-stmt` を下げる
- **`Error: D1_ERROR: Statement too long`** → `--max-stmt-bytes 80000` 等に下げる
- **`rate limit exceeded`** → `SLEEP_BETWEEN=1 ./scripts/import_to_d1.sh` で律速
- **途中で落ちた** → ログ末尾の `Resume with: ... --start-chunk N` を実行
