import type { H3Event } from 'h3'
import type { PermissionGroup } from '~/types/erp'

export const ACCESS_COOKIE_NAME = 'erp_access_token'
export const REFRESH_COOKIE_NAME = 'erp_refresh_token'
export const DEMO_ACCESS_COOKIE_NAME = 'erp_demo_access_token'

export interface BackendTokenPairResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  tenant_id: string
  role?: string | null
  permission_group?: PermissionGroup
  user_id?: string
  email?: string
  username?: string | null
  full_name?: string
  company_id?: string | null
  employee_id?: string | null
  must_change_password?: boolean
}

export interface BackendPrincipalResponse {
  user_id: string
  email: string
  username?: string | null
  full_name?: string
  tenant_id: string
  role: string
  permission_group?: PermissionGroup
  company_id?: string | null
  employee_id?: string | null
  must_change_password?: boolean
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
  const rawBase = (
    config.apiBaseUrl ||
    config.public?.apiBaseUrl ||
    config.public?.apiBase ||
    ''
  ).trim()

  if (!rawBase) {
    throw createError({
      statusCode: 500,
      statusMessage: 'Server misconfiguration: apiBaseUrl is not configured'
    })
  }

  return rawBase.replace(/\/+$/, '')
}

export function decodeJwtClaimsUnsafe(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3 || !parts[1]) {
      return null
    }
    const base64 = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
    const binary = atob(padded)
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    const jsonStr = new TextDecoder().decode(bytes)
    const parsed = JSON.parse(jsonStr) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
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

