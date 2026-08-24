/**
 * The evidence panel. Draws what the server sent; computes nothing.
 *
 * It sits BESIDE the claim rather than under a disclosure, because evidence a reviewer has to
 * remember to open is evidence that does not get checked.
 */
import type { Answer, CellRange, Slot } from './api'

type OpenCell = (sheet: string, ref: string) => void

function Cells({ ranges, onOpen }: { ranges: CellRange[]; onOpen?: OpenCell }) {
  // Contiguous cells are shown as one block (S10:S19) — a click highlights the whole run in the
  // sheet, instead of 36 separate rows the reader has to reassemble by eye.
  return (
    <div className="flex flex-wrap gap-1.5">
      {ranges.map((r, i) => (
        <button key={i} type="button" onClick={() => onOpen?.(r.sheet, r.ref)}
          title={`${r.sheet}!${r.ref} — open the sheet and highlight ${r.count} cell${r.count === 1 ? '' : 's'}`}
          className="mono inline-flex items-center gap-1 rounded-md border border-orange-200 bg-orange-50 px-1.5 py-0.5 text-[11px] text-orange-700 transition hover:border-orange-400 hover:bg-orange-100">
          {r.ref}
          {r.count > 1 && <span className="text-orange-400">· {r.count}</span>}
        </button>
      ))}
    </div>
  )
}

function Sql({ sql }: { sql: string }) {
  return (
    <pre className="overflow-x-auto rounded bg-stone-900 p-2 text-[11px] leading-relaxed text-stone-100">
      {sql}
    </pre>
  )
}

function SlotView({ slot, onOpen }: { slot: Slot; onOpen?: OpenCell }) {
  // A derived value is a TREE: the ratio holds its numerator and denominator, each with their own
  // cells. Rendering recursively is what keeps "every number traces to cells" true one level down.
  if (slot.parts.length > 0) {
    return (
      <div className="space-y-2">
        <div className="text-sm">
          <span className="font-semibold">{slot.formatted}</span>
          {slot.unit ? ` ${slot.unit}` : ''} <span className="text-stone-500">— computed</span>
        </div>
        {slot.derivation && <div className="text-xs text-stone-500">{slot.derivation}</div>}
        {slot.parts.map((p) => (
          <div key={p.name} className="border-l-2 border-stone-200 pl-3">
            <div className="text-xs">
              <code className="rounded bg-stone-200 px-1">{p.name}</code> ={' '}
              <span className="font-semibold">{p.formatted}</span>{' '}
              <span className="text-stone-500">· {p.citations.length} cells</span>
            </div>
            {p.sql && <div className="mt-1"><Sql sql={p.sql} /></div>}
            <div className="mt-1"><Cells ranges={p.ranges} onOpen={onOpen} /></div>
          </div>
        ))}
      </div>
    )
  }
  return (
    <div className="space-y-1">
      <div className="text-xs">
        <code className="rounded bg-stone-200 px-1">{slot.name}</code> ={' '}
        <span className="font-semibold">{slot.formatted}</span>
        {slot.unit ? ` ${slot.unit}` : ''}{' '}
        <span className="text-stone-500">
          · {slot.citations.length} cell{slot.citations.length === 1 ? '' : 's'}
        </span>
      </div>
      <Cells ranges={slot.ranges} onOpen={onOpen} />
    </div>
  )
}

export function Trace({ answer, onOpenCell }: { answer: Answer; onOpenCell?: OpenCell }) {
  if (answer.status !== 'answered') {
    return (
      <p className="text-xs text-stone-500">
        {answer.status === 'abstained'
          ? 'Refusal text is composed from the catalog, not written by the model.'
          : 'Waiting for you to say which one you meant.'}
      </p>
    )
  }
  return (
    <div className="space-y-3">
      <div className="text-xs font-semibold text-stone-600">
        {answer.citation_count} source cell{answer.citation_count === 1 ? '' : 's'}
      </div>
      {answer.slots.map((s) => <SlotView key={s.name} slot={s} onOpen={onOpenCell} />)}
      {answer.echo && (
        <div className="text-xs text-stone-500">
          <span className="font-medium">Computed as:</span> {answer.echo}
        </div>
      )}
      {answer.sql && <Sql sql={answer.sql} />}
    </div>
  )
}
