import { confirmResetPassword, resetPassword } from 'aws-amplify/auth'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { authErrorMessage } from '../auth/errors'
import { AuthCard, AuthForm, Field, PASSWORD_HINT } from '../components/AuthCard'

export function ForgotPassword() {
  const navigate = useNavigate()
  const initialEmail = (useLocation().state as { email?: string } | null)?.email ?? ''
  const [step, setStep] = useState<'request' | 'reset'>('request')
  const [email, setEmail] = useState(initialEmail)
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(authErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const requestCode = () =>
    run(async () => {
      if (!email) throw new Error('Enter your email.')
      await resetPassword({ username: email.trim() })
      setNotice(`If an account exists for ${email.trim()}, we sent it a reset code.`)
      setStep('reset')
    })

  const submitReset = () =>
    run(async () => {
      if (!code || !password) throw new Error('Enter the code and a new password.')
      if (password !== confirmPassword) throw new Error('Passwords do not match.')
      await confirmResetPassword({ username: email.trim(), confirmationCode: code.trim(), newPassword: password })
      navigate('/login', { state: { email: email.trim(), notice: 'Password updated. Sign in with your new password.' } })
    })

  const footer = (
    <>
      Remembered it? <Link to="/login">Back to sign in</Link>
    </>
  )

  if (step === 'reset') {
    return (
      <AuthCard title="Set a new password" subtitle="Enter the code from your email and choose a new password." footer={footer}>
        <AuthForm onSubmit={submitReset} submitLabel="Update password" busy={busy} error={error} notice={notice}>
          <Field
            label="Reset code"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <Field
            label="New password"
            name="password"
            type="password"
            autoComplete="new-password"
            hint={PASSWORD_HINT}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Field
            label="Confirm new password"
            name="confirmPassword"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
          <button type="button" className="link-button" onClick={() => void requestCode()} disabled={busy}>
            Resend code
          </button>
        </AuthForm>
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Reset your password" subtitle="We'll email you a code to reset it." footer={footer}>
      <AuthForm onSubmit={requestCode} submitLabel="Send reset code" busy={busy} error={error} notice={notice}>
        <Field label="Email" name="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </AuthForm>
    </AuthCard>
  )
}
