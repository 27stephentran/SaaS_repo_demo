import { createError, createEvent, deleteCookie, getCookie, getHeader, getRequestURL, setCookie, setResponseStatus } from 'h3'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { validateCompanyProxyPath } from '~/server/api/companies/[...path]'
import {
  ACCESS_COOKIE_NAME,
  DEMO_ACCESS_COOKIE_NAME,
  REFRESH_COOKIE_NAME,
  assertSameOriginCsrf,
  forwardAuthorizedRequest,
  forwardDemoAuthorizedRequest,
  getBackendBaseUrl
} from '~/server/utils/backendProxy'

vi.mock('h3', async (importOriginal) => {
  const actual = await importOriginal<typeof import('h3')>()
  return {
    ...actual,
    readRawBody: vi.fn(async (evt: { _rawBody?: string }) => evt._rawBody)
  }
})

function toBase64Url(input: string): string {
  const bytes = new TextEncoder().encode(input)
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function makeJwt(payload: Record<string, unknown>): string {
  const header = toBase64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
  const body = toBase64Url(JSON.stringify(payload))
  return `${header}.${body}.signature`
}

function makeEvent(headers: Record<string, string> = {}, url = '/api/erp/orders') {
  const resHeaders = new Map<string, unknown>()
  const res = {
    statusCode: 200,
    statusMessage: '',
    getHeader(name: string) {
      return resHeaders.get(name.toLowerCase())
    },
    setHeader(name: string, value: unknown) {
      resHeaders.set(name.toLowerCase(), value)
    },
    removeHeader(name: string) {
      resHeaders.delete(name.toLowerCase())
    },
    appendHeader(name: string, value: string | string[]) {
      const key = name.toLowerCase()
      const existing = resHeaders.get(key)
      const list = Array.isArray(existing) ? existing : existing ? [String(existing)] : []
      const next = Array.isArray(value) ? value : [value]
      resHeaders.set(key, [...list, ...next])
    }
  }
  return createEvent(
    {
      method: 'GET',
      url,
      headers: {
        host: 'erp.example.com',
        ...headers
      }
    } as never,
    res as never
  )
}

describe('backendProxy fail-closed config and session forwarding guards', () => {
  beforeEach(() => {
    vi.stubGlobal('createError', createError)
    vi.stubGlobal('getCookie', getCookie)
    vi.stubGlobal('setCookie', setCookie)
    vi.stubGlobal('deleteCookie', deleteCookie)
    vi.stubGlobal('getHeader', getHeader)
    vi.stubGlobal('getRequestURL', getRequestURL)
    vi.stubGlobal('setResponseStatus', setResponseStatus)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('fails closed (HTTP 500) when apiBaseUrl is empty or unconfigured (no localhost:8000 fallback)', () => {
    vi.stubGlobal('useRuntimeConfig', () => ({
      apiBaseUrl: '',
      public: { apiBaseUrl: '', apiBase: '' }
    }))

    let caught: unknown
    try {
      getBackendBaseUrl()
    } catch (err) {
      caught = err
    }
    expect(caught).toBeDefined()
    expect((caught as { statusCode?: number }).statusCode).toBe(500)
    expect((caught as { statusMessage?: string }).statusMessage).toMatch(/apiBaseUrl is not configured/)

    vi.stubGlobal('useRuntimeConfig', () => ({
      apiBaseUrl: '   ',
      public: {}
    }))
    expect(() => getBackendBaseUrl()).toThrow(/apiBaseUrl is not configured/)

    vi.stubGlobal('useRuntimeConfig', () => ({
      apiBaseUrl: 'https://api.erp.example.com///',
      public: {}
    }))
    expect(getBackendBaseUrl()).toBe('https://api.erp.example.com')
  })

  it('rejects unauthenticated requests and blocks demo_api tokens from requireLiveApi routes', async () => {
    const unauthEvent = makeEvent()
    await expect(
      forwardAuthorizedRequest(unauthEvent, 'https://api.erp.example.com/api/v1/erp/orders', {
        method: 'GET',
        requireLiveApi: true
      })
    ).rejects.toMatchObject({ statusCode: 401 })

    const demoJwt = makeJwt({ sub: 'demo-1', role: 'demo', permission_group: 'demo_api' })
    const demoEvent = makeEvent({
      cookie: `${ACCESS_COOKIE_NAME}=${demoJwt}`
    })
    await expect(
      forwardAuthorizedRequest(demoEvent, 'https://api.erp.example.com/api/v1/erp/orders', {
        method: 'GET',
        requireLiveApi: true
      })
    ).rejects.toMatchObject({
      statusCode: 403,
      statusMessage: 'Forbidden: Live operator session required'
    })
  })

  it('forwards live_api requests and refreshes expired access tokens via single-flight refresh', async () => {
    vi.stubGlobal('useRuntimeConfig', () => ({
      apiBaseUrl: 'https://api.erp.example.com',
      public: {}
    }))

    const liveJwt = makeJwt({ sub: 'user-1', role: 'admin', permission_group: 'live_api' })
    const rotatedJwt = makeJwt({ sub: 'user-1', role: 'admin', permission_group: 'live_api' })

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Token expired' }), {
          status: 401,
          headers: { 'content-type': 'application/json' }
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: rotatedJwt,
            refresh_token: 'new-refresh-token',
            token_type: 'bearer',
            expires_in: 900,
            tenant_id: 'acme',
            role: 'admin',
            permission_group: 'live_api'
          }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' }
          }
        )
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify([{ id: 'ord-1' }]), {
          status: 200,
          headers: { 'content-type': 'application/json' }
        })
      )
    vi.stubGlobal('fetch', fetchMock)

    const event = makeEvent({
      cookie: `${ACCESS_COOKIE_NAME}=${liveJwt}; ${REFRESH_COOKIE_NAME}=old-refresh-token`
    })

    const data = await forwardAuthorizedRequest(
      event,
      'https://api.erp.example.com/api/v1/erp/orders',
      {
        method: 'GET',
        requireLiveApi: true
      }
    )

    expect(data).toEqual([{ id: 'ord-1' }])
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://api.erp.example.com/api/v1/auth/refresh',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('requires demo session for forwardDemoAuthorizedRequest and uses erp_demo_access_token', async () => {
    const noDemoEvent = makeEvent()
    await expect(
      forwardDemoAuthorizedRequest(noDemoEvent, 'https://api.erp.example.com/api/v1/demo/notify-telegram', {
        method: 'POST',
        body: '{}'
      })
    ).rejects.toMatchObject({ statusCode: 401 })

    const demoJwt = makeJwt({ sub: 'demo-1', role: 'demo', permission_group: 'demo_api' })
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          dispatched: true,
          simulated: true,
          telegram_user_id: 'demo_ops_channel',
          order_code: 'DEMO-ORD-8841'
        }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' }
        }
      )
    )
    vi.stubGlobal('fetch', fetchMock)

    const demoEvent = makeEvent({
      cookie: `${DEMO_ACCESS_COOKIE_NAME}=${demoJwt}`
    })

    const res = await forwardDemoAuthorizedRequest(
      demoEvent,
      'https://api.erp.example.com/api/v1/demo/notify-telegram',
      {
        method: 'POST',
        body: JSON.stringify({ order_code: 'DEMO-ORD-8841' })
      }
    )

    expect(res).toMatchObject({ dispatched: true, order_code: 'DEMO-ORD-8841' })
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.erp.example.com/api/v1/demo/notify-telegram',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({
          Authorization: `Bearer ${demoJwt}`
        })
      })
    )
  })

  it('validates x-forwarded-proto (http|https only) and x-forwarded-host (HOST_HEADER_PATTERN) in assertSameOriginCsrf', () => {
    vi.stubEnv('NODE_ENV', 'test')
    vi.stubGlobal('useRuntimeConfig', () => ({
      allowedOrigins: '',
      public: { siteOrigin: '' }
    }))

    // Valid http/https forwarded proto + valid host in non-production
    const validHttpsForwarded = makeEvent({
      origin: 'https://proxy.erp.example.com:8443',
      'x-forwarded-host': 'proxy.erp.example.com:8443',
      'x-forwarded-proto': 'https'
    })
    expect(() => assertSameOriginCsrf(validHttpsForwarded)).not.toThrow()

    const validHttpForwarded = makeEvent({
      origin: 'http://proxy.erp.example.com',
      'x-forwarded-host': 'proxy.erp.example.com',
      'x-forwarded-proto': 'http'
    })
    expect(() => assertSameOriginCsrf(validHttpForwarded)).not.toThrow()

    // Disallowed x-forwarded-proto schemes must be rejected
    for (const badProto of ['javascript', 'ftp', 'file', 'https://evil.com#']) {
      const badProtoEvent = makeEvent({
        origin: 'https://proxy.erp.example.com',
        'x-forwarded-host': 'proxy.erp.example.com',
        'x-forwarded-proto': badProto
      })
      expect(() => assertSameOriginCsrf(badProtoEvent)).toThrow(/Origin mismatch/)
    }

    // Malformed / injected x-forwarded-host values must fail HOST_HEADER_PATTERN
    for (const badHost of [
      'attacker.example.com/path',
      'user@attacker.example.com',
      'evil host.example.com',
      'attacker.example.com:999999',
      'attacker.example.com?x=1'
    ]) {
      const badHostEvent = makeEvent({
        origin: 'https://attacker.example.com',
        'x-forwarded-host': badHost,
        'x-forwarded-proto': 'https'
      })
      expect(() => assertSameOriginCsrf(badHostEvent)).toThrow(/Origin mismatch/)
    }

    // In production without configured allowedOrigins, arbitrary x-forwarded-host is NOT trusted
    vi.stubEnv('NODE_ENV', 'production')
    const prodSpoofedForwarded = makeEvent({
      origin: 'https://spoofed.example.com',
      'x-forwarded-host': 'spoofed.example.com',
      'x-forwarded-proto': 'https'
    })
    expect(() => assertSameOriginCsrf(prodSpoofedForwarded)).toThrow(/Origin mismatch/)

    // In production WITH explicit allowedOrigins/siteOrigin, configured origin is trusted
    vi.stubGlobal('useRuntimeConfig', () => ({
      allowedOrigins: 'https://app.saas-erp.vn',
      public: { siteOrigin: 'https://portal.saas-erp.vn' }
    }))
    const prodConfiguredEvent = makeEvent({
      origin: 'https://app.saas-erp.vn',
      'x-forwarded-host': 'app.saas-erp.vn',
      'x-forwarded-proto': 'https'
    })
    expect(() => assertSameOriginCsrf(prodConfiguredEvent)).not.toThrow()

    const prodUnlistedForwarded = makeEvent({
      origin: 'https://evil.example.com',
      'x-forwarded-host': 'evil.example.com',
      'x-forwarded-proto': 'https'
    })
    expect(() => assertSameOriginCsrf(prodUnlistedForwarded)).toThrow(/Origin mismatch/)
  })

  it('validates allowed company proxy paths and blocks traversal attempts', () => {
    expect(validateCompanyProxyPath('')).toBe('')
    expect(validateCompanyProxyPath('/')).toBe('')
    expect(validateCompanyProxyPath('banks')).toBe('banks')
    expect(validateCompanyProxyPath('/comp-1/bank')).toBe('comp-1/bank')
    expect(validateCompanyProxyPath('comp-1/managers')).toBe('comp-1/managers')
    expect(validateCompanyProxyPath('comp-1/restore')).toBe('comp-1/restore')

    const blockedPaths = [
      '.',
      '..',
      '../erp/orders',
      '%2e%2e/secret',
      'comp-1/%2e%2e/bank',
      'comp-1/%252e%252e/bank',
      'comp-1\\..\\admin',
      'comp-1?admin=true'
    ]
    for (const candidate of blockedPaths) {
      expect(() => validateCompanyProxyPath(candidate)).toThrow(/Invalid companies proxy path/)
    }
  })

  it('handles change-password, login, and companies Nitro BFF routes end-to-end', async () => {
    vi.stubGlobal('useRuntimeConfig', () => ({
      apiBaseUrl: 'https://api.erp.example.com',
      public: {}
    }))
    vi.stubGlobal('defineEventHandler', (fn: (event: unknown) => unknown) => fn)
    vi.stubGlobal('readBody', async (evt: { _rawBody?: string }) =>
      evt._rawBody ? JSON.parse(evt._rawBody) : null
    )

    const makeBodyEvent = (
      method: string,
      url: string,
      bodyObj: unknown,
      headers: Record<string, string> = {}
    ) => {
      const raw = bodyObj !== undefined ? JSON.stringify(bodyObj) : ''
      const resHeaders = new Map<string, unknown>()
      const res = {
        statusCode: 200,
        statusMessage: '',
        getHeader: (n: string) => resHeaders.get(n.toLowerCase()),
        setHeader: (n: string, v: unknown) => resHeaders.set(n.toLowerCase(), v),
        removeHeader: (n: string) => resHeaders.delete(n.toLowerCase()),
        appendHeader: (n: string, v: string | string[]) => {
          const k = n.toLowerCase()
          const cur = resHeaders.get(k)
          const list = Array.isArray(cur) ? cur : cur ? [String(cur)] : []
          resHeaders.set(k, [...list, ...(Array.isArray(v) ? v : [v])])
        }
      }
      const event = createEvent(
        {
          method,
          url,
          headers: {
            host: 'erp.example.com',
            origin: 'http://erp.example.com',
            'content-type': 'application/json',
            ...headers
          }
        } as never,
        res as never
      ) as ReturnType<typeof createEvent> & { _rawBody?: string }
      event._rawBody = raw
      return { event, resHeaders }
    }

    const liveJwt = makeJwt({ sub: 'u-1', role: 'staff', permission_group: 'live_api' })
    const rotatedJwt = makeJwt({ sub: 'u-1', role: 'staff', permission_group: 'live_api' })

    const fetchMock = vi
      .fn()
      // 1. login POST /api/v1/auth/login
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: liveJwt,
            refresh_token: 'ref-1',
            token_type: 'bearer',
            expires_in: 900,
            tenant_id: 'acme',
            role: 'staff',
            permission_group: 'live_api',
            user_id: 'u-1',
            username: 'staff_01',
            full_name: 'Staff One',
            company_id: 'comp-1',
            employee_id: 'emp-1',
            must_change_password: true
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
      // 2. login GET /api/v1/auth/me
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            user_id: 'u-1',
            email: 'staff01@acme.io',
            username: 'staff_01',
            full_name: 'Staff One',
            tenant_id: 'acme',
            role: 'staff',
            permission_group: 'live_api',
            company_id: 'comp-1',
            employee_id: 'emp-1',
            must_change_password: true
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
      // 3. change-password POST /api/v1/auth/change-password (returns rotated tokens)
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            access_token: rotatedJwt,
            refresh_token: 'ref-2',
            token_type: 'bearer',
            expires_in: 900,
            tenant_id: 'acme',
            role: 'staff',
            permission_group: 'live_api',
            user_id: 'u-1',
            email: 'staff01@acme.io',
            username: 'staff_01',
            full_name: 'Staff One',
            company_id: 'comp-1',
            employee_id: 'emp-1',
            must_change_password: false
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
      // 4. change-password GET /api/v1/auth/me fails -> fallback to tokenPair metadata
      .mockRejectedValueOnce(new Error('me timeout'))
      // 5. companies index POST /api/v1/companies
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: 'comp-1',
            name: 'Saigon Roastery',
            code: 'SGN',
            bank_code: 'VCB',
            bank_account_number: '1029384756'
          }),
          { status: 201, headers: { 'content-type': 'application/json' } }
        )
      )
      // 6. companies [...path] PATCH /api/v1/companies/comp-1/bank
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            id: 'comp-1',
            name: 'Saigon Roastery',
            code: 'SGN',
            bank_code: 'MBB',
            bank_account_number: '9988776655'
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )

    vi.stubGlobal('fetch', fetchMock)

    const loginHandler = (await import('~/server/api/auth/login.post')).default as (e: unknown) => Promise<unknown>
    const { event: loginEvt } = makeBodyEvent('POST', '/api/auth/login', {
      identifier: 'staff_01',
      password: 'TempPassword123',
      tenant_id: 'acme'
    })
    const loginResult = await loginHandler(loginEvt)
    expect(loginResult).toMatchObject({
      authenticated: true,
      username: 'staff_01',
      company_id: 'comp-1',
      employee_id: 'emp-1',
      must_change_password: true
    })

    const changePwHandler = (await import('~/server/api/auth/change-password.post')).default as (
      e: unknown
    ) => Promise<unknown>
    const { event: cpEvt, resHeaders: cpResHeaders } = makeBodyEvent(
      'POST',
      '/api/auth/change-password',
      { current_password: 'TempPassword123', new_password: 'NewStrongPassword123' },
      { cookie: `${ACCESS_COOKIE_NAME}=${liveJwt}; ${REFRESH_COOKIE_NAME}=ref-1` }
    )
    const cpResult = await changePwHandler(cpEvt)
    expect(cpResult).toMatchObject({
      authenticated: true,
      must_change_password: false,
      company_id: 'comp-1'
    })
    expect(cpResHeaders.get('set-cookie')).toBeDefined()

    const companiesIndexHandler = (await import('~/server/api/companies/index')).default as (
      e: unknown
    ) => Promise<unknown>
    const { event: compCreateEvt } = makeBodyEvent(
      'POST',
      '/api/companies',
      {
        name: 'Saigon Roastery',
        code: 'sgn',
        bank_code: 'vcb',
        bank_account_number: '1029384756',
        bank_account_name: 'CONG TY SAIGON'
      },
      {
        cookie: `${ACCESS_COOKIE_NAME}=${rotatedJwt}`,
        'x-tenant-id': 'acme'
      }
    )
    const createdCompany = await companiesIndexHandler(compCreateEvt)
    expect(createdCompany).toMatchObject({ id: 'comp-1', code: 'SGN' })

    const companiesPathHandler = (await import('~/server/api/companies/[...path]')).default as (
      e: unknown
    ) => Promise<unknown>
    const { event: bankEvt } = makeBodyEvent(
      'PATCH',
      '/api/companies/comp-1/bank',
      {
        bank_code: 'mbb',
        bank_account_number: '9988776655',
        bank_account_name: 'CONG TY SAIGON'
      },
      { cookie: `${ACCESS_COOKIE_NAME}=${rotatedJwt}` }
    )
    bankEvt.context.params = { path: 'comp-1/bank' }
    const updatedBank = await companiesPathHandler(bankEvt)
    expect(updatedBank).toMatchObject({ bank_code: 'MBB', bank_account_number: '9988776655' })
  })
})
