import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  AuthUser,
  Customer,
  Employee,
  ErpTelemetryMetrics,
  InventoryItem,
  LoginCredentials,
  Order,
  OrderCreatePayload,
  OrderStatus,
  WebhookEventLog,
  WebhookEventStatus,
  WebhookLogResponse,
  WebhookSource
} from '~/types/erp'

export const ALLOWED_ORDER_TRANSITIONS: Record<OrderStatus, readonly OrderStatus[]> = {
  PENDING_PAYMENT: ['PAID', 'CANCELLED'],
  PAID: ['FULFILLING', 'CANCELLED'],
  FULFILLING: ['COMPLETED', 'CANCELLED'],
  COMPLETED: [],
  CANCELLED: []
}

export function isValidOrderTransition(from: OrderStatus, to: OrderStatus): boolean {
  return ALLOWED_ORDER_TRANSITIONS[from].includes(to)
}

const INITIAL_INVENTORY: InventoryItem[] = [
  {
    id: '11111111-1111-4111-8111-111111111101',
    tenant_id: 'default-tenant',
    sku: 'COFFEE-ARABICA-1KG',
    name: 'Cau Dat Arabica Specialty Beans (1kg)',
    description: 'Washed process high-altitude Lam Dong Arabica coffee beans with floral and citrus notes.',
    category: 'Coffee Beans',
    price_vnd: 480000,
    stock_quantity: 42,
    has_embedding: true,
    similarity_score: 0.96,
    similarity_distance: 0.08,
    updated_at: '2026-11-18T07:30:00Z'
  },
  {
    id: '11111111-1111-4111-8111-111111111102',
    tenant_id: 'default-tenant',
    sku: 'MATCHA-UJI-CEREMONIAL-100G',
    name: 'Uji Ceremonial Grade Matcha Tin (100g)',
    description: 'First-harvest stone-ground Uji green tea powder for usucha and premium lattes.',
    category: 'Tea & Matcha',
    price_vnd: 650000,
    stock_quantity: 19,
    has_embedding: true,
    similarity_score: 0.91,
    similarity_distance: 0.18,
    updated_at: '2026-11-18T07:45:00Z'
  },
  {
    id: '11111111-1111-4111-8111-111111111103',
    tenant_id: 'default-tenant',
    sku: 'EQUIP-V60-COPPER-02',
    name: 'Hario V60 Copper Dripper Size 02',
    description: 'High thermal conductivity copper pour-over coffee dripper for specialty extraction.',
    category: 'Brewing Gear',
    price_vnd: 1850000,
    stock_quantity: 7,
    has_embedding: true,
    similarity_score: 0.88,
    similarity_distance: 0.24,
    updated_at: '2026-11-18T08:00:00Z'
  },
  {
    id: '11111111-1111-4111-8111-111111111104',
    tenant_id: 'default-tenant',
    sku: 'SYRUP-MONIN-VANILLA-700ML',
    name: 'Monin Madagascar Vanilla Syrup (700ml)',
    description: 'Pure cane sugar culinary vanilla syrup for specialty coffee beverages.',
    category: 'Barista Syrups',
    price_vnd: 245000,
    stock_quantity: 64,
    has_embedding: true,
    similarity_score: 0.84,
    similarity_distance: 0.32,
    updated_at: '2026-11-18T08:10:00Z'
  }
]

const INITIAL_CUSTOMERS: Customer[] = [
  {
    id: '22222222-2222-4222-8222-222222222201',
    tenant_id: 'default-tenant',
    meta_psid: 'PSID_992814',
    full_name: 'Pham Gia Huy (FB Messenger)',
    phone: '+84901234501',
    created_at: '2026-11-18T07:00:00Z'
  },
  {
    id: '22222222-2222-4222-8222-222222222202',
    tenant_id: 'default-tenant',
    meta_psid: 'PSID_884120',
    full_name: 'Doan Thu Ha (Instagram DM)',
    phone: '+84901234502',
    created_at: '2026-11-18T07:10:00Z'
  },
  {
    id: '22222222-2222-4222-8222-222222222203',
    tenant_id: 'default-tenant',
    meta_psid: 'PSID_773190',
    full_name: 'Vu Hoang Nam (FB Messenger)',
    phone: '+84901234503',
    created_at: '2026-11-18T07:20:00Z'
  }
]

