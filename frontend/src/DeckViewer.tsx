/**
 * The generated deck, read in-window instead of downloaded. Each slide shows the finding's
 * sentence and its cited cells — click a cell to open the sheet and verify the number, exactly
 * like an answer's evidence. The .pptx is still one click away for sharing.
 */
import { useEffect, useState } from 'react'
import type { Deck } from './api'
import { SheetViewer } from './SheetViewer'

export function DeckViewer({ deck, workbook, onClose }: {
  deck: Deck; workbook: string; onClose: () => void
}) {
  const [viewer, setViewer] = useState<{ sheet: string; a1: string } | null>(null)

  useEffect(() => {
    // Esc closes the open sheet first, the deck second.
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !viewer) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, viewer])

  return (
    <>
      <div className="fixed inset-0 z-40 grid place-items-center bg-stone-900/40 p-4 backdrop-blur-sm"
        onClick={onClose}>
        <div className="card animate-popin flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl"
          onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-3 border-b border-black/5 px-5 py-3">
            <span className="grad-accent grid h-7 w-7 place-items-center rounded-lg text-xs text-white">✦</span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-semibold">{deck.title}</div>
              <div className="truncate text-[11px] text-stone-400">{deck.subtitle}</div>
            </div>
            <a href={deck.pptx} download={`${workbook}.pptx`}
              className="rounded-lg border border-stone-300 px-3 py-1.5 text-xs font-medium text-stone-700 transition hover:bg-stone-50">
              Download .pptx
            </a>
            <button onClick={onClose}
              className="grid h-8 w-8 place-items-center rounded-lg text-stone-400 hover:bg-stone-100 hover:text-stone-700">✕</button>
          </div>

          <div className="min-h-0 flex-1 space-y-3 overflow-auto bg-stone-50 p-4">
            {deck.slides.map((sl, i) => (
              <div key={i} className="rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
                <div className="mb-2 text-[11px] font-medium tracking-widest text-stone-400 uppercase">
                  Slide {i + 1}
                </div>
                <div className="text-[15px] leading-relaxed text-stone-800">{sl.text}</div>
                {sl.cells.length > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {sl.cells.slice(0, 12).map((c, j) => (
                      <button key={j} onClick={() => setViewer({ sheet: c.sheet, a1: c.a1 })}
                        title={`${c.sheet}!${c.a1} — open the sheet`}
                        className="mono rounded-md border border-orange-200 bg-orange-50 px-1.5 py-0.5 text-[11px] text-orange-700 transition hover:border-orange-400 hover:bg-orange-100">
                        {c.a1}
                      </button>
                    ))}
                    {sl.cells.length > 12 && (
                      <span className="text-[11px] text-stone-400">+{sl.cells.length - 12} more cells</span>
                    )}
                  </div>
                )}
              </div>
            ))}
            {deck.closing && (
              <div className="rounded-xl border border-orange-200 bg-orange-50 p-5 text-[15px] font-medium text-stone-800">
                {deck.closing}
              </div>
            )}
          </div>

          <div className="border-t border-black/5 px-5 py-2 text-[11px] text-stone-400">
            Every number links to its source cell — click a cell to open the sheet. Esc to close.
          </div>
        </div>
      </div>

      {viewer && (
        <SheetViewer source={{ workbook }} sheet={viewer.sheet} a1={viewer.a1}
          onClose={() => setViewer(null)} />
      )}
    </>
  )
}
