# GitHub Actions ワークフロー

このディレクトリのワークフロー一覧と、必要な Secrets の管理メモ。

## ワークフロー

### `keiba-reminder.yml` (既存)
- 役割: Slack へリマインダ通知のみ
  - 土 09:30 JST: 土朝 chain 前リマインダ
  - 日 08:00 JST: 日朝 picks リマインダ
- 実体の picks/settle は Mac launchd 側で実行している前提
- このワークフローは「通知だけ」「DB不要」なので picks-weekly.yml とは別物として共存

### `picks-weekly.yml` (新規)
- 役割: 週次で picks 生成 + Discord 通知 + R2 アップロード + リポへコミット
- 起動: 毎週 金 23:00 JST (= 金 14:00 UTC, `cron: '0 14 * * 5'`)。`workflow_dispatch` で手動実行も可。
- 処理ステップ:
  1. Python 3.11 + 依存 (numpy, pandas, requests, beautifulsoup4, lxml) インストール
  2. `keiba.db` の存在チェック (無ければ fail)
  3. 翌日土曜・翌々日日曜の日付を動的算出 (JST)
  4. `python keiba-dashboard/live/scrape_all.py YYYYMMDD YYYYMMDD` で土日2日分を netkeiba から取込
  5. `python keiba-dashboard/live/runner.py picks --from $sat --to $sun` で picks 生成
  6. `keiba-dashboard/state/picks/{today}.json` を読み取り Discord Webhook に通知
     - 通知本文は `🏇 {date} {venue}{race_num}R {race_name}` 形式 (5/23 出力に合わせる)
  7. Cloudflare R2 に picks / portfolio をアップロード (スマホアプリ用、失敗してもワークフローは継続)
     - `picks/{today}.json` (履歴)
     - `picks/latest.json` (最新ポインタ。アプリはここを取りに行く)
     - `portfolio.json` (最新ポートフォリオ)
  8. picks.json を main にコミット & push (`Yamadorinaruho` 名義、`Co-Authored-By` なし)

## 必要な Secrets

リポジトリの Settings → Secrets and variables → Actions で設定:

| Secret 名 | 用途 | 必須ワークフロー |
| --- | --- | --- |
| `SLACK_WEBHOOK_URL` | Slack 通知 (リマインダ) | `keiba-reminder.yml` |
| `DISCORD_WEBHOOK_URL` | Discord 通知 (週次 picks) | `picks-weekly.yml` |
| `CLOUDFLARE_API_TOKEN` | Cloudflare R2 アップロード (Workers R2 Edit 権限) | `picks-weekly.yml` |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Account ID | `picks-weekly.yml` |

`GITHUB_TOKEN` は標準で付与され、`contents: write` 権限でコミット&pushする。

### Cloudflare API Token 作成手順

1. Cloudflare Dashboard → My Profile → API Tokens → Create Token
2. テンプレートではなく Custom Token で次の権限を付与:
   - **Account / Workers R2 Storage / Edit**
3. アカウントスコープは対象アカウントのみに絞る
4. 発行されたトークンを `CLOUDFLARE_API_TOKEN` として登録
5. `CLOUDFLARE_ACCOUNT_ID` は Dashboard 右サイドバーの Account ID をコピーして登録

## Cloudflare R2 セットアップ

スマホアプリ (keiba-picks-mobile) が picks を取得するための R2 バケットを用意する。

### R2 バケット作成

ローカル (もしくは任意の wrangler 環境) で:

```bash
# 初回のみ Cloudflare にログイン
npx wrangler login

# バケット作成 (バケット名は picks-weekly.yml の R2_BUCKET と一致させること)
npx wrangler r2 bucket create keiba-picks
```

GitHub Actions 側では `npx wrangler@3 r2 object put` で都度実行するため、wrangler のグローバルインストールは不要。`CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID` が環境変数として渡れば動作する。

### アップロードされる object key

