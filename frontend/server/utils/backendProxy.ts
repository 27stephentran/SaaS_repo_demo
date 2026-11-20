import type { H3Event } from 'h3'

export const ACCESS_COOKIE_NAME = 'erp_access_token'
export const REFRESH_COOKIE_NAME = 'erp_refresh_token'

export interface BackendTokenPairResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  tenant_id: string
  role?: string | null
}

export interface BackendPrincipalResponse {
  user_id: string
  email: string
  tenant_id: string
  role: string
}

const refreshInFlight = new Map<string, Promise<BackendTokenPairResponse>>()

function readProcessEnv(key: string): string | undefined {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
  return proc?.env?.[key]
}

export function isProductionRuntime(): boolean {
  const nodeEnv = readProcessEnv('NODE_ENV')
  if (nodeEnv) {
    return nodeEnv === 'production'
  }
  const config = useRuntimeConfig()
  return Boolean((config as { isProduction?: boolean }).isProduction)
}

export function getBackendBaseUrl(): string {
  const config = useRuntimeConfig() as {
    apiBaseUrl?: string
    public?: {
      apiBaseUrl?: string
      apiBase?: string
    }
  }
  const rawBase =
    config.apiBaseUrl ||
    config.public?.apiBaseUrl ||
    config.public?.apiBase ||
    'http://localhost:8000'
  return rawBase.replace(/\/+$/, '')
}

export function setAuthCookies(event: H3Event, tokens: BackendTokenPairResponse): void {
  const secure = isProductionRuntime()
  const accessMaxAge = Math.max(60, Number(tokens.expires_in) || 900)
  const refreshMaxAge = 60 * 60 * 24 * 7

  setCookie(event, ACCESS_COOKIE_NAME, tokens.access_token, {
    httpOnly: true,
    sameSite: 'strict',
    secure,
    path: '/',
    maxAge: accessMaxAge
  })

  setCookie(event, REFRESH_COOKIE_NAME, tokens.refresh_token, {
    httpOnly: true,
    sameSite: 'strict',
    secure,
    path: '/',
    maxAge: refreshMaxAge
  })
}

export function clearAuthCookies(event: H3Event): void {
  const secure = isProductionRuntime()
  const cookieOpts = {
    httpOnly: true,
    sameSite: 'strict' as const,
    secure,
    path: '/'
  }
  deleteCookie(event, ACCESS_COOKIE_NAME, cookieOpts)
  deleteCookie(event, REFRESH_COOKIE_NAME, cookieOpts)
}

export function getAuthCookies(event: H3Event): {
  accessToken: string | undefined
  refreshToken: string | undefined
} {
  return {
    accessToken: getCookie(event, ACCESS_COOKIE_NAME),
    refreshToken: getCookie(event, REFRESH_COOKIE_NAME)
  }
}

function getAllowedOrigins(event: H3Event): Set<string> {
  const allowed = new Set<string>()
  const requestUrl = getRequestURL(event)
  allowed.add(requestUrl.origin)

  const host = getHeader(event, 'host')
  const proto = requestUrl.protocol.replace(/:$/, '') || 'http'
  if (host) {
    allowed.add(`${proto}://${host}`)
  }

  const forwardedHostRaw = getHeader(event, 'x-forwarded-host')
  const forwardedProtoRaw = getHeader(event, 'x-forwarded-proto')
  const forwardedHost = forwardedHostRaw?.split(',')[0]?.trim()
  const forwardedProto = forwardedProtoRaw?.split(',')[0]?.trim() || proto
  if (forwardedHost) {
    allowed.add(`${forwardedProto}://${forwardedHost}`)
  }

  return allowed
}

