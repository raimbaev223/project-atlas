/**
 * Project Atlas — desktop half.
 *
 * Two tabs over ONE file: <project>/.atlas/atlas.yaml. The graph is a radial tree
 * computed from `parent`; the Documentation tab derives every page from the same
 * nodes. Nothing here knows a project's stack. All disk and model work lives in
 * dashboard/plugin_api.py → atlas_core.
 *
 * Plain ESM loaded uncompiled: jsx()/jsxs() calls, never JSX syntax. Only
 * @hermes/plugin-sdk, react and react/jsx-runtime resolve.
 */

import {
  Badge,
  Button,
  Codicon,
  ErrorState,
  GlyphSpinner,
  PALETTE_AREA,
  ROUTES_AREA,
  ScrollArea,
  SearchField,
  SIDEBAR_NAV_AREA,
  Switch,
  Tip,
  host,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'atlas'
const ROUTE = '/atlas'
const NAME = 'Project Atlas'
const STORAGE_KEY = 'project'

const STATUS_LABEL = { full: 'описано полностью', partial: 'описано частично', none: 'без документации' }
const KIND_LABEL = {
  ui: 'интерфейс', service: 'сервис', runtime: 'рантайм', interface: 'точка входа',
  data: 'данные', infra: 'инфраструктура', library: 'библиотека', tool: 'инструмент', other: 'другое'
}
const PHASE_LABEL = { read: 'читаю код', describe: 'описываю узел', empty: 'нет данных', done: 'готово' }

const CSS = `
.at-root{position:relative;display:flex;height:100%;min-height:0;flex-direction:column;color:var(--ui-text-primary);font-size:13px}
.at-head{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;padding:12px 16px;border-bottom:1px solid var(--ui-stroke-secondary);flex-shrink:0}
.at-brand{display:flex;flex-direction:column;gap:1px;min-width:150px}
.at-brand b{font-size:14px;letter-spacing:.02em}
.at-brand span{font-size:11px;color:var(--ui-text-tertiary)}
.at-proj{display:flex;align-items:center;gap:8px;min-width:0}
.at-select{max-width:340px;min-width:190px;background:transparent;border:1px solid var(--ui-stroke-secondary);border-radius:8px;color:var(--ui-text-primary);padding:5px 8px;font:inherit;font-size:12px}
.at-path{background:transparent;border:1px solid var(--ui-stroke-secondary);border-radius:8px;color:var(--ui-text-primary);padding:5px 8px;font:inherit;font-size:12px;width:230px}
.at-path::placeholder{color:var(--ui-text-quaternary)}
.at-tabs{display:inline-flex;gap:2px;padding:2px;border:1px solid var(--ui-stroke-secondary);border-radius:9px;background:color-mix(in srgb,var(--ui-accent) 5%,transparent)}
.at-tab{border:0;background:transparent;color:var(--ui-text-tertiary);font:inherit;font-size:12px;padding:5px 12px;border-radius:7px;cursor:pointer}
.at-tab:hover{color:var(--ui-text-primary)}
.at-tab.on{background:color-mix(in srgb,var(--ui-accent) 18%,transparent);color:var(--ui-text-primary)}
.at-spacer{flex:1}
.at-cov{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--ui-text-tertiary);white-space:nowrap}
.at-dot{display:inline-block;width:7px;height:7px;border-radius:50%;flex:0 0 auto}
.at-dot.full{background:var(--ui-accent)}
.at-dot.partial{background:var(--ui-text-secondary);box-shadow:inset 0 0 0 1px var(--ui-stroke-secondary)}
.at-dot.none{background:transparent;border:1px dashed var(--ui-stroke-secondary)}
.at-dot.stale{background:var(--ui-accent);opacity:.45}
.at-body{position:relative;display:flex;min-height:0;flex:1}
.at-graph-wrap{display:grid;grid-template-columns:minmax(0,1fr) 300px;height:100%;min-height:0;width:100%}
@media (max-width:920px){.at-graph-wrap{grid-template-columns:1fr;grid-template-rows:minmax(320px,1fr) auto}}
.at-stage{position:relative;min-height:0;overflow:hidden}
.at-svg{width:100%;height:100%;display:block;touch-action:none;cursor:grab;user-select:none}
.at-svg.drag{cursor:grabbing}
.at-edge{stroke:var(--ui-stroke-secondary);stroke-width:1.1;fill:none}
.at-edge.full{stroke:color-mix(in srgb,var(--ui-accent) 45%,transparent)}
.at-edge.partial{stroke:color-mix(in srgb,var(--ui-text-secondary) 55%,transparent)}
.at-edge.none{stroke-dasharray:2 4;opacity:.7}
.at-link{stroke:color-mix(in srgb,var(--ui-accent) 60%,transparent);stroke-width:1;fill:none;stroke-dasharray:3 4;opacity:.65}
.at-center-ring{fill:color-mix(in srgb,var(--ui-accent) 10%,transparent);stroke:var(--ui-accent);stroke-width:2}
.at-center-core{fill:var(--ui-accent);opacity:.9}
.at-center-label{fill:var(--ui-text-primary);font-size:11.5px;font-weight:600}
.at-node{cursor:pointer;outline:none}
.at-node .halo{fill:transparent;stroke-width:1}
.at-node .core{fill:var(--ui-surface,transparent)}
.at-node.full .ring{stroke:var(--ui-accent);fill:color-mix(in srgb,var(--ui-accent) 16%,transparent)}
.at-node.partial .ring{stroke:var(--ui-text-secondary);fill:color-mix(in srgb,var(--ui-text-secondary) 14%,transparent)}
.at-node.none .ring{stroke:var(--ui-stroke-secondary);stroke-dasharray:3 3;fill:transparent}
.at-node.stale .halo{stroke:var(--ui-accent);stroke-dasharray:2 3;opacity:.5}
.at-node.sel .ring{stroke:var(--ui-text-primary);stroke-width:2.6}
.at-node:hover .ring{filter:brightness(1.25)}
.at-node:focus-visible .ring{stroke:var(--ui-text-primary);stroke-width:3}
.at-label{fill:var(--ui-text-secondary);font-size:10.5px;pointer-events:none}
.at-label.d1{fill:var(--ui-text-primary);font-size:12px;font-weight:600}
.at-label.d2{fill:var(--ui-text-secondary);font-size:10.5px}
.at-zoom{position:absolute;left:12px;bottom:12px;display:flex;gap:4px;z-index:4}
.at-zbtn{width:26px;height:26px;border:1px solid var(--ui-stroke-secondary);border-radius:7px;background:color-mix(in srgb,var(--ui-accent) 6%,transparent);color:var(--ui-text-secondary);cursor:pointer;font:inherit;line-height:1}
.at-zbtn:hover{color:var(--ui-text-primary);background:color-mix(in srgb,var(--ui-accent) 14%,transparent)}
.at-legend{position:absolute;left:12px;top:10px;display:flex;flex-wrap:wrap;gap:10px;font-size:10.5px;color:var(--ui-text-tertiary);z-index:3;pointer-events:none}
.at-legend span{display:inline-flex;align-items:center;gap:5px}
.at-side{display:flex;flex-direction:column;min-height:0;border-left:1px solid var(--ui-stroke-secondary)}
@media (max-width:920px){.at-side{border-left:0;border-top:1px solid var(--ui-stroke-secondary);max-height:260px}}
.at-side-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:10px 12px;border-bottom:1px solid var(--ui-stroke-secondary)}
.at-side-head b{font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--ui-text-tertiary)}
.at-card{padding:12px;display:flex;flex-direction:column;gap:9px;min-height:0}
.at-card h3{margin:0;font-size:14px}
.at-card p{margin:0;color:var(--ui-text-secondary);line-height:1.5}
.at-card .at-crumb{font-size:10.5px;color:var(--ui-text-tertiary)}
.at-meta{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.at-code{font-family:var(--ui-font-mono,ui-monospace,SFMono-Regular,Menlo,monospace);font-size:11px;background:color-mix(in srgb,var(--ui-text-primary) 6%,transparent);border:1px solid var(--ui-stroke-secondary);border-radius:4px;padding:1px 5px;word-break:break-all}
.at-chips{display:flex;flex-wrap:wrap;gap:5px}
.at-chip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--ui-stroke-secondary);border-radius:999px;padding:2px 8px;font-size:11px;color:var(--ui-text-secondary);background:transparent;cursor:pointer;font:inherit}
.at-chip:hover{color:var(--ui-text-primary);border-color:color-mix(in srgb,var(--ui-accent) 45%,transparent)}
.at-docs{display:grid;grid-template-columns:250px minmax(0,1fr);height:100%;min-height:0;width:100%}
@media (max-width:820px){.at-docs{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}}
.at-tree{border-right:1px solid var(--ui-stroke-secondary);min-height:0;display:flex;flex-direction:column}
.at-tree-search{padding:8px 8px 6px}
.at-tree-list{overflow:auto;padding:2px 6px 12px;min-height:0}
.at-tree-row{display:flex;align-items:center;gap:6px;width:100%;text-align:left;border:0;background:transparent;color:var(--ui-text-secondary);font:inherit;font-size:12px;padding:4px 6px;border-radius:6px;cursor:pointer;border-left:2px solid transparent}
.at-tree-row:hover{background:color-mix(in srgb,var(--ui-accent) 7%,transparent);color:var(--ui-text-primary)}
.at-tree-row.on{background:color-mix(in srgb,var(--ui-accent) 13%,transparent);color:var(--ui-text-primary);border-left-color:var(--ui-accent)}
.at-tree-row .nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.at-tree-row .st{margin-left:auto;font-size:10px;color:var(--ui-text-quaternary)}
.at-page{min-height:0;overflow:auto;padding:18px 22px 28px}
.at-page-inner{max-width:860px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
.at-page h2{margin:0;font-size:20px;line-height:1.25}
.at-page h4{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ui-text-tertiary)}
.at-page .at-crumb{font-size:11px;color:var(--ui-text-tertiary)}
.at-sec{border-top:1px solid var(--ui-stroke-secondary);padding-top:12px}
.at-md{line-height:1.65;color:var(--ui-text-secondary);font-size:12.5px}
.at-md p{margin:0 0 8px}
.at-md h1,.at-md h2,.at-md h3{color:var(--ui-text-primary);margin:12px 0 6px;font-size:14px}
.at-md ul{margin:0 0 8px;padding-left:20px}
.at-md li{margin:2px 0}
.at-md pre{margin:6px 0 10px;padding:9px 10px;border:1px solid var(--ui-stroke-secondary);border-radius:7px;overflow:auto;background:color-mix(in srgb,var(--ui-text-primary) 4%,transparent)}
.at-md code{font-family:var(--ui-font-mono,ui-monospace,Menlo,monospace);font-size:11.5px;background:color-mix(in srgb,var(--ui-text-primary) 6%,transparent);border:1px solid var(--ui-stroke-secondary);border-radius:4px;padding:0 4px}
.at-md pre code{border:0;background:transparent;padding:0}
.at-dec{border-left:2px solid var(--ui-accent);padding:4px 10px;margin:0 0 8px;background:color-mix(in srgb,var(--ui-accent) 5%,transparent)}
.at-dec b{display:block;color:var(--ui-text-primary);margin-bottom:2px}
.at-dec .why{color:var(--ui-text-secondary)}
.at-ev{display:flex;flex-wrap:wrap;gap:4px;margin-top:5px}
.at-todo{margin:0;padding-left:18px;color:var(--ui-text-secondary);line-height:1.55}
.at-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;height:100%;padding:30px;text-align:center;color:var(--ui-text-tertiary)}
.at-empty b{color:var(--ui-text-primary);font-size:14px}
.at-banner{margin:8px 16px 0;border:1px solid color-mix(in srgb,var(--ui-accent) 35%,var(--ui-stroke-secondary));border-radius:9px;padding:8px 11px;display:flex;flex-direction:column;gap:6px;background:color-mix(in srgb,var(--ui-accent) 5%,transparent);flex-shrink:0}
.at-bar{height:4px;border-radius:3px;background:var(--ui-stroke-secondary);overflow:hidden}
.at-bar i{display:block;height:100%;background:var(--ui-accent);transition:width .25s ease}
.at-banner-row{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--ui-text-secondary)}
.at-plan{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);width:min(720px,calc(100% - 24px));z-index:20;border:1px solid color-mix(in srgb,var(--ui-accent) 45%,var(--ui-stroke-secondary));border-radius:12px;padding:12px 14px;background:color-mix(in srgb,var(--ui-surface,var(--ui-text-primary)) 4%,var(--ui-bg,transparent));backdrop-filter:blur(14px);box-shadow:0 10px 34px color-mix(in srgb,var(--ui-text-primary) 18%,transparent);display:flex;flex-direction:column;gap:8px}
.at-plan h4{margin:0;font-size:12px;color:var(--ui-text-primary)}
.at-plan .nums{display:flex;flex-wrap:wrap;gap:12px;font-size:11.5px;color:var(--ui-text-secondary)}
.at-plan .ids{max-height:92px;overflow:auto;font-size:11px;color:var(--ui-text-tertiary);display:flex;flex-wrap:wrap;gap:4px}
.at-plan .acts{display:flex;justify-content:flex-end;gap:6px}
.at-orphan{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--ui-text-tertiary);padding:2px 0}
.at-note{font-size:11.5px;color:var(--ui-text-tertiary)}
.at-lock{display:flex;align-items:center;gap:7px;font-size:11.5px;color:var(--ui-text-secondary)}
.at-err{margin:12px 16px;padding:10px;border:1px solid color-mix(in srgb,var(--ui-danger,var(--ui-text-secondary)) 45%,var(--ui-stroke-secondary));border-radius:8px;font-size:12px;color:var(--ui-text-secondary)}
`

// ─── small helpers ────────────────────────────────────────────────────────────

function basename(path) {
  const parts = String(path || '').split('/').filter(Boolean)
  return parts[parts.length - 1] || path
}

function trimLabel(text, max = 20) {
  const value = String(text || '')
  return value.length > max ? value.slice(0, max - 1) + '…' : value
}

function coverageText(cov) {
  if (!cov || !cov.total) return 'карты пока нет'
  return `${cov.full}/${cov.total} полностью · ${cov.partial} частично · ${cov.none} без описания`
}

function statusDot(status, stale) {
  return jsx('span', { className: `at-dot ${status || 'none'}`, title: STATUS_LABEL[status] || '' })
}

function indexNodes(nodes) {
  const map = new Map()
  for (const node of nodes || []) map.set(node.id, node)
  return map
}

function buildChildren(nodes) {
  const map = new Map()
  for (const node of nodes || []) {
    const parent = node.parent && node.parent !== 'root' ? node.parent : 'root'
    if (!map.has(parent)) map.set(parent, [])
    map.get(parent).push(node)
  }
  return map
}

function breadcrumbOf(node, index) {
  const chain = [node]
  let cur = node
  let guard = 0
  while (cur && cur.parent && cur.parent !== 'root' && index.has(cur.parent) && guard < 12) {
    cur = index.get(cur.parent)
    if (chain.includes(cur)) break
    chain.push(cur)
    guard += 1
  }
  return chain.reverse()
}

function nodeDepth(node, index) {
  let depth = 1
  let cur = node
  let guard = 0
  while (cur && cur.parent && cur.parent !== 'root' && index.has(cur.parent) && guard < 12) {
    cur = index.get(cur.parent)
    depth += 1
    guard += 1
  }
  return Math.min(depth, 4)
}

function countLeaves(node, children) {
  const kids = children.get(node.id) || []
  if (!kids.length) return 1
  return kids.reduce((sum, child) => sum + countLeaves(child, children), 0)
}

function radialLayout(nodes, width, height) {
  const index = indexNodes(nodes)
  const children = buildChildren(nodes)
  const tops = (nodes || []).filter((n) => !n.parent || n.parent === 'root' || !index.has(n.parent))
  const positions = new Map()
  if (!tops.length) return { positions, width, height, children }
  const cx = width / 2
  const cy = height / 2
  const radius = Math.max(120, Math.min(width, height) / 2 - 26)
  const rings = [0, radius * 0.34, radius * 0.55, radius * 0.73, radius * 0.88]

  const place = (node, a0, a1, depth) => {
    const mid = (a0 + a1) / 2
    const r = rings[Math.min(depth, 4)]
    positions.set(node.id, {
      x: cx + Math.cos(mid) * r,
      y: cy + Math.sin(mid) * r,
      depth: Math.min(depth, 4),
      angle: mid
    })
    const kids = children.get(node.id) || []
    if (!kids.length) return
    const total = kids.reduce((sum, child) => sum + countLeaves(child, children), 0)
    let start = a0
    for (const child of kids) {
      const portion = (a1 - a0) * (countLeaves(child, children) / Math.max(1, total))
      place(child, start, start + portion, depth + 1)
      start += portion
    }
  }

  if (tops.length === 1) {
    place(tops[0], -Math.PI / 2 - 0.95, -Math.PI / 2 + 0.95, 1)
  } else {
    const span = Math.PI * 2 * 0.93
    let start = -Math.PI / 2 - span / 2
    const total = tops.reduce((sum, node) => sum + countLeaves(node, children), 0)
    for (const node of tops) {
      const portion = span * (countLeaves(node, children) / Math.max(1, total))
      place(node, start, start + portion, 1)
      start += portion
    }
  }
  return { positions, width, height, children }
}

function inlineMarkdown(text) {
  const parts = String(text || '').split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
  return parts.map((part, i) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return jsx('code', { children: part.slice(1, -1) }, `c${i}`)
    }
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      return jsx('strong', { children: part.slice(2, -2) }, `b${i}`)
    }
    return part
  })
}

