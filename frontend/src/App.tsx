/**
 * Shell: sources on the left, one conversation in the middle.
 *
 * Choosing a source is not cosmetic. Each workbook has its own catalog, its own table, its own
 * absent-concepts list and its own compiler defaults — so it changes what the model can see, what
 * SQL compiles against, and what gets refused. Switching also drops the conversation: server
 * memory is keyed `session:workbook`, so a transcript from another file would otherwise sit above
 * an empty memory (D62).
 */
import { useEffect, useState } from 'react'
import { api, type ModelChoice, type Workbook } from './api'
import { Chat, type Turn } from './Chat'
import { Onboard } from './Onboard'
import { Sources } from './Sources'

const SESSION = Math.random().toString(36).slice(2)

export default function App() {
  const [workbooks, setWorkbooks] = useState<Workbook[]>([])
  const [current, setCurrent] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [adding, setAdding] = useState(false)
  const [providers, setProviders] = useState<Record<string, { env: string; models: string[] }>>({})
  const [model, setModel] = useState<ModelChoice>(
    { provider: 'deepseek', model: '', api_key: '', local_first: false })

  async function refresh(select?: string) {
    const w = (await api.workbooks()).filter((x) => x.ingested)
    setWorkbooks(w)
    setCurrent((c) => select ?? (c || w[0]?.id || ''))
  }

  useEffect(() => { refresh() }, [])
  useEffect(() => {
    api.providers().then((p) => {
      setProviders(p.providers)
      setModel((m) => ({ ...m, provider: p.default, model: p.providers[p.default]?.models[0] ?? '' }))
    })
  }, [])

  useEffect(() => {
    if (!current) return setSuggestions([])
    api.detail(current).then((d) => setSuggestions(d.suggestions)).catch(() => setSuggestions([]))
  }, [current])

  function pick(id: string) {
    if (id === current) return
    setCurrent(id)
    setTurns([])
  }

  async function del(id: string) {
    try { await api.remove(id) } catch (e) { alert((e as Error).message); return }
    if (id === current) { setCurrent(''); setTurns([]) }
    await refresh(id === current ? undefined : current)
  }

  const wb = workbooks.find((w) => w.id === current)
  const ctrl = 'rounded-lg border border-black/10 bg-white/70 px-2.5 py-1.5 text-xs text-stone-700 ring-accent transition hover:bg-white'

  return (
    <div className="flex h-screen flex-col text-stone-900">
      <header className="glass sticky top-0 z-20 flex shrink-0 items-center gap-3 border-b border-black/5 px-4 py-2.5">
        <div className="flex items-center gap-2.5">
          <div className="grad-accent grid h-7 w-7 place-items-center rounded-lg text-sm font-bold text-white shadow-sm">◧</div>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">Grounded</div>
            <div className="-mt-0.5 text-[10px] font-medium tracking-wide text-stone-400 uppercase">Ask your spreadsheet</div>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-1.5">
          <select value={model.provider}
            onChange={(e) => setModel({ ...model, provider: e.target.value, model: providers[e.target.value]?.models[0] ?? '' })}
            className={ctrl}>
            {Object.keys(providers).map((p) => <option key={p}>{p}</option>)}
          </select>
          <select value={model.model} onChange={(e) => setModel({ ...model, model: e.target.value })} className={ctrl}>
            {(providers[model.provider]?.models ?? []).map((m) => <option key={m}>{m}</option>)}
          </select>
          {/* Never read from the server's .env: a demo that silently borrows a stored credential
              shows the reviewer something they did not configure. */}
          <input type="password" placeholder={providers[model.provider]?.env ?? 'API key'}
            value={model.api_key} onChange={(e) => setModel({ ...model, api_key: e.target.value })}
            className={`${ctrl} w-40 font-mono`} />
          <label className="ml-1 flex items-center gap-1.5 text-xs text-stone-500">
            <input type="checkbox" checked={model.local_first}
              onChange={(e) => setModel({ ...model, local_first: e.target.checked })}
              className="accent-orange-600" />
            local
          </label>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <Sources workbooks={workbooks} current={current} onPick={pick} onDelete={del}
          onAdd={() => setAdding(true)} busy={adding} />

        <main className="min-w-0 flex-1 overflow-y-auto">
          {adding ? (
            <div className="px-6">
              <Onboard model={model}
                onDone={(id) => { refresh(id); setTurns([]); setTimeout(() => setAdding(false), 1400) }}
                onCancel={() => setAdding(false)} />
            </div>
          ) : wb ? (
            <Chat workbook={wb} workbooks={workbooks} model={model} session={SESSION}
              turns={turns} setTurns={setTurns} suggestions={suggestions} onPick={pick} />
          ) : (
            <div className="grid h-full place-items-center px-6 text-center">
              <div className="animate-fadeup max-w-md">
                <div className="grad-accent mx-auto grid h-14 w-14 place-items-center rounded-2xl text-2xl text-white shadow-lg">◧</div>
                <h1 className="mt-6 text-3xl font-bold tracking-tight">Grounded Analytics Agent</h1>
                <p className="mt-2 text-sm font-medium text-stone-400">Ask any spreadsheet.</p>
                <p className="mt-3 text-[15px] leading-relaxed text-stone-500">
                  Drop in a file. I read its shape, ask you a couple of things, then answer questions —
                  and every number links back to the exact cell it came from.
                </p>
                <button onClick={() => setAdding(true)}
                  className="grad-accent mt-7 rounded-xl px-5 py-2.5 text-sm font-medium text-white shadow-md transition hover:opacity-95">
                  + Add a spreadsheet
                </button>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}
