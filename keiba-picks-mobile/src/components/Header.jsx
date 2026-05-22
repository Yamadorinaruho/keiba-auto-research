export default function Header({ date, currentCap, totalRoiPct }) {
  const roiSign = totalRoiPct >= 0 ? '+' : ''
  const roiColor = totalRoiPct >= 0 ? 'text-emerald-400' : 'text-rose-400'

  return (
    <header className="safe-pt sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/90 backdrop-blur">
      <div className="px-4 pt-3 pb-3">
        <div className="flex items-baseline justify-between">
          <h1 className="text-base font-bold tracking-tight text-zinc-100">
            <span className="mr-1">競馬</span>
            <span className="text-amber-400">Picks</span>
          </h1>
          <span className="text-xs text-zinc-400">{date}</span>
        </div>
        <div className="mt-2 flex items-center gap-4 text-xs">
          <div>
            <span className="text-zinc-500">cap </span>
            <span className="font-mono font-semibold text-zinc-100">
              ¥{currentCap.toLocaleString()}
            </span>
          </div>
          <div>
            <span className="text-zinc-500">ROI </span>
            <span className={`font-mono font-semibold ${roiColor}`}>
              {roiSign}
              {(totalRoiPct * 100).toFixed(2)}%
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}
