/**
 * The sources rail. Which spreadsheets this assistant can see, and how to add one.
 *
 * A source is a workbook whose spec a human confirmed. An uploaded file that has not been through
 * that step is deliberately not a source and cannot be asked about (D59) — it appears only as
 * something you may start reading.
 */
import { useState } from 'react'
import type { Workbook } from './api'

export function Sources({ workbooks, current, onPick, onDelete, onAdd, busy }: {
  workbooks: Workbook[]
  current: string
  onPick: (id: string) => void
  onDelete: (id: string) => void
  onAdd: () => void
  busy: boolean
}) {
  const [confirming, setConfirming] = useState<string | null>(null)
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-black/5 bg-white/40">
      <div className="px-4 pt-4 pb-2 text-[11px] font-semibold tracking-widest text-stone-400 uppercase">
        Sources
      </div>

      <div className="flex-1 space-y-1.5 overflow-y-auto px-3">
        {workbooks.length === 0 && (
          <p className="px-2 py-6 text-sm text-stone-400">Nothing yet. Add a spreadsheet to begin.</p>
        )}
        {workbooks.map((w) => {
          const active = w.id === current
          return (
            <div key={w.id}
              className={`group lift relative flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5 transition
                ${active ? 'card ring-1 ring-orange-200' : 'border border-transparent hover:bg-white/70'}`}
              onClick={() => onPick(w.id)}>
              <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg text-sm
                ${active ? 'grad-accent text-white' : 'bg-stone-100 text-stone-500'}`}>▦</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-stone-800">{w.file}</span>
                <span className="mt-0.5 block truncate text-xs text-stone-400">
                  {w.sheets.length} sheet{w.sheets.length === 1 ? '' : 's'} · one {w.entity} per row
                </span>
              </span>
              {confirming === w.id ? (
                <div className="absolute top-1/2 right-2 flex -translate-y-1/2 items-center gap-1"
                  onClick={(e) => e.stopPropagation()}>
                  <button onClick={() => { setConfirming(null); onDelete(w.id) }}
                    className="rounded-md bg-red-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-red-700">Delete</button>
                  <button onClick={() => setConfirming(null)}
                    className="rounded-md bg-stone-100 px-2 py-1 text-[11px] font-medium text-stone-600 hover:bg-stone-200">Cancel</button>
                </div>
              ) : (
                <button title="Delete this source and its data"
                  onClick={(e) => { e.stopPropagation(); setConfirming(w.id) }}
                  className="absolute top-1/2 right-2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-lg text-stone-400
                             opacity-0 transition group-hover:opacity-100 hover:bg-red-50 hover:text-red-600">
                  🗑
                </button>
              )}
              {active && confirming !== w.id &&
                <span className="pointer-events-none absolute top-2.5 right-2.5 h-1.5 w-1.5 rounded-full bg-orange-500 group-hover:opacity-0" />}
            </div>
          )
        })}
      </div>

      <div className="p-3">
        <button onClick={onAdd} disabled={busy}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-stone-300 px-3 py-2.5 text-sm font-medium
                     text-stone-500 transition hover:border-orange-400 hover:bg-white hover:text-orange-600 disabled:opacity-40">
          <span className="text-base leading-none">+</span> Add a spreadsheet
        </button>
      </div>

      <div className="border-t border-black/5 px-4 py-3 text-center text-[11px] leading-relaxed text-stone-400">
        Made with <span className="text-red-400">❤</span> by Gaurav Kumar
        <span className="mt-0.5 block text-stone-300">© 2026 Grounded Analytics Agent</span>
      </div>
    </aside>
  )
}
