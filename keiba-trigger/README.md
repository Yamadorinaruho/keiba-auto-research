# keiba-trigger

GitHub Actions の高頻度スケジュール(`*/10` 等)が**自走しない**（GAは高頻度cronを間引く。`*/1`/`*/5`/`*/10` いずれも自走ゼロを実測）問題への対策。

Cloudflare の **Cron Trigger**（無料・確実に発火・最短1分）から `summer-picks.yml` の `workflow_dispatch` を叩き、巡回(`notify`)を確実に定期起動する。

```
Cloudflare Worker (cron */3 *、Worker側で土日0-7 UTC=9-17 JSTに絞る)
  → GitHub API workflow_dispatch (mode=notify)
    → GA巡回ジョブ → 発走前ライブオッズで買い目をSlack通知
```

※ Cloudflareは `*/3 0-7 * * 6,0` 形式のcronを拒否するため、cronは `*/3 * * * *`(3分毎)
  にして稼働窓(土日0-7 UTC)の判定は Worker の scheduled ハンドラ内で行う。
※ `workers_dev = false`(cron専用・公開URL無し)。手動テストは下記 curl で。

朝(`schedule`)と夜(`settle`)は once-daily で GA cron が動くため、当面 GA 側に据え置き（このWorkerは巡回のみ代替）。

## セットアップ手順（ユーザー作業）

1. **GitHub PAT を発行**（fine-grained 推奨）
   - 対象リポジトリ: `Yamadorinaruho/keiba-auto-research`
   - 権限: **Repository permissions → Actions: Read and write**（workflow_dispatch に必要）

2. **デプロイ**
   ```bash
   cd keiba-trigger
   npx wrangler login            # 初回のみ(Cloudflareアカウント認証)
   npx wrangler secret put GITHUB_TOKEN   # 上のPATを貼る
   npx wrangler secret put TRIGGER_KEY    # 手動テスト用の任意文字列(任意)
   npx wrangler deploy
   ```

3. **動作テスト**（cronを待たずに即確認。Worker相当のAPIを直接叩く）
   ```bash
   curl -X POST \
     -H "Authorization: Bearer $(gh auth token)" \
     -H "Accept: application/vnd.github+json" \
     -H "X-GitHub-Api-Version: 2022-11-28" \
     -H "User-Agent: keiba-trigger-worker" \
     https://api.github.com/repos/Yamadorinaruho/keiba-auto-research/actions/workflows/summer-picks.yml/dispatches \
     -d '{"ref":"main","inputs":{"mode":"notify","date":""}}'
   ```
   → HTTP 204 で成功（`gh run list --workflow=summer-picks.yml` にworkflow_dispatchが出る）。

## 注意
- cron は **UTC**。`*/3 0-7 * * 6,0` = 土日 0:00-7:59 UTC = **9:00-16:59 JST を3分毎**。
- Cloudflare Workers 無料枠で cron trigger 利用可。
- `GITHUB_TOKEN` は必ず secret（`wrangler secret put`）。`wrangler.toml` の `[vars]` に平文で置かない。