const INITIAL_EMPLOYEES: Employee[] = [
  {
    id: '33333333-3333-4333-8333-333333333301',
    tenant_id: 'default-tenant',
    full_name: 'Minh Tran',
    role: 'Lead Fulfillment Specialist',
    telegram_user_id: 'tg_849102938',
    is_active: true
  },
  {
    id: '33333333-3333-4333-8333-333333333302',
    tenant_id: 'default-tenant',
    full_name: 'Linh Nguyen',
    role: 'Warehouse & VietQR Reconciliation',
    telegram_user_id: 'tg_849556120',
    is_active: true
  },
  {
    id: '33333333-3333-4333-8333-333333333303',
    tenant_id: 'default-tenant',
    full_name: 'Khoa Le',
    role: 'Express Dispatch Courier',
    telegram_user_id: 'tg_849883412',
    is_active: true
  }
]

const INITIAL_ORDERS: Order[] = [
  {
    id: '44444444-4444-4444-8444-444444444401',
    tenant_id: 'default-tenant',
    order_code: 'ORD-2026-8841',
    customer_id: '22222222-2222-4222-8222-222222222201',
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
    sepay_reference: 'SEPAY8841',
    sepay_transaction_id: null,
    vietqr_url: 'https://qr.sepay.vn/img?bank=MBBank&acc=0000123456789&amount=960000&des=SEPAY8841',
    sepay_qr_url: 'https://qr.sepay.vn/img?bank=MBBank&acc=0000123456789&amount=960000&des=SEPAY8841',
    created_at: '2026-11-18T08:12:00Z'
  },
  {
    id: '44444444-4444-4444-8444-444444444402',
    tenant_id: 'default-tenant',
    order_code: 'ORD-2026-8840',
    customer_id: '22222222-2222-4222-8222-222222222202',
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
    sepay_reference: 'SEPAY8840',
    sepay_transaction_id: 'SEPAY-TXN-8840',
    assigned_employee_id: '33333333-3333-4333-8333-333333333301',
    created_at: '2026-11-18T08:05:00Z'
  },
  {
    id: '44444444-4444-4444-8444-444444444403',
    tenant_id: 'default-tenant',
    order_code: 'ORD-2026-8839',
    customer_id: '22222222-2222-4222-8222-222222222203',
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
    sepay_reference: 'SEPAY8839',
    sepay_transaction_id: 'SEPAY-TXN-8839',
    assigned_employee_id: '33333333-3333-4333-8333-333333333302',
    created_at: '2026-11-18T07:40:00Z'
  },
  {
    id: '44444444-4444-4444-8444-444444444404',
    tenant_id: 'default-tenant',
    order_code: 'ORD-2026-8838',
    customer_id: '22222222-2222-4222-8222-222222222204',
    customer_name: 'Nguyen Minh Chau (FB Messenger)',
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
    sepay_reference: 'SEPAY8838',
    sepay_transaction_id: 'SEPAY-TXN-8838',
    assigned_employee_id: '33333333-3333-4333-8333-333333333303',
    created_at: '2026-11-18T06:55:00Z'
  }
]

function extractHttpStatus(err: unknown): number | undefined {
  if (!err || typeof err !== 'object') return undefined
  const record = err as { statusCode?: number; status?: number; response?: { status?: number } }
  return record.statusCode ?? record.status ?? record.response?.status
}

function extractErrorMessage(err: unknown, fallback: string): string {
  if (!err || typeof err !== 'object') return fallback
  const record = err as {
    data?: { detail?: unknown; message?: unknown }
    statusMessage?: unknown
    message?: unknown
  }
  if (typeof record.data?.detail === 'string' && record.data.detail.trim()) {
    return record.data.detail
  }
  if (typeof record.data?.message === 'string' && record.data.message.trim()) {
    return record.data.message
  }
  if (typeof record.statusMessage === 'string' && record.statusMessage.trim()) {
    return record.statusMessage
  }
  if (typeof record.message === 'string' && record.message.trim()) {
    return record.message
  }
  return fallback
}

