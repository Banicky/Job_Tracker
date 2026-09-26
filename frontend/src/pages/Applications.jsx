import { Fragment, useState } from 'react'

const STATUS_LABELS = {
  applied: 'Applied',
  assessment: 'Assessment',
  interviewing: 'Interviewing',
  offer: 'Offer',
  rejected: 'Rejected',
}

const EVENT_LABELS = {
  application_confirmation: 'Applied',
  rejection: 'Rejected',
  interview_request: 'Interview',
  assessment: 'Assessment',
  offer: 'Offer',
  other: 'Update',
}

// Badge colors reuse the email type classes.
const STATUS_BADGE = {
  applied: 'type-application_confirmation',
  assessment: 'type-assessment',
  interviewing: 'type-interview_request',
  offer: 'type-offer',
  rejected: 'type-rejection',
}

function formatDay(value) {
  if (!value) return '—'
  return new Date(value + 'Z').toLocaleDateString()
}

function Responded({ app }) {
  if (app.responded) {
    return <span>Yes{app.days_to_response != null && <span className="dash-muted"> ({app.days_to_response}d)</span>}</span>
  }
  if (app.ghosted) return <span className="dash-muted" title="No response in 30+ days">No (ghosted)</span>
  return <span className="dash-muted">Not yet</span>
}

function Stats({ apps }) {
  const total = apps.length
  const responded = apps.filter(a => a.responded).length
  const count = status => apps.filter(a => a.status === status).length
  return (
    <dl className="dash-stats">
      <div><dt>Applications</dt><dd>{total}</dd></div>
      <div><dt>Response rate</dt><dd>{total ? Math.round((responded / total) * 100) : 0}%</dd></div>
      <div><dt>Interviewing</dt><dd>{count('interviewing')}</dd></div>
      <div><dt>Offers</dt><dd>{count('offer')}</dd></div>
      <div><dt>Rejected</dt><dd>{count('rejected')}</dd></div>
      <div><dt>Ghosted</dt><dd>{apps.filter(a => a.ghosted).length}</dd></div>
    </dl>
  )
}

function Applications({ apps }) {
  const [expanded, setExpanded] = useState(null)

  if (apps.length === 0) {
    return <p className="dash-muted">No applications yet. They're built from classified job emails after each sync.</p>
  }

  return (
    <>
      <Stats apps={apps} />
      <div className="dash-table-wrap">
        <table className="dash-table">
          <thead>
            <tr>
              <th>Company / role</th><th>Status</th><th>Applied</th><th>Responded</th><th>Rejected</th><th>Last activity</th>
            </tr>
          </thead>
          <tbody>
            {apps.map(a => {
              const open = expanded === a.id
              return (
                <Fragment key={a.id}>
                  <tr className="dash-row-clickable" onClick={() => setExpanded(open ? null : a.id)} aria-expanded={open}>
                    <td>
                      <div><span className="dash-caret" aria-hidden="true">{open ? '▾' : '▸'}</span> {a.company}</div>
                      <div className="dash-muted">{[a.role, a.level, a.ats].filter(Boolean).join(' · ') || 'Role unknown'}</div>
                    </td>
                    <td className="nowrap">
                      <span className={`dash-badge ${STATUS_BADGE[a.status]}`}>{STATUS_LABELS[a.status] ?? a.status}</span>
                      {a.rejection_stage && a.rejection_stage !== 'applied' && (
                        <div className="dash-muted">at {STATUS_LABELS[a.rejection_stage].toLowerCase()}</div>
                      )}
                    </td>
                    <td className="nowrap">{formatDay(a.applied_at)}</td>
                    <td className="nowrap"><Responded app={a} /></td>
                    <td className="nowrap">{formatDay(a.rejected_at)}</td>
                    <td className="nowrap">{formatDay(a.last_activity_at)}</td>
                  </tr>
                  {open && (
                    <tr className="dash-timeline-row">
                      <td colSpan={6}>
                        <ol className="dash-timeline">
                          {a.events.map((ev, i) => (
                            <li key={i}>
                              <span className="nowrap dash-muted">{formatDay(ev.occurred_at)}</span>
                              <span className={`dash-badge type-${ev.type}`}>{EVENT_LABELS[ev.type] ?? ev.type}</span>
                              {ev.gmail_message_id ? (
                                <a href={`https://mail.google.com/mail/u/0/#all/${ev.gmail_message_id}`} target="_blank" rel="noreferrer">
                                  {ev.subject || '(no subject)'}
                                </a>
                              ) : (
                                <span>{ev.subject}</span>
                              )}
                            </li>
                          ))}
                        </ol>
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

export default Applications