function Markdown({ text }) {
  if (!text) return null
  const blocks = []
  let paragraph = []
  let list = null
  let code = null
  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push(jsx('p', { children: inlineMarkdown(paragraph.join(' ')) }, `p${blocks.length}`))
      paragraph = []
    }
  }
  const flushList = () => {
    if (list) {
      blocks.push(jsx('ul', { children: list }, `u${blocks.length}`))
      list = null
    }
  }
  for (const line of String(text).split('\n')) {
    if (code !== null) {
      if (line.trim().startsWith('```')) {
        blocks.push(jsx('pre', { children: jsx('code', { children: code.join('\n') }) }, `pre${blocks.length}`))
        code = null
      } else {
        code.push(line)
      }
      continue
    }
    if (line.trim().startsWith('```')) {
      flushParagraph()
      flushList()
      code = []
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      blocks.push(jsx('h3', { children: inlineMarkdown(heading[2]) }, `h${blocks.length}`))
      continue
    }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line)
    if (bullet) {
      flushParagraph()
      if (!list) list = []
      list.push(jsx('li', { children: inlineMarkdown(bullet[1]) }, `l${list.length}`))
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }
    flushList()
    paragraph.push(line.trim())
  }
  flushParagraph()
  flushList()
  if (code !== null) {
    blocks.push(jsx('pre', { children: jsx('code', { children: code.join('\n') }) }, `pre${blocks.length}`))
  }
  return jsx('div', { className: 'at-md', children: blocks })
}

