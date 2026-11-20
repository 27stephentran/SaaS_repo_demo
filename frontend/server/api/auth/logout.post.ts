import {
  assertSameOriginCsrf,
  clearAuthCookies,
  getAuthCookies,
  getBackendBaseUrl
} from '~/server/utils/backendProxy'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const { accessToken, refreshToken } = getAuthCookies(event)
  if (accessToken) {
    const backendBase = getBackendBaseUrl()
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

  clearAuthCookies(event)
  return {
    authenticated: false,
    logged_out: true
  }
})
