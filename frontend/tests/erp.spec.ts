import { createError, createEvent, getHeader, getRequestURL } from 'h3'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { validateErpProxyPath } from '~/server/api/erp/[...path]'
import { assertSameOriginCsrf } from '~/server/utils/backendProxy'
import {
  ALLOWED_ORDER_TRANSITIONS,
  isValidOrderTransition,
  useErpStore
} from '~/stores/erp'
import type {
  Order,
  OrderCreatePayload,
  OrderStatus,
  RestoreResponse,
  SoftDeleteResponse,
  TelegramDispatchResponse
} from '~/types/erp'

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

function makeSampleOrder(overrides: Partial<Order> = {}): Order {
  return {
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
    created_at: '2026-11-19T10:00:00Z',
    ...overrides
  }
}

describe('useErpStore empty initial state, order transitions, and actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('starts completely empty with zero hardcoded seed data', () => {
    const store = useErpStore()

    expect(store.inventory).toEqual([])
    expect(store.orders).toEqual([])
    expect(store.customers).toEqual([])
    expect(store.employees).toEqual([])
    expect(store.webhookFeed).toEqual([])
    expect(store.metrics).toBeNull()
    expect(store.currentUser).toBeNull()
    expect(store.isAuthenticated).toBe(false)
    expect(store.isLiveAuthenticated).toBe(false)
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
    const pendingOrder = makeSampleOrder({ status: 'PENDING_PAYMENT' })
    store.orders = [pendingOrder]

    await expect(
      store.updateOrderStatus(pendingOrder.id, 'COMPLETED' as OrderStatus)
    ).rejects.toThrow(/Invalid order transition/)
    expect(fetchMock).toHaveBeenCalledTimes(0)
    expect(store.orders[0]?.status).toBe('PENDING_PAYMENT')
  })

  it('rolls back optimistic order status update when backend PATCH fails', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce({ statusCode: 500, statusMessage: 'DB error' })
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.orders = [makeSampleOrder({ id: 'ord-1', status: 'PENDING_PAYMENT' })]

    await expect(store.updateOrderStatus('ord-1', 'PAID')).rejects.toMatchObject({ statusCode: 500 })
    expect(store.orders[0]?.status).toBe('PENDING_PAYMENT')
    expect(store.updatingOrderIds['ord-1']).toBeUndefined()
  })

  it('submits server-authoritative order payload without forged unit_price_vnd', async () => {
    const serverOrder = makeSampleOrder()
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

  it('supports softDeleteInventory and restoreInventory actions', async () => {
    const softDeleted: SoftDeleteResponse = {
      resource: 'inventory',
      record_id: 'inv-1',
      tenant_id: 'default-tenant',
      is_active: false,
      deactivated_at: '2026-11-20T09:00:00Z'
    }
    const restored: RestoreResponse = {
      resource: 'inventory',
      record_id: 'inv-1',
      tenant_id: 'default-tenant',
      is_active: true,
      deactivated_at: null
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(softDeleted)
      .mockResolvedValueOnce(restored)
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.inventory = [
      {
        id: 'inv-1',
        sku: 'SKU-1',
        name: 'Specialty Coffee',
        description: 'Arabica beans',
        price_vnd: 480000,
        stock_quantity: 10,
        has_embedding: true,
        is_active: true,
        deactivated_at: null
      }
    ]

    const delRes = await store.softDeleteInventory('inv-1')
    expect(delRes.is_active).toBe(false)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/erp/inventory/inv-1', { method: 'DELETE' })
    expect(store.inventory[0]?.is_active).toBe(false)
    expect(store.inventory[0]?.deactivated_at).toBe('2026-11-20T09:00:00Z')
    expect(store.mutatingInventoryIds['inv-1']).toBeUndefined()

    const restoreRes = await store.restoreInventory('inv-1')
    expect(restoreRes.is_active).toBe(true)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/erp/inventory/inv-1/restore', { method: 'POST' })
    expect(store.inventory[0]?.is_active).toBe(true)
    expect(store.inventory[0]?.deactivated_at).toBeNull()
    expect(store.mutatingInventoryIds['inv-1']).toBeUndefined()
  })

  it('supports softDeleteCustomer and restoreCustomer actions', async () => {
    const softDeleted: SoftDeleteResponse = {
      resource: 'customers',
      record_id: 'cust-1',
      tenant_id: 'default-tenant',
      is_active: false,
      deactivated_at: '2026-11-20T09:10:00Z'
    }
    const restored: RestoreResponse = {
      resource: 'customers',
      record_id: 'cust-1',
      tenant_id: 'default-tenant',
      is_active: true,
      deactivated_at: null
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(softDeleted)
      .mockResolvedValueOnce(restored)
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.customers = [
      {
        id: 'cust-1',
        meta_psid: 'PSID_001',
        full_name: 'Nguyen Van A',
        is_active: true,
        deactivated_at: null,
        created_at: '2026-11-20T08:00:00Z'
      }
    ]

    const delRes = await store.softDeleteCustomer('cust-1')
    expect(delRes.is_active).toBe(false)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/erp/customers/cust-1', { method: 'DELETE' })
    expect(store.customers[0]?.is_active).toBe(false)
    expect(store.customers[0]?.deactivated_at).toBe('2026-11-20T09:10:00Z')
    expect(store.mutatingCustomerIds['cust-1']).toBeUndefined()

    const restoreRes = await store.restoreCustomer('cust-1')
    expect(restoreRes.is_active).toBe(true)
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/erp/customers/cust-1/restore', { method: 'POST' })
    expect(store.customers[0]?.is_active).toBe(true)
    expect(store.customers[0]?.deactivated_at).toBeNull()
    expect(store.mutatingCustomerIds['cust-1']).toBeUndefined()
  })

  it('dispatches notifyOrderTelegram and handles 401 unauthorized state', async () => {
    const dispatchRes: TelegramDispatchResponse = {
      dispatched: true,
      simulated: false,
      telegram_user_id: '123456789',
      order_code: 'ORD-2026-9001',
      preview_text: 'Order ORD-2026-9001 dispatched'
    }
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(dispatchRes)
      .mockResolvedValueOnce(dispatchRes)
      .mockRejectedValueOnce({ statusCode: 401, statusMessage: 'Unauthorized' })
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      email: 'ops@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api'
    }

    const resWithId = await store.notifyOrderTelegram('ord-1', '123456789')
    expect(resWithId).toEqual(dispatchRes)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/erp/orders/ord-1/notify-telegram', {
      method: 'POST',
      body: { telegram_user_id: '123456789' }
    })
    expect(store.notifyingOrderIds['ord-1']).toBeUndefined()

    await store.notifyOrderTelegram('ord-2')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/erp/orders/ord-2/notify-telegram', {
      method: 'POST',
      body: {}
    })

    await expect(store.notifyOrderTelegram('ord-3')).rejects.toMatchObject({ statusCode: 401 })
    expect(store.currentUser).toBeNull()
    expect(store.requiresAuth).toBe(true)
    expect(store.notifyingOrderIds['ord-3']).toBeUndefined()
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
