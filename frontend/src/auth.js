/**
 * Authentication module using AWS Amplify v6.
 *
 * Loads Cognito configuration at runtime from /auth-config.json (deployed by
 * CDK to the frontend S3 bucket) so that the React build does not need to
 * know Cognito resource IDs at compile time.
 */

import { Amplify } from 'aws-amplify'
import {
  fetchAuthSession,
  signInWithRedirect,
  signOut as amplifySignOut,
  getCurrentUser,
} from 'aws-amplify/auth'

let _configured = false
let _configPromise = null

/**
 * Load /auth-config.json and configure Amplify.  Safe to call multiple
 * times — the config is fetched once and cached.
 */
export async function configureAuth() {
  if (_configured) return
  if (_configPromise) return _configPromise

  _configPromise = (async () => {
    let config
    try {
      const resp = await fetch('/auth-config.json')
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      config = await resp.json()
    } catch {
      // Fallback for local development — set these in frontend/.env.local
      config = {
        userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID,
        userPoolClientId: import.meta.env.VITE_COGNITO_CLIENT_ID,
        domain: import.meta.env.VITE_COGNITO_DOMAIN,
        region: import.meta.env.VITE_AWS_REGION || 'us-west-2',
        redirectSignIn: window.location.origin + '/',
        redirectSignOut: window.location.origin + '/',
      }
    }

    Amplify.configure({
      Auth: {
        Cognito: {
          userPoolId: config.userPoolId,
          userPoolClientId: config.userPoolClientId,
          loginWith: {
            oauth: {
              domain: config.domain,
              scopes: ['openid', 'email', 'profile'],
              redirectSignIn: [config.redirectSignIn],
              redirectSignOut: [config.redirectSignOut],
              responseType: 'code',
            },
          },
        },
      },
    })

    _configured = true
  })()

  return _configPromise
}

/**
 * Get the current ID token (JWT) for API Gateway Cognito authorizer.
 * The CognitoUserPoolsAuthorizer validates ID tokens (which contain the
 * `aud` claim matching the client ID), not access tokens.
 * Returns null if the user is not authenticated.  Amplify automatically
 * refreshes expired tokens.
 */
export async function getIdToken() {
  try {
    const session = await fetchAuthSession()
    return session.tokens?.idToken?.toString() || null
  } catch {
    return null
  }
}

/**
 * Check if the user is currently authenticated.
 */
export async function isAuthenticated() {
  try {
    await getCurrentUser()
    return true
  } catch {
    return false
  }
}

/**
 * Redirect to Cognito Hosted UI login page.
 */
export function signIn() {
  signInWithRedirect()
}

/**
 * Sign out the current user and redirect to logout URL.
 */
export async function signOut() {
  await amplifySignOut()
}