// ─── page ─────────────────────────────────────────────────────────────────────

function AtlasPage({ ctx }) {
  const queryClient = useQueryClient()
  const profile = useValue(host.state.focusedSessionProfile) || useValue(host.state.profile) || 'default'
  const cwd = useValue(host.state.cwd) || ''

  const [project, setProject] = useState(() => {
    try {
      return ctx.storage.get(STORAGE_KEY, '') || ''
    } catch {
      return ''
    }
  })
  const [manual, setManual] = useState('')
  const [showManual, setShowManual] = useState(false)
  const [tab, setTab] = useState('tree')
  const [selected, setSelected] = useState('')
  const [query, setQuery] = useState('')
  const [showLinks, setShowLinks] = useState(true)
  const [jobId, setJobId] = useState('')
  const [plan, setPlan] = useState(null)
  const [actionError, setActionError] = useState('')

  const projectsQuery = useQuery({
    queryKey: ['atlas', 'projects', profile],
    queryFn: () => host.request('projects.list', { profile }),
    enabled: Boolean(profile),
    retry: 0
  })

  const folders = useMemo(() => {
    const out = []
    for (const item of projectsQuery.data?.projects || []) {
      for (const folder of item.folders || []) {
        out.push({ path: folder.path, label: `${item.name} · ${basename(folder.path)}`, project: item.name })
      }
    }
    return out
  }, [projectsQuery.data])

  const candidatesQuery = useQuery({
    queryKey: ['atlas', 'candidates', folders.map((f) => f.path).join('|')],
    queryFn: async () => {
      const out = []
      for (const folder of folders) {
        try {
          const answer = await ctx.rest(`/candidates?root=${encodeURIComponent(folder.path)}`)
          for (const item of answer.items || []) {
            out.push({ path: item.path, label: `${folder.project} · ${item.path.replace(folder.path + '/', '')}` })
          }
        } catch {
          // a missing backend only hides candidates; the folder itself stays selectable
        }
      }
      return out
    },
    enabled: folders.length > 0,
    retry: 0,
    staleTime: 60_000
  })

  const options = useMemo(() => {
    const seen = new Set()
    const out = []
    for (const item of [...folders, ...((candidatesQuery.data && candidatesQuery.data.items) || [])]) {
      if (!item?.path || seen.has(item.path)) continue
      seen.add(item.path)
      out.push(item)
    }
    return out
  }, [folders, candidatesQuery.data])

  useEffect(() => {
    if (project) return
    if (!options.length) return
    const under = options
      .filter((item) => cwd && (cwd === item.path || cwd.startsWith(item.path + '/')))
      .sort((a, b) => b.path.length - a.path.length)[0]
    const next = under || options[0]
    setProject(next.path)
  }, [project, options, cwd])

  const stateQuery = useQuery({
    queryKey: ['atlas', 'state', project],
    queryFn: () => ctx.rest(`/state?path=${encodeURIComponent(project)}&stale=1`),
    enabled: Boolean(project),
    retry: 0,
    staleTime: 5_000
  })
  const state = stateQuery.data
  const nodes = state?.nodes || []
  const stale = state?.stale || {}
  const index = useMemo(() => indexNodes(nodes), [nodes])

  const jobQuery = useQuery({
    queryKey: ['atlas', 'job', jobId],
    queryFn: () => ctx.rest(`/job/${jobId}`),
    enabled: Boolean(jobId),
    retry: 0,
    refetchInterval: 1200
  })
  const job = jobQuery.data?.job

  useEffect(() => {
    if (!job) return
    if (job.status !== 'done' && job.status !== 'error') return
    setJobId('')
    queryClient.invalidateQueries({ queryKey: ['atlas', 'state', project] })
    if (job.status === 'done') {
      host.notify({ kind: 'success', message: `Project Atlas: ${job.result?.updated?.length || 0} узлов обновлено` })
      if (!selected && job.result?.updated?.length === 1) setSelected(job.result.updated[0])
    } else {
      host.notify({ kind: 'error', message: `Project Atlas: ${job.error || 'сборка не удалась'}` })
    }
  }, [job, project, queryClient, selected])

  const chooseProject = (path) => {
    setProject(path)
    setSelected('')
    setPlan(null)
    setActionError('')
    try {
      ctx.storage.set(STORAGE_KEY, path)
    } catch {
      // storage is optional
    }
  }

  const refresh = () => {
    setActionError('')
    queryClient.invalidateQueries({ queryKey: ['atlas', 'state', project] })
  }

  const runScan = async () => {
    setActionError('')
    try {
      await ctx.rest('/scan', { method: 'POST', body: { path: project } })
      refresh()
    } catch (error) {
      setActionError(String(error?.message || error))
    }
  }

  const askBuild = async (scope, deep) => {
    setActionError('')
    try {
      const next = await ctx.rest('/plan', { method: 'POST', body: { path: project, scope } })
      if (!next.nodes?.length) {
        setActionError('Нечего обновлять: в этой области нет узлов.')
        return
      }
      if (next.nodes.length <= 1) {
        await startBuild(scope, next.hash, deep)
        return
      }
      setPlan({ ...next, deep: Boolean(deep) })
    } catch (error) {
      setActionError(String(error?.message || error))
    }
  }

  const startBuild = async (scope, confirm, deep) => {
    setPlan(null)
    setActionError('')
    try {
      const answer = await ctx.rest('/build', {
        method: 'POST',
        body: { path: project, scope, confirm, deep: Boolean(deep) }
      })
      setJobId(answer.job.id)
    } catch (error) {
      const detail = error?.detail || error?.message || error
      setActionError(typeof detail === 'string' ? detail : JSON.stringify(detail))
    }
  }

  const buildAll = () => askBuild('all', !state?.exists)
  const buildStale = () => askBuild('stale', false)
  const buildNode = (nodeId) => askBuild(`node:${nodeId}`, false)

  const toggleLock = async (nodeId, locked) => {
    try {
      await ctx.rest('/lock', { method: 'POST', body: { path: project, node_id: nodeId, locked } })
      refresh()
    } catch (error) {
      setActionError(String(error?.message || error))
    }
  }

  const selectedNode = selected ? index.get(selected) : null

  return jsxs('div', {
    className: 'at-root',
    children: [
      jsx('style', { children: CSS }),
      jsxs('header', {
        className: 'at-head',
        children: [
          jsxs('div', {
            className: 'at-brand',
            children: [
              jsx('b', { children: NAME }),
              jsx('span', { children: 'карта проекта и документация из кода' })
            ]
          }),
          jsxs('div', {
            className: 'at-proj',
            children: [
              jsx('select', {
                className: 'at-select',
                value: project,
                onChange: (event) => chooseProject(event.target.value),
                title: project || 'выберите проект',
                children: options.length
                  ? options.map((item) => jsx('option', { value: item.path, children: item.label }, item.path))
                  : jsx('option', { value: '', children: 'нет проектов в профиле' })
              }),
              jsx(Button, {
                variant: 'outline',
                size: 'xs',
                onClick: () => setShowManual((value) => !value),
                children: 'путь…'
              })
            ]
          }),
          showManual
            ? jsxs('div', {
                className: 'at-proj',
                children: [
                  jsx('input', {
                    className: 'at-path',
                    placeholder: '/абсолютный/путь',
                    value: manual,
                    onChange: (event) => setManual(event.target.value),
                    onKeyDown: (event) => {
                      if (event.key === 'Enter' && manual.trim()) chooseProject(manual.trim())
                    }
                  }),
                  jsx(Button, {
                    size: 'xs',
                    onClick: () => manual.trim() && chooseProject(manual.trim()),
                    children: 'Открыть'
                  })
                ]
              })
            : null,
          jsx('div', { className: 'at-spacer' }),
          jsxs('div', {
            className: 'at-tabs',
            children: [
              jsx('button', {
                className: `at-tab ${tab === 'tree' ? 'on' : ''}`,
                onClick: () => setTab('tree'),
                children: 'Древо навыков'
              }),
              jsx('button', {
                className: `at-tab ${tab === 'docs' ? 'on' : ''}`,
                onClick: () => setTab('docs'),
                children: 'Документация'
              })
            ]
          }),
          jsxs('div', {
            className: 'at-cov',
            children: [
              statusDot('full'),
              jsx('span', { children: coverageText(state?.coverage) }),
              state?.exists
                ? jsx(Tip, {
                    label: state.facts?.files ? `${state.facts.files} файлов · ${state.facts.manifests} манифестов` : 'факты не собраны',
                    children: jsx('span', { children: '·' })
                  })
                : null
            ]
          }),
          jsx(Button, { variant: 'outline', size: 'xs', onClick: runScan, children: 'Пересканировать' }),
          state?.exists
            ? jsx(Button, { size: 'xs', onClick: buildStale, children: 'Обновить устаревшие' })
            : jsx(Button, { size: 'xs', onClick: buildAll, children: 'Построить карту' })
        ]
      }),
      actionError ? jsx('div', { className: 'at-err', children: actionError }) : null,
      job ? jsx(JobBanner, { job }) : null,
      stateQuery.isLoading && !state
        ? jsx('div', { className: 'at-empty', children: [jsx(GlyphSpinner, {}), 'читаю карту…'] })
        : stateQuery.error && !state
          ? jsx('div', { className: 'at-empty', children: jsx(ErrorState, { title: 'Не удалось прочитать карту', description: String(stateQuery.error?.message || stateQuery.error) }) })
          : !project
            ? jsx('div', { className: 'at-empty', children: [jsx('b', { children: 'Выберите проект' }), 'Project Atlas покажет его карту и документацию.'] })
            : !state?.exists
              ? jsx(EmptyMap, { project, onBuild: buildAll, onScan: runScan })
              : jsx('div', {
                  className: 'at-body',
                  children: tab === 'tree'
                    ? jsx(GraphTab, {
                        state,
                        selected,
                        onSelect: setSelected,
                        showLinks,
                        onShowLinks: setShowLinks,
                        onBuild: buildNode,
                        onOpenDocs: (nodeId) => {
                          setSelected(nodeId)
                          setTab('docs')
                        }
                      })
                    : jsx(DocsTab, {
                        state,
                        selected,
                        onSelect: setSelected,
                        onBuild: buildNode,
                        onShowGraph: (nodeId) => {
                          setSelected(nodeId)
                          setTab('tree')
                        },
                        onToggleLock: toggleLock,
                        query,
                        onQuery: setQuery
                      })
                }),
      plan ? jsx(PlanPanel, { plan, onCancel: () => setPlan(null), onRun: () => startBuild(plan.scope, plan.hash, plan.deep) }) : null
    ]
  })
}

