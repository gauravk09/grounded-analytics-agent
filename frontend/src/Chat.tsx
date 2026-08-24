/**
 * The conversation. Transcript above, composer below, evidence beside each answer.
 *
 * Three outcomes get three treatments, because they are different events and flattening them into
 * "a response" is how a deliberate refusal starts reading like a failure:
 *
 *   answered   green   — with its source cells beside it
 *   clarify    amber   — with buttons; the system asked YOU something
 *   abstained  blue    — a composed sentence, not an error
 */
import { useState } from 'react'
import { api, type Answer, type ModelChoice, type Workbook } from './api'
import { Trace } from './Trace'
import { SheetViewer } from './SheetViewer'

export type Turn = { question: string; answer: Answer }

const TONE: Record<Answer['status'], { bar: string; chip: string; label: string; icon: string }> = {
  answered:  { bar: 'bg-emerald-500', chip: 'bg-emerald-50 text-emerald-700 ring-emerald-200', label: 'Answered',  icon: '✓' },
  clarify:   { bar: 'bg-amber-500',   chip: 'bg-amber-50 text-amber-700 ring-amber-200',       label: 'Needs input', icon: '?' },
  abstained: { bar: 'bg-sky-500',     chip: 'bg-sky-50 text-sky-700 ring-sky-200',             label: 'Abstained',  icon: '—' },
}

function resolved(question: string, clarification: string, choice: string) {
  const term = clarification.match(/"(.+?)"/)
  if (term) return question.replace(new RegExp(term[1], 'i'), choice)
  return `${question.replace(/\s*\?\s*$/, '')} for ${choice}?`
}