function normalizeInventoryItem(item: InventoryItem): InventoryItem {
  const computedScore =
    item.similarity_score != null
      ? item.similarity_score
      : item.similarity_distance != null
        ? Math.max(0, Number((1 - item.similarity_distance / 2).toFixed(4)))
        : null

  return {
    ...item,
    category: item.category || 'General Merchandise',
    similarity_score: computedScore
  }
}

function normalizeOrder(order: Order): Order {
  const items = Array.isArray(order.items) && order.items.length > 0
    ? order.items
    : Array.isArray(order.items_json)
      ? order.items_json
      : []

  const vietqrUrl = order.vietqr_url ?? order.sepay_qr_url ?? null
  const sepayReference = order.sepay_reference || order.sepay_transaction_id || order.order_code

  return {
    ...order,
    customer_name: order.customer_name || `Customer ${order.customer_id.slice(0, 8)}`,
    items,
    items_json: items,
    vietqr_url: vietqrUrl,
    sepay_qr_url: vietqrUrl,
    sepay_reference: sepayReference
  }
}

function normalizeWebhookStatus(rawStatus?: string): WebhookEventStatus {
  const upper = (rawStatus || 'PROCESSED').toUpperCase()
  if (upper === 'PENDING' || upper === 'PROCESSING') return 'PENDING'
  if (upper === 'FAILED' || upper === 'REJECTED' || upper === 'ERROR') return 'FAILED'
  return 'PROCESSED'
}

function normalizeWebhookSource(rawSource?: string): WebhookSource {
  if (rawSource === 'META_GRAPH' || rawSource === 'SEPAY_IPN' || rawSource === 'TELEGRAM_BOT') {
    return rawSource
  }
  return 'SEPAY_IPN'
}

function normalizeWebhookLog(log: WebhookLogResponse): WebhookEventLog {
  if (log.source && log.event_type && log.summary && log.timestamp) {
    return {
      id: String(log.id),
      source: normalizeWebhookSource(log.source),
      event_type: log.event_type,
      summary: log.summary,
      status: normalizeWebhookStatus(log.status),
      timestamp: log.timestamp
    }
  }

  const amount = typeof log.transfer_amount === 'number' ? log.transfer_amount : 0
  const orderSuffix = log.order_code ? ` -> Order ${log.order_code}` : ''
  const refSuffix = log.reference_code ? ` (ref: ${log.reference_code})` : ''
  const txId = log.sepay_transaction_id || String(log.id)

  return {
    id: String(log.id),
    source: normalizeWebhookSource(log.source),
    event_type: log.event_type || (log.transfer_type === 'in' ? 'transaction.credit_verified' : `transaction.${log.transfer_type || 'webhook'}`),
    summary: log.summary || `SePay IPN ${txId}: ${amount.toLocaleString('vi-VN')} VND${orderSuffix}${refSuffix}`,
    status: normalizeWebhookStatus(log.processing_status || log.status),
    timestamp: log.created_at || log.timestamp || new Date().toISOString()
  }
}