export function setDemoAuthCookie(event: H3Event, tokens: BackendTokenPairResponse): void {
  const secure = isProductionRuntime()
  const accessMaxAge = Math.max(60, Number(tokens.expires_in) || 900)

  setCookie(event, DEMO_ACCESS_COOKIE_NAME, tokens.access_token, {
    httpOnly: true,
    sameSite: 'strict',
    secure,
    path: '/',
    maxAge: accessMaxAge
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

export function clearDemoAuthCookie(event: H3Event): void {
  const secure = isProductionRuntime()
  deleteCookie(event, DEMO_ACCESS_COOKIE_NAME, {
    httpOnly: true,
    sameSite: 'strict' as const,
    secure,
    path: '/'
  })
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

export function getDemoAuthCookie(event: H3Event): string | undefined {
  return getCookie(event, DEMO_ACCESS_COOKIE_NAME)
}

const HOST_HEADER_PATTERN = /^[a-zA-Z0-9.-]+(:\d{1,5})?$/
const ALLOWED_FORWARDED_PROTOS = new Set(['http', 'https'])

function getConfiguredAllowedOrigins(): Set<string> {
  const rawEntries: string[] = []
  try {
    if (typeof useRuntimeConfig === 'function') {
      const config = useRuntimeConfig() as {
        allowedOrigins?: string
        public?: { siteOrigin?: string }
      }
      if (config.allowedOrigins) {
        rawEntries.push(...config.allowedOrigins.split(','))
      }
      if (config.public?.siteOrigin) {
        rawEntries.push(config.public.siteOrigin)
      }
    }
  } catch {
    // Outside Nuxt runtime (e.g. standalone unit tests)
  }

  const envOrigins = readProcessEnv('NUXT_ALLOWED_ORIGINS')
  if (envOrigins) {
    rawEntries.push(...envOrigins.split(','))
  }
  const envSiteOrigin = readProcessEnv('NUXT_PUBLIC_SITE_ORIGIN')
  if (envSiteOrigin) {
    rawEntries.push(envSiteOrigin)
  }

  const normalized = new Set<string>()
  for (const entry of rawEntries) {
    const trimmed = entry.trim()
    if (!trimmed) continue
    try {
      normalized.add(new URL(trimmed).origin)
    } catch {
      // Ignore malformed origin entries
    }
  }
  return normalized
}

function getAllowedOrigins(event: H3Event): Set<string> {
  const allowed = new Set<string>()
  const requestUrl = getRequestURL(event)
  allowed.add(requestUrl.origin)

  const configuredOrigins = getConfiguredAllowedOrigins()
  for (const origin of configuredOrigins) {
    allowed.add(origin)
  }

  const rawProto = requestUrl.protocol.replace(/:$/, '').toLowerCase()
  const proto = ALLOWED_FORWARDED_PROTOS.has(rawProto) ? rawProto : 'http'

  const host = getHeader(event, 'host')?.trim()
  if (host && HOST_HEADER_PATTERN.test(host)) {
    allowed.add(`${proto}://${host}`)
  }

  const forwardedHostRaw = getHeader(event, 'x-forwarded-host')
  const forwardedProtoRaw = getHeader(event, 'x-forwarded-proto')
  const forwardedHost = forwardedHostRaw?.split(',')[0]?.trim()
  const forwardedProtoCandidate = forwardedProtoRaw?.split(',')[0]?.trim().toLowerCase()

  if (
    forwardedHost &&
    HOST_HEADER_PATTERN.test(forwardedHost) &&
    (!forwardedProtoCandidate || ALLOWED_FORWARDED_PROTOS.has(forwardedProtoCandidate))
  ) {
    const effectiveProto = forwardedProtoCandidate || proto
    const candidateOrigin = `${effectiveProto}://${forwardedHost}`
    if (configuredOrigins.has(candidateOrigin) || (configuredOrigins.size === 0 && !isProductionRuntime())) {
      allowed.add(candidateOrigin)
    }
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
      const tokenRecord =
        data && typeof data === 'object' && !Array.isArray(data)
          ? (data as Record<string, unknown>)
          : null
      if (
        !res.ok ||
        !tokenRecord ||
        typeof tokenRecord.access_token !== 'string' ||
        !tokenRecord.access_token.trim() ||
        typeof tokenRecord.refresh_token !== 'string' ||
        !tokenRecord.refresh_token.trim() ||
        typeof tokenRecord.expires_in !== 'number' ||
        !Number.isFinite(tokenRecord.expires_in) ||
        typeof tokenRecord.tenant_id !== 'string' ||
        !tokenRecord.tenant_id.trim()
      ) {
        throw createError({
          statusCode: res.status && res.status >= 400 ? res.status : 401,
          statusMessage: 'Session expired or refresh token revoked',
          data
        })
      }

      return tokenRecord as unknown as BackendTokenPairResponse
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
    requireLiveApi?: boolean
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

  if (init.requireLiveApi) {
    const claims = decodeJwtClaimsUnsafe(tokenToUse)
    if (claims?.permission_group === 'demo_api' || claims?.role === 'demo') {
      throw createError({
        statusCode: 403,
        statusMessage: 'Forbidden: Live operator session required'
      })
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

export async function forwardDemoAuthorizedRequest(
  event: H3Event,
  targetUrl: string,
  init: {
    method: string
    headers?: Record<string, string>
    body?: string
  }
): Promise<unknown> {
  let demoToken = getDemoAuthCookie(event)
  if (!demoToken) {
    const { accessToken } = getAuthCookies(event)
    if (accessToken) {
      const claims = decodeJwtClaimsUnsafe(accessToken)
      if (claims?.permission_group === 'demo_api' || claims?.role === 'demo') {
        demoToken = accessToken
      }
    }
  }

  if (!demoToken) {
    throw createError({
      statusCode: 401,
      statusMessage: 'Demo session required'
    })
  }

  let res: Response
  try {
    res = await fetch(targetUrl, {
      method: init.method,
      headers: {
        Accept: 'application/json',
        ...(init.headers ?? {}),
        Authorization: `Bearer ${demoToken}`
      },
      body: init.body
    })
  } catch {
    throw createError({
      statusCode: 502,
      statusMessage: 'Backend service unavailable'
    })
  }

  const data = await parseBackendResponse(res)
  if (!res.ok) {
    if (res.status === 401) {
      clearDemoAuthCookie(event)
    }
    throwBackendHttpError(res.status, data, res.statusText || 'Demo backend request failed')
  }

  setResponseStatus(event, res.status)
  return data
}