export function Chat({ workbook, workbooks, model, session, turns, setTurns, suggestions, onPick }: {
  workbook: Workbook
  workbooks: Workbook[]
  model: ModelChoice
  session: string
  turns: Turn[]
  setTurns: React.Dispatch<React.SetStateAction<Turn[]>>
  suggestions: string[]
  onPick: (id: string) => void
}) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [picking, setPicking] = useState(false)
  const [mode, setMode] = useState<'ask' | 'deck'>('ask')
  const [deckBusy, setDeckBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [viewer, setViewer] = useState<{ sheet: string; a1: string } | null>(null)

  async function send(question: string) {
    if (!question.trim() || busy) return
    setBusy(true); setError(null); setQ('')
    try {
      const answer = await api.ask(question, workbook.id, session, model)
      setTurns((t) => [...t, { question, answer }])
    } catch (e) {
      setError((e as Error).message)
    } finally { setBusy(false) }
  }

  async function generateDeck(brief: string) {
    if (deckBusy) return
    setDeckBusy(true); setNotice('Building your presentation… every number stays traced to its cell.')
    setError(null); setQ('')
    try {
      await api.deck(workbook.id, brief, model)
      setNotice('✓ Presentation ready — check your downloads.')
    } catch (e) { setNotice(null); setError((e as Error).message) }
    finally { setDeckBusy(false) }
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-5xl flex-col">
      <div className="flex-1 overflow-y-auto px-6 py-8">
        {turns.length === 0 && (
          <div className="animate-fadeup mx-auto max-w-2xl pt-12 text-center">
            <div className="grad-accent mx-auto grid h-12 w-12 place-items-center rounded-2xl text-xl text-white shadow-lg">✦</div>
            <h1 className="mt-5 text-2xl font-bold tracking-tight">
              Ask <span className="grad-text">{workbook.file}</span>
            </h1>
            <p className="mt-2.5 text-[15px] leading-relaxed text-stone-500">
              Every number links to the cell it came from. When the file can't answer, it says so.
            </p>
            <div className="mt-8 grid gap-2.5 text-left sm:grid-cols-2">
              {suggestions.map((s, i) => (
                <button key={s} onClick={() => send(s)}
                  style={{ animationDelay: `${i * 60}ms` }}
                  className="lift card animate-fadeup group flex items-center gap-3 rounded-xl border border-black/5 px-4 py-3 text-sm text-stone-700">
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-lg bg-orange-50 text-xs text-orange-500 transition group-hover:bg-orange-100">↗</span>
                  <span className="min-w-0">{s}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-8">
          {turns.map((t, i) => {
            const tone = TONE[t.answer.status]
            return (
              <div key={i} className="animate-fadeup space-y-3">
                {/* the question, as a user bubble */}
                <div className="flex justify-end">
                  <div className="grad-accent max-w-[80%] rounded-2xl rounded-br-md px-4 py-2.5 text-sm font-medium text-white shadow-sm">
                    {t.question}
                  </div>
                </div>

                <div className="grid items-start gap-4 lg:grid-cols-2">
                  {/* the answer card */}
                  <div className="card relative overflow-hidden rounded-2xl border border-black/5 pl-4">
                    <span className={`absolute top-0 bottom-0 left-0 w-1 ${tone.bar}`} />
                    <div className="px-4 py-3.5">
                      <span className={`mb-2 inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ${tone.chip}`}>
                        <span>{tone.icon}</span> {tone.label}
                      </span>
                      <div className="text-[15px] leading-relaxed text-stone-800">{t.answer.text}</div>
                      {t.answer.scope_options.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {t.answer.scope_options.map((o) => (
                            <button key={o} onClick={() => send(resolved(t.question, t.answer.text, o))}
                              className="lift rounded-lg border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100">
                              {o}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  {/* the evidence — bounded and scrollable so a derived tree can't push the claim off-screen */}
                  <div className="card max-h-96 overflow-y-auto rounded-2xl border border-black/5 p-4">
                    <div className="mb-2 text-[11px] font-semibold tracking-widest text-stone-400 uppercase">Evidence</div>
                    <Trace answer={t.answer} onOpenCell={(sheet, a1) => setViewer({ sheet, a1 })} />
                  </div>
                </div>
              </div>
            )
          })}

          {busy && (
            <div className="animate-fadeup flex items-center gap-2 pl-1 text-sm text-stone-400">
              <span className="thinking-dot" />
              <span className="thinking-dot" style={{ animationDelay: '.2s' }} />
              <span className="thinking-dot" style={{ animationDelay: '.4s' }} />
              <span className="ml-1">planning the query…</span>
            </div>
          )}
        </div>

        {error && <div className="animate-popin mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">{error}</div>}
      </div>

      {/* composer */}
      <div className="px-6 pb-6">
        {picking && (
          <div className="card animate-popin mx-auto mb-2.5 max-w-3xl rounded-2xl border border-black/5 p-2">
            <div className="px-2 py-1 text-[11px] font-semibold tracking-widest text-stone-400 uppercase">Ask a different spreadsheet</div>
            {workbooks.map((w) => (
              <button key={w.id} onClick={() => { onPick(w.id); setPicking(false) }}
                className={`block w-full truncate rounded-lg px-2.5 py-1.5 text-left text-sm transition
                  ${w.id === workbook.id ? 'bg-orange-50 text-orange-700' : 'hover:bg-stone-100'}`}>
                {w.file}
              </button>
            ))}
          </div>
        )}
        {notice && (
          <div className="animate-popin mx-auto mb-2.5 flex max-w-3xl items-center gap-2 rounded-xl border border-orange-200 bg-orange-50 px-3 py-2 text-sm text-orange-800">
            {deckBusy && <span className="thinking-dot" />}
            {notice}
          </div>
        )}
        <form onSubmit={(e) => { e.preventDefault(); mode === 'deck' ? generateDeck(q) : send(q) }}
          className={`card mx-auto flex max-w-3xl items-center gap-2 rounded-2xl border p-1.5 pl-2 transition
            ${mode === 'deck' ? 'border-orange-200 ring-1 ring-orange-100' : 'border-black/5'}`}>
          <button type="button" onClick={() => setPicking(!picking)} title="Choose a spreadsheet"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl text-lg leading-none text-stone-500 transition hover:bg-stone-100 hover:text-stone-800">+</button>
          <button type="button" onClick={() => { setMode((m) => (m === 'ask' ? 'deck' : 'ask')); setNotice(null) }}
            title="Toggle presentation mode"
            className={`flex h-9 shrink-0 items-center gap-1.5 rounded-xl px-2.5 text-xs font-medium transition
              ${mode === 'deck' ? 'grad-accent text-white shadow-sm' : 'text-stone-500 hover:bg-stone-100'}`}>
            ✦ <span className="hidden sm:inline">Presentation</span>
          </button>
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={mode === 'deck'
              ? `Describe the presentation… (or leave blank for an overview)`
              : `Ask about ${workbook.file}…`}
            className="ring-accent flex-1 rounded-xl bg-transparent px-2 py-2 text-[15px] outline-none placeholder:text-stone-400" />
          <button disabled={mode === 'deck' ? deckBusy : (busy || !q.trim())}
            className="grad-accent grid h-9 w-9 shrink-0 place-items-center rounded-xl text-white shadow-sm transition hover:opacity-95 disabled:opacity-30"
            title={mode === 'deck' ? 'Generate presentation' : 'Ask'}>
            {(mode === 'deck' ? deckBusy : busy)
              ? <span className="h-2 w-2 animate-ping rounded-full bg-white" />
              : <span className="text-lg leading-none">{mode === 'deck' ? '✦' : '↑'}</span>}
          </button>
        </form>
        <p className="mt-2 text-center text-[11px] text-stone-400">
          {mode === 'deck'
            ? 'Builds a traceable deck — charts and findings, every number linked to its cell.'
            : 'Answers are grounded in the file — each number links to its source cell.'}
        </p>
      </div>

      {viewer && (
        <SheetViewer source={{ workbook: workbook.id }} sheet={viewer.sheet} a1={viewer.a1}
          onClose={() => setViewer(null)} />
      )}
    </div>
  )
}