export const useErpStore = defineStore('erp', () => {
  const currentUser = ref<AuthUser | null>(null)
  const isAuthenticated = computed<boolean>(() => Boolean(currentUser.value?.authenticated))
  const requiresAuth = ref<boolean>(false)
  const isAuthenticating = ref<boolean>(false)
  const authError = ref<string | null>(null)

  const inventory = ref<InventoryItem[]>([...INITIAL_INVENTORY])
  const orders = ref<Order[]>([...INITIAL_ORDERS])
  const customers = ref<Customer[]>([...INITIAL_CUSTOMERS])
  const employees = ref<Employee[]>([...INITIAL_EMPLOYEES])
  const webhookFeed = ref<WebhookEventLog[]>([])
  const metrics = ref<ErpTelemetryMetrics | null>(null)

  const isLoadingInventory = ref<boolean>(false)
  const isLoadingOrders = ref<boolean>(false)
  const updatingOrderIds = ref<Record<string, boolean>>({})
  const isCreatingOrder = ref<boolean>(false)
  const isLoadingWebhooks = ref<boolean>(false)
  const webhookError = ref<string | null>(null)
  const lastSearchQuery = ref<string>('')
  const avgRagLatencyMs = ref<number>(340)

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

  function markUnauthorizedIf401(err: unknown): boolean {
    if (extractHttpStatus(err) === 401) {
      currentUser.value = null
      requiresAuth.value = true
      return true
    }
    return false
  }

  async function fetchSession(): Promise<AuthUser | null> {
    try {
      const data = await $fetch<AuthUser>('/api/auth/me')
      currentUser.value = {
        ...data,
        authenticated: true
      }
      requiresAuth.value = false
      return currentUser.value
    } catch {
      currentUser.value = null
      return null
    }
  }

  async function login(credentials: LoginCredentials): Promise<AuthUser> {
    isAuthenticating.value = true
    authError.value = null
    try {
      const data = await $fetch<AuthUser>('/api/auth/login', {
        method: 'POST',
        body: {
          email: credentials.email.trim(),
          password: credentials.password,
          tenant_id: credentials.tenant_id.trim() || 'default-tenant'
        }
      })
      currentUser.value = {
        ...data,
        authenticated: true
      }
      requiresAuth.value = false
      return currentUser.value
    } catch (err) {
      authError.value = extractErrorMessage(err, 'Invalid operator credentials')
      throw err
    } finally {
      isAuthenticating.value = false
    }
  }

  async function logout(): Promise<void> {
    try {
      await $fetch('/api/auth/logout', { method: 'POST' })
    } catch {
      // Clear local session state even if server session already expired
    } finally {
      currentUser.value = null
      requiresAuth.value = true
    }
  }

  async function fetchInventory(): Promise<void> {
    isLoadingInventory.value = true
    try {
      const data = await $fetch<InventoryItem[]>('/api/erp/inventory')
      if (Array.isArray(data) && data.length > 0) {
        inventory.value = data.map(normalizeInventoryItem)
      }
    } catch (err) {
      markUnauthorizedIf401(err)
      // Keep resilient local seed data when unauthenticated/offline in preview mode
    } finally {
      isLoadingInventory.value = false
    }
  }

  async function fetchOrders(): Promise<void> {
    isLoadingOrders.value = true
    try {
      const data = await $fetch<Order[]>('/api/erp/orders')
      if (Array.isArray(data)) {
        orders.value = data.map(normalizeOrder)
      }
    } catch (err) {
      markUnauthorizedIf401(err)
      // Keep local seed orders if unauthenticated/offline
    } finally {
      isLoadingOrders.value = false
    }
  }

  async function fetchCustomers(): Promise<void> {
    try {
      const data = await $fetch<Customer[]>('/api/erp/customers')
      if (Array.isArray(data) && data.length > 0) {
        customers.value = data
      }
    } catch (err) {
      markUnauthorizedIf401(err)
      // Keep local seed customers if unauthenticated/offline
    }
  }

  async function fetchEmployees(): Promise<void> {
    try {
      const data = await $fetch<Employee[]>('/api/erp/employees')
      if (Array.isArray(data) && data.length > 0) {
        employees.value = data
      }
    } catch (err) {
      markUnauthorizedIf401(err)
      // Keep local seed employees if unauthenticated/offline
    }
  }

  async function fetchWebhookLogs(): Promise<void> {
    isLoadingWebhooks.value = true
    webhookError.value = null
    try {
      const data = await $fetch<WebhookLogResponse[]>('/api/erp/webhooks/logs')
      if (Array.isArray(data)) {
        webhookFeed.value = data.map(normalizeWebhookLog)
      } else {
        webhookFeed.value = []
      }
    } catch (err) {
      const status = extractHttpStatus(err)
      if (status === 404) {
        webhookFeed.value = []
      } else if (markUnauthorizedIf401(err)) {
        webhookFeed.value = []
      } else {
        webhookFeed.value = []
        webhookError.value = extractErrorMessage(err, 'Unable to load webhook telemetry')
      }
    } finally {
      isLoadingWebhooks.value = false
    }
  }

  async function fetchMetrics(): Promise<void> {
    try {
      const data = await $fetch<ErpTelemetryMetrics>('/api/erp/metrics')
      if (data && typeof data === 'object') {
        metrics.value = data
        if (typeof data.avg_rag_latency_ms === 'number' && data.avg_rag_latency_ms > 0) {
          avgRagLatencyMs.value = Math.round(data.avg_rag_latency_ms)
        }
      }
    } catch (err) {
      const status = extractHttpStatus(err)
      if (status !== 404) {
        markUnauthorizedIf401(err)
      }
    }
  }

  async function searchInventorySemantic(query: string): Promise<void> {
    const trimmed = query.trim()
    lastSearchQuery.value = trimmed
    if (!trimmed) {
      await fetchInventory()
      return
    }

    isLoadingInventory.value = true
    try {
      const data = await $fetch<InventoryItem[]>('/api/erp/inventory/search', {
        method: 'POST',
        body: {
          query: trimmed,
          tenant_id: currentUser.value?.tenant_id || 'default-tenant',
          limit: 5
        }
      })
      if (Array.isArray(data)) {
        inventory.value = data.map(normalizeInventoryItem)
        return
      }
    } catch (err) {
      markUnauthorizedIf401(err)
      // Fallback semantic simulation over seed inventory when offline/unauthenticated
      const lower = trimmed.toLowerCase()
      inventory.value = INITIAL_INVENTORY.filter(
        (item) =>
          item.name.toLowerCase().includes(lower) ||
          item.description.toLowerCase().includes(lower) ||
          item.sku.toLowerCase().includes(lower) ||
          (item.category ?? '').toLowerCase().includes(lower)
      ).map((item, idx) =>
        normalizeInventoryItem({
          ...item,
          similarity_distance: Number((0.1 + idx * 0.08).toFixed(4)),
          similarity_score: Number((0.95 - idx * 0.04).toFixed(2))
        })
      )
    } finally {
      isLoadingInventory.value = false
    }
  }

  async function createOrder(payload: OrderCreatePayload): Promise<Order> {
    isCreatingOrder.value = true
    try {
      // Server-authoritative pricing: only submit customer_id, optional assigned_employee_id, and { sku, quantity }
      const requestBody: OrderCreatePayload = {
        customer_id: payload.customer_id,
        ...(payload.assigned_employee_id ? { assigned_employee_id: payload.assigned_employee_id } : {}),
        items: payload.items.map((item) => ({
          sku: item.sku,
          quantity: item.quantity
        }))
      }
      const created = await $fetch<Order>('/api/erp/orders', {
        method: 'POST',
        body: requestBody
      })
      const normalized = normalizeOrder(created)
      orders.value.unshift(normalized)
      return normalized
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      isCreatingOrder.value = false
    }
  }

  async function updateOrderStatus(orderId: string, status: OrderStatus): Promise<void> {
    const target = orders.value.find((o) => o.id === orderId)
    if (!target) {
      return
    }
    const previousStatus = target.status
    const previousAssignedEmployeeId = target.assigned_employee_id
    if (!isValidOrderTransition(previousStatus, status)) {
      throw new Error(`Invalid order transition: ${previousStatus} -> ${status}`)
    }

    updatingOrderIds.value[orderId] = true
    target.status = status
    if (status === 'PAID' && !target.assigned_employee_id && employees.value.length > 0) {
      target.assigned_employee_id = employees.value[0]?.id ?? null
    }

    try {
      const updated = await $fetch<Order>(`/api/erp/orders/${orderId}/status`, {
        method: 'PATCH',
        body: { status }
      })
      if (updated) {
        Object.assign(target, normalizeOrder(updated))
      }
    } catch (err) {
      target.status = previousStatus
      target.assigned_employee_id = previousAssignedEmployeeId
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete updatingOrderIds.value[orderId]
    }
  }

  return {
    currentUser,
    isAuthenticated,
    requiresAuth,
    isAuthenticating,
    authError,
    inventory,
    orders,
    customers,
    employees,
    webhookFeed,
    metrics,
    isLoadingInventory,
    isLoadingOrders,
    updatingOrderIds,
    isCreatingOrder,
    isLoadingWebhooks,
    webhookError,
    lastSearchQuery,
    avgRagLatencyMs,
    ordersByStatus,
    fetchSession,
    login,
    logout,
    fetchInventory,
    fetchOrders,
    fetchCustomers,
    fetchEmployees,
    fetchWebhookLogs,
    fetchMetrics,
    searchInventorySemantic,
    createOrder,
    updateOrderStatus
  }
})
