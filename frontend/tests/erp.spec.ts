import { createEvent, createError, getHeader, getRequestURL } from 'h3'
import { createPinia, setActivePinia } from 'pinia'
import { validateErpProxyPath } from '~/server/api/erp/[...path]'
import { assertSameOriginCsrf } from '~/server/utils/backendProxy'
import {
  ALLOWED_ORDER_TRANSITIONS,
  isValidOrderTransition,
  useErpStore
} from '~/stores/erp'
import type { Order, OrderCreatePayload, OrderStatus } from '~/types/erp'

interface VitestExpectation {
  toBe(expected: unknown): void
  toEqual(expected: unknown): void
  toMatchObject(expected: Record<string, unknown>): void
  toHaveLength(expected: number): void
  toBeUndefined(): void
  toHaveBeenCalledTimes(expected: number): void
  toHaveBeenCalledWith(...args: unknown[]): void
  toThrow(expected?: string | RegExp): void
  not: VitestExpectation
  rejects: {
    toThrow(expected?: string | RegExp): Promise<void>
  }
}

interface VitestMock<TArgs extends unknown[] = unknown[], TReturn = unknown> {
  (...args: TArgs): TReturn
  mockResolvedValueOnce(value: Awaited<TReturn>): VitestMock<TArgs, TReturn>
  mockRejectedValueOnce(error: unknown): VitestMock<TArgs, TReturn>
}

declare const describe: (name: string, fn: () => void) => void
declare const it: (name: string, fn: () => void | Promise<void>) => void
declare const beforeEach: (fn: () => void) => void
declare const afterEach: (fn: () => void) => void
declare const expect: (actual: unknown) => VitestExpectation
declare const vi: {
  fn<TArgs extends unknown[] = unknown[], TReturn = unknown>(
    impl?: (...args: TArgs) => TReturn
  ): VitestMock<TArgs, TReturn>
  stubGlobal(name: string, value: unknown): void
  unstubAllGlobals(): void
  restoreAllMocks(): void
}

function makeH3Event(headers: Record<string, string>, url = '/api/erp/orders') {
  const req = {
    method: 'POST',
    url,
    headers: {
      host: 'erp.example.com',
      ...headers
    }
  }
  const res = {}
  return createEvent(req as never, res as never)
}

describe('useErpStore order transitions, authoritative payload, and telemetry mapping', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('enforces ALLOWED_ORDER_TRANSITIONS state machine rules', async () => {
    expect(ALLOWED_ORDER_TRANSITIONS).toEqual({
      PENDING_PAYMENT: ['PAID', 'CANCELLED'],
      PAID: ['FULFILLING', 'CANCELLED'],
      FULFILLING: ['COMPLETED', 'CANCELLED'],
      COMPLETED: [],
      CANCELLED: []
    })

    expect(isValidOrderTransition('PENDING_PAYMENT', 'PAID')).toBe(true)
    expect(isValidOrderTransition('PENDING_PAYMENT', 'CANCELLED')).toBe(true)
    expect(isValidOrderTransition('PAID', 'FULFILLING')).toBe(true)
    expect(isValidOrderTransition('FULFILLING', 'COMPLETED')).toBe(true)

    expect(isValidOrderTransition('PENDING_PAYMENT', 'COMPLETED')).toBe(false)
    expect(isValidOrderTransition('PENDING_PAYMENT', 'FULFILLING')).toBe(false)
    expect(isValidOrderTransition('COMPLETED', 'PAID')).toBe(false)
    expect(isValidOrderTransition('CANCELLED', 'PENDING_PAYMENT')).toBe(false)

    const fetchMock = vi.fn()
    vi.stubGlobal('$fetch', fetchMock)
    const store = useErpStore()
    const pendingOrder = store.orders.find((order) => order.status === 'PENDING_PAYMENT')
    expect(pendingOrder).not.toBeUndefined()

    await expect(
      store.updateOrderStatus(pendingOrder!.id, 'COMPLETED' as OrderStatus)
    ).rejects.toThrow(/Invalid order transition/)
    expect(fetchMock).toHaveBeenCalledTimes(0)
  })

  it('submits server-authoritative order payload without forged unit_price_vnd', async () => {
    const serverOrder: Order = {
      id: '55555555-5555-4555-8555-555555555501',
      tenant_id: 'default-tenant',
      order_code: 'ORD-2026-9001',
      customer_id: '22222222-2222-4222-8222-222222222201',
      items: [
        {
          sku: 'COFFEE-ARABICA-1KG',
          name: 'Cau Dat Arabica Specialty Beans (1kg)',
          quantity: 2,
          unit_price_vnd: 480000
        }
      ],
      total_amount_vnd: 960000,
      status: 'PENDING_PAYMENT',
      sepay_reference: 'SEPAY9001',
      vietqr_url: 'https://qr.sepay.vn/img?amount=960000&des=SEPAY9001',
      created_at: '2026-11-19T10:00:00Z'
    }

    const fetchMock = vi.fn().mockResolvedValueOnce(serverOrder)
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    const maliciousPayload = {
      customer_id: '22222222-2222-4222-8222-222222222201',
      items: [
        {
          sku: 'COFFEE-ARABICA-1KG',
          quantity: 2,
          unit_price_vnd: 1
        }
      ]
    } as unknown as OrderCreatePayload

    const created = await store.createOrder(maliciousPayload)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith('/api/erp/orders', {
      method: 'POST',
      body: {
        customer_id: '22222222-2222-4222-8222-222222222201',
        items: [{ sku: 'COFFEE-ARABICA-1KG', quantity: 2 }]
      }
    })
    expect(created.total_amount_vnd).toBe(960000)
  })

  it('normalizes webhook telemetry logs and backend metrics', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce([
        {
          id: 101,
          sepay_transaction_id: 'TXN-7788',
          order_code: 'ORD-2026-8841',
          transfer_type: 'in',
          transfer_amount: 960000,
          reference_code: 'SEPAY8841',
          processing_status: 'processed',
          created_at: '2026-11-19T09:15:00Z'
        },
        {
          id: 102,
          source: 'TELEGRAM_BOT',
          event_type: 'dispatch.failed',
          summary: 'Telegram webhook retry queued',
          status: 'FAILED',
          timestamp: '2026-11-19T09:16:00Z'
        }
      ])
      .mockResolvedValueOnce({
        avg_rag_latency_ms: 214.6,
        active_inventory_skus: 4,
        vectorized_skus: 4,
        pending_vietqr_orders: 1,
        paid_and_dispatched_orders: 3,
        total_orders: 4,
        webhook_events_count: 2
      })
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    await store.fetchWebhookLogs()
    await store.fetchMetrics()

    expect(store.webhookFeed).toHaveLength(2)
    expect(store.webhookFeed[0]).toMatchObject({
      id: '101',
      source: 'SEPAY_IPN',
      event_type: 'transaction.credit_verified',
      status: 'PROCESSED'
    })
    expect(store.webhookFeed[1]).toMatchObject({
      id: '102',
      source: 'TELEGRAM_BOT',
      event_type: 'dispatch.failed',
      status: 'FAILED'
    })
    expect(store.avgRagLatencyMs).toBe(215)
    expect(store.metrics?.paid_and_dispatched_orders).toBe(3)
  })
})

