import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDemoStore } from '~/stores/demo'
import type { AuthUser, TelegramDispatchResponse } from '~/types/erp'

describe('useDemoStore isolated sandbox state, reset, and Telegram dispatch', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('initializes with isolated demo-sandbox seed data', () => {
    const store = useDemoStore()

    expect(store.inventory.length).toBeGreaterThan(0)
    expect(store.orders.length).toBeGreaterThan(0)
    expect(store.customers.length).toBeGreaterThan(0)
    expect(store.employees.length).toBeGreaterThan(0)
    expect(store.webhookFeed.length).toBeGreaterThan(0)

    expect(store.inventory.every((item) => item.tenant_id === 'demo-sandbox')).toBe(true)
    expect(store.orders.every((order) => order.tenant_id === 'demo-sandbox')).toBe(true)
    expect(store.customers.every((cust) => cust.tenant_id === 'demo-sandbox')).toBe(true)
    expect(store.employees.every((emp) => emp.tenant_id === 'demo-sandbox')).toBe(true)
  })

  it('mutates demo state locally and restores clean initial state on resetDemoState()', () => {
    const store = useDemoStore()
    const initialInventoryCount = store.inventory.length
    const initialOrderCount = store.orders.length
    const initialCustomerCount = store.customers.length
    const initialWebhookCount = store.webhookFeed.length

    const targetItem = store.inventory[0]!
    const targetCustomer = store.customers[0]!
    const targetOrder = store.orders.find((o) => o.status === 'PENDING_PAYMENT')!

    store.softDeleteInventory(targetItem.id)
    expect(store.inventory[0]?.is_active).toBe(false)
    expect(store.inventory[0]?.deactivated_at).not.toBeNull()

    store.restoreInventory(targetItem.id)
    expect(store.inventory[0]?.is_active).toBe(true)
    expect(store.inventory[0]?.deactivated_at).toBeNull()

    store.softDeleteCustomer(targetCustomer.id)
    expect(store.customers[0]?.is_active).toBe(false)

    store.restoreCustomer(targetCustomer.id)
    expect(store.customers[0]?.is_active).toBe(true)

    const createdOrder = store.createOrder({
      customer_id: targetCustomer.id,
      items: [{ sku: targetItem.sku, quantity: 1 }]
    })
    expect(store.orders).toHaveLength(initialOrderCount + 1)
    expect(createdOrder.tenant_id).toBe('demo-sandbox')

    store.updateOrderStatus(targetOrder.id, 'PAID')
    expect(targetOrder.status).toBe('PAID')

    store.searchQuery = 'Arabica'
    store.showArchived = true
    store.telegramError = 'Simulated transient error'
    store.lastTelegramResult = {
      dispatched: true,
      simulated: true,
      telegram_user_id: 'demo_ops_channel',
      order_code: 'DEMO-ORD-8841'
    }

    store.resetDemoState()

    expect(store.inventory).toHaveLength(initialInventoryCount)
    expect(store.orders).toHaveLength(initialOrderCount)
    expect(store.customers).toHaveLength(initialCustomerCount)
    expect(store.webhookFeed).toHaveLength(initialWebhookCount)
    expect(store.orders.find((o) => o.id === targetOrder.id)?.status).toBe('PENDING_PAYMENT')
    expect(store.searchQuery).toBe('')
    expect(store.showArchived).toBe(false)
    expect(store.telegramError).toBeNull()
    expect(store.lastTelegramResult).toBeNull()
  })

  it('triggerTelegramNotification calls /api/auth/demo-signin then /api/demo/notify-telegram', async () => {
    const demoSession: AuthUser = {
      authenticated: true,
      user_id: 'demo-user-01',
      email: 'demo@sandbox.local',
      tenant_id: 'demo-sandbox',
      role: 'demo',
      permission_group: 'demo_api'
    }
    const tgResponse: TelegramDispatchResponse = {
      dispatched: true,
      simulated: true,
      telegram_user_id: 'demo_ops_channel',
      order_code: 'DEMO-ORD-8841',
      preview_text: 'Simulated Telegram dispatch for DEMO-ORD-8841'
    }

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(demoSession)
      .mockResolvedValueOnce(tgResponse)
    vi.stubGlobal('$fetch', fetchMock)

    const store = useDemoStore()
    const targetOrder = store.orders[0]!
    const initialFeedLen = store.webhookFeed.length

    const result = await store.triggerTelegramNotification(targetOrder)

    expect(result).toEqual(tgResponse)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/auth/demo-signin', {
      method: 'POST'
    })
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/demo/notify-telegram', {
      method: 'POST',
      body: {
        order_code: targetOrder.order_code,
        total_amount_vnd: targetOrder.total_amount_vnd,
        items_summary: '2x Cau Dat Arabica Specialty Beans (1kg)',
        customer_name: targetOrder.customer_name,
        telegram_user_id: 'demo_ops_channel',
        sepay_reference: targetOrder.sepay_reference
      }
    })
    expect(store.demoSession).toEqual(demoSession)
    expect(store.lastTelegramResult).toEqual(tgResponse)
    expect(store.isNotifyingTelegram).toBe(false)
    expect(store.telegramError).toBeNull()
    expect(store.webhookFeed).toHaveLength(initialFeedLen + 1)
    expect(store.webhookFeed[0]).toMatchObject({
      source: 'TELEGRAM_BOT',
      event_type: 'dispatch.telegram_simulated',
      status: 'PROCESSED'
    })
  })

  it('re-authenticates demo session on 401 and retries /api/demo/notify-telegram', async () => {
    const refreshedSession: AuthUser = {
      authenticated: true,
      user_id: 'demo-user-01',
      email: 'demo@sandbox.local',
      tenant_id: 'demo-sandbox',
      role: 'demo',
      permission_group: 'demo_api'
    }
    const tgResponse: TelegramDispatchResponse = {
      dispatched: true,
      simulated: true,
      telegram_user_id: 'demo_ops_channel',
      order_code: 'DEMO-ORD-8841'
    }

    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce({ statusCode: 401, statusMessage: 'Demo token expired' })
      .mockResolvedValueOnce(refreshedSession)
      .mockResolvedValueOnce(tgResponse)
    vi.stubGlobal('$fetch', fetchMock)

    const store = useDemoStore()
    store.demoSession = { ...refreshedSession }
    const result = await store.triggerTelegramNotification(store.orders[0]!)

    expect(result).toEqual(tgResponse)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/demo/notify-telegram', expect.any(Object))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/demo-signin', { method: 'POST' })
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/demo/notify-telegram', expect.any(Object))
  })
})
