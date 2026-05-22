export default function PortfolioStatus({ portfolio }) {
  if (!portfolio) return null

  const { current_cap, initial_cap, total_roi_pct, total_bets, total_hits } =
    portfolio
  const hitRate = total_bets > 0 ? (total_hits / total_bets) * 100 : 0

  return (
    <section className="mt-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        portfolio
      </h2>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Stat label="現在 cap" value={`¥${current_cap.toLocaleString()}`} />
        <Stat label="初期 cap" value={`¥${initial_cap.toLocaleString()}`} />
        <Stat
          label="通算 ROI"
          value={`${total_roi_pct >= 0 ? '+' : ''}${(total_roi_pct * 100).toFixed(2)}%`}
          color={total_roi_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}
        />
        <Stat
          label="的中率"
          value={`${hitRate.toFixed(1)}% (${total_hits}/${total_bets})`}
        />
      </div>
    </section>
  )
}

function Stat({ label, value, color = 'text-zinc-100' }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-sm font-semibold ${color}`}>
        {value}
      </div>
    </div>
  )
}
