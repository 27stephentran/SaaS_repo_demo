import {
  assertSameOriginCsrf,
  forwardAuthorizedRequest,
  getBackendBaseUrl,
  parseBackendResponse,
  setAuthCookies
} from '~/server/utils/backendProxy'
import {
  parseChangePasswordRequest,
  serializeAuthSessionResponse,
  serializeTokenPairResponse
} from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const rawBody = await readBody<unknown>(event).catch(() => null)
  const payload = parseChangePasswordRequest(rawBody)

  const backendBase = getBackendBaseUrl()
  const upstreamData = await forwardAuthorizedRequest(
    event,
    `${backendBase}/api/v1/auth/change-password`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload),
      requireLiveApi: true
    }
  )

  if (
    upstreamData &&
    typeof upstreamData === 'object' &&
    'access_token' in upstreamData &&
    'refresh_token' in upstreamData
  ) {
    const tokenPair = serializeTokenPairResponse(upstreamData, 'live_api')
    setAuthCookies(event, tokenPair)

    let principalRaw: unknown = null
    try {
      const meRes = await fetch(`${backendBase}/api/v1/auth/me`, {
        method: 'GET',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${tokenPair.access_token}`
        }
      })
      if (meRes.ok) {
        principalRaw = await parseBackendResponse(meRes)
      }
    } catch {
      // Fallback to token metadata
    }

    if (principalRaw && typeof principalRaw === 'object') {
      return serializeAuthSessionResponse(principalRaw, tokenPair.email || undefined)
    }

    return serializeAuthSessionResponse({
      ...(tokenPair.user_id ? { user_id: tokenPair.user_id } : {}),
      email: tokenPair.email || tokenPair.username || '',
      ...(tokenPair.username !== undefined ? { username: tokenPair.username } : {}),
      ...(tokenPair.full_name !== undefined ? { full_name: tokenPair.full_name } : {}),
      tenant_id: tokenPair.tenant_id,
      role: tokenPair.role || 'staff',
      permission_group: tokenPair.permission_group,
      ...(tokenPair.company_id !== undefined ? { company_id: tokenPair.company_id } : {}),
      ...(tokenPair.employee_id !== undefined ? { employee_id: tokenPair.employee_id } : {}),
      must_change_password: false
    })
  }

  if (upstreamData && typeof upstreamData === 'object') {
    return serializeAuthSessionResponse({
      ...(upstreamData as Record<string, unknown>),
      must_change_password: false
    })
  }

  const principal = await forwardAuthorizedRequest(event, `${backendBase}/api/v1/auth/me`, {
    method: 'GET',
    requireLiveApi: true
  })
  return serializeAuthSessionResponse(principal)
})
