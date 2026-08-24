/**
 * The source, in context. Click a cited cell (in an answer or during onboarding) and the actual
 * sheet opens with that cell highlighted — so a reviewer VERIFIES the number against the file,
 * not just reads its address. Values and formulas straight from the workbook; nothing computed.
 */
import { useEffect, useRef, useState } from 'react'
import { api, type Sheet } from './api'

function colLetter(n: number): string {
  let s = ''
  while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = (n - m - 1) / 26 }
  return s
}

/** "S10:S19" -> ["S10".."S19"] (same-column runs, which is what our ranges are); "M42" -> ["M42"]. */
function rangeCells(ref: string): string[] {
  if (!ref.includes(':')) return [ref]
  const [a, b] = ref.split(':')
  const ma = a.match(/([A-Z]+)(\d+)/)
  const mb = b.match(/(\d+)/)
  if (!ma || !mb) return [a]
  const col = ma[1], r1 = +ma[2], r2 = +mb[1]
  const out: string[] = []
  for (let r = r1; r <= r2; r++) out.push(col + r)
  return out
}

export function SheetViewer({ source, sheet, a1, onClose }: {
  source: { workbook?: string; file?: string }; sheet: string; a1: string; onClose: () => void
}) {
  const [data, setData] = useState<Sheet | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    setData(null); setErr(null)
    api.sheet(source, sheet).then(setData).catch((e) => setErr((e as Error).message))
  }, [source.workbook, source.file, sheet])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const hitRef = useRef<HTMLTableCellElement>(null)
  useEffect(() => {
    if (data) hitRef.current?.scrollIntoView({ block: 'center', inline: 'center' })
  }, [data])

  const targetSet = new Set(rangeCells(a1))
  const byA1 = new Map((data?.cells ?? []).map((c) => [c.a1, c]))
  const target = (data?.cells ?? []).find((c) => targetSet.has(c.a1))
  const tr = target?.r ?? 1
  const lo = Math.max(1, tr - 14), hi = Math.min(data?.max_row ?? 1, tr + 14)
  const rows = []
  for (let r = lo; r <= hi; r++) rows.push(r)
  const cols = []
  for (let c = 1; c <= (data?.max_col ?? 1); c++) cols.push(c)

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-stone-900/40 p-4 backdrop-blur-sm"
      onClick={onClose}>
      <div className="card animate-popin flex max-h-[86vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-black/5 px-5 py-3">
          <span className="grad-accent grid h-7 w-7 place-items-center rounded-lg text-xs text-white">▦</span>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{sheet}</div>
            <div className="text-[11px] text-stone-400">source for <span className="mono text-orange-600">{sheet}!{a1}</span>{targetSet.size > 1 ? ` · ${targetSet.size} cells` : ''}</div>
          </div>
          {target && targetSet.size === 1 && (
            <div className="ml-auto mr-2 hidden text-right sm:block">
              <div className="mono text-sm font-semibold text-stone-800">{target.v}</div>
              {target.f && <div className="mono text-[11px] text-stone-400">{target.f}</div>}
            </div>
          )}
          <button onClick={onClose} className="grid h-8 w-8 place-items-center rounded-lg text-stone-400 hover:bg-stone-100 hover:text-stone-700">✕</button>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-3">
          {err && <div className="p-6 text-sm text-red-600">{err}</div>}
          {!data && !err && <div className="p-6 text-sm text-stone-400">Loading the sheet…</div>}
          {data && (
            <table className="border-separate border-spacing-0 text-xs">
              <thead>
                <tr>
                  <th className="sticky top-0 left-0 z-10 bg-stone-100" />
                  {cols.map((c) => (
                    <th key={c} className={`sticky top-0 z-0 min-w-[70px] border-b border-stone-200 bg-stone-100 px-2 py-1 font-medium
                      ${target && c === target.c ? 'text-orange-600' : 'text-stone-400'}`}>{colLetter(c)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r}>
                    <td className={`sticky left-0 z-0 border-r border-stone-200 bg-stone-100 px-2 py-1 text-right font-medium
                      ${target && targetSet.has(`${colLetter(target.c)}${r}`) ? 'text-orange-600' : 'text-stone-400'}`}>{r}</td>
                    {cols.map((c) => {
                      const cell = byA1.get(`${colLetter(c)}${r}`)
                      const hit = targetSet.has(`${colLetter(c)}${r}`)
                      return (
                        <td key={c} title={cell?.f ?? ''} ref={hit ? hitRef : undefined}
                          className={`max-w-[220px] truncate border-b border-stone-100 px-2 py-1 whitespace-nowrap
                            ${hit ? 'bg-orange-100 font-semibold text-orange-800 ring-2 ring-orange-400 ring-inset'
                                  : cell ? 'text-stone-700' : 'text-stone-300'}
                            ${cell && !isNaN(Number(cell.v)) ? 'text-right mono' : ''}`}>
                          {cell?.v ?? ''}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="border-t border-black/5 px-5 py-2 text-[11px] text-stone-400">
          Showing rows {lo}–{hi}. The highlighted cell is where this number came from. Esc to close.
        </div>
      </div>
    </div>
  )
}
