import {
  defineEventHandler,
  getHeader,
  getMethod,
  getRequestURL,
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

export default defineEventHandler(async (event) => {
  const method = getMethod(event).toUpperCase()
  if (MUTATING_METHODS.has(method)) {
    assertSameOriginCsrf(event)
  }

  const backendBase = getBackendBaseUrl()
  const search = getRequestURL(event).search
  const targetUrl = `${backendBase}/api/v1/companies${search}`

  const headers: Record<string, string> = {}
  const tenantHeader = getHeader(event, 'x-tenant-id')
  if (tenantHeader) {
    headers['X-Tenant-Id'] = tenantHeader
  }

  let body: string | undefined
  if (method !== 'GET' && method !== 'HEAD') {
    const rawBody = await readRawBody(event, 'utf8')
    body = parseCompanyWritePayload('', method, rawBody)
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

  return serializeCompanyResponse('', method, upstreamData)
})
