/**
 * Drop-in replacement for fetch() that attaches the Cognito ID token
 * as an Authorization header.
 *
 * Presigned S3 URLs (containing X-Amz-Signature) are passed through
 * without modification — they carry their own authentication.
 */

import { getIdToken, signIn } from '../auth'

/**
 * Wrapper around fetch that adds the Authorization header with a JWT token.
 * If the token is missing (session expired), redirects to sign-in.
 */
export async function authFetch(url, options = {}) {
  // Skip auth header for presigned S3 URLs
  if (typeof url === 'string' && url.includes('X-Amz-Signature')) {
    return fetch(url, options)
  }

  const token = await getIdToken()
  if (!token) {
    signIn()
    // Return a never-resolving promise to prevent callers from seeing an
    // error while the redirect happens
    return new Promise(() => {})
  }

  const headers = new Headers(options.headers || {})
  headers.set('Authorization', token)

  const resp = await fetch(url, { ...options, headers })

  // If API returns 401, redirect to sign-in
  if (resp.status === 401) {
    signIn()
    return new Promise(() => {})
  }

  return resp
}
