import { confirmSignUp, resendSignUpCode, signUp } from 'aws-amplify/auth'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { authErrorMessage } from '../auth/errors'
import { AuthCard, AuthForm, Field, PASSWORD_HINT } from '../components/AuthCard'

interface SignUpState {
  email?: string
  confirm?: boolean
}

export function SignUp() {
  const navigate = useNavigate()
  const initial = (useLocation().state as SignUpState | null) ?? {}
  const [step, setStep] = useState<'details' | 'confirm'>(initial.confirm ? 'confirm' : 'details')
  const [email, setEmail] = useState(initial.email ?? '')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(
    initial.confirm ? 'Your account is not verified yet. Enter the code we emailed you.' : null,
  )

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

  const submitDetails = () =>
    run(async () => {
      if (!email || !password) throw new Error('Enter an email and password.')
      if (password !== confirmPassword) throw new Error('Passwords do not match.')
      const { nextStep } = await signUp({
        username: email.trim(),
        password,
        options: { userAttributes: { email: email.trim() } },
      })
      if (nextStep.signUpStep === 'DONE') {
        navigate('/login', { state: { email: email.trim(), notice: 'Account created. Sign in to continue.' } })
        return
      }
      setNotice(`We sent a verification code to ${email.trim()}.`)
      setStep('confirm')
    })

  const submitCode = () =>
    run(async () => {
      if (!code) throw new Error('Enter the verification code.')
      await confirmSignUp({ username: email.trim(), confirmationCode: code.trim() })
      navigate('/login', { state: { email: email.trim(), notice: 'Email verified. Sign in to continue.' } })
    })

  const resend = () =>
    run(async () => {
      await resendSignUpCode({ username: email.trim() })
      setNotice(`A new code is on its way to ${email.trim()}.`)
    })

  const footer = (
    <>
      Already have an account? <Link to="/login">Sign in</Link>
    </>
  )

  if (step === 'confirm') {
    return (
      <AuthCard title="Verify your email" subtitle="Enter the 6-digit code from your inbox." footer={footer}>
        <AuthForm onSubmit={submitCode} submitLabel="Verify" busy={busy} error={error} notice={notice}>
          {!initial.email && (
            <Field label="Email" name="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
          )}
          <Field
            label="Verification code"
            name="code"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <button type="button" className="link-button" onClick={() => void resend()} disabled={busy || !email}>
            Resend code
          </button>
        </AuthForm>
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Create your account" subtitle="Start researching with Jimmy." footer={footer}>
      <AuthForm onSubmit={submitDetails} submitLabel="Create account" busy={busy} error={error} notice={notice}>
        <Field label="Email" name="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          hint={PASSWORD_HINT}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <Field
          label="Confirm password"
          name="confirmPassword"
          type="password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
        />
      </AuthForm>
    </AuthCard>
  )
}
