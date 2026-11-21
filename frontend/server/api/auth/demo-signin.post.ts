import {
  assertSameOriginCsrf,
  decodeJwtClaimsUnsafe,
  getBackendBaseUrl,
  parseBackendResponse,
  setDemoAuthCookie,
  throwBackendHttpError
} from '~/server/utils/backendProxy'
import {
  serializeAuthSessionResponse,
  serializeTokenPairResponse
} from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const backendBase = getBackendBaseUrl()
  let res: Response
  try {
    res = await fetch(`${backendBase}/api/v1/auth/demo-signin`, {
      method: 'POST',
      headers: {
        Accept: 'application/json'
      }
    })
  } catch {
    throw createError({
      statusCode: 502,
      statusMessage: 'Authentication backend unavailable'
    })
  }

  const data = await parseBackendResponse(res)
  if (!res.ok) {
    throwBackendHttpError(res.status || 401, data, 'Unable to initialize demo session')
  }

  const tokenPair = serializeTokenPairResponse(data, 'demo_api')
  setDemoAuthCookie(event, tokenPair)

  const claims = decodeJwtClaimsUnsafe(tokenPair.access_token)
  const claimEmail =
    typeof claims?.email === 'string' && claims.email.trim()
      ? claims.email
      : 'demo@sandbox.local'

  return serializeAuthSessionResponse(
    {
      user_id: typeof claims?.sub === 'string' ? claims.sub : undefined,
      tenant_id: tokenPair.tenant_id,
      role: tokenPair.role || 'demo',
      permission_group: 'demo_api',
      email: claimEmail
    },
    claimEmail
  )
})
