import { useCallback, useEffect, useState } from 'react'
import './Dashboard.css'
import Applications from './Applications.jsx'

function formatDate(value) {
  if (!value) return '—'
  // Backend sends naive UTC timestamps.
  return new Date(value + 'Z').toLocaleString()
}

const TYPE_LABELS = {
  application_confirmation: 'Applied',
  rejection: 'Rejected',
  interview_request: 'Interview',
  assessment: 'Assessment',
  offer: 'Offer',
  recruiter_outreach: 'Recruiter',
  other: 'Other',
  not_job_related: 'Not job',
}

function progressText(s) {
  if (s.phase === 'matching') return 'Grouping emails into applications…'
  if (s.phase === 'classifying') {
    return `Classifying… ${s.classified} / ${s.classify_total} emails`
  }
  return `Running ${s.kind} sync… ${s.total ? `${s.fetched} / ${s.total} emails` : 'searching Gmail'}`
}

function Dashboard({ user }) {
  const [status, setStatus] = useState(null)
  const [emails, setEmails] = useState([])
  const [apps, setApps] = useState([])
  const [error, setError] = useState(null)
  const [jobOnly, setJobOnly] = useState(true)

  const loadEmails = useCallback(() => {
    fetch(`/api/emails?limit=500&job_only=${jobOnly}`)
      .then(res => res.json())
      .then(setEmails)
  }, [jobOnly])

  const loadApps = useCallback(() => {
    fetch('/api/applications')
      .then(res => res.json())
      .then(setApps)
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer
    let wasRunning = false

    async function poll() {
      const res = await fetch('/api/sync/status')
      if (!res.ok || cancelled) return
      const next = await res.json()
      setStatus(next)
      if (wasRunning && !next.running) {
        loadEmails()
        loadApps()
      }
      wasRunning = next.running
      timer = setTimeout(poll, next.running ? 1500 : 30000)
    }

    poll()
    loadEmails()
    loadApps()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [loadEmails, loadApps])

  async function startSync(kind) {
    if (kind === 'reclassify') {
      const count = status?.email_count ?? 0
      const cost = (count * 0.003).toFixed(2)
      const ok = window.confirm(
        `Reclassify all ${count} emails?\n\nThis re-sends every stored email to Claude and overwrites existing results. Estimated cost: ~$${cost}.`
      )
      if (!ok) return
    }
    setError(null)
    const url = {
      reclassify: '/api/classify/reset',
      rebuild: '/api/applications/rebuild',
    }[kind] ?? `/api/sync/${kind}`
    const res = await fetch(url, { method: 'POST' })
    if (!res.ok) {
      setError((await res.json()).detail ?? 'Could not start sync')
      return
    }
    setStatus(s => ({ ...s, running: true, kind, phase: null, total: 0, fetched: 0, error: null }))
  }

  async function logout() {
    await fetch('/api/logout', { method: 'POST' })
    window.location.assign('/')
  }

  const running = status?.running

  const actions = [
    {
      kind: 'backfill',
      primary: true,
      label: status?.backfill_completed_at ? 'Re-run backfill (last 6 months)' : 'Import last 6 months of emails',
      hint: 'Download job-related emails from the last 6 months, then classify and group them. Skips emails already imported.',
      disabled: running,
    },
    {
      kind: 'incremental',
      label: 'Check for new emails',
      hint: 'Fetch, classify, and group emails received since the last sync. Runs automatically every 5 minutes.',
      disabledHint: 'Available after the first backfill finishes.',
      disabled: running || !status?.backfill_completed_at,
    },
    {
      kind: 'classify',
      label: 'Classify pending',
      hint: 'Send only unclassified stored emails to Claude. No Gmail fetch.',
      disabledHint: 'Nothing to classify.',
      disabled: running || !status?.unclassified_count,
    },
    {
      kind: 'reclassify',
      label: 'Reclassify all',
      hint: 'Re-send every stored email to Claude and overwrite results. Use after changing the classifier prompt. Costs money.',
      disabled: running || !status?.email_count,
    },
    {
      kind: 'rebuild',
      label: 'Rebuild applications',
      hint: 'Regroup classified emails into applications. Free, no Claude calls. Use after the grouping logic changes.',
      disabled: running || !status?.email_count,
    },
  ]

  return (
    <main className="dash">
      <header className="dash-header">
        <h1>Job Tracker</h1>
        <div className="dash-user">
          <span>{user.email}</span>
          <button type="button" className="dash-link" onClick={logout}>Sign out</button>
        </div>
      </header>

      <section className="dash-card" aria-labelledby="sync-title">
        <h2 id="sync-title">Gmail sync</h2>
        {status && (
          <dl className="dash-stats">
            <div><dt>Emails stored</dt><dd>{status.email_count}</dd></div>
            <div><dt>Unclassified</dt><dd>{status.unclassified_count}</dd></div>
            <div><dt>Last synced</dt><dd>{formatDate(status.last_synced_at)}</dd></div>
            <div><dt>Backfill</dt><dd>{status.backfill_completed_at ? `Done ${formatDate(status.backfill_completed_at)}` : 'Not run yet'}</dd></div>
          </dl>
        )}

        {running && (
          <p className="dash-progress" role="status">
            {progressText(status)}
          </p>
        )}
        {(error || status?.error) && <p className="dash-error" role="alert">{error || status.error}</p>}

        <div className="dash-actions">
          {actions.map(a => (
            // Tooltip lives on a wrapper because disabled buttons don't reliably show titles.
            <span key={a.kind} title={a.disabled && a.disabledHint ? `${a.hint}

${a.disabledHint}` : a.hint}>
              <button type="button" className={`dash-button${a.primary ? '' : ' secondary'}`} disabled={a.disabled} onClick={() => startSync(a.kind)}>
                {a.label}
              </button>
            </span>
          ))}
        </div>
      </section>

      <section className="dash-card" aria-labelledby="apps-title">
        <h2 id="apps-title">Applications</h2>
        <Applications apps={apps} />
      </section>

      <details className="dash-card dash-emails">
        <summary><h2 id="emails-title">Emails</h2></summary>
        <div className="dash-card-header">
          <label className="dash-toggle">
            <input type="checkbox" checked={jobOnly} onChange={e => setJobOnly(e.target.checked)} />
            Job-related only
          </label>
          <span className="dash-muted">{emails.length} shown</span>
        </div>
        {emails.length === 0 ? (
          <p className="dash-muted">{jobOnly ? 'No classified job emails yet.' : 'No emails yet. Run the import above.'}</p>
        ) : (
          <div className="dash-table-wrap">
            <table className="dash-table">
              <thead>
                <tr><th>Received</th><th>Type</th><th>Company / role</th><th>Subject</th></tr>
              </thead>
              <tbody>
                {emails.map(e => (
                  <tr key={e.id}>
                    <td className="nowrap">{formatDate(e.received_at)}</td>
                    <td className="nowrap">
                      {e.type ? (
                        <span className={`dash-badge type-${e.type}`}>{TYPE_LABELS[e.type] ?? e.type}</span>
                      ) : (
                        <span className="dash-muted">Pending</span>
                      )}
                      {e.confidence != null && e.confidence < 0.7 && (
                        <div className="dash-muted" title="Low classifier confidence">low conf.</div>
                      )}
                    </td>
                    <td>
                      <div>{e.company ?? '—'}</div>
                      <div className="dash-muted">{[e.role, e.level !== 'unknown' && e.level, e.ats].filter(Boolean).join(' · ')}</div>
                    </td>
                    <td>
                      <a href={`https://mail.google.com/mail/u/0/#all/${e.gmail_message_id}`} target="_blank" rel="noreferrer">
                        {e.subject || '(no subject)'}
                      </a>
                      <div className="dash-muted">{e.sender}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
    </main>
  )
}

export default Dashboard
