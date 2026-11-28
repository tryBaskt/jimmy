import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

export function Home() {
  const { user, signOut } = useAuth()
  const navigate = useNavigate()

  const handleSignOut = async () => {
    await signOut()
    navigate('/login', { replace: true })
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="brand">Jimmy</div>
        <h1>You're signed in</h1>
        <p className="subtitle">{user?.email}</p>
        <button type="button" onClick={() => void handleSignOut()}>
          Sign out
        </button>
      </section>
    </main>
  )
}
