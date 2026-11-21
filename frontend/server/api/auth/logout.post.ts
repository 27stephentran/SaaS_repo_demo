import {
  assertSameOriginCsrf,
  clearAuthCookies,
  clearDemoAuthCookie,
  getAuthCookies,
  getBackendBaseUrl,
  getDemoAuthCookie
} from '~/server/utils/backendProxy'
import { serializeLogoutResponse } from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const { accessToken, refreshToken } = getAuthCookies(event)
  const demoAccessToken = getDemoAuthCookie(event)

  if (!accessToken && !refreshToken && !demoAccessToken) {
    throw createError({
      statusCode: 401,
      statusMessage: 'Authentication required'
    })
  }

  const backendBase = getBackendBaseUrl()
  if (accessToken) {
    try {
      await fetch(`${backendBase}/api/v1/auth/logout`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          Authorization: `Bearer ${accessToken}`
        },
        body: JSON.stringify({
          refresh_token: refreshToken ?? null
        })
      })
    } catch {
      // Always clear browser cookies even if upstream token is already expired or backend unreachable
    }
  }

  if (demoAccessToken) {
    try {
      await fetch(`${backendBase}/api/v1/auth/logout`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          Authorization: `Bearer ${demoAccessToken}`
        },
        body: JSON.stringify({ refresh_token: null })
      })
    } catch {
      // Best-effort revocation for demo token
    }
  }

  clearAuthCookies(event)
  clearDemoAuthCookie(event)

  return serializeLogoutResponse({
    status: 'logged_out',
    revoked: true
  })
})
