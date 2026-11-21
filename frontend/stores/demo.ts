import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ALLOWED_ORDER_TRANSITIONS, isValidOrderTransition } from '~/stores/erp'
import type {
  AuthUser,
  Customer,
  Employee,
  InventoryItem,
  Order,
  OrderCreatePayload,
  OrderStatus,
  TelegramDispatchResponse,
  WebhookEventLog
} from '~/types/erp'

function createDemoInventory(): InventoryItem[] {
  return [
    {
      id: 'demo-inv-01',
      tenant_id: 'demo-sandbox',
      sku: 'COFFEE-ARABICA-1KG',
      name: 'Cau Dat Arabica Specialty Beans (1kg)',
      description: 'Washed process high-altitude Lam Dong Arabica coffee beans with floral and citrus notes.',
      category: 'Coffee Beans',
      price_vnd: 480000,
      stock_quantity: 42,
      has_embedding: true,
      is_active: true,
      deactivated_at: null,
      updated_at: '2026-11-20T07:30:00Z'
    },
    {
      id: 'demo-inv-02',
      tenant_id: 'demo-sandbox',
      sku: 'MATCHA-UJI-CEREMONIAL-100G',
      name: 'Uji Ceremonial Grade Matcha Tin (100g)',
      description: 'First-harvest stone-ground Uji green tea powder for usucha and premium lattes.',
      category: 'Tea & Matcha',
      price_vnd: 650000,
      stock_quantity: 19,
      has_embedding: true,
      is_active: true,
      deactivated_at: null,
      updated_at: '2026-11-20T07:45:00Z'
    },
    {
      id: 'demo-inv-03',
      tenant_id: 'demo-sandbox',
      sku: 'EQUIP-V60-COPPER-02',
      name: 'Hario V60 Copper Dripper Size 02',
      description: 'High thermal conductivity copper pour-over coffee dripper for specialty extraction.',
      category: 'Brewing Gear',
      price_vnd: 1850000,
      stock_quantity: 7,
      has_embedding: true,
      is_active: true,
      deactivated_at: null,
      updated_at: '2026-11-20T08:00:00Z'
    },
    {
      id: 'demo-inv-04',
      tenant_id: 'demo-sandbox',
      sku: 'SYRUP-MONIN-VANILLA-700ML',
      name: 'Monin Madagascar Vanilla Syrup (700ml)',
      description: 'Pure cane sugar culinary vanilla syrup for specialty coffee beverages.',
      category: 'Barista Syrups',
      price_vnd: 245000,
      stock_quantity: 64,
      has_embedding: true,
      is_active: true,
      deactivated_at: null,
      updated_at: '2026-11-20T08:10:00Z'
    }
  ]
}

function createDemoCustomers(): Customer[] {
  return [
    {
      id: 'demo-cust-01',
      tenant_id: 'demo-sandbox',
      meta_psid: 'PSID_DEMO_101',
      full_name: 'Pham Gia Huy (FB Messenger)',
      phone: '+84900000101',
      is_active: true,
      deactivated_at: null,
      created_at: '2026-11-20T07:00:00Z'
    },
    {
      id: 'demo-cust-02',
      tenant_id: 'demo-sandbox',
      meta_psid: 'PSID_DEMO_102',
      full_name: 'Doan Thu Ha (Instagram DM)',
      phone: '+84900000102',
      is_active: true,
      deactivated_at: null,
      created_at: '2026-11-20T07:10:00Z'
    },
    {
      id: 'demo-cust-03',
      tenant_id: 'demo-sandbox',
      meta_psid: 'PSID_DEMO_103',
      full_name: 'Vu Hoang Nam (FB Messenger)',
      phone: '+84900000103',
      is_active: true,
      deactivated_at: null,
      created_at: '2026-11-20T07:20:00Z'
    }
  ]
}

function createDemoEmployees(): Employee[] {
  return [
    {
      id: 'demo-emp-01',
      tenant_id: 'demo-sandbox',
      full_name: 'Minh Tran',
      role: 'Lead Fulfillment Specialist',
      telegram_user_id: 'demo_ops_channel',
      is_active: true,
      deactivated_at: null
    },
    {
      id: 'demo-emp-02',
      tenant_id: 'demo-sandbox',
      full_name: 'Linh Nguyen',
      role: 'Warehouse Reconciliation',
      telegram_user_id: 'demo_warehouse_ops',
      is_active: true,
      deactivated_at: null
    }
  ]
}

