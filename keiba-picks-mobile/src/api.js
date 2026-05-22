// R2 から picks / portfolio を取得する API ラッパー
// VITE_R2_URL が設定されていなければモックを返す
// picks 生成は GitHub Actions の workflow_dispatch をリモートトリガーして
// keiba-picks-mobile/public/picks/{date}.json を polling fetch する方式
import { mockPicks, mockPortfolio } from './mock-data.js'

const R2_BASE = import.meta.env.VITE_R2_URL
const GITHUB_TOKEN = import.meta.env.VITE_GITHUB_TOKEN
const GITHUB_OWNER = import.meta.env.VITE_GITHUB_OWNER
const GITHUB_REPO = import.meta.env.VITE_GITHUB_REPO
const WORKFLOW_FILE = 'picks-on-demand.yml'

// polling 設定
const POLL_INTERVAL_MS = 10_000
const POLL_MAX_ATTEMPTS = 30 // 10秒 × 30回 = 5分

/**
 * 指定日の picks を取得 (既存R2経路、初回ロード用)
 * @param {string} date YYYY-MM-DD
 * @returns {Promise<object>} picks JSON
 */
export async function fetchPicks(date) {
  if (!R2_BASE) {
    return mockPicks
  }
  const url = `${R2_BASE.replace(/\/$/, '')}/picks/${date}.json`
  try {
    const res = await fetch(url, { cache: 'no-store' })
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }
    return await res.json()
  } catch (err) {
    console.warn('fetchPicks failed, fallback to mock:', err)
    return mockPicks
  }
}

/**
 * portfolio 全体サマリーを取得
 * @returns {Promise<object>} portfolio JSON
 */
export async function fetchPortfolio() {
  if (!R2_BASE) {
    return mockPortfolio
  }
  const url = `${R2_BASE.replace(/\/$/, '')}/portfolio.json`
  try {
    const res = await fetch(url, { cache: 'no-store' })
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`)
    }
    return await res.json()
  } catch (err) {
    console.warn('fetchPortfolio failed, fallback to mock:', err)
    return mockPortfolio
  }
}

/**
 * GitHub Actions の workflow_dispatch を叩いて picks 生成を開始する
 * @param {string} dateFrom YYYY-MM-DD
 * @param {string} dateTo YYYY-MM-DD
 */
async function triggerWorkflow(dateFrom, dateTo) {
  const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${GITHUB_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      ref: 'main',
      inputs: { date_from: dateFrom, date_to: dateTo },
    }),
  })
  // workflow_dispatch は成功時 204 No Content
  if (res.status !== 204) {
    let detail = ''
    try {
      const j = await res.json()
      detail = j?.message || ''
    } catch {
      try {
        detail = await res.text()
      } catch {
        // ignore
      }
    }
    throw new Error(
      `GitHub API workflow_dispatch 失敗: HTTP ${res.status}${detail ? `: ${detail}` : ''}`
    )
  }
}

/**
 * /picks/{date}.json を polling で取得 (Cloudflare Pages 経由)
 * @param {string} date YYYY-MM-DD
 * @param {number} startedAt 開始エポック (キャッシュバスター用)
 * @returns {Promise<object>} picks JSON
 */
async function pollPicksFile(date, startedAt) {
  // private repo のため GitHub API contents endpoint (PAT認証) で fetch する
  // Cloudflare Pages は GitHub 連携してないので相対パスでは新ファイルを取得できない
  const apiBase = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents`
  const filePath = `keiba-picks-mobile/public/picks/${date}.json`
  const url = `${apiBase}/${filePath}?ref=main`
  for (let i = 0; i < POLL_MAX_ATTEMPTS; i++) {
    try {
      const res = await fetch(`${url}&t=${startedAt}_${i}`, {
        cache: 'no-store',
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept: 'application/vnd.github.raw+json',
        },
      })
      if (res.status === 200) {
        try {
          return await res.json()
        } catch {
          // JSON parse失敗時は生成中扱いで継続
        }
      }
      // 404 → まだ未コミット
    } catch (err) {
      console.warn(`pollPicksFile attempt ${i + 1} failed:`, err)
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
  }
  throw new Error(
    `picks 生成タイムアウト (${POLL_MAX_ATTEMPTS * POLL_INTERVAL_MS / 1000}秒経過)。GitHub Actions のログを確認してください。`
  )
}

/**
 * picks を生成する (GitHub Actions リモートトリガー + polling)
 * @param {{ dateFrom: string, dateTo: string }} params
 * @returns {Promise<{ picks: object, elapsed_sec: number }>}
 */
export async function generatePicks({ dateFrom, dateTo }) {
  // GitHub 連携 env 未設定で R2 も無い場合: 開発用モックフォールバック
  if (!GITHUB_TOKEN || !GITHUB_OWNER || !GITHUB_REPO) {
    if (!R2_BASE) {
      await new Promise((r) => setTimeout(r, 600))
      return { picks: mockPicks, elapsed_sec: 0.6 }
    }
    throw new Error(
      'VITE_GITHUB_TOKEN / VITE_GITHUB_OWNER / VITE_GITHUB_REPO が未設定です'
    )
  }

  const startedAt = Date.now()
  await triggerWorkflow(dateFrom, dateTo)
  const picks = await pollPicksFile(dateFrom, startedAt)
  const elapsed_sec = (Date.now() - startedAt) / 1000
  return { picks, elapsed_sec }
}

/**
 * 今日の日付 (JST) を YYYY-MM-DD で返す
 */
export function todayJstString() {
  const now = new Date()
  const jst = new Date(now.getTime() + 9 * 60 * 60 * 1000)
  return jst.toISOString().slice(0, 10)
}

/**
 * 翌土曜 (JST) を YYYY-MM-DD で返す。
 * 今日が土曜の場合は今日を返し、日曜の場合は6日後、それ以外は次の土曜まで。
 */
export function nextSaturdayJstString() {
  const now = new Date()
  const jst = new Date(now.getTime() + 9 * 60 * 60 * 1000)
  const dow = jst.getUTCDay() // JSTに+9した後の UTC dow = JST dow
  // 0:Sun 1:Mon ... 6:Sat
  const daysUntilSat = dow === 6 ? 0 : (6 - dow + 7) % 7
  const target = new Date(jst.getTime() + daysUntilSat * 24 * 60 * 60 * 1000)
  return target.toISOString().slice(0, 10)
}
