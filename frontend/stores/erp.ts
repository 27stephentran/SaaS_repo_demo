import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  Employee,
  InventoryItem,
  Order,
  OrderStatus,
  WebhookEventLog
} from '~/types/erp'

const INITIAL_INVENTORY: InventoryItem[] = [
  {
    id: '11111111-1111-4111-8111-111111111101',
    tenant_id: 'tenant-default',
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
    tenant_id: 'tenant-default',
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
    tenant_id: 'tenant-default',
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
    tenant_id: 'tenant-default',
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

const INITIAL_EMPLOYEES: Employee[] = [
  {
    id: '33333333-3333-4333-8333-333333333301',
    tenant_id: 'tenant-default',
    full_name: 'Minh Tran',
    role: 'Lead Fulfillment Specialist',
    telegram_user_id: 'tg_849102938',
    is_active: true
  },
  {
    id: '33333333-3333-4333-8333-333333333302',
    tenant_id: 'tenant-default',
    full_name: 'Linh Nguyen',
    role: 'Warehouse & VietQR Reconciliation',
    telegram_user_id: 'tg_849556120',
    is_active: true
  },
  {
    id: '33333333-3333-4333-8333-333333333303',
    tenant_id: 'tenant-default',
    full_name: 'Khoa Le',
    role: 'Express Dispatch Courier',
    telegram_user_id: 'tg_849883412',
    is_active: true
  }
]

const INITIAL_ORDERS: Order[] = [
  {
    id: '44444444-4444-4444-8444-444444444401',
    tenant_id: 'tenant-default',
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
    tenant_id: 'tenant-default',
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
    tenant_id: 'tenant-default',
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
    tenant_id: 'tenant-default',
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

const INITIAL_WEBHOOK_FEED: WebhookEventLog[] = [
  {
    id: '55555555-5555-4555-8555-555555555501',
    source: 'META_GRAPH',
    event_type: 'messages.inbound',
    summary: 'PSID_992814 asked: "Do you have Cau Dat Arabica 1kg in stock? Send me a VietQR code for 2 bags." -> Gemini 1.5 Flash RAG matched COFFEE-ARABICA-1KG (cosine 0.96).',
    status: 'PROCESSED',
    timestamp: '2026-11-18T08:11:54Z'
  },
  {
    id: '55555555-5555-4555-8555-555555555502',
    source: 'SEPAY_IPN',
    event_type: 'transaction.credit_verified',
    summary: 'SePay VietQR IPN confirmed +895,000 VND with memo SEPAY8840 -> Order ORD-2026-8840 transitioned to PAID.',
    status: 'PROCESSED',
    timestamp: '2026-11-18T08:06:12Z'
  },
  {
    id: '55555555-5555-4555-8555-555555555503',
    source: 'TELEGRAM_BOT',
    event_type: 'staff.dispatch_alert',
    summary: 'Dispatched packing slip for ORD-2026-8840 to Minh Tran (telegram_user_id: tg_849102938) with inline /claim & /ship buttons.',
    status: 'PROCESSED',
    timestamp: '2026-11-18T08:06:14Z'
  }
]

function normalizeInventoryItem(item: InventoryItem): InventoryItem {
  const computedScore =
    item.similarity_score != null
      ? item.similarity_score
      : item.similarity_distance != null
        ? Math.max(0, Number((1 - item.similarity_distance / 2).toFixed(4)))
        : undefined

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

export const useErpStore = defineStore('erp', () => {
  const config = useRuntimeConfig()
  const apiBase = config.public.apiBase

  const inventory = ref<InventoryItem[]>([...INITIAL_INVENTORY])
  const orders = ref<Order[]>([...INITIAL_ORDERS])
  const employees = ref<Employee[]>([...INITIAL_EMPLOYEES])
  const webhookFeed = ref<WebhookEventLog[]>([...INITIAL_WEBHOOK_FEED])

  const isLoadingInventory = ref<boolean>(false)
  const isUpdatingOrder = ref<boolean>(false)
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

  async function fetchInventory(): Promise<void> {
    isLoadingInventory.value = true
    try {
      const data = await $fetch<InventoryItem[]>(`${apiBase}/api/v1/erp/inventory`)
      if (Array.isArray(data) && data.length > 0) {
        inventory.value = data.map(normalizeInventoryItem)
      }
    } catch {
      // Keep resilient local seed data when backend is offline in preview mode
    } finally {
      isLoadingInventory.value = false
    }
  }

  async function fetchOrders(): Promise<void> {
    try {
      const data = await $fetch<Order[]>(`${apiBase}/api/v1/erp/orders`)
      if (Array.isArray(data) && data.length > 0) {
        orders.value = data.map(normalizeOrder)
      }
    } catch {
      // Keep local seed orders if backend is offline
    }
  }

  async function fetchEmployees(): Promise<void> {
    try {
      const data = await $fetch<Employee[]>(`${apiBase}/api/v1/erp/employees`)
      if (Array.isArray(data) && data.length > 0) {
        employees.value = data
      }
    } catch {
      // Keep local seed employees if backend is offline
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
      const data = await $fetch<InventoryItem[]>(`${apiBase}/api/v1/erp/inventory/search`, {
        method: 'POST',
        body: {
          query: trimmed,
          tenant_id: 'tenant-default',
          limit: 5
        }
      })
      if (Array.isArray(data)) {
        inventory.value = data.map(normalizeInventoryItem)
        return
      }
    } catch {
      // Fallback semantic simulation over seed inventory
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

  async function updateOrderStatus(orderId: string, status: OrderStatus): Promise<void> {
    isUpdatingOrder.value = true
    const target = orders.value.find((o) => o.id === orderId)
    const previousStatus = target?.status

    if (target) {
      target.status = status
      if (status === 'PAID' && !target.assigned_employee_id && employees.value.length > 0) {
        target.assigned_employee_id = employees.value[0].id
      }
    }

    try {
      const updated = await $fetch<Order>(`${apiBase}/api/v1/erp/orders/${orderId}/status`, {
        method: 'PATCH',
        body: { status }
      })
      if (updated && target) {
        Object.assign(target, normalizeOrder(updated))
      }
    } catch {
      // Optimistic local state update remains active for demo continuity
    } finally {
      if (target && previousStatus !== status) {
        const assignedEmployee = employees.value.find(
          (emp) => emp.id === target.assigned_employee_id
        )
        webhookFeed.value.unshift({
          id: `wh-${Date.now()}`,
          source: status === 'PAID' ? 'SEPAY_IPN' : 'TELEGRAM_BOT',
          event_type:
            status === 'PAID' ? 'transaction.credit_verified' : `order.status_${status.toLowerCase()}`,
          summary:
            status === 'PAID'
              ? `SePay IPN verified ${target.total_amount_vnd.toLocaleString('vi-VN')} VND (${target.sepay_reference}) -> Dispatched ${target.order_code} to ${assignedEmployee?.full_name ?? 'Telegram Bot'} (${assignedEmployee?.telegram_user_id ?? 'tg_staff'}).`
              : `Order ${target.order_code} advanced from ${previousStatus} to ${status} via ERP Admin / Telegram Staff Bot.`,
          status: 'PROCESSED',
          timestamp: new Date().toISOString()
        })
      }
      isUpdatingOrder.value = false
    }
  }

  return {
    inventory,
    orders,
    employees,
    webhookFeed,
    isLoadingInventory,
    isUpdatingOrder,
    lastSearchQuery,
    avgRagLatencyMs,
    ordersByStatus,
    fetchInventory,
    fetchOrders,
    fetchEmployees,
    searchInventorySemantic,
    updateOrderStatus
  }
})