function createDemoOrders(): Order[] {
  return [
    {
      id: 'demo-ord-01',
      tenant_id: 'demo-sandbox',
      order_code: 'DEMO-ORD-8841',
      customer_id: 'demo-cust-01',
      customer_name: 'Pham Gia Huy (FB Messenger)',
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
      sepay_reference: 'DEMO8841',
      sepay_transaction_id: null,
      vietqr_url: 'https://qr.sepay.vn/img?bank=MBBank&acc=DEMO_SANDBOX&amount=960000&des=DEMO8841',
      sepay_qr_url: 'https://qr.sepay.vn/img?bank=MBBank&acc=DEMO_SANDBOX&amount=960000&des=DEMO8841',
      assigned_employee_id: 'demo-emp-01',
      is_active: true,
      created_at: '2026-11-20T08:12:00Z'
    },
    {
      id: 'demo-ord-02',
      tenant_id: 'demo-sandbox',
      order_code: 'DEMO-ORD-8840',
      customer_id: 'demo-cust-02',
      customer_name: 'Doan Thu Ha (Instagram DM)',
      items: [
        {
          sku: 'MATCHA-UJI-CEREMONIAL-100G',
          name: 'Uji Ceremonial Grade Matcha Tin (100g)',
          quantity: 1,
          unit_price_vnd: 650000
        },
        {
          sku: 'SYRUP-MONIN-VANILLA-700ML',
          name: 'Monin Madagascar Vanilla Syrup (700ml)',
          quantity: 1,
          unit_price_vnd: 245000
        }
      ],
      total_amount_vnd: 895000,
      status: 'PAID',
      sepay_reference: 'DEMO8840',
      sepay_transaction_id: 'DEMO-TXN-8840',
      assigned_employee_id: 'demo-emp-01',
      is_active: true,
      created_at: '2026-11-20T08:05:00Z'
    },
    {
      id: 'demo-ord-03',
      tenant_id: 'demo-sandbox',
      order_code: 'DEMO-ORD-8839',
      customer_id: 'demo-cust-03',
      customer_name: 'Vu Hoang Nam (FB Messenger)',
      items: [
        {
          sku: 'EQUIP-V60-COPPER-02',
          name: 'Hario V60 Copper Dripper Size 02',
          quantity: 1,
          unit_price_vnd: 1850000
        }
      ],
      total_amount_vnd: 1850000,
      status: 'FULFILLING',
      sepay_reference: 'DEMO8839',
      sepay_transaction_id: 'DEMO-TXN-8839',
      assigned_employee_id: 'demo-emp-02',
      is_active: true,
      created_at: '2026-11-20T07:40:00Z'
    },
    {
      id: 'demo-ord-04',
      tenant_id: 'demo-sandbox',
      order_code: 'DEMO-ORD-8838',
      customer_id: 'demo-cust-01',
      customer_name: 'Pham Gia Huy (FB Messenger)',
      items: [
        {
          sku: 'COFFEE-ARABICA-1KG',
          name: 'Cau Dat Arabica Specialty Beans (1kg)',
          quantity: 1,
          unit_price_vnd: 480000
        }
      ],
      total_amount_vnd: 480000,
      status: 'COMPLETED',
      sepay_reference: 'DEMO8838',
      sepay_transaction_id: 'DEMO-TXN-8838',
      assigned_employee_id: 'demo-emp-01',
      is_active: true,
      created_at: '2026-11-20T06:55:00Z'
    }
  ]
}

function createDemoWebhookFeed(): WebhookEventLog[] {
  return [
    {
      id: 'demo-evt-01',
      source: 'SEPAY_IPN',
      event_type: 'transaction.credit_verified',
      summary: 'SePay IPN DEMO-TXN-8840: 895,000 VND -> Order DEMO-ORD-8840 (ref: DEMO8840)',
      status: 'PROCESSED',
      timestamp: '2026-11-20T08:06:00Z'
    },
    {
      id: 'demo-evt-02',
      source: 'META_GRAPH',
      event_type: 'messenger.order_created',
      summary: 'AI Sales Assistant created order DEMO-ORD-8841 with VietQR payment link',
      status: 'PROCESSED',
      timestamp: '2026-11-20T08:12:00Z'
    },
    {
      id: 'demo-evt-03',
      source: 'TELEGRAM_BOT',
      event_type: 'dispatch.order_assigned',
      summary: 'Fulfillment dispatch queued for DEMO-ORD-8839 -> Linh Nguyen',
      status: 'PROCESSED',
      timestamp: '2026-11-20T07:42:00Z'
    }
  ]
}