function EmptyMap({ project, onBuild, onScan }) {
  return jsxs('div', {
    className: 'at-empty',
    children: [
      jsx(Codicon, { name: 'project', size: '1.4rem' }),
      jsx('b', { children: basename(project) }),
      jsx('span', { children: 'Карты ещё нет. Сканер соберёт факты, затем агент опишет ветки проекта в .atlas/atlas.yaml.' }),
      jsxs('div', {
        style: { display: 'flex', gap: '6px', marginTop: '4px' },
        children: [
          jsx(Button, { variant: 'outline', size: 'xs', onClick: onScan, children: 'Только сканировать' }),
          jsx(Button, { size: 'xs', onClick: onBuild, children: 'Построить карту' })
        ]
      })
    ]
  })
}

function JobBanner({ job }) {
  const pct = job.total ? Math.round((job.done / job.total) * 100) : job.status === 'done' ? 100 : 6
  return jsxs('div', {
    className: 'at-banner',
    children: [
      jsxs('div', {
        className: 'at-banner-row',
        children: [
          job.status === 'error' ? null : jsx(GlyphSpinner, {}),
          jsx('span', { children: job.status === 'error' ? 'Ошибка сборки' : job.status === 'done' ? 'Сборка завершена' : PHASE_LABEL[job.phase] || 'работаю' }),
          jsx('span', { style: { color: 'var(--ui-text-quaternary)' }, children: job.node ? `· ${job.node}` : '' }),
          jsx('span', { style: { marginLeft: 'auto' }, children: job.status === 'error' ? job.error : `${job.done}/${job.total || '?'}` })
        ]
      }),
      jsx('div', { className: 'at-bar', children: jsx('i', { style: { width: `${Math.min(100, pct)}%` } }) })
    ]
  })
}