| key | 内容 | 用途 |
| --- | --- | --- |
| `picks/{YYYY-MM-DD}.json` | 該当日の picks (履歴) | 過去分閲覧 |
| `picks/latest.json` | 最新 picks (上書き) | アプリの「最新を取りに行く」用 |
| `portfolio.json` | 現在のポートフォリオ | アプリのポートフォリオ表示 |

### 公開設定 (選択肢)

#### 選択肢 A: R2 public bucket (推奨・最小構成)

- 設定:
  - Cloudflare Dashboard → R2 → `keiba-picks` → Settings → **Public access (R2.dev subdomain)** を Enable
  - もしくは:
    ```bash
    npx wrangler r2 bucket dev-url enable keiba-picks
    ```
  - 任意でカスタムドメイン (`picks.example.com` 等) を設定可能 (R2 → Custom Domains)
- アプリ側からは公開URL (`https://pub-xxxxx.r2.dev/picks/latest.json` 等) を直接 fetch
- **pros**: 簡単。Workers 不要。Pages Functions 不要。
- **cons**: URL を知っていれば誰でもアクセス可能。
  - ただし picks レベルの情報は実害が小さく、アプリ側 (Cloudflare Access 等) で UI を保護すれば実用上問題なし。

#### 選択肢 B: Workers proxy + R2 (URL秘匿が必要な場合)

- 構成: 静的サイト/アプリ → Cloudflare Workers (認証チェック) → R2 binding で fetch
- **pros**: トークン/Cookie 認証を強制できる。URL が漏れても直アクセス不可。
- **cons**: Workers (および R2 binding) を実装する必要あり。
- 実装スケッチ:
  ```js
  // wrangler.toml で [[r2_buckets]] binding = "PICKS", bucket_name = "keiba-picks"
  export default {
    async fetch(req, env) {
      // ここで認証チェック (例: Cloudflare Access JWT 検証)
      const url = new URL(req.url);
      const key = url.pathname.replace(/^\//, "");
      const obj = await env.PICKS.get(key);
      if (!obj) return new Response("not found", { status: 404 });
      return new Response(obj.body, {
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    },
  };
  ```

**初期は選択肢 A を推奨**。Pages 側を Cloudflare Access で保護すれば実害なし。秘匿要件が出てきた段階で B に移行する。

## keiba.db の配布について (TODO)

`runner.py picks` は `plot_wealth_data.py` のサブ関数経由で **DB全件をスキャン** してペルソナを構築する。
そのためフルDBがランナー上に必要だが、現状の `keiba.db` は約 **1.1GB** あるため通常の git push では扱えない。

### 検討した永続化案

| 案 | 概要 | 備考 |
| --- | --- | --- |
| A: scrape→commit | scrape結果をリポに毎回コミット | **picks-weekly.yml はこれを採用**。差分は週1MB程度。ただし DB本体は別途配布が必須 |
| B: artifact 保存 | actions/upload-artifact | 90日で消える。週次ジョブ間の引き継ぎには弱い |
| C: 外部ストレージ | Cloudflare R2 / S3 にDBアップロード | DBサイズが大きい今後本命。要設定 |

### TODO: DB配布方法を決める

現実装は「`keiba.db` がリポにそのまま入っている」前提で動く (存在チェックで fail させる)。
本番運用するためには以下のいずれかが必要:

- [ ] **Git LFS で keiba.db を追跡** (簡単だが LFS 帯域コストに注意)
- [ ] **R2 / S3 にDBをホストし、ワークフロー先頭で取得** (コスト最適。要 `AWS_*` or R2 用 Secret 追加)
- [ ] **ローカルで scrape 済みのDBを毎週手動アップロード** (運用負荷高、暫定案)

決定するまで `picks-weekly.yml` は `keiba.db` 不在で fail する。`workflow_dispatch` で動作確認するには上記いずれかの整備が前提。

## 動作確認手順

1. Settings → Secrets で `DISCORD_WEBHOOK_URL` を設定
2. keiba.db を上記いずれかの方法でランナーから参照可能にする
3. Actions タブから `picks weekly` を選び `Run workflow` (manual dispatch)
4. Discord に通知が届き、`keiba-dashboard/state/picks/YYYY-MM-DD.json` が main に commit されることを確認
