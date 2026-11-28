import type { FormEvent, InputHTMLAttributes, ReactNode } from 'react'

interface AuthCardProps {
  title: string
  subtitle?: string
  children: ReactNode
  footer?: ReactNode
}

export function AuthCard({ title, subtitle, children, footer }: AuthCardProps) {
  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="brand">Jimmy</div>
        <h1>{title}</h1>
        {subtitle && <p className="subtitle">{subtitle}</p>}
        {children}
        {footer && <div className="auth-footer">{footer}</div>}
      </section>
    </main>
  )
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  hint?: string
}

export function Field({ label, hint, id, ...input }: FieldProps) {
  const fieldId = id ?? input.name
  return (
    <label className="field" htmlFor={fieldId}>
      <span>{label}</span>
      <input id={fieldId} {...input} />
      {hint && <small>{hint}</small>}
    </label>
  )
}

interface AuthFormProps {
  onSubmit: () => Promise<void>
  submitLabel: string
  busy: boolean
  error: string | null
  notice?: string | null
  children: ReactNode
}

export function AuthForm({ onSubmit, submitLabel, busy, error, notice, children }: AuthFormProps) {
  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    void onSubmit()
  }
  return (
    <form className="auth-form" onSubmit={handleSubmit} noValidate>
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {children}
      <button type="submit" disabled={busy}>
        {busy ? 'Please wait…' : submitLabel}
      </button>
    </form>
  )
}

export const PASSWORD_HINT = 'At least 8 characters, with upper and lower case letters, a number and a symbol.'
