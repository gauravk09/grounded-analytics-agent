/**
 * Adding a spreadsheet, as one continuous motion: upload → read → answer a few questions → done.
 *
 * It lives INSIDE the conversation rather than on a separate screen, because that is what it is —
 * the assistant asking you what it could not work out for itself. Splitting it into its own page
 * made it feel like configuration; here it reads as a short exchange.
 *
 * What gets asked is only what the file cannot say (D62). Layout — header row, data rows, value
 * columns — is worked out by counting and merely SHOWN, because you can check it by looking.
 * Meaning cannot be checked by looking and cannot be derived at all, so it is asked.
 */
import { useEffect, useRef, useState } from 'react'
import { api, type ModelChoice, type Proposal, type SheetSpec, type Spec } from './api'
import { SheetViewer } from './SheetViewer'

type Phase = 'pick' | 'reading' | 'confirm' | 'ingesting' | 'done'

export function Onboard({ model, onDone, onCancel }: {
  model: ModelChoice
  onDone: (workbook: string) => void
  onCancel: () => void
}) {
  const [phase, setPhase] = useState<Phase>('pick')
  const [files, setFiles] = useState<string[]>([])
  const [file, setFile] = useState('')
  const [p, setP] = useState<Proposal | null>(null)
  const [spec, setSpec] = useState<Spec | null>(null)
  const [counts, setCounts] = useState<Record<string, number> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showGrid, setShowGrid] = useState<string | null>(null)
  const picker = useRef<HTMLInputElement>(null)
  const [viewer, setViewer] = useState<{ sheet: string; a1: string } | null>(null)

  useEffect(() => { api.files().then((f) => { setFiles(f); setFile(f[0] ?? '') }) }, [])

  async function read(name: string) {
    setPhase('reading'); setError(null)
    try {
      // No model unless a key was supplied. Geometry never needs one; names are proposed only
      // when someone has paid for a model, and are editable either way.
      const prop = await api.propose(name, Boolean(model.api_key), model)
      setP(prop); setSpec(prop.spec); setPhase('confirm')
    } catch (e) { setError((e as Error).message); setPhase('pick') }
  }

  async function upload(f: File) {
    setPhase('reading'); setError(null)
    try {
      const { file: name } = await api.upload(f)
      setFile(name); setFiles(await api.files()); await read(name)
    } catch (e) { setError((e as Error).message); setPhase('pick') }
  }

  async function accept() {
    if (!spec) return
    setPhase('ingesting'); setError(null)
    try {
      const r = await api.confirm(spec)
      setCounts(r.counts); setPhase('done'); onDone(r.workbook)
    } catch (e) { setError((e as Error).message); setPhase('confirm') }
  }

  function patch(name: string, change: Partial<SheetSpec>) {
    setSpec((s) => s && { ...s, sheets: s.sheets.map((sh) => sh.name === name ? { ...sh, ...change } : sh) })
  }

  const grouped = spec?.sheets.filter((s) => 'section_header' in (s.row_kinds ?? {})) ?? []
  const unsure = Object.entries(spec?.confidence ?? {}).filter(([, v]) => v !== 'high')

  const Card = ({ children }: { children: React.ReactNode }) => (
    <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">{children}</div>
  )

  return (
    <div className="mx-auto w-full max-w-3xl space-y-4 py-6">
      {error && <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm
                                text-red-800">{error}</div>}

      {phase === 'pick' && (
        <Card>
          <h2 className="mb-1 text-lg font-semibold">Add a spreadsheet</h2>
          <p className="mb-4 text-sm text-stone-500">
            I'll work out its layout by looking at the shape of the sheet, then ask you the few
            things the file itself can't tell me.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <button onClick={() => picker.current?.click()}
              className="rounded-lg bg-stone-900 px-4 py-2 text-sm text-white">
              Choose a file…
            </button>
            <input ref={picker} type="file" accept=".xlsx,.xlsm,.csv,.tsv" className="hidden"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            {files.length > 0 && (
              <>
                <span className="text-sm text-stone-400">or one already here:</span>
                <select value={file} onChange={(e) => setFile(e.target.value)}
                  className="rounded border border-stone-300 px-2 py-1.5 text-sm">
                  {files.map((f) => <option key={f}>{f}</option>)}
                </select>
                <button onClick={() => read(file)}
                  className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm">
                  Read it
                </button>
              </>
            )}
            <button onClick={onCancel} className="ml-auto text-sm text-stone-500 underline">
              Cancel
            </button>
          </div>
        </Card>
      )}

      {(phase === 'reading' || phase === 'ingesting') && (
        <Card>
          <div className="flex items-center gap-3">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-stone-300
                             border-t-stone-900" />
            <span className="text-sm">
              {phase === 'reading'
                ? 'Reading the shape of every sheet…'
                : 'Reading every cell, and writing down where each one came from…'}
            </span>
          </div>
        </Card>
      )}

      {phase === 'confirm' && spec && p && (
        <>
          <Card>
            <h2 className="mb-3 text-lg font-semibold">Here's what I found in {spec.file}</h2>
            <ul className="space-y-1.5 text-sm">
              {spec.sheets.map((sh) => (
                <li key={sh.name}>
                  <button onClick={() => setShowGrid(showGrid === sh.name ? null : sh.name)}
                    className="text-left hover:underline">
                    <span className="font-medium">{sh.name}</span>
                    <span className="text-stone-500">
                      {' '}— headings on row {sh.header_row},{' '}
                      {sh.last_data_row - sh.first_data_row + 1} rows of data, names in column{' '}
                      {sh.label_column}, numbers in {sh.first_value_column}–{sh.last_value_column}
                    </span>
                  </button>
                  <button onClick={() => setViewer({ sheet: sh.name, a1: `${sh.first_value_column}${sh.first_data_row}` })}
                    className="mono ml-2 rounded px-1 text-xs text-orange-600 underline decoration-orange-300 underline-offset-2 hover:bg-orange-50">
                    open the sheet ↗
                  </button>
                  {showGrid === sh.name && (
                    <pre className="mt-2 max-h-72 overflow-auto rounded bg-stone-50 p-2
                                    text-[11px] leading-tight">{p.grids[sh.name]}</pre>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-stone-400">
              Click a sheet to see it. Layout is worked out by counting — no model involved.
            </p>
            {unsure.length > 0 && (
              <p className="mt-2 text-xs text-amber-700">
                Less sure about: {unsure.map(([k, v]) => `${k} (${v})`).join(' · ')}
              </p>
            )}
          </Card>

          <Card>
            <h2 className="mb-1 text-lg font-semibold">A few things the file doesn't say</h2>
            <p className="mb-4 text-sm text-stone-500">
              None of these appear in any cell, so I can't work them out — but they decide how your
              questions are understood.
            </p>

            <div className="grid gap-3 sm:grid-cols-4">
              {([['entity', 'Each row is a…', 'state, item, account'],
                 ['period', 'Each column is a…', 'year, quarter'],
                 ['measure', 'The numbers are…', 'value, amount'],
                 ['unit', 'Unit', 'tonnes, ₹ crore']] as const).map(([k, label, ph]) => (
                <label key={k} className="text-sm">
                  <div className="mb-1 text-stone-500">{label}</div>
                  <input value={(spec[k] as string) ?? ''} placeholder={ph}
                    onChange={(e) => setSpec({ ...spec, [k]: e.target.value })}
                    className="w-full rounded border border-stone-300 px-2 py-1.5 text-sm" />
                </label>
              ))}
            </div>

            {spec.sheets.length > 1 && (
              <div className="mt-5">
                <div className="mb-2 text-sm font-medium">
                  These sheets look like variants of each other — what's different about them?
                </div>
                <div className="grid gap-3 sm:grid-cols-3">
                  {spec.sheets.map((sh) => (
                    <label key={sh.name} className="text-sm">
                      <div className="mb-1 truncate text-stone-500" title={sh.name}>{sh.name}</div>
                      <input placeholder="e.g. product=MS"
                        value={Object.entries(sh.constants).map(([k, v]) => `${k}=${v}`).join(', ')}
                        onChange={(e) => patch(sh.name, {
                          constants: Object.fromEntries(
                            e.target.value.split(',').map((x) => x.trim())
                              .filter((x) => x.includes('='))
                              .map((x) => [x.slice(0, x.indexOf('=')), x.slice(x.indexOf('=') + 1)])),
                        })}
                        className="w-full rounded border border-stone-300 px-2 py-1.5 font-mono text-xs" />
                    </label>
                  ))}
                </div>
              </div>
            )}

            {grouped.length > 0 && (
              <div className="mt-5 text-sm">
                <div className="mb-1">
                  Rows are grouped under headings like{' '}
                  <b>{p.examples[grouped[0].name] ?? 'a heading'}</b> — those headings are a…
                </div>
                <input placeholder="region, department, category"
                  value={grouped[0].section_dimension ?? ''}
                  onChange={(e) => setSpec({
                    ...spec,
                    sheets: spec.sheets.map((sh) => grouped.some((g) => g.name === sh.name)
                      ? { ...sh, section_dimension: e.target.value || null } : sh),
                  })}
                  className="w-full max-w-md rounded border border-stone-300 px-2 py-1.5 text-sm" />
                <p className="mt-1 text-xs text-stone-400">
                  Without a name for the grouping, every grouped row loses one of its citations.
                </p>
              </div>
            )}

            <div className="mt-6 flex items-center gap-3">
              <button onClick={accept}
                className="rounded-lg bg-emerald-700 px-4 py-2 text-sm text-white">
                That's right — add it
              </button>
              <button onClick={onCancel} className="text-sm text-stone-500 underline">Cancel</button>
            </div>
          </Card>
        </>
      )}

      {phase === 'done' && counts && (
        <Card>
          <h2 className="mb-1 text-lg font-semibold">Added.</h2>
          <p className="text-sm text-stone-600">
            {counts.rows.toLocaleString()} rows · {counts.receipts.toLocaleString()} source-cell
            receipts · {counts.formulas.toLocaleString()} formulas kept
          </p>
          <p className="mt-2 text-xs text-stone-400">
            Every one of those receipts records the sheet and cell a number came from. Ask away.
          </p>
        </Card>
      )}

      {viewer && spec && (
        <SheetViewer source={{ file: spec.file }} sheet={viewer.sheet} a1={viewer.a1}
          onClose={() => setViewer(null)} />
      )}
    </div>
  )
}
