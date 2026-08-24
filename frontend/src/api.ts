/**
 * The typed edge of the network.
 *
 * Every field here mirrors something the server already computed. Note what is ABSENT: there is no
 * `narration` and no raw numeric value. The server sends finished sentences (`text`) and finished
 * figures (`formatted`), because a browser that assembled either would become a second place
 * numbers are made — the exact property that makes a hallucinated figure unrepresentable.
 */

export type Citation = {
  sheet: string
  a1: string
  raw_value: string | null
  formula: string | null
}

export type CellRange = { sheet: string; ref: string; cells: string[]; count: number }

export type Slot = {
  name: string
  formatted: string          // already formatted by execute(); never re-formatted here
  unit: string | null
  derivation: string | null  // e.g. "m1 / m2 x 100"
  sql: string | null
  citations: Citation[]
  ranges: CellRange[]        // contiguous cells collapsed into blocks (S10:S19)
  parts: Slot[]              // derived values carry their operands, so lineage is a tree
}

export type Answer = {
  question: string
  status: 'answered' | 'abstained' | 'clarify'
  text: string
  echo: string | null
  sql: string | null
  scope_options: string[]
  slots: Slot[]
  citation_count: number
}

export type Detail = {
  spec: Spec
  notes: { sheet: string; a1: string; text: string }[]
  columns: { name: string; description: string; labels: string[] | null; aliases: string[] }[]
  absent: string[]
  suggestions: string[]        // generated from THIS file's labels, never hardcoded
  unknown_annotations: string[]
}

export type SheetCell = { a1: string; r: number; c: number; v: string; f: string | null }
export type Sheet = { sheet: string; max_row: number; max_col: number; cells: SheetCell[] }
export type DeckSlide = { text: string; cells: { sheet: string; a1: string }[] }
export type Deck = { title: string; subtitle: string; closing: string; slides: DeckSlide[]; pptx: string }

export type Workbook = {
  id: string
  file: string
  table: string
  entity: string
  period: string
  measure: string
  unit: string | null
  sheets: string[]
  ingested: boolean
}

export type SheetSpec = {
  name: string
  header_row: number
  label_column: string
  alt_label_columns: string[]
  first_data_row: number
  last_data_row: number
  first_value_column: string
  last_value_column: string
  note_rows: number[]
  row_kinds: Record<string, string>
  section_dimension: string | null
  constants: Record<string, string>
}

export type Spec = {
  file: string
  table: string
  entity: string
  period: string
  measure: string
  unit: string | null
  sheets: SheetSpec[]
  annotations: Record<string, unknown>
  absent_concepts: string[]
  confidence: Record<string, string>
  notes_for_reviewer: string[]
}

export type Proposal = {
  spec: Spec
  grids: Record<string, string>       // sheet -> shape sketch. Structure is SHOWN, not asked.
  examples: Record<string, string | null>
}

export type ModelChoice = {
  provider: string
  model: string
  api_key: string
  local_first: boolean
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    // FastAPI puts the reason in `detail`. Surfacing it beats a bare status code: "no workbook
    // 'budget'" is actionable, "422" is not.
    const msg = await r.json().catch(() => null)
    throw new Error(msg?.detail ?? `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export const api = {
  workbooks: () => call<Workbook[]>('/api/workbooks'),
  detail: (id: string) => call<Detail>(`/api/workbooks/${id}`),
  files: () => call<string[]>('/api/files'),
  providers: () => call<{ default: string; providers: Record<string, { env: string; models: string[] }> }>('/api/providers'),

  ask: (question: string, workbook: string, session: string, m: ModelChoice) =>
    call<Answer>('/api/ask', { question, workbook, session, ...m }),

  propose: (file: string, use_model: boolean, m: ModelChoice) =>
    call<Proposal>('/api/propose', { file, use_model, ...m }),

  confirm: (spec: Spec) =>
    call<{ workbook: string; counts: Record<string, number> }>('/api/confirm', { spec }),

  clear: (session: string) => call<unknown>(`/api/session/${session}/clear`, {}),

  sheet: (source: { workbook?: string; file?: string }, sheet: string) => {
    const q = new URLSearchParams({ sheet })
    if (source.workbook) q.set('workbook', source.workbook)
    if (source.file) q.set('file', source.file)
    return call<Sheet>(`/api/sheet?${q.toString()}`)
  },

  // Returns the deck as JSON so the client can show it in-window (each slide's number stays welded
  // to its cells). The .pptx is built server-side too and downloaded on demand via `deck.pptx`.
  deck: async (workbook: string, goal: string, m: ModelChoice): Promise<Deck> => {
    const r = await fetch('/api/deck', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ workbook, goal, ...m }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? 'deck failed')
    return r.json()
  },

  remove: async (id: string) => {
    const r = await fetch(`/api/workbooks/${encodeURIComponent(id)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? 'delete failed')
    return r.json() as Promise<{ deleted: string }>
  },

  upload: async (f: File) => {
    const fd = new FormData()
    fd.append('f', f)
    const r = await fetch('/api/upload', { method: 'POST', body: fd })
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? 'upload failed')
    return r.json() as Promise<{ file: string }>
  },
}
