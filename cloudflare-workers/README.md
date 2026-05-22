# keiba-api (Cloudflare Workers + Hono.js)

`keiba-dashboard/api.py` (FastAPI + SQLite) を Cloudflare Workers + Hono.js + D1 へ移植する作業ディレクトリ。

このフェーズではルーティング雛形と型定義だけを置いている。実装本体は次フェーズで埋める。

## 構成

```
cloudflare-workers/
├── package.json       # hono / wrangler / @cloudflare/workers-types / typescript
├── wrangler.toml      # name=keiba-api, D1 binding=KEIBA_DB
├── tsconfig.json      # strict, target ES2022
├── .gitignore         # node_modules, dist, .wrangler
├── src/
│   ├── index.ts       # Hono ルーター (全 19 エンドポイントを TODO 付きで定義)
│   └── types.ts       # Bindings + 主要レスポンス型 stub
└── README.md          # このファイル
```

セットアップ:

```
cd cloudflare-workers
npm install
wrangler d1 create keiba                # database_id を wrangler.toml に反映
npm run dev                              # http://127.0.0.1:8787 で起動
```

D1 への移行は `keiba.db` (約 1.2 GB SQLite) を縮小 / 分割するなり、最初は read-only な
`d1 execute --file=schema.sql` でスキーマだけ作って ETL する想定。

---

## エンドポイント一覧 (19 endpoints / 21 paths)

優先度の基準:

- **A**: keiba-dashboard のメインタブで叩かれる。これが無いと UI 主要機能が落ちる。
- **B**: サブタブ / 補助 / 詳細ビュー。あると便利。
- **C**: デバッグ / 将来計画 / 一度しか呼ばれない静的データ。

| # | Method | Path | FastAPI 関数 | 必要な D1 クエリ概要 | 優先度 |
|---|--------|------|--------------|----------------------|--------|
| 1 | GET | `/api/meta` | `get_meta` | 9 個の `SELECT DISTINCT col` + `COUNT(*)`。filter UI の選択肢供給用。結果は module-scope cache。 | **A** |
| 2 | GET | `/api/summary` | `get_summary` | filter (venue/surface/class/condition/year/gender) で WHERE。`COUNT/AVG` の集計 1 本。 | **A** |
| 3 | GET | `/api/roi-heatmap` (= `/api/accuracy-heatmap`) | `get_accuracy_heatmap` | `GROUP BY row_axis, col_axis`。distance_band は `CASE` 式。row/col/grand totals は JS で再集計。 | **A** |
| 4 | GET | `/api/rotation` | `get_rotation` | race_name 完全一致で 4 サブクエリ (summary / top_prev / prev_races / prev_finish_detail)。 | **A** |
| 5 | GET | `/api/rotation-condition` | `get_rotation_condition` | `/api/rotation` と同形だが race conditions (venue/surface/distance/class) で絞る。 | **B** |
| 6 | GET | `/api/profit-scan` (= `/api/strength-scan`) | `get_strength_scan` | 11 ブロックの UNION ALL (場所/馬場/距離/状態/人気/コンビ/騎手/種牡馬)。95% CI 下限を JS で計算。 | **A** |
| 7 | GET | `/api/upset-ranking` | `get_upset_ranking` | 3 グルーピング (venue / surface×distance_band / class)。upset_score = `(1-fav_win)*avg_payout/1000`。 | **B** |
| 8 | GET | `/api/search` | `search_entries` | `WHERE {col} LIKE '%q%'` で stats + 最新 50 件。col は `horse|jockey|sire|trainer`。 | **A** |
| 9 | GET | `/api/entries` | `list_entries` | pagination + sort whitelist + 5 列 LIKE OR 検索。`COUNT(*)` + `LIMIT/OFFSET`。 | **A** |
| 10 | GET | `/api/condition-strength` | `get_condition_strength` | jockey/sire/trainer/venue/surface/distance の任意組合せ。stats + by_class breakdown。 | **B** |
| 11 | GET | `/api/horse-form` | `get_horse_form` | horse 完全一致で stats / 最新 20 件 / surface×distance_band 内訳。 | **A** |
| 12 | GET | `/api/race-entries` | `get_race_entries` | race_name LIKE + year で出走馬一覧。 | **A** |
| 13 | POST | `/api/race-analysis` | `race_analysis` | リクエスト body で渡された各馬について 4 種の LIKE 集計 → composite_score 計算 → ソート。 | **A** |
| 14 | GET | `/api/backtest-g1` | `backtest_g1` | 全 G1 レースを walk-forward。`date_cutoff` 付きで 4 サブクエリ × N 頭 × M レース。**重い**。 | **B** |
| 15 | GET | `/api/strategies` | `get_strategies` | `strategy_results.json` (約 485 KB) を返すだけ。 | **B** |
| 16 | GET | `/api/wealth-curve` | `get_wealth_curve` | `wealth_data.json` (約 4 MB) を返すだけ。 | **B** |
| 17 | GET | `/api/edge-validation` | `get_edge_validation` | `edge_data.json` (約 110 KB) を返すだけ。 | **B** |
| 18 | GET | `/api/analytics` | `get_analytics` | `analytics_data.json` (約 7 KB) を返すだけ。 | **B** |
| 19 | GET | `/api/live-state` | `get_live_state` | `state/portfolio.json` + `state/picks/*.json` の最新を返す。日次更新。 | **A** |