// ─── graph tab ────────────────────────────────────────────────────────────────

function GraphTab({ state, selected, onSelect, showLinks, onShowLinks, onBuild, onOpenDocs }) {
  const nodes = state.nodes || []
  const stale = state.stale || {}
  const index = useMemo(() => indexNodes(nodes), [nodes])
  const selectedNode = index.get(selected)
  const tops = nodes.filter((n) => !n.parent || n.parent === 'root' || !index.has(n.parent))

  return jsxs('div', {
    className: 'at-graph-wrap',
    children: [
      jsxs('div', {
        className: 'at-stage',
        children: [
          jsxs('div', {
            className: 'at-legend',
            children: [
              jsxs('span', { children: [jsx('i', { className: 'at-dot full' }), 'полностью'] }),
              jsxs('span', { children: [jsx('i', { className: 'at-dot partial' }), 'частично'] }),
              jsxs('span', { children: [jsx('i', { className: 'at-dot none' }), 'без описания'] }),
              jsxs('span', { children: [jsx('i', { className: 'at-dot stale' }), 'код изменился'] })
            ]
          }),
          tops.length
            ? jsx(Graph, { nodes, project: state.project, selected, onSelect, showLinks, stale })
            : jsx('div', { className: 'at-empty', children: 'В atlas.yaml нет узлов.' })
        ]
      }),
      jsx('aside', {
        className: 'at-side',
        children: [
          jsxs('div', {
            className: 'at-side-head',
            children: [
              jsx('b', { children: 'Узел' }),
              jsxs('label', {
                className: 'at-lock',
                children: [
                  jsx(Switch, { checked: Boolean(showLinks), onCheckedChange: onShowLinks, size: 'xs' }),
                  'связи'
                ]
              })
            ]
          }),
          jsx(NodeCard, {
            node: selectedNode,
            state,
            stale,
            onBuild,
            onOpenDocs,
            onSelect
          })
        ]
      })
    ]
  })
}

