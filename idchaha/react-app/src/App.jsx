import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

function App() {
  const [riotId, setRiotId] = useState('')
  const [region, setRegion] = useState('na')
  const [status, setStatus] = useState('enter a riot id')
  const [statusType, setStatusType] = useState('idle')
  const [consoleLines, setConsoleLines] = useState([])
  const [progressValue, setProgressValue] = useState(0)
  const [result, setResult] = useState(null)
  const [previewPlayer, setPreviewPlayer] = useState(null)
  const [searchCount, setSearchCount] = useState(0)
  const [copied, setCopied] = useState(false)
  const [activeTab, setActiveTab] = useState('names')
  const [loading, setLoading] = useState(false)
  const [pointer, setPointer] = useState({ x: 18, y: 14 })
  const brand = useTypewriter('idchaha.lol')
  const sourceRef = useRef(null)

  useEffect(() => {
    fetch('/api/session', { credentials: 'include' })
      .then(() => fetch('/api/stats', { credentials: 'include' }))
      .then((response) => response.json())
      .then((payload) => setSearchCount(payload.searches ?? 0))
      .catch(() => {})
    return () => sourceRef.current?.close()
  }, [])

  const parsedId = useMemo(() => parseRiotId(riotId), [riotId])
  const trackerUrl = useMemo(() => {
    const riotIdForTracker = result?.player?.riot_id || (parsedId ? `${parsedId.name}#${parsedId.tag}` : '')
    return riotIdForTracker
      ? `https://tracker.gg/valorant/profile/riot/${encodeURIComponent(riotIdForTracker)}`
      : null
  }, [parsedId, result])

  async function search(event) {
    event.preventDefault()

    if (!parsedId) {
      setStatus('use player#tag')
      setStatusType('error')
      return
    }

    try {
      const sessionResponse = await fetch('/api/session', { credentials: 'include' })
      if (!sessionResponse.ok) {
        setStatus('search blocked')
        setStatusType('error')
        return
      }
    } catch {
      setStatus('search blocked')
      setStatusType('error')
      return
    }

    sourceRef.current?.close()
    setLoading(true)
    setResult(null)
    setPreviewPlayer(null)
    setCopied(false)
    setConsoleLines(['searching'])
    setActiveTab('names')
    setProgressValue(4)
    setStatus('searching')
    setStatusType('loading')

    const params = new URLSearchParams({
      name: parsedId.name,
      tag: parsedId.tag,
      region,
    })

    const source = new EventSource(`/api/search?${params}`)
    sourceRef.current = source

    source.addEventListener('progress', (event) => {
      const payload = JSON.parse(event.data)
      const percent = progressPercent(payload)
      const nextPercent = percent ?? progressValue
      const message = progressMessage(payload, nextPercent)
      if (payload.player) {
        setPreviewPlayer(payload.player)
      }
      if (payload.search_count) {
        setSearchCount(payload.search_count)
      }
      setStatus(message)
      setStatusType('loading')
      setProgressValue(nextPercent)
      if (shouldLogLine(payload, message)) {
        setConsoleLines((items) => [message, ...items].slice(0, 5))
      }
    })

    source.addEventListener('result', (event) => {
      const payload = JSON.parse(event.data)
      setResult(payload)
      if (payload.search_count) {
        setSearchCount(payload.search_count)
      }
      setProgressValue(100)
      setStatus(`found ${payload.names.length} known id${payload.names.length === 1 ? '' : 's'}`)
      setStatusType('success')
      setLoading(false)
      source.close()
    })

    source.addEventListener('error', (event) => {
      let message = 'search connection closed'
      if (event.data) {
        try {
          message = JSON.parse(event.data).error ?? message
        } catch {
          message = event.data
        }
      }
      const cleanMessage = cleanErrorMessage(message)
      setStatus(cleanMessage)
      setStatusType('error')
      setLoading(false)
      source.close()
    })
  }

  async function copyNames() {
    if (!result?.names?.length) return

    const lines = [
      `${result.player.riot_id} name history`,
      '',
      ...result.names.map((entry) => `${entry.riot_id} - ${entry.date}`),
    ]
    const text = lines.join('\n')

    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      textarea.remove()
    }

    setCopied(true)
    window.setTimeout(() => setCopied(false), 1400)
  }

  return (
    <main
      className={[
        'app',
        loading ? 'is-loading' : '',
        result || loading ? 'has-results' : '',
      ].filter(Boolean).join(' ')}
      style={{ '--mx': `${pointer.x}%`, '--my': `${pointer.y}%` }}
      onPointerMove={(event) => {
        setPointer({
          x: (event.clientX / window.innerWidth) * 100,
          y: (event.clientY / window.innerHeight) * 100,
        })
      }}
    >
      <div className="ambient" aria-hidden="true" />
      <section className="intro">
        <h1>{brand}<span aria-hidden="true" /></h1>
      </section>
      <p className="globalCount">{searchCount.toLocaleString()} searches</p>

      <section className="searchStage">
        <form className="search" onSubmit={search}>
          <label htmlFor="riot-id">riot id</label>
          <div className="searchRow">
            <input
              id="riot-id"
              value={riotId}
              onChange={(event) => setRiotId(event.target.value)}
              placeholder="name#tag"
              autoComplete="off"
            />
            <button type="submit" disabled={loading}>
              <span>{loading ? 'wait' : 'search'}</span>
            </button>
          </div>
          <div className="regionRow">
            <label htmlFor="region">region</label>
            <select id="region" value={region} onChange={(event) => setRegion(event.target.value)}>
              <option value="na">NA</option>
              <option value="eu">EU</option>
              <option value="ap">AP</option>
              <option value="kr">KR</option>
              <option value="latam">LATAM</option>
              <option value="br">BR</option>
            </select>
          </div>
        </form>
      </section>

      {statusType !== 'idle' && (
        <section className={`status ${statusType}`}>
          <p>{status}</p>
        </section>
      )}

      {(loading || result) && (
        <section className={result && !loading ? 'loadingLine is-done' : 'loadingLine'} aria-hidden={result && !loading}>
          <b style={{ width: `${progressValue}%` }} />
        </section>
      )}

      {(loading || consoleLines.some((line) => line.startsWith('name found:'))) && (
        <section className={result && !loading ? 'searchConsole is-done' : 'searchConsole'}>
          {consoleLines.map((line, index) => (
            <p key={`${line}-${index}`} style={{ '--delay': `${index * 42}ms` }}>{line}</p>
          ))}
        </section>
      )}

      {(previewPlayer || result) && (
        <section className="preview">
          {result ? <PlayerSummary result={result} /> : <AccountPreview player={previewPlayer} />}
        </section>
      )}

      {result && (
        <section className="results">
          <nav className="tabs" aria-label="Result views">
            {['names', 'stats', 'servers'].map((tab) => (
              <button
                type="button"
                key={tab}
                className={activeTab === tab ? 'active' : ''}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
            <a className="tabLink" href={trackerUrl} target="_blank" rel="noreferrer">tracker</a>
          </nav>
          <div className="tabPanel">
            {activeTab === 'names' && <Names names={result.names} onCopy={copyNames} copied={copied} />}
            {activeTab === 'stats' && <Stats stats={result.stats} rank={result.rank} />}
            {activeTab === 'servers' && <Servers servers={result.servers} />}
          </div>
        </section>
      )}
    </main>
  )
}

function PlayerSummary({ result }) {
  const { player, rank } = result
  return (
    <div className="summary">
      <div className="avatarWrap">
        <img src={player.card_url} alt="" />
      </div>
      <div className="player">
        <small>player</small>
        <strong>{player.riot_id}</strong>
        <span>level {player.account_level ?? 'unknown'}</span>
      </div>
      <Metric label="rank" value={rank.current.display} />
      <Metric label="peak" value={rank.peak.display} />
    </div>
  )
}

function AccountPreview({ player }) {
  return (
    <div className="summary previewSummary">
      <div className="avatarWrap">
        <img src={player.card_url} alt="" />
      </div>
      <div className="player">
        <small>player</small>
        <strong>{player.riot_id}</strong>
        <span>level {player.account_level ?? 'unknown'}</span>
      </div>
      <Metric label="rank" value="loading" />
      <Metric label="peak" value="loading" />
    </div>
  )
}

function Metric({ label, value, note = '' }) {
  return (
    <div className="metric">
      <small>{label}</small>
      <strong>{value}</strong>
      {note && <span>{note}</span>}
    </div>
  )
}

function Names({ names, onCopy, copied }) {
  if (!names.length) {
    return <p className="empty">no previous names found in sampled matches</p>
  }

  return (
    <div className="nameList">
      <div className="panelHead">
        <p>known riot ids</p>
        <div className="panelActions">
          <span>{names.length} found</span>
          <button type="button" onClick={onCopy}>{copied ? 'copied' : 'copy all'}</button>
        </div>
      </div>
      {names.map((entry, index) => (
        <article key={`${entry.riot_id}-${entry.date}`} style={{ '--delay': `${index * 55}ms` }}>
          <strong>{entry.riot_id}</strong>
          <time>{entry.date}</time>
        </article>
      ))}
    </div>
  )
}

function Stats({ stats, rank }) {
  const items = [
    ['current', rank.current.display],
    ['peak', rank.peak.display],
    ['k/d', stats.kd_ratio.toFixed(2)],
    ['kills', stats.kills],
    ['deaths', stats.deaths],
    ['assists', stats.assists],
    ['avg score', stats.avg_score],
    ['hs%', `${stats.hs_percent.toFixed(1)}%`],
    ['agent', stats.most_agent],
    ['map', stats.most_map],
  ]

  return (
    <div className="statGrid">
      {items.map(([label, value]) => (
        <Metric key={label} label={label} value={value} note="" />
      ))}
    </div>
  )
}

function Servers({ servers }) {
  const max = servers.all[0]?.count || 1
  return (
    <div className="servers">
      <div className="serverTop">
        <Metric label="last server" value={servers.last.name} note={servers.last.date} />
        <Metric
          label="most common"
          value={servers.most_common.name}
          note={`${servers.most_common.count} matches`}
        />
      </div>
      {servers.all.map((server) => (
        <div className="serverBar" key={server.name}>
          <span>{server.name}</span>
          <i><b style={{ width: `${Math.max(4, (server.count / max) * 100)}%` }} /></i>
          <em>{server.count}</em>
        </div>
      ))}
    </div>
  )
}

function parseRiotId(value) {
  const trimmed = value.trim()
  const splitIndex = trimmed.lastIndexOf('#')
  if (splitIndex <= 0 || splitIndex === trimmed.length - 1) return null
  return {
    name: trimmed.slice(0, splitIndex).trim(),
    tag: trimmed.slice(splitIndex + 1).trim(),
  }
}

function useTypewriter(text) {
  const [value, setValue] = useState('')
  const [index, setIndex] = useState(0)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    const full = text
    const atEnd = !deleting && index === full.length
    const atStart = deleting && index === 0
    const delay = atEnd ? 1450 : atStart ? 420 : deleting ? 55 : 92

    const timer = window.setTimeout(() => {
      if (atEnd) {
        setDeleting(true)
        return
      }
      if (atStart) {
        setDeleting(false)
        return
      }

      const nextIndex = deleting ? index - 1 : index + 1
      setIndex(nextIndex)
      setValue(full.slice(0, nextIndex))
    }, delay)

    return () => window.clearTimeout(timer)
  }, [deleting, index, text])

  return value
}

