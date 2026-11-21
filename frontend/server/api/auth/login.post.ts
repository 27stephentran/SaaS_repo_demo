import {
  assertSameOriginCsrf,
  getBackendBaseUrl,
  parseBackendResponse,
  setAuthCookies,
  throwBackendHttpError
} from '~/server/utils/backendProxy'
import {
  parseLoginRequest,
  serializeAuthSessionResponse,
  serializeTokenPairResponse
} from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const rawBody = await readBody<unknown>(event).catch(() => null)
  const payload = parseLoginRequest(rawBody)

  const backendBase = getBackendBaseUrl()
  const upstreamPayload = {
    ...(payload.identifier ? { identifier: payload.identifier } : {}),
    ...(payload.email ? { email: payload.email } : {}),
    password: payload.password,
    tenant_id: payload.tenant_id
  }

  let res: Response
  try {
    res = await fetch(`${backendBase}/api/v1/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      body: JSON.stringify(upstreamPayload)
    })
  } catch {
    throw createError({
      statusCode: 502,
      statusMessage: 'Authentication backend unavailable'
    })
  }

  const data = await parseBackendResponse(res)
  if (!res.ok) {
    throwBackendHttpError(res.status || 401, data, 'Invalid credentials')
  }

  const tokenPair = serializeTokenPairResponse(data, 'live_api')
  if (tokenPair.role === 'demo') {
    throw createError({
      statusCode: 403,
      statusMessage: 'Demo accounts cannot sign in to live operator sessions'
    })
  }

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
    // Fall back to tokenPair metadata if /me is temporarily unavailable
  }

  const fallbackEmail = tokenPair.email || payload.email || payload.identifier || ''
  if (principalRaw && typeof principalRaw === 'object') {
    return serializeAuthSessionResponse(principalRaw, fallbackEmail)
  }

  return serializeAuthSessionResponse(
    {
      ...(tokenPair.user_id ? { user_id: tokenPair.user_id } : {}),
      tenant_id: tokenPair.tenant_id,
      role: tokenPair.role || 'staff',
      permission_group: tokenPair.permission_group,
      email: fallbackEmail,
      ...(tokenPair.username !== undefined ? { username: tokenPair.username } : {}),
      ...(tokenPair.full_name !== undefined ? { full_name: tokenPair.full_name } : {}),
      ...(tokenPair.company_id !== undefined ? { company_id: tokenPair.company_id } : {}),
      ...(tokenPair.employee_id !== undefined ? { employee_id: tokenPair.employee_id } : {}),
      ...(tokenPair.must_change_password !== undefined
        ? { must_change_password: tokenPair.must_change_password }
        : {})
    },
    fallbackEmail
  )
})