function Graph({ nodes, project, selected, onSelect, showLinks, stale }) {
  const shell = useRef(null)
  const svgRef = useRef(null)
  const [size, setSize] = useState({ w: 760, h: 560 })
  const [view, setView] = useState({ x: 0, y: 0, k: 1 })
  const drag = useRef(null)

  const index = useMemo(() => indexNodes(nodes), [nodes])
  const layout = useMemo(() => radialLayout(nodes, size.w, size.h), [nodes, size.w, size.h])

  useEffect(() => {
    const element = shell.current
    if (!element) return undefined
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      setSize({ w: Math.max(320, rect.width), h: Math.max(340, rect.height) })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = svgRef.current
    if (!element) return undefined
    const onWheel = (event) => {
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      const mx = event.clientX - rect.left
      const my = event.clientY - rect.top
      setView((current) => {
        const factor = Math.exp(-event.deltaY * 0.0014)
        const k = Math.min(2.8, Math.max(0.55, current.k * factor))
        return {
          k,
          x: mx - (mx - current.x) * (k / current.k),
          y: my - (my - current.y) * (k / current.k)
        }
      })
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [])

  useEffect(() => {
    setView({ x: 0, y: 0, k: 1 })
  }, [nodes.length])

  const pos = layout.positions

  const edges = []
  const center = { x: size.w / 2, y: size.h / 2 }
  for (const node of nodes) {
    const to = pos.get(node.id)
    if (!to) continue
    const isTop = !node.parent || node.parent === 'root' || !pos.has(node.parent)
    const from = isTop ? center : pos.get(node.parent)
    if (from && to) edges.push({ from, to, status: node.status, id: node.id })
  }

  const links = []
  if (showLinks) {
    for (const node of nodes) {
      const from = pos.get(node.id)
      if (!from) continue
      for (const link of node.links || []) {
        const to = pos.get(link.to)
        if (to) links.push({ from, to, id: `${node.id}-${link.to}` })
      }
    }
  }

  const siblings = useMemo(() => {
    const map = new Map()
    for (const node of nodes) {
      const parent = node.parent || 'root'
      if (!map.has(parent)) map.set(parent, [])
      map.get(parent).push(node)
    }
    return map
  }, [nodes])

  const moveSelection = (node, key) => {
    if (!node) return
    if (key === 'ArrowUp' && node.parent && index.has(node.parent)) {
      onSelect(node.parent)
      return
    }
    const kids = siblings.get(node.id) || []
    if (key === 'ArrowDown' && kids.length) {
      onSelect(kids[0].id)
      return
    }
    const peers = siblings.get(node.parent || 'root') || []
    const at = peers.findIndex((item) => item.id === node.id)
    if (key === 'ArrowLeft' && at > 0) onSelect(peers[at - 1].id)
    if (key === 'ArrowRight' && at >= 0 && at < peers.length - 1) onSelect(peers[at + 1].id)
  }

  const labelFor = (node, point) => {
    if (!point) return null
    if (point.depth >= 3 && view.k < 1.25 && node.id !== selected) return null
    const radius = point.depth === 1 ? 22 : point.depth === 2 ? 15 : 10
    const right = Math.cos(point.angle) >= 0
    const x = point.x + (right ? radius + 8 : -(radius + 8))
    const y = point.y + 4
    return jsx('text', {
      x,
      y,
      className: `at-label d${point.depth}`,
      textAnchor: right ? 'start' : 'end',
      children: trimLabel(node.name, point.depth === 1 ? 24 : point.depth === 2 ? 18 : 14)
    }, `t-${node.id}`)
  }

  return jsxs('div', {
    ref: shell,
    style: { position: 'absolute', inset: 0 },
    children: [
      jsxs('svg', {
        ref: svgRef,
        className: `at-svg ${drag.current ? 'drag' : ''}`,
        viewBox: `0 0 ${size.w} ${size.h}`,
        role: 'img',
        'aria-label': 'Древо навыков проекта',
        onPointerDown: (event) => {
          if (event.button !== 0) return
          drag.current = { x: event.clientX, y: event.clientY, base: view }
          event.currentTarget.setPointerCapture?.(event.pointerId)
        },
        onPointerMove: (event) => {
          const current = drag.current
          if (!current) return
          setView({
            ...current.base,
            x: current.base.x + (event.clientX - current.x),
            y: current.base.y + (event.clientY - current.y)
          })
        },
        onPointerUp: (event) => {
          drag.current = null
          event.currentTarget.releasePointerCapture?.(event.pointerId)
        },
        onPointerCancel: () => {
          drag.current = null
        },
        children: [
          jsxs('g', {
            transform: `translate(${view.x} ${view.y}) scale(${view.k})`,
            children: [
              jsxs('g', {
                className: 'at-center',
                children: [
                  jsx('circle', { className: 'at-center-ring', cx: center.x, cy: center.y, r: 25 }),
                  jsx('circle', { className: 'at-center-core', cx: center.x, cy: center.y, r: 17 }),
                  jsx('text', {
                    className: 'at-center-label',
                    x: center.x,
                    y: center.y + 42,
                    textAnchor: 'middle',
                    children: trimLabel(project || 'project', 24)
                  })
                ]
              }),
              edges.map((edge) =>
                jsx('line', {
                  x1: edge.from.x,
                  y1: edge.from.y,
                  x2: edge.to.x,
                  y2: edge.to.y,
                  className: `at-edge ${edge.status || 'none'}`
                }, `e-${edge.id}`)
              ),
              links.map((link) => {
                const mx = (link.from.x + link.to.x) / 2 - (link.to.y - link.from.y) * 0.12
                const my = (link.from.y + link.to.y) / 2 + (link.to.x - link.from.x) * 0.12
                return jsx('path', {
                  d: `M ${link.from.x} ${link.from.y} Q ${mx} ${my} ${link.to.x} ${link.to.y}`,
                  className: 'at-link'
                }, `x-${link.id}`)
              }),
              nodes.map((node) => {
                const point = pos.get(node.id)
                if (!point) return null
                const radius = point.depth === 1 ? 19 : point.depth === 2 ? 13 : point.depth === 3 ? 9 : 7
                const classes = ['at-node', node.status || 'none']
                if (selected === node.id) classes.push('sel')
                if (stale && stale[node.id]) classes.push('stale')
                return jsxs('g', {
                  className: classes.join(' '),
                  tabIndex: 0,
                  role: 'button',
                  'aria-label': `${node.name} — ${STATUS_LABEL[node.status] || ''}`,
                  onClick: (event) => {
                    event.stopPropagation()
                    onSelect(node.id)
                  },
                  onKeyDown: (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      onSelect(node.id)
                    } else if (event.key.startsWith('Arrow')) {
                      event.preventDefault()
                      moveSelection(node, event.key)
                    }
                  },
                  children: [
                    jsx('title', { children: `${node.name}\n${node.summary || ''}` }),
                    jsx('circle', { className: 'halo', cx: point.x, cy: point.y, r: radius + 5 }),
                    jsx('circle', { className: 'ring', cx: point.x, cy: point.y, r: radius }),
                    jsx('circle', { className: 'core', cx: point.x, cy: point.y, r: Math.max(3, radius - 6) }),
                    labelFor(node, point)
                  ]
                }, node.id)
              })
            ]
          })
        ]
      }),
      jsxs('div', {
        className: 'at-zoom',
        children: [
          jsx('button', { className: 'at-zbtn', onClick: () => setView((v) => ({ ...v, k: Math.min(2.8, v.k * 1.18) })), title: 'Приблизить', children: '+' }),
          jsx('button', { className: 'at-zbtn', onClick: () => setView((v) => ({ ...v, k: Math.max(0.55, v.k / 1.18) })), title: 'Отдалить', children: '−' }),
          jsx('button', { className: 'at-zbtn', onClick: () => setView({ x: 0, y: 0, k: 1 }), title: 'Сбросить', children: '⟲' })
        ]
      })
    ]
  })
}

function NodeCard({ node, state, stale, onBuild, onOpenDocs, onSelect }) {
  if (!node) {
    return jsx('div', {
      className: 'at-card',
      children: [
        jsx('p', { children: 'Кликните по узлу, чтобы открыть карточку.' }),
        jsxs('div', {
          className: 'at-note',
          children: [
            'Узлов: ', String(state.coverage?.total || 0), ' · ',
            'полностью: ', String(state.coverage?.full || 0)
          ]
        })
      ]
    })
  }
  const index = indexNodes(state.nodes || [])
  const chain = breadcrumbOf(node, index)
  const children = (state.nodes || []).filter((item) => item.parent === node.id)
  const isStale = Boolean(stale[node.id])
  return jsxs('div', {
    className: 'at-card',
    children: [
      jsx('div', { className: 'at-crumb', children: chain.map((item) => item.name).join(' / ') }),
      jsx('h3', { children: node.name }),
      jsxs('div', {
        className: 'at-meta',
        children: [
          jsx(Badge, { variant: 'outline', children: STATUS_LABEL[node.status] || node.status }),
          jsx(Badge, { variant: 'outline', children: `${Math.round((node.confidence || 0) * 100)}%` }),
          node.locked ? jsx(Badge, { variant: 'outline', children: 'locked' }) : null,
          isStale ? jsx(Badge, { variant: 'outline', children: 'код изменился' }) : null
        ]
      }),
      jsx('p', { children: node.summary || 'Агент нашёл код, но ещё не описал этот узел.' }),
      jsx('div', {
        className: 'at-chips',
        children: (node.paths || []).slice(0, 4).map((path) => jsx('code', { className: 'at-code', children: path }, path))
      }),
      children.length
        ? jsx('div', {
            className: 'at-chips',
            children: children.slice(0, 6).map((child) =>
              jsx('button', { className: 'at-chip', onClick: () => onSelect(child.id), children: [statusDot(child.status), child.name] }, child.id)
            )
          })
        : null,
      jsxs('div', {
        style: { display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '2px' },
        children: [
          jsx(Button, { size: 'xs', onClick: () => onBuild(node.id), children: node.status === 'none' ? 'Документировать' : 'Обновить' }),
          jsx(Button, { variant: 'outline', size: 'xs', onClick: () => onOpenDocs(node.id), children: 'Открыть страницу' })
        ]
      })
    ]
  })
}

// ─── docs tab ─────────────────────────────────────────────────────────────────

function DocsTab({ state, selected, onSelect, onBuild, onShowGraph, onToggleLock, query, onQuery }) {
  const nodes = state.nodes || []
  const index = useMemo(() => indexNodes(nodes), [nodes])
  const selectedNode = index.get(selected) || nodes.find((node) => !node.parent || node.parent === 'root') || nodes[0]
  const children = useMemo(() => buildChildren(nodes), [nodes])
  const stale = state.stale || {}

  const matches = useMemo(() => {
    const text = query.trim().toLowerCase()
    if (!text) return null
    return nodes.filter((node) =>
      `${node.id} ${node.name} ${node.summary || ''} ${(node.paths || []).join(' ')}`.toLowerCase().includes(text)
    )
  }, [nodes, query])

  const renderTree = (parent, depth) => {
    const items = children.get(parent) || []
    const out = []
    for (const node of items) {
      out.push(jsxs('button', {
        className: `at-tree-row ${selectedNode && node.id === selectedNode.id ? 'on' : ''}`,
        style: { paddingLeft: `${6 + depth * 12}px` },
        onClick: () => onSelect(node.id),
        title: node.summary || node.name,
        children: [
          statusDot(node.status),
          jsx('span', { className: 'nm', children: node.name }),
          stale[node.id] ? jsx('span', { className: 'at-dot stale', title: 'код изменился' }) : null
        ]
      }, node.id))
      if ((children.get(node.id) || []).length) out.push(...renderTree(node.id, depth + 1))
    }
    return out
  }

  return jsxs('div', {
    className: 'at-docs',
    children: [
      jsxs('div', {
        className: 'at-tree',
        children: [
          jsx('div', {
            className: 'at-tree-search',
            children: jsx(SearchField, {
              value: query,
              onChange: onQuery,
              placeholder: 'поиск по карте…'
            })
          }),
          jsx('div', {
            className: 'at-tree-list',
            children: matches
              ? matches.map((node) =>
                  jsxs('button', {
                    className: 'at-tree-row',
                    onClick: () => onSelect(node.id),
                    children: [
                      statusDot(node.status),
                      jsx('span', { className: 'nm', children: breadcrumbOf(node, index).map((item) => item.name).join(' / ') })
                    ]
                  }, node.id)
                )
              : renderTree('root', 0)
          }),
          (state.orphans || []).length
            ? jsxs('div', {
                style: { borderTop: '1px solid var(--ui-stroke-secondary)', padding: '8px 10px' },
                children: [
                  jsx('div', { className: 'at-note', style: { marginBottom: '4px' }, children: `Сироты (${state.orphans.length})` }),
                  state.orphans.slice(0, 8).map((orphan) =>
                    jsxs('div', {
                      className: 'at-orphan',
                      title: orphan.reason || '',
                      children: [
                        jsx(Codicon, { name: 'file-directory', size: '0.7rem' }),
                        jsx('span', { children: orphan.path }),
                        jsx('span', { style: { marginLeft: 'auto' }, children: `→ ${orphan.suggestParent || 'root'}` })
                      ]
                    }, orphan.path)
                  )
                ]
              })
            : null
        ]
      }),
      jsx('div', {
        className: 'at-page',
        children: selectedNode
          ? jsx(NodePage, {
              node: selectedNode,
              state,
              stale,
              onBuild,
              onShowGraph,
              onToggleLock,
              onSelect
            })
          : jsx('div', { className: 'at-empty', children: 'Выберите узел слева.' })
      })
    ]
  })
}

function NodePage({ node, state, stale, onBuild, onShowGraph, onToggleLock, onSelect }) {
  const nodes = state.nodes || []
  const index = indexNodes(nodes)
  const chain = breadcrumbOf(node, index)
  const children = nodes.filter((item) => item.parent === node.id)
  const incoming = nodes.filter((item) => (item.links || []).some((link) => link.to === node.id))
  const isStale = Boolean(stale[node.id])
  return jsx(ScrollArea, {
    className: 'at-page',
    children: jsxs('div', {
      className: 'at-page-inner',
      children: [
        jsxs('div', {
          children: [
            jsx('div', { className: 'at-crumb', children: chain.map((item) => item.name).join(' / ') }),
            jsxs('div', {
              style: { display: 'flex', alignItems: 'flex-start', gap: '10px', flexWrap: 'wrap' },
              children: [
                jsx('h2', { style: { flex: '1 1 auto' }, children: node.name }),
                jsxs('div', {
                  style: { display: 'flex', gap: '5px', flexWrap: 'wrap', paddingTop: '4px' },
                  children: [
                    jsx(Badge, { variant: 'outline', children: STATUS_LABEL[node.status] || node.status }),
                    jsx(Badge, { variant: 'outline', children: `уверенность ${Math.round((node.confidence || 0) * 100)}%` }),
                    jsx(Badge, { variant: 'outline', children: KIND_LABEL[node.kind] || node.kind }),
                    isStale ? jsx(Badge, { variant: 'outline', children: 'код изменился' }) : null,
                    node.locked ? jsx(Badge, { variant: 'outline', children: 'locked' }) : null
                  ]
                })
              ]
            }),
            jsx('div', {
              style: { display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px' },
              children: [
                jsx(Button, { size: 'xs', onClick: () => onBuild(node.id), children: 'Обновить страницу' }),
                jsx(Button, { variant: 'outline', size: 'xs', onClick: () => onShowGraph(node.id), children: 'Показать в графе' }),
                jsxs('label', {
                  className: 'at-lock',
                  style: { marginLeft: '4px' },
                  children: [
                    jsx(Switch, {
                      checked: Boolean(node.locked),
                      onCheckedChange: (value) => onToggleLock(node.id, value),
                      size: 'xs'
                    }),
                    'не перезаписывать'
                  ]
                })
              ]
            })
          ]
        }),
        node.summary ? jsx('div', { style: { color: 'var(--ui-text-primary)', fontSize: '13.5px', lineHeight: 1.55 }, children: node.summary }) : null,
        node.overview
          ? jsx(Markdown, { text: node.overview })
          : jsx('div', {
              className: 'at-sec',
              children: jsx('p', {
                className: 'at-note',
                children: 'Описания пока нет. Нажмите «Документировать» — агент прочитает код по путям узла и составит страницу.'
              })
            }),
        jsxs('div', {
          className: 'at-sec',
          children: [
            jsx('h4', { children: 'Где в коде' }),
            jsx('div', {
              className: 'at-chips',
              children: (node.paths || []).map((path) => jsx('code', { className: 'at-code', children: path }, path))
            })
          ]
        }),
        children.length
          ? jsxs('div', {
              className: 'at-sec',
              children: [
                jsx('h4', { children: 'Что внутри' }),
                jsx('div', {
                  className: 'at-chips',
                  children: children.map((child) =>
                    jsxs('button', {
                      className: 'at-chip',
                      onClick: () => onSelect(child.id),
                      children: [statusDot(child.status), child.name]
                    }, child.id)
                  )
                })
              ]
            })
          : null,
        (node.decisions || []).length
          ? jsxs('div', {
              className: 'at-sec',
              children: [
                jsx('h4', { children: 'Принятые решения' }),
                node.decisions.map((decision, i) =>
                  jsxs('div', {
                    className: 'at-dec',
                    children: [
                      jsx('b', { children: decision.title }),
                      jsx('div', { className: 'why', children: decision.why }),
                      (decision.evidence || []).length
                        ? jsx('div', {
                            className: 'at-ev',
                            children: decision.evidence.map((evidence) => jsx('code', { className: 'at-code', children: evidence }, evidence))
                          })
                        : null
                    ]
                  }, `d${i}`)
                )
              ]
            })
          : null,
        (node.links || []).length || incoming.length
          ? jsxs('div', {
              className: 'at-sec',
              children: [
                jsx('h4', { children: 'Связи' }),
                jsxs('div', {
                  className: 'at-chips',
                  children: [
                    ...(node.links || []).map((link) => {
                      const target = index.get(link.to)
                      return jsx('button', {
                        className: 'at-chip',
                        onClick: () => target && onSelect(link.to),
                        children: `${target ? target.name : link.to} · ${link.type}`
                      }, `out-${link.to}-${link.type}`)
                    }),
                    ...incoming.map((source) => {
                      const link = (source.links || []).find((item) => item.to === node.id)
                      return jsx('button', {
                        className: 'at-chip',
                        onClick: () => onSelect(source.id),
                        children: `${source.name} → · ${link?.type || ''}`
                      }, `in-${source.id}`)
                    })
                  ]
                })
              ]
            })
          : null,
        (node.todo || []).length
          ? jsxs('div', {
              className: 'at-sec',
              children: [
                jsx('h4', { children: 'Открытые вопросы' }),
                jsx('ul', {
                  className: 'at-todo',
                  children: node.todo.map((item, i) => jsx('li', { children: item }, `t${i}`))
                })
              ]
            })
          : null,
        jsx('div', {
          className: 'at-note',
          style: { borderTop: '1px solid var(--ui-stroke-secondary)', paddingTop: '10px' },
          children: `источник: .atlas/atlas.yaml · собрано ${state.generated || '—'} · hash ${node.sourceHash || '—'}`
        })
      ]
    })
  })
}

// ─── plan panel ───────────────────────────────────────────────────────────────

function PlanPanel({ plan, onCancel, onRun }) {
  const tokens = plan.approx_tokens ? `${plan.approx_tokens.toLocaleString('ru-RU')} токенов` : '—'
  return jsxs('div', {
    className: 'at-plan',
    children: [
      jsxs('h4', {
        children: [
          'План построения · ',
          plan.scope === 'all' ? 'полная карта' : plan.scope === 'stale' ? 'только устаревшее' : plan.scope
        ]
      }),
      jsxs('div', {
        className: 'nums',
        children: [
          jsx('span', { children: `${plan.nodes?.length || 0} узлов` }),
          jsx('span', { children: `${plan.files || 0} файлов` }),
          jsx('span', { children: `${Math.round((plan.bytes || 0) / 1024)} КБ` }),
          jsx('span', { children: `≈ ${tokens}` }),
          jsx('span', { children: `${plan.calls || 0} вызовов модели` })
        ]
      }),
      jsx('div', {
        className: 'ids',
        children: (plan.nodes || []).map((node) =>
          jsx('span', {
            className: 'at-chip',
            title: node.locked ? 'заблокирован — будет пропущен' : '',
            children: `${node.id}${node.locked ? ' 🔒' : ''}`
          }, node.id)
        )
      }),
      (plan.warnings || []).length ? jsx('div', { className: 'at-note', children: plan.warnings.join(' · ') }) : null,
      jsxs('div', {
        className: 'acts',
        children: [
          jsx(Button, { variant: 'outline', size: 'xs', onClick: onCancel, children: 'Отмена' }),
          jsx(Button, { size: 'xs', onClick: onRun, children: 'Запустить' })
        ]
      })
    ]
  })
}

// ─── registration ─────────────────────────────────────────────────────────────

export default {
  id: ID,
  name: NAME,
  description: 'Code-derived project map (skill tree) and documentation from .atlas/atlas.yaml.',
  register(ctx) {
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: ROUTE },
        render: () => jsx(AtlasPage, { ctx })
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { path: ROUTE, label: NAME, codicon: 'project' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'atlas.open',
          label: 'Открыть Project Atlas',
          keywords: ['atlas', 'project', 'map', 'docs', 'documentation', 'карта'],
          run: () => host.navigate(ROUTE)
        }
      }
    ])
  }
}
