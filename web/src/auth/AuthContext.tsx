import { fetchUserAttributes, getCurrentUser, signOut as amplifySignOut } from 'aws-amplify/auth'
import { Hub } from 'aws-amplify/utils'
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react'
import { isAuthConfigured } from '../config'

export interface AuthUser {
  userId: string
  email: string
}

interface AuthState {
  user: AuthUser | null
  loading: boolean
  refresh: () => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

async function loadUser(): Promise<AuthUser | null> {
  if (!isAuthConfigured) return null
  try {
    const { userId } = await getCurrentUser()
    const attributes = await fetchUserAttributes()
    return { userId, email: attributes.email ?? '' }
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    setUser(await loadUser())
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
    return Hub.listen('auth', ({ payload }) => {
      if (payload.event === 'signedIn' || payload.event === 'signedOut' || payload.event === 'tokenRefresh_failure') {
        void refresh()
      }
    })
  }, [refresh])

  const signOut = useCallback(async () => {
    await amplifySignOut()
    setUser(null)
  }, [])

  return <AuthContext.Provider value={{ user, loading, refresh, signOut }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