export function assertSameOriginCsrf(event: H3Event): void {
  const secFetchSite = getHeader(event, 'sec-fetch-site')?.trim().toLowerCase()
  const origin = getHeader(event, 'origin')?.trim()
  const referer = getHeader(event, 'referer')?.trim()

  if (secFetchSite && secFetchSite !== 'same-origin') {
    throw createError({
      statusCode: 403,
      statusMessage: 'Forbidden: Cross-site request blocked by CSRF protection'
    })
  }

  const allowedOrigins = getAllowedOrigins(event)

  if (origin) {
    if (origin === 'null') {
      throw createError({
        statusCode: 403,
        statusMessage: 'Forbidden: Null origin rejected by CSRF protection'
      })
    }
    let parsedOrigin: string
    try {
      parsedOrigin = new URL(origin).origin
    } catch {
      throw createError({
        statusCode: 403,
        statusMessage: 'Forbidden: Invalid Origin header'
      })
    }
    if (!allowedOrigins.has(parsedOrigin)) {
      throw createError({
        statusCode: 403,
        statusMessage: 'Forbidden: Origin mismatch'
      })
    }
    return
  }

  if (referer) {
    let parsedRefererOrigin: string
    try {
      parsedRefererOrigin = new URL(referer).origin
    } catch {
      throw createError({
        statusCode: 403,
        statusMessage: 'Forbidden: Invalid Referer header'
      })
    }
    if (!allowedOrigins.has(parsedRefererOrigin)) {
      throw createError({
        statusCode: 403,
        statusMessage: 'Forbidden: Referer origin mismatch'
      })
    }
    return
  }

  if (secFetchSite === 'same-origin') {
    return
  }

  throw createError({
    statusCode: 403,
    statusMessage: 'Forbidden: Missing same-origin CSRF verification headers'
  })
}

export async function parseBackendResponse(res: Response): Promise<unknown> {
  const contentType = res.headers.get('content-type') || ''
  const rawText = await res.text()
  if (!rawText) {
    return null
  }
  if (contentType.includes('application/json')) {
    try {
      return JSON.parse(rawText) as unknown
    } catch {
      return { detail: rawText }
    }
  }
  return rawText
}

export function throwBackendHttpError(status: number, data: unknown, fallbackMessage: string): never {
  let message = fallbackMessage
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      message = detail
    }
  }
  throw createError({
    statusCode: status,
    statusMessage: message,
    data
  })
}

export async function refreshTokensSingleFlight(
  event: H3Event,
  refreshToken: string
): Promise<BackendTokenPairResponse> {
  let pending = refreshInFlight.get(refreshToken)
  if (!pending) {
    const backendBase = getBackendBaseUrl()
    pending = (async () => {
      const res = await fetch(`${backendBase}/api/v1/auth/refresh`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json'
        },
        body: JSON.stringify({ refresh_token: refreshToken })
      })

      const data = await parseBackendResponse(res)
      if (!res.ok || !data || typeof data !== 'object' || !('access_token' in data)) {
        throw createError({
          statusCode: res.status || 401,
          statusMessage: 'Session expired or refresh token revoked',
          data
        })
      }

      return data as BackendTokenPairResponse
    })().finally(() => {
      refreshInFlight.delete(refreshToken)
    })
    refreshInFlight.set(refreshToken, pending)
  }

  const rotated = await pending
  setAuthCookies(event, rotated)
  return rotated
}

export async function forwardAuthorizedRequest(
  event: H3Event,
  targetUrl: string,
  init: {
    method: string
    headers?: Record<string, string>
    body?: string
  }
): Promise<unknown> {
  const { accessToken, refreshToken } = getAuthCookies(event)
  let tokenToUse = accessToken

  if (!tokenToUse) {
    if (!refreshToken) {
      throw createError({
        statusCode: 401,
        statusMessage: 'Authentication required'
      })
    }
    try {
      const rotated = await refreshTokensSingleFlight(event, refreshToken)
      tokenToUse = rotated.access_token
    } catch (err) {
      clearAuthCookies(event)
      throw err
    }
  }

  const executeUpstream = async (bearerToken: string): Promise<Response> => {
    try {
      return await fetch(targetUrl, {
        method: init.method,
        headers: {
          Accept: 'application/json',
          ...(init.headers ?? {}),
          Authorization: `Bearer ${bearerToken}`
        },
        body: init.body
      })
    } catch {
      throw createError({
        statusCode: 502,
        statusMessage: 'Backend service unavailable'
      })
    }
  }

  let res = await executeUpstream(tokenToUse)

  if (res.status === 401 && refreshToken) {
    try {
      const rotated = await refreshTokensSingleFlight(event, refreshToken)
      res = await executeUpstream(rotated.access_token)
    } catch (err) {
      clearAuthCookies(event)
      throw err
    }
  }

  const data = await parseBackendResponse(res)

  if (!res.ok) {
    if (res.status === 401) {
      clearAuthCookies(event)
    }
    throwBackendHttpError(res.status, data, res.statusText || 'Backend request failed')
  }

  setResponseStatus(event, res.status)
  return data
}