function progressPercent(payload) {
  if (payload.stage === 'names' && payload.total) {
    return 45 + (payload.current / payload.total) * 50
  }

  return {
    search: 4,
    profile: 12,
    account: 8,
    matches: 18,
    summary: 35,
    names: 45,
  }[payload.stage] ?? 0
}

function progressMessage(payload, percent) {
  if (payload.message.toLowerCase().startsWith('name found:')) {
    return payload.message.toLowerCase()
  }

  if (payload.stage === 'names' && payload.total) {
    return `searching ${Math.round(percent)}%`
  }

  return payload.message
    .toLowerCase()
    .replace(/\bchecked\s+\d+\/\d+\b/gi, `checked ${Math.round(percent)}%`)
    .replace(/\b\d+\/\d+\b/g, `${Math.round(percent)}%`)
}

function shouldLogLine(payload, message) {
  return payload.stage === 'profile' || message.startsWith('name found:')
}

function cleanErrorMessage(message) {
  const lower = String(message || '').toLowerCase()
  if (lower.includes('too many searches')) {
    return 'too many searches, try again later'
  }

  if (lower.includes('unauthorized') || lower.includes('forbidden')) {
    return 'search blocked'
  }

  if (lower.includes('account not found') || lower.includes('could not find player')) {
    return 'account not found'
  }

  if (lower.includes('no stored competitive matches')) {
    return 'no match data found'
  }

  return 'search failed'
}

export default App
