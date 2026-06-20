# keiba-trigger

GitHub Actions の高頻度スケジュール(`*/10` 等)が**自走しない**（GAは高頻度cronを間引く。`*/1`/`*/5`/`*/10` いずれも自走ゼロを実測）問題への対策。

Cloudflare の **Cron Trigger**（無料・確実に発火・最短1分）から `summer-picks.yml` の `workflow_dispatch` を叩き、巡回(`notify`)を確実に定期起動する。

```
Cloudflare Worker (cron */3, 土日0-7 UTC=9-17 JST)
  → GitHub API workflow_dispatch (mode=notify)
    → GA巡回ジョブ → 発走前ライブオッズで買い目をSlack通知
```

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

3. **動作テスト**（cronを待たずに即確認）
   ```
   https://keiba-trigger.<あなたのworkers.devサブドメイン>.workers.dev/?key=<TRIGGER_KEY>&mode=notify
   ```
   → GA の summer picks が起動すれば成功（`gh run list --workflow=summer-picks.yml` で確認）。

## 注意
- cron は **UTC**。`*/3 0-7 * * 6,0` = 土日 0:00-7:59 UTC = **9:00-16:59 JST を3分毎**。
- Cloudflare Workers 無料枠で cron trigger 利用可。
- `GITHUB_TOKEN` は必ず secret（`wrangler secret put`）。`wrangler.toml` の `[vars]` に平文で置かない。
