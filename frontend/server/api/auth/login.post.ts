import {
  assertSameOriginCsrf,
  getBackendBaseUrl,
  parseBackendResponse,
  setAuthCookies,
  throwBackendHttpError,
  type BackendTokenPairResponse
} from '~/server/utils/backendProxy'

interface LoginRequestBody {
  email?: string
  password?: string
  tenant_id?: string
}

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const body = await readBody<LoginRequestBody>(event).catch(() => null)
  const email = body?.email?.trim().toLowerCase() ?? ''
  const password = body?.password ?? ''
  const tenantId = body?.tenant_id?.trim() || 'default-tenant'

  if (!email || !password) {
    throw createError({
      statusCode: 422,
      statusMessage: 'Email and password are required'
    })
  }

  const backendBase = getBackendBaseUrl()
  let res: Response
  try {
    res = await fetch(`${backendBase}/api/v1/auth/token`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      body: JSON.stringify({
        email,
        password,
        tenant_id: tenantId
      })
    })
  } catch {
    throw createError({
      statusCode: 502,
      statusMessage: 'Authentication backend unavailable'
    })
  }

  const data = await parseBackendResponse(res)
  if (!res.ok || !data || typeof data !== 'object' || !('access_token' in data)) {
    throwBackendHttpError(res.status || 401, data, 'Invalid credentials')
  }

  const tokenPair = data as BackendTokenPairResponse
  setAuthCookies(event, tokenPair)

  return {
    authenticated: true,
    tenant_id: tokenPair.tenant_id,
    role: tokenPair.role || 'staff',
    email
  }
})
