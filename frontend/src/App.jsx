import { useEffect, useState } from 'react'
import Login from './pages/Login.jsx'
import Dashboard from './pages/Dashboard.jsx'

function App() {
  const [user, setUser] = useState(undefined) // undefined = loading, null = logged out

  useEffect(() => {
    fetch('/api/me')
      .then(res => (res.ok ? res.json() : null))
      .then(setUser)
      .catch(() => setUser(null))
  }, [])

  if (user === undefined) return null
  if (!user) return <Login />
  return <Dashboard user={user} />
}

export default App