export const useDemoStore = defineStore('demo', () => {
  const inventory = ref<InventoryItem[]>(createDemoInventory())
  const orders = ref<Order[]>(createDemoOrders())
  const customers = ref<Customer[]>(createDemoCustomers())
  const employees = ref<Employee[]>(createDemoEmployees())
  const webhookFeed = ref<WebhookEventLog[]>(createDemoWebhookFeed())

  const demoSession = ref<AuthUser | null>(null)
  const isNotifyingTelegram = ref<boolean>(false)
  const lastTelegramResult = ref<TelegramDispatchResponse | null>(null)
  const telegramError = ref<string | null>(null)
  const searchQuery = ref<string>('')
  const showArchived = ref<boolean>(false)

  const ordersByStatus = computed<Record<OrderStatus, Order[]>>(() => {
    const grouped: Record<OrderStatus, Order[]> = {
      PENDING_PAYMENT: [],
      PAID: [],
      FULFILLING: [],
      COMPLETED: [],
      CANCELLED: []
    }
    for (const order of orders.value) {
      grouped[order.status].push(order)
    }
    return grouped
  })

  function resetDemoState(): void {
    inventory.value = createDemoInventory()
    orders.value = createDemoOrders()
    customers.value = createDemoCustomers()
    employees.value = createDemoEmployees()
    webhookFeed.value = createDemoWebhookFeed()
    lastTelegramResult.value = null
    telegramError.value = null
    searchQuery.value = ''
    showArchived.value = false
  }

  function createOrder(payload: OrderCreatePayload): Order {
    const customer = customers.value.find((c) => c.id === payload.customer_id && c.is_active !== false)
    if (!customer) {
      throw new Error('Select an active customer before creating a demo order')
    }
    const resolvedItems = payload.items
      .filter((line) => line.sku && line.quantity >= 1)
      .map((line) => {
        const inv = inventory.value.find((item) => item.sku === line.sku && item.is_active !== false)
        if (!inv) {
          throw new Error(`SKU ${line.sku} is archived or unavailable`)
        }
        return {
          sku: inv.sku,
          name: inv.name,
          quantity: Math.floor(line.quantity),
          unit_price_vnd: inv.price_vnd
        }
      })

    if (resolvedItems.length === 0) {
      throw new Error('Add at least one valid product line')
    }

    const total = resolvedItems.reduce((sum, item) => sum + item.unit_price_vnd * item.quantity, 0)
    const suffix = String(Math.floor(1000 + Math.random() * 9000))
    const orderCode = `DEMO-ORD-${suffix}`
    const refCode = `DEMO${suffix}`

    const created: Order = {
      id: `demo-ord-${Date.now()}`,
      tenant_id: 'demo-sandbox',
      order_code: orderCode,
      customer_id: customer.id,
      customer_name: customer.full_name,
      items: resolvedItems,
      total_amount_vnd: total,
      status: 'PENDING_PAYMENT',
      sepay_reference: refCode,
      sepay_transaction_id: null,
      vietqr_url: `https://qr.sepay.vn/img?bank=MBBank&acc=DEMO_SANDBOX&amount=${total}&des=${refCode}`,
      sepay_qr_url: `https://qr.sepay.vn/img?bank=MBBank&acc=DEMO_SANDBOX&amount=${total}&des=${refCode}`,
      assigned_employee_id: payload.assigned_employee_id || null,
      is_active: true,
      created_at: new Date().toISOString()
    }

    orders.value.unshift(created)
    webhookFeed.value.unshift({
      id: `demo-evt-${Date.now()}`,
      source: 'META_GRAPH',
      event_type: 'order.created',
      summary: `Created sandbox order ${orderCode} (${total.toLocaleString('vi-VN')} VND)`,
      status: 'PROCESSED',
      timestamp: created.created_at
    })

    return created
  }

  function updateOrderStatus(orderId: string, nextStatus: OrderStatus): void {
    const target = orders.value.find((order) => order.id === orderId)
    if (!target) return
    if (!isValidOrderTransition(target.status, nextStatus)) {
      const allowed = ALLOWED_ORDER_TRANSITIONS[target.status].join(', ') || 'none'
      throw new Error(`Invalid transition ${target.status} -> ${nextStatus} (allowed: ${allowed})`)
    }
    target.status = nextStatus
    target.updated_at = new Date().toISOString()
    if (nextStatus === 'PAID' && !target.assigned_employee_id && employees.value.length > 0) {
      target.assigned_employee_id = employees.value[0]?.id ?? null
    }
    webhookFeed.value.unshift({
      id: `demo-evt-${Date.now()}`,
      source: nextStatus === 'PAID' ? 'SEPAY_IPN' : 'TELEGRAM_BOT',
      event_type: `order.${nextStatus.toLowerCase()}`,
      summary: `Order ${target.order_code} transitioned to ${nextStatus}`,
      status: 'PROCESSED',
      timestamp: target.updated_at
    })
  }

  function softDeleteInventory(itemId: string): void {
    const target = inventory.value.find((item) => item.id === itemId)
    if (target) {
      target.is_active = false
      target.deactivated_at = new Date().toISOString()
    }
  }

  function restoreInventory(itemId: string): void {
    const target = inventory.value.find((item) => item.id === itemId)
    if (target) {
      target.is_active = true
      target.deactivated_at = null
    }
  }

  function softDeleteCustomer(customerId: string): void {
    const target = customers.value.find((c) => c.id === customerId)
    if (target) {
      target.is_active = false
      target.deactivated_at = new Date().toISOString()
    }
  }

  function restoreCustomer(customerId: string): void {
    const target = customers.value.find((c) => c.id === customerId)
    if (target) {
      target.is_active = true
      target.deactivated_at = null
    }
  }

  async function ensureDemoSession(): Promise<AuthUser> {
    if (demoSession.value?.authenticated && demoSession.value.permission_group === 'demo_api') {
      return demoSession.value
    }
    const session = await $fetch<AuthUser>('/api/auth/demo-signin', {
      method: 'POST'
    })
    demoSession.value = session
    return session
  }

  async function triggerTelegramNotification(order: Order): Promise<TelegramDispatchResponse> {
    isNotifyingTelegram.value = true
    telegramError.value = null
    try {
      await ensureDemoSession()

      const assignee = employees.value.find((emp) => emp.id === order.assigned_employee_id)
      const itemsSummary =
        order.items.map((item) => `${item.quantity}x ${item.name}`).join(', ') || order.order_code

      const payload = {
        order_code: order.order_code,
        total_amount_vnd: order.total_amount_vnd,
        items_summary: itemsSummary.slice(0, 480),
        customer_name: order.customer_name ?? null,
        telegram_user_id: assignee?.telegram_user_id ?? null,
        sepay_reference: order.sepay_reference ?? null
      }

      let response: TelegramDispatchResponse
      try {
        response = await $fetch<TelegramDispatchResponse>('/api/demo/notify-telegram', {
          method: 'POST',
          body: payload
        })
      } catch (err) {
        const status =
          err && typeof err === 'object' && 'statusCode' in err
            ? (err as { statusCode?: number }).statusCode
            : undefined
        if (status === 401) {
          demoSession.value = null
          await ensureDemoSession()
          response = await $fetch<TelegramDispatchResponse>('/api/demo/notify-telegram', {
            method: 'POST',
            body: payload
          })
        } else {
          throw err
        }
      }

      lastTelegramResult.value = response
      webhookFeed.value.unshift({
        id: `demo-tg-${Date.now()}`,
        source: 'TELEGRAM_BOT',
        event_type: response.simulated ? 'dispatch.telegram_simulated' : 'dispatch.telegram_sent',
        summary: `Telegram alert for ${order.order_code} (${response.simulated ? 'simulated' : 'live'}) -> ${response.telegram_user_id || 'demo chat'}`,
        status: response.dispatched ? 'PROCESSED' : 'FAILED',
        timestamp: new Date().toISOString()
      })
      return response
    } catch (err) {
      const msg =
        err && typeof err === 'object' && 'statusMessage' in err && typeof (err as { statusMessage?: unknown }).statusMessage === 'string'
          ? (err as { statusMessage: string }).statusMessage
          : 'Failed to dispatch demo Telegram alert'
      telegramError.value = msg
      throw err
    } finally {
      isNotifyingTelegram.value = false
    }
  }

  return {
    inventory,
    orders,
    customers,
    employees,
    webhookFeed,
    demoSession,
    isNotifyingTelegram,
    lastTelegramResult,
    telegramError,
    searchQuery,
    showArchived,
    ordersByStatus,
    resetDemoState,
    createOrder,
    updateOrderStatus,
    softDeleteInventory,
    restoreInventory,
    softDeleteCustomer,
    restoreCustomer,
    ensureDemoSession,
    triggerTelegramNotification
  }
})
