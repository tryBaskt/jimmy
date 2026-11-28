import { signIn } from 'aws-amplify/auth'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { authErrorMessage } from '../auth/errors'
import { AuthCard, AuthForm, Field } from '../components/AuthCard'

export function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { refresh } = useAuth()
  const notice = (location.state as { notice?: string } | null)?.notice ?? null
  const [email, setEmail] = useState((location.state as { email?: string } | null)?.email ?? '')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    if (!email || !password) {
      setError('Enter your email and password.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const { nextStep } = await signIn({ username: email.trim(), password })
      if (nextStep.signInStep === 'CONFIRM_SIGN_UP') {
        navigate('/signup', { state: { email: email.trim(), confirm: true } })
        return
      }
      if (nextStep.signInStep === 'RESET_PASSWORD') {
        navigate('/forgot-password', { state: { email: email.trim() } })
        return
      }
      if (nextStep.signInStep !== 'DONE') {
        setError(`Additional sign-in step required: ${nextStep.signInStep}`)
        return
      }
      await refresh()
      navigate('/', { replace: true })
    } catch (err) {
      setError(authErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <AuthCard
      title="Sign in"
      subtitle="Welcome back."
      footer={
        <>
          New to Jimmy? <Link to="/signup">Create an account</Link>
        </>
      }
    >
      <AuthForm onSubmit={submit} submitLabel="Sign in" busy={busy} error={error} notice={notice}>
        <Field label="Email" name="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Link className="inline-link" to="/forgot-password" state={{ email }}>
          Forgot password?
        </Link>
      </AuthForm>
    </AuthCard>
  )
}