合計エンドポイント関数: 19 / 別名込みのパス数: 21 (`/api/roi-heatmap`↔`/api/accuracy-heatmap`, `/api/profit-scan`↔`/api/strength-scan`)。

---

## 移行上の TODO / 注意点 (SQL 等価変換が困難な箇所)

### 1. `keiba.db` の D1 移行 (前提作業)

- `keiba.db` は 1.2 GB の SQLite。D1 の上限は 1 DB あたり 10 GB / 1 行あたり 1 MB 以内なのでサイズは収まるが、`d1 execute --file=` 経由の bulk insert はかなり遅い。**ETL は別途バッチで** (Python から CSV エクスポート → `wrangler d1 execute --file=insert.sql` か R2 経由)。
- インデックス: `entries(race_name)`, `entries(horse)`, `entries(jockey)`, `entries(sire)`, `entries(trainer)`, `entries(date)`, `entries(class)`, `entries(year)` あたりは必須。Python 版は PRAGMA `cache_size=-64000` で逃げているが Workers では効かないのでインデックス勝負。
- `LIKE '%foo%'` は接頭辞でない LIKE なので index が効かない。`/api/search` `/api/horse-form` `/api/race-analysis` 等は実測してから FTS5 化を検討。

### 2. `_compute_composite` のリーク防止ロジック (`/api/backtest-g1`)

- `date_cutoff` 引数で過去データだけを使うように制限している (リーク防止)。**SQL は等価に書けるが、レースごとに 4 クエリ × N 頭走らせるので Workers の CPU 時間 (50ms / リクエスト, paid plan で 30s) を超える危険大**。
- **対策案**: オフライン (今の Python) で全 G1 の backtest を回し、結果 JSON を R2 に置く。Worker は静的に返すだけ。`/api/strategies` 等と同じ扱いに格下げ。

### 3. `/api/profit-scan` の UNION ALL 11 ブロック

- 1 SQL で 11 個の `SELECT … GROUP BY` を UNION ALL している。D1 は WASM SQLite なので動くはずだが、各ブロックで `base_params` を 11 回複製してバインドする必要がある (Python 版もそうしている)。
- 95% CI 下限: `p - 1.96 * sqrt(p(1-p)/n)`。SQL では書きづらいので JS で post-process が妥当。

### 4. ROI heatmap の集計 (`/api/roi-heatmap`)

- `row_totals` / `col_totals` / `grand_total` を SQL の WITH ROLLUP 無しで JS 側ループで計算している。D1 への移植は素直に動くが、`avg_finish` の row/col total が `Σ(avg_finish * n) / Σn` でなく `Σ(finish_sum) / Σn` のはずなのでロジック確認が必要 (Python 版もこの近似で動いている)。
- `axis = "distance_band"` のときは `CASE` 式を SELECT と GROUP BY 両方に同じ文字列で埋め込んでいる点に注意。

