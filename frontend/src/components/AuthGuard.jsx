import { useState, useEffect } from 'react'
import { configureAuth, isAuthenticated, signIn } from '../auth'

/**
 * Wraps the application and ensures the user is authenticated before
 * rendering children.  Shows a loading state while checking auth status,
 * and redirects to Cognito Hosted UI if not logged in.
 */
function AuthGuard({ children }) {
  const [authState, setAuthState] = useState('loading') // loading | authenticated | redirecting

  useEffect(() => {
    let cancelled = false

    async function checkAuth() {
      try {
        await configureAuth()
        const authed = await isAuthenticated()
        if (cancelled) return

        if (authed) {
          setAuthState('authenticated')
        } else {
          setAuthState('redirecting')
          signIn()
        }
      } catch (err) {
        console.error('Auth check failed:', err)
        if (!cancelled) {
          setAuthState('redirecting')
          signIn()
        }
      }
    }

    checkAuth()
    return () => { cancelled = true }
  }, [])

  if (authState === 'loading' || authState === 'redirecting') {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        height: '100vh',
        fontFamily: 'var(--font-sans, system-ui, sans-serif)',
      }}>
        <div style={{ textAlign: 'center' }}>
          <p style={{ fontSize: '1rem', color: '#666' }}>
            {authState === 'loading'
              ? 'Checking authentication...'
              : 'Redirecting to sign in...'}
          </p>
        </div>
      </div>
    )
  }

  return children
}

export default AuthGuard
