import { useState } from 'react'
import './Login.css'

// Change this to wherever your backend starts the Google OAuth flow.
const GOOGLE_OAUTH_URL = '/api/auth/google'

// The backend can redirect back to /login?error=<message> on failure.
function getInitialError() {
  return new URLSearchParams(window.location.search).get('error')
}

function Login() {
  const [isRedirecting, setIsRedirecting] = useState(false)
  const [error] = useState(getInitialError)

  function handleLogin() {
    setIsRedirecting(true)
    window.location.assign(GOOGLE_OAUTH_URL)
  }

  return (
    <main className="login">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <BriefcaseIcon />
          <span>Job Tracker</span>
        </div>

        <h1 id="login-title">Welcome back</h1>
        <p className="login-subtitle">
          Sign in to keep track of your applications, interviews, and offers.
        </p>

        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}

        <button
          type="button"
          className="login-provider"
          onClick={handleLogin}
          disabled={isRedirecting}
          aria-busy={isRedirecting}
        >
          {isRedirecting ? <span className="login-spinner" aria-hidden="true" /> : <GoogleIcon />}
          <span>{isRedirecting ? 'Redirecting…' : 'Continue with Google'}</span>
        </button>

      </section>
    </main>
  )
}

function BriefcaseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
      <path d="M2 13h20" />
    </svg>
  )
}

function GoogleIcon() {
  return (
    <svg viewBox="0 0 48 48" width="20" height="20" aria-hidden="true">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  )
}

export default Login