### 5. `/api/race-analysis` の composite_score

- Pydantic で受けている body を Hono では手動 validate する必要がある (`hono/validator` + zod 推奨)。
- 各馬について **4 つの LIKE クエリを直列実行** している。N 頭 = 18 の場合 72 クエリ。D1 で `prepare().bind().run()` を Promise.all で並列化すれば多少改善する。
- 重み 0.35 / 0.20 / 0.20 / 0.25 は Python 版ハードコード。設定化検討。

### 6. ファイル読み出し系 (15–19): R2 への移行が必須

| エンドポイント | 元ファイル | サイズ | 推奨ストレージ |
|----------------|-----------|--------|----------------|
| `/api/strategies` | `strategy_results.json` | 485 KB | R2 (KV は 25 MB 制限内だが書き換え頻度的に R2) |
| `/api/wealth-curve` | `wealth_data.json` | 4.0 MB | R2 (KV value 上限 25 MB だが応答サイズも考慮) |
| `/api/edge-validation` | `edge_data.json` | 110 KB | R2 or assets |
| `/api/analytics` | `analytics_data.json` | 7 KB | KV or assets |
| `/api/live-state` | `state/portfolio.json` + `state/picks/*.json` | 数 KB × N | R2 (日次更新あり) |

`wrangler.toml` の `[[r2_buckets]]` バインディングを次フェーズで追加する。

### 7. キャッシュ戦略

- FastAPI 版は `_meta_cache`, `_strength_cache`, `_backtest_g1_cache` などモジュール変数でメモリキャッシュしていた。Workers は isolate ごとに状態がリセットされるので **Cache API (`caches.default`) か KV に置く**必要がある。
- `/api/meta` のような変動の少ないものは `Cache-Control: public, max-age=300` + Cache API で十分。
- `/api/backtest-g1` は完全静的化 (上記 §2 参照)。

### 8. CORS

- FastAPI: `allow_origins=["*"]` / `allow_credentials=True` の組合せは仕様上は無効だが許容されている。Hono の `cors()` でも同じ挙動を再現するため `origin: "*"` + `credentials: true` を入れた。ブラウザによって警告が出る可能性あり、要確認。

---

## 次フェーズの実装優先順位

1. **インフラ整備**
   - `keiba.db` から D1 へのスキーマ移行と ETL (read 専用テーブル `entries` だけで十分)。
   - R2 バケット作成 + 静的 JSON (strategies / wealth-curve / edge-validation / analytics) のアップロード。
2. **A 優先のエンドポイント実装** (UI が即動くようにする)
   1. `/api/meta` (フィルタ UI の前提)
   2. `/api/summary`
   3. `/api/entries`
   4. `/api/search`
   5. `/api/horse-form`
   6. `/api/race-entries`
   7. `/api/roi-heatmap` (`/api/accuracy-heatmap` alias)
   8. `/api/rotation`
   9. `/api/profit-scan` (`/api/strength-scan` alias)
   10. `/api/race-analysis` (POST、最重要分析機能)
   11. `/api/live-state`
3. **B 優先のエンドポイント実装**
   - `/api/rotation-condition`, `/api/upset-ranking`, `/api/condition-strength`
   - 静的 JSON 配信系 (`/api/strategies`, `/api/wealth-curve`, `/api/edge-validation`, `/api/analytics`)
4. **C 優先 (バッチ化)**
   - `/api/backtest-g1` は Workers 内ではなく Python 側で日次計算し JSON を R2 にアップする方針へ切り替える。
5. **計測 & チューニング**
   - 各エンドポイントの p95 レイテンシ計測 (`wrangler tail` + Logpush)。
   - LIKE クエリのインデックス / FTS5 化判断。
   - キャッシュヒット率の確認 (`/api/meta`, `/api/summary`)。
