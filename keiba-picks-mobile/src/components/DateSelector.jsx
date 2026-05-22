/**
 * 日付選択 + 実行ボタン
 * props:
 *   value: string (YYYY-MM-DD)
 *   onChange: (string) => void
 *   onSubmit: () => void
 *   loading: boolean
 */
export default function DateSelector({ value, onChange, onSubmit, loading }) {
  return (
    <section className="mt-4 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        picks 生成
      </h2>
      <div className="flex items-center gap-3">
        <label
          htmlFor="picks-date"
          className="shrink-0 text-sm text-zinc-300"
        >
          日付
        </label>
        <input
          id="picks-date"
          type="date"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={loading}
          className="min-h-[44px] flex-1 rounded-xl border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 focus:border-amber-400 focus:outline-none disabled:opacity-50"
        />
      </div>

      <button
        type="button"
        onClick={onSubmit}
        disabled={loading || !value}
        className="mt-3 flex min-h-[48px] w-full items-center justify-center gap-2 rounded-xl bg-amber-500 px-4 py-3 text-base font-bold text-zinc-950 shadow transition active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-400"
      >
        {loading ? (
          <>
            <Spinner />
            <span>picks 生成中… (3-5分かかります)</span>
          </>
        ) : (
          <span>✨ picks を生成</span>
        )}
      </button>
    </section>
  )
}

function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-zinc-950/30 border-t-zinc-950"
      aria-hidden="true"
    />
  )
}
