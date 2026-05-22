# keiba-picks-mobile

スマホ専用ミニアプリ。当日/翌日の競馬picks (買い目+金額) を見るだけのシンプル構成。

- React 19 + Vite + Tailwind CSS
- データソース: Cloudflare R2 にアップロードされた `picks/{YYYY-MM-DD}.json` / `portfolio.json`
- 開発時は `src/mock-data.js` がフォールバック

## セットアップ

```bash
cd keiba-picks-mobile
npm install
```

## ローカル開発

```bash
npm run dev
```

http://localhost:5173 を開く。スマホで実機確認する場合は `vite.config.js` の `server.host: true` で LAN IP からアクセス可。

`VITE_R2_URL` 未設定時はモックデータが表示される。

## 環境変数

`.env.local` (開発用) / `.env.production` (本番ビルド用) を作成して:

```
VITE_R2_URL=https://your-r2-bucket.example.com
VITE_GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
VITE_GITHUB_OWNER=Yamadorinaruho
VITE_GITHUB_REPO=keiba-auto-research
```

- `VITE_R2_URL`: 既存の picks/portfolio JSON 配信先 (R2)。初期画面ロード時のみ使用。
- `VITE_GITHUB_TOKEN`: 後述の Fine-grained PAT。picks 生成ボタン押下時に workflow_dispatch を発火する。
- `VITE_GITHUB_OWNER` / `VITE_GITHUB_REPO`: ワークフローを持つリポジトリ。

R2 配下の構成想定:

```
{VITE_R2_URL}/picks/2026-05-23.json
{VITE_R2_URL}/portfolio.json
```

picks 生成フロー:

1. アプリが `POST https://api.github.com/repos/{owner}/{repo}/actions/workflows/picks-on-demand.yml/dispatches` を発火 (PAT 認証)
2. GitHub Actions が scrape → runner.py picks を実行
3. `keiba-picks-mobile/public/picks/{date_from}.json` にコピーして main にコミット
4. Cloudflare Pages が再デプロイ
5. アプリが `/picks/{date_from}.json` を10秒おきに polling (最大5分タイムアウト) → 200 で表示

## GitHub PAT (Fine-grained) 作成手順

`VITE_GITHUB_TOKEN` 用の personal access token を作る:

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → **Generate new token**
2. Token name: `keiba-picks-mobile` 等わかりやすい名前
3. Expiration: お好み (90日 / 1年 / カスタム)
4. Resource owner: `Yamadorinaruho`
5. Repository access: **Only select repositories** → `Yamadorinaruho/keiba-auto-research` のみ選択
6. Repository permissions:
   - **Actions: Read and write** (workflow_dispatch を叩くために必須)
   - **Contents: Read-only** (ワークフロー一覧を引くため)
7. Generate token → 表示されたトークンをコピーして `.env.production` / `.env.local` の `VITE_GITHUB_TOKEN` に貼る

注意: トークンはビルドに埋め込まれるため公開しないこと。Cloudflare Pages の環境変数にも同じ値を登録する。

## ビルド

```bash
npm run build
```

`dist/` に静的アセットが出力される。

```bash
npm run preview
```

でビルド結果をローカル確認可。

## Cloudflare Pages デプロイ

### 初回 (CLI)

```bash
npm install -g wrangler
wrangler login
npm run build
wrangler pages deploy dist --project-name=keiba-picks-mobile
```

### 継続デプロイ (GitHub 連携)

Cloudflare Pages dashboard で:

- リポジトリ接続後、Build 設定を以下で登録:
  - Build command: `npm run build`
  - Build output directory: `dist`
  - Root directory: `keiba-picks-mobile`
- 環境変数 `VITE_R2_URL` を Pages 環境変数に設定。

`wrangler.toml` の `pages_build_output_dir = "dist"` を尊重。

### SPA fallback

`public/_routes.json` は Cloudflare Pages の SPA ルーティング設定。今は実質1画面だが、後続で `/history` 等を増やす際に有効。

## ファイル構成

```
src/
├ App.jsx                  メイン画面 (本日のpicks + portfolio)
├ main.jsx                 React entry
├ index.css                Tailwind ディレクティブ
├ api.js                   R2 fetch ラッパー (失敗時 mock-data)
├ mock-data.js             開発/フォールバック用ダミー
└ components/
   ├ Header.jsx            上部ヘッダ (日付 / cap / ROI)
   ├ DateSelector.jsx      日付選択 + picks 生成ボタン
   ├ PicksCard.jsx         1レース分カード + コピーボタン
   └ PortfolioStatus.jsx   portfolio サマリー
```

## 仕様メモ

- picks.json は `merged` (安全運用) と `dup` (攻め運用) の2つの戦略を持つ。`App.jsx` の `mergeRaces` で race_id ごとに集約してカード化。
- 馬券種は今のところ複勝固定 (将来拡張可)。
- ダーク基調、金額のアクセントは amber-400。
- `[コピー]` ボタンで `clipboard.writeText` → JRA即PAT入力時にペーストする想定。
