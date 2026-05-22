# keiba-dashboard デプロイ手順 (Cloudflare Pages)

このドキュメントは `keiba-dashboard/` (React + Vite SPA) を Cloudflare Pages にデプロイする手順をまとめたものです。
バックエンド (Workers) は別プロジェクトで、URL は `VITE_API_URL` 経由で受け取ります。

## 1. Cloudflare Pages のビルド設定

Cloudflare ダッシュボード > Workers & Pages > Create > Pages から作成します。

| 項目 | 値 |
| --- | --- |
| Framework preset | None (もしくは Vite) |
| Build command | `npm run build` |
| Build output directory | `dist` |
| Root directory | `keiba-dashboard` (モノレポなのでサブディレクトリを指定) |
| Node version | `20` (環境変数 `NODE_VERSION=20` を設定) |
| Install command | `npm ci` (デフォルトでOK) |

ビルド成果物のエントリは `index.html` と `race.html` の 2 つです (`vite.config.js` の `rollupOptions.input` 参照)。

## 2. 環境変数

ビルド時に Vite が `import.meta.env.VITE_API_URL` を埋め込みます。
Production / Preview それぞれで設定してください。

### Production

| キー | 例 | 用途 |
| --- | --- | --- |
| `VITE_API_URL` | `https://keiba-api.example.workers.dev` | 本番 Workers の URL |
| `NODE_VERSION` | `20` | Node ランタイム指定 |

### Preview

| キー | 例 | 用途 |
| --- | --- | --- |
| `VITE_API_URL` | `https://keiba-api-staging.example.workers.dev` | ステージング Workers の URL |
| `NODE_VERSION` | `20` | Node ランタイム指定 |

`VITE_API_URL` を未設定にすると相対パス (`''`) にフォールバックし、Pages と同一オリジンに API があるとみなします。
別オリジンの Workers を呼ぶ場合は必ず設定してください。

### ローカル開発

`vite` の dev server (`npm run dev`) は `http://localhost:8001` のバックエンドを叩きます (`src/api.js` の `import.meta.env.DEV` 分岐)。
別ポートを使いたい場合は `src/api.js` を直接書き換えてください。

## 3. ドメイン

デフォルトでは `keiba-dashboard.pages.dev` のようなサブドメインが Pages 作成時に割り当てられます (実際のサブドメインは登録時に決定)。

カスタムドメインを使う場合は Pages の「Custom domains」から追加してください。

## 4. デプロイ手順

### 方法 A: GitHub 連携 (推奨)

1. Cloudflare Pages で GitHub リポジトリを連携
2. 上記の Build 設定 / 環境変数を入力
3. `main` ブランチへの push で Production、それ以外のブランチで Preview デプロイが自動実行される

### 方法 B: `wrangler pages deploy` で直接アップロード

CI を介さずローカルから直接デプロイする場合:

```bash
cd keiba-dashboard
npm ci
VITE_API_URL=https://keiba-api.example.workers.dev npm run build
npx wrangler pages deploy dist --project-name=keiba-dashboard
```

初回のみ `wrangler login` で認証が必要です。
プロジェクト名 (`--project-name`) は `wrangler.toml` の `name` と合わせます。

## 5. SPA ルーティング

`public/_routes.json` で Pages の Functions ルーティングから静的アセットを除外しています。
React Router 等で `/` 以外のパスを使う場合、Pages が `index.html` を返すように Cloudflare 側のリダイレクトルール (`_redirects` ファイル) を追加してください。
現状の `index.html` / `race.html` 構成では追加設定なしで動作します。

## 6. CORS の注意

`VITE_API_URL` が Pages と別オリジンの場合、バックエンド (Workers) 側で CORS ヘッダを返す必要があります。
最低限、以下を許可してください:

- `Access-Control-Allow-Origin: https://keiba-dashboard.pages.dev` (本番)
- `Access-Control-Allow-Origin: https://*.keiba-dashboard.pages.dev` (Preview のサブドメイン)
- カスタムドメインを使う場合はそのドメインも追加
- `Access-Control-Allow-Methods: GET, POST, OPTIONS`
- `Access-Control-Allow-Headers: Content-Type`

Preview デプロイは `<commit-hash>.keiba-dashboard.pages.dev` のような可変サブドメインで配信されるため、
ワイルドカード許可かオリジンの動的判定 (`Origin` ヘッダ検査 → echo back) のどちらかが必要です。

## 7. トラブルシュート

- ビルドが Node のバージョン違いで落ちる → 環境変数 `NODE_VERSION=20` を確認
- `import.meta.env.VITE_API_URL` が `undefined` のままビルドされる → 環境変数のスコープ (Production/Preview) を間違えていないか確認
- API リクエストが CORS でブロック → Workers 側の `Access-Control-Allow-Origin` を再確認
- `public/_routes.json` が反映されない → `dist/_routes.json` に出力されているか (Vite は `public/` 配下を自動コピー) 確認
