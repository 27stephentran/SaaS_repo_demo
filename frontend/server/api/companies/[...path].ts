import {
  createError,
  defineEventHandler,
  getHeader,
  getMethod,
  getRequestURL,
  getRouterParam,
  readRawBody
} from 'h3'
import {
  assertSameOriginCsrf,
  forwardAuthorizedRequest,
  getBackendBaseUrl
} from '~/server/utils/backendProxy'
import {
  parseCompanyWritePayload,
  serializeCompanyResponse
} from '~/server/utils/serializers'

const MUTATING_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])
const COMPANY_PATH_ALLOWLIST = /^[a-zA-Z0-9/_-]+$/

export function validateCompanyProxyPath(rawPath: string): string {
  const trimmedPath = rawPath.replace(/^\/+/, '')
  if (!trimmedPath) {
    return ''
  }

  let decodedSegments: string[]
  try {
    decodedSegments = trimmedPath.split('/').map((segment) => decodeURIComponent(segment))
  } catch {
    throw createError({
      statusCode: 400,
      statusMessage: 'Invalid companies proxy path'
    })
  }

  if (
    decodedSegments.some((segment) => {
      if (!segment || segment === '.' || segment === '..') return true
      return segment.split('/').some((part) => !part || part === '.' || part === '..')
    })
  ) {
    throw createError({
      statusCode: 400,
      statusMessage: 'Invalid companies proxy path'
    })
  }

  const cleanPath = decodedSegments.join('/')
  if (!COMPANY_PATH_ALLOWLIST.test(cleanPath)) {
    throw createError({
      statusCode: 400,
      statusMessage: 'Invalid companies proxy path'
    })
  }

  return cleanPath
}

export default defineEventHandler(async (event) => {
  const method = getMethod(event).toUpperCase()
  if (MUTATING_METHODS.has(method)) {
    assertSameOriginCsrf(event)
  }

  const rawPath = getRouterParam(event, 'path') ?? ''
  const cleanPath = validateCompanyProxyPath(rawPath)

  const backendBase = getBackendBaseUrl()
  const search = getRequestURL(event).search
  const suffix = cleanPath ? `/${cleanPath}` : ''
  const targetUrl = `${backendBase}/api/v1/companies${suffix}${search}`

  const headers: Record<string, string> = {}
  const tenantHeader = getHeader(event, 'x-tenant-id')
  if (tenantHeader) {
    headers['X-Tenant-Id'] = tenantHeader
  }

  let body: string | undefined
  if (method !== 'GET' && method !== 'HEAD') {
    const rawBody = await readRawBody(event, 'utf8')
    body = parseCompanyWritePayload(cleanPath, method, rawBody)
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json'
    }
  }

  const upstreamData = await forwardAuthorizedRequest(event, targetUrl, {
    method,
    headers,
    body,
    requireLiveApi: true
  })

  return serializeCompanyResponse(cleanPath, method, upstreamData)
})
