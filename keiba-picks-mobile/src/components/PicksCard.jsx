import { useState } from 'react'

/**
 * 1レース分のpicks表示カード
 * props.race: {
 *   race_id, date, race_name, venue, race_num, surface, distance,
 *   picks: number[],
 *   horse_names: {[num]: name},
 *   mergedTotal: number,
 *   dupTotal: number,
 *   dupCount: number, // dup戦略数 (n戦略合算 と表示)
 * }
 */
export default function PicksCard({ race }) {
  const [copied, setCopied] = useState(false)

  const total = (race.mergedTotal || 0) + (race.dupTotal || 0)
  const dateLabel = formatDateMd(race.date)
  const horseLabel = race.picks
    .map((n) => `${n}番 ${race.horse_names?.[n] ?? ''}`.trim())
    .join(' / ')

  const copyText = buildCopyText(race)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(copyText)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // フォールバック: 一時 textarea
      const ta = document.createElement('textarea')
      ta.value = copyText
      document.body.appendChild(ta)
      ta.select()
      try {
        document.execCommand('copy')
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      } finally {
        document.body.removeChild(ta)
      }
    }
  }

  return (
    <article className="rounded-2xl border border-zinc-800 bg-zinc-900 p-4 shadow-lg">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-bold leading-tight text-zinc-100">
          <span className="mr-1">🏇</span>
          {dateLabel} {race.venue}
          {race.race_num}R {race.race_name}
        </h3>
      </div>
      <p className="mt-1 text-[11px] text-zinc-400">
        ({race.surface}
        {race.distance}m)
      </p>

      <div className="mt-3 text-sm">
        <Row label="馬券種" value="複勝" />
        <Row label="馬" value={horseLabel} valueClass="text-zinc-100 font-semibold" />
      </div>

      <div className="my-3 border-t border-dashed border-zinc-800" />

      <div className="space-y-1.5 text-sm">
        {race.mergedTotal > 0 && (
          <BetRow label="安全運用 (merged)" amount={race.mergedTotal} />
        )}
        {race.dupTotal > 0 && (
          <BetRow
            label="攻め運用 (dup)"
            amount={race.dupTotal}
            note={race.dupCount > 1 ? `${race.dupCount}戦略合算` : null}
          />
        )}
      </div>

      <div className="my-3 border-t border-dashed border-zinc-800" />

      <button
        type="button"
        onClick={handleCopy}
        className="flex w-full items-center justify-between rounded-xl bg-amber-500/10 px-4 py-3 text-sm font-semibold text-amber-300 ring-1 ring-amber-400/30 transition active:scale-[0.98] active:bg-amber-500/20"
      >
        <span>{copied ? 'コピー完了' : 'コピー'}</span>
        <span className="font-mono">合計 ¥{total.toLocaleString()}</span>
      </button>
    </article>
  )
}

function Row({ label, value, valueClass = 'text-zinc-200' }) {
  return (
    <div className="flex gap-3 leading-relaxed">
      <span className="w-16 shrink-0 text-zinc-500">{label}</span>
      <span className={valueClass}>{value}</span>
    </div>
  )
}

function BetRow({ label, amount, note }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-zinc-400">{label}</span>
      <span className="font-mono font-semibold text-amber-400">
        ¥{amount.toLocaleString()}
        {note && (
          <span className="ml-1.5 font-sans text-[10px] text-zinc-500">
            ({note})
          </span>
        )}
      </span>
    </div>
  )
}

function formatDateMd(d) {
  if (!d) return ''
  const m = d.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return d
  return `${m[2]}/${m[3]}`
}

function buildCopyText(race) {
  const horseLabel = race.picks
    .map((n) => `${n}番 ${race.horse_names?.[n] ?? ''}`.trim())
    .join(' / ')
  const total = (race.mergedTotal || 0) + (race.dupTotal || 0)
  const lines = [
    `${formatDateMd(race.date)} ${race.venue}${race.race_num}R ${race.race_name}`,
    `複勝 / ${horseLabel}`,
  ]
  if (race.mergedTotal > 0) {
    lines.push(`merged: ¥${race.mergedTotal.toLocaleString()}`)
  }
  if (race.dupTotal > 0) {
    const dupNote = race.dupCount > 1 ? ` (${race.dupCount}戦略)` : ''
    lines.push(`dup: ¥${race.dupTotal.toLocaleString()}${dupNote}`)
  }
  lines.push(`合計 ¥${total.toLocaleString()}`)
  return lines.join('\n')
}