describe('Nitro CSRF & ERP proxy path traversal guards', () => {
  beforeEach(() => {
    vi.stubGlobal('createError', createError)
    vi.stubGlobal('getHeader', getHeader)
    vi.stubGlobal('getRequestURL', getRequestURL)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('allows same-origin CSRF requests and blocks cross-site or null origins', () => {
    const sameOriginEvent = makeH3Event({
      origin: 'http://erp.example.com',
      'sec-fetch-site': 'same-origin'
    })
    expect(() => assertSameOriginCsrf(sameOriginEvent)).not.toThrow()

    const crossSiteEvent = makeH3Event({
      origin: 'http://erp.example.com',
      'sec-fetch-site': 'cross-site'
    })
    expect(() => assertSameOriginCsrf(crossSiteEvent)).toThrow(/Cross-site/)

    const nullOriginEvent = makeH3Event({
      origin: 'null'
    })
    expect(() => assertSameOriginCsrf(nullOriginEvent)).toThrow(/Null origin/)

    const evilOriginEvent = makeH3Event({
      origin: 'https://attacker.example.com'
    })
    expect(() => assertSameOriginCsrf(evilOriginEvent)).toThrow(/Origin mismatch/)

    const missingHeadersEvent = makeH3Event({})
    expect(() => assertSameOriginCsrf(missingHeadersEvent)).toThrow(
      /Missing same-origin CSRF/
    )
  })

  it('validates allowed ERP proxy paths and blocks encoded traversal attempts', () => {
    expect(validateErpProxyPath('orders')).toBe('orders')
    expect(
      validateErpProxyPath('/orders/44444444-4444-4444-8444-444444444401/status')
    ).toBe('orders/44444444-4444-4444-8444-444444444401/status')
    expect(validateErpProxyPath('inventory/search')).toBe('inventory/search')

    const blockedPaths = [
      '',
      '/',
      '.',
      '..',
      '../auth/login',
      '%2e%2e/auth/login',
      'orders/%2e%2e/secret',
      'orders/%2E%2E/secret',
      'orders/%252e%252e/secret',
      'orders/..%2fadmin',
      'orders\\..\\admin',
      'orders?admin=true'
    ]

    for (const candidate of blockedPaths) {
      expect(() => validateErpProxyPath(candidate)).toThrow(/Invalid ERP proxy path/)
    }
  })
})
