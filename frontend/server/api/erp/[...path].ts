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

const MUTATING_METHODS = new Set(['POST', 'PATCH', 'PUT', 'DELETE'])
const ERP_PATH_ALLOWLIST = /^[a-zA-Z0-9/_-]+$/

export function validateErpProxyPath(rawPath: string): string {
  const trimmedPath = rawPath.replace(/^\/+/, '')
  if (!trimmedPath) {
    throw createError({
      statusCode: 400,
      statusMessage: 'Invalid ERP proxy path'
    })
  }

  let decodedSegments: string[]
  try {
    decodedSegments = trimmedPath.split('/').map((segment) => decodeURIComponent(segment))
  } catch {
    throw createError({
      statusCode: 400,
      statusMessage: 'Invalid ERP proxy path'
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
      statusMessage: 'Invalid ERP proxy path'
    })
  }

  const cleanPath = decodedSegments.join('/')
  if (!ERP_PATH_ALLOWLIST.test(cleanPath)) {
    throw createError({
      statusCode: 400,
      statusMessage: 'Invalid ERP proxy path'
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
  const cleanPath = validateErpProxyPath(rawPath)

  const backendBase = getBackendBaseUrl()
  const search = getRequestURL(event).search
  const targetUrl = `${backendBase}/api/v1/erp/${cleanPath}${search}`

  const headers: Record<string, string> = {}
  const tenantHeader = getHeader(event, 'x-tenant-id')
  if (tenantHeader) {
    headers['X-Tenant-Id'] = tenantHeader
  }

  let body: string | undefined
  if (method !== 'GET' && method !== 'HEAD') {
    const rawBody = await readRawBody(event, 'utf8')
    if (rawBody !== undefined && rawBody.length > 0) {
      body = rawBody
      headers['Content-Type'] = getHeader(event, 'content-type') || 'application/json'
    }
  }

  return await forwardAuthorizedRequest(event, targetUrl, {
    method,
    headers,
    body
  })
})
