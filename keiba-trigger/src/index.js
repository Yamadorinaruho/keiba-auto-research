/**
 * keiba-trigger — Cloudflare Worker (Cron Trigger)
 *
 * GitHub Actions の高頻度スケジュール(毎分〜10分毎)が自走しない問題への対策。
 * Cloudflare の Cron Trigger(無料・確実に発火)から summer-picks.yml の
 * workflow_dispatch を叩き、巡回(notify)を確実に定期起動する。
 *
 * 設定:
 *   wrangler.toml の [vars] に GITHUB_OWNER / GITHUB_REPO / WORKFLOW_FILE / GIT_REF
 *   GITHUB_TOKEN は secret: `wrangler secret put GITHUB_TOKEN`
 *     (fine-grained PAT / 対象リポジトリの Actions: Read and write 権限)
 */

async function dispatch(env, mode) {
  const url = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}` +
              `/actions/workflows/${env.WORKFLOW_FILE}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "keiba-trigger-worker",   // GitHub APIはUA必須(無いと403)
      "Content-Type": "application/json",
    },
    // date="" → ワークフロー側で当日(JST)を採用。
    body: JSON.stringify({ ref: env.GIT_REF || "main", inputs: { mode, date: "" } }),
  });
  const ok = res.status === 204;   // workflow_dispatch 成功は 204 No Content
  if (!ok) console.error(`dispatch(${mode}) failed: HTTP ${res.status} ${await res.text()}`);
  else console.log(`dispatch(${mode}) ok`);
  return ok;
}

export default {
  // Cron Trigger(3分毎)。稼働窓=土日 0-7 UTC(9-17 JST)のみGAを叩く。
  async scheduled(event, env, ctx) {
    const d = new Date(event.scheduledTime);
    const dow = d.getUTCDay();   // 0=日 6=土
    const h = d.getUTCHours();
    if ((dow === 6 || dow === 0) && h >= 0 && h <= 7) {
      ctx.waitUntil(dispatch(env, "notify"));
    } else {
      console.log(`skip (UTC dow=${dow} h=${h} 稼働窓外)`);
    }
  },

  // 手動テスト用: GET /?key=<TRIGGER_KEY>&mode=notify でdispatch。
  async fetch(req, env) {
    const u = new URL(req.url);
    if (env.TRIGGER_KEY && u.searchParams.get("key") === env.TRIGGER_KEY) {
      const mode = u.searchParams.get("mode") || "notify";
      const ok = await dispatch(env, mode);
      return new Response(ok ? `dispatched: ${mode}\n` : "dispatch failed\n",
                          { status: ok ? 200 : 502 });
    }
    return new Response("keiba-trigger: ok (cron driven)\n");
  },
};
