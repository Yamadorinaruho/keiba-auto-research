import { useEffect, useMemo, useState } from 'react'
import Header from './components/Header.jsx'
import PicksCard from './components/PicksCard.jsx'
import PortfolioStatus from './components/PortfolioStatus.jsx'
import DateSelector from './components/DateSelector.jsx'
import {
  fetchPicks,
  fetchPortfolio,
  generatePicks,
  todayJstString,
  nextSaturdayJstString,
} from './api.js'

export default function App() {
  const initialDate = todayJstString()
  const [date, setDate] = useState(initialDate)
  const [selectedDate, setSelectedDate] = useState(nextSaturdayJstString())
  const [picks, setPicks] = useState(null)
  const [portfolio, setPortfolio] = useState(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState(null)
  const [genError, setGenError] = useState(null)
  const [elapsedSec, setElapsedSec] = useState(null)

  // 初期表示: 既存の R2 から本日分の picks/portfolio を取得
  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setError(null)
      try {
        const [p, pf] = await Promise.all([
          fetchPicks(initialDate),
          fetchPortfolio(),
        ])
        if (cancelled) return
        setPicks(p)
        setPortfolio(pf)
      } catch (e) {
        if (cancelled) return
        setError(e?.message ?? String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleGenerate = async () => {
    if (!selectedDate) return
    setGenerating(true)
    setGenError(null)
    setElapsedSec(null)
    try {
      const res = await generatePicks({
        dateFrom: selectedDate,
        dateTo: selectedDate,
      })
      setPicks(res?.picks ?? null)
      setDate(selectedDate)
      setElapsedSec(typeof res?.elapsed_sec === 'number' ? res.elapsed_sec : null)
    } catch (e) {
      setGenError(e?.message ?? String(e))
    } finally {
      setGenerating(false)
    }
  }

  const races = useMemo(() => mergeRaces(picks), [picks])
  const currentCap = picks?.merged?.current_cap ?? portfolio?.current_cap ?? 0
  const totalRoiPct = portfolio?.total_roi_pct ?? 0

  return (
    <div className="mx-auto min-h-screen max-w-md">
      <Header
        date={date}
        currentCap={currentCap}
        totalRoiPct={totalRoiPct}
      />

      <main className="safe-pb px-4 pb-10">
        <DateSelector
          value={selectedDate}
          onChange={setSelectedDate}
          onSubmit={handleGenerate}
          loading={generating}
        />

        {genError && (
          <div
            role="alert"
            className="mt-3 rounded-xl border border-rose-900/60 bg-rose-950/40 p-3 text-sm text-rose-300"
          >
            エラー: {genError}
          </div>
        )}

        {elapsedSec !== null && !genError && (
          <div className="mt-3 rounded-xl border border-emerald-900/60 bg-emerald-950/30 p-2 text-center text-xs text-emerald-300">
            生成完了 ({elapsedSec.toFixed(1)}秒)
          </div>
        )}

        {loading && (
          <div className="mt-10 text-center text-sm text-zinc-500">
            読み込み中…
          </div>
        )}

        {error && !loading && (
          <div className="mt-6 rounded-xl border border-rose-900/60 bg-rose-950/40 p-3 text-sm text-rose-300">
            読み込みエラー: {error}
          </div>
        )}

        {!loading && !error && (
          <>
            <section className="mt-4">
              <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
                投票指示 ({date})
              </h2>
              {races.length === 0 ? (
                <div className="rounded-2xl border border-zinc-800 bg-zinc-900/50 p-6 text-center text-sm text-zinc-400">
                  推奨レースなし
                </div>
              ) : (
                <div className="space-y-3">
                  {races.map((r) => (
                    <PicksCard key={r.race_id} race={r} />
                  ))}
                </div>
              )}
            </section>

            <PortfolioStatus portfolio={portfolio} />

            <div className="mt-6 text-center">
              <a
                href="#history"
                className="inline-block rounded-lg px-3 py-2 text-xs text-zinc-500 underline-offset-4 hover:underline"
                onClick={(e) => {
                  e.preventDefault()
                  alert('履歴表示は今後実装予定です')
                }}
              >
                履歴を見る (準備中)
              </a>
            </div>
          </>
        )}
      </main>
    </div>
  )
}

/**
 * picks.merged.races と picks.dup.races を race_id ごとに集約。
 * 同一race_id内でpicks(=対象馬)の集合をまとめ、merged/dup の合計金額を返す。
 */
function mergeRaces(picks) {
  if (!picks) return []
  const map = new Map()

  const addBucket = (race, kind) => {
    const id = race.race_id
    if (!map.has(id)) {
      map.set(id, {
        race_id: id,
        date: race.date,
        race_name: race.race_name,
        venue: race.venue,
        race_num: race.race_num,
        surface: race.surface,
        distance: race.distance,
        picks: [...race.picks],
        horse_names: { ...(race.horse_names ?? {}) },
        mergedTotal: 0,
        dupTotal: 0,
        dupCount: 0,
      })
    }
    const cur = map.get(id)
    // 馬の集合をマージ
    for (const n of race.picks) {
      if (!cur.picks.includes(n)) cur.picks.push(n)
    }
    Object.assign(cur.horse_names, race.horse_names ?? {})
    if (kind === 'merged') {
      cur.mergedTotal += race.total ?? race.bet_per ?? 0
    } else {
      cur.dupTotal += race.total ?? race.bet_per ?? 0
      cur.dupCount += 1
    }
  }

  for (const r of picks.merged?.races ?? []) addBucket(r, 'merged')
  for (const r of picks.dup?.races ?? []) addBucket(r, 'dup')

  // race_num 昇順、venue でソート
  return Array.from(map.values()).sort((a, b) => {
    if (a.venue !== b.venue) return a.venue.localeCompare(b.venue, 'ja')
    return (a.race_num ?? 0) - (b.race_num ?? 0)
  })
}
