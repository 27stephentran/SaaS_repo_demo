import {
  assertSameOriginCsrf,
  getBackendBaseUrl,
  parseBackendResponse,
  setAuthCookies,
  throwBackendHttpError
} from '~/server/utils/backendProxy'
import {
  parseSignupRequest,
  serializeAuthSessionResponse,
  serializeTokenPairResponse
} from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const rawBody = await readBody<unknown>(event).catch(() => null)
  const payload = parseSignupRequest(rawBody)

  const backendBase = getBackendBaseUrl()
  let res: Response
  try {
    res = await fetch(`${backendBase}/api/v1/auth/signup`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json'
      },
      body: JSON.stringify(payload)
    })
  } catch {
    throw createError({
      statusCode: 502,
      statusMessage: 'Authentication backend unavailable'
    })
  }

  const data = await parseBackendResponse(res)
  if (!res.ok) {
    throwBackendHttpError(res.status || 400, data, 'Unable to register operator account')
  }

  const tokenPair = serializeTokenPairResponse(data, 'live_api')
  setAuthCookies(event, tokenPair)
  setResponseStatus(event, res.status === 201 ? 201 : 200)

  return serializeAuthSessionResponse(
    {
      tenant_id: tokenPair.tenant_id,
      role: tokenPair.role || 'staff',
      permission_group: tokenPair.permission_group,
      email: payload.email
    },
    payload.email
  )
})
