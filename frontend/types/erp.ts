export interface InventoryItem {
  id: string
  tenant_id?: string
  sku: string
  name: string
  description: string
  category?: string
  price_vnd: number
  stock_quantity: number
  has_embedding: boolean
  similarity_score?: number
  similarity_distance?: number | null
  updated_at?: string
}

export interface Customer {
  id: string
  tenant_id?: string
  meta_psid: string
  full_name: string
  phone?: string
  created_at: string
}

export interface Employee {
  id: string
  tenant_id?: string
  full_name: string
  role: string
  telegram_user_id: string
  is_active: boolean
}

export type OrderStatus =
  | 'PENDING_PAYMENT'
  | 'PAID'
  | 'FULFILLING'
  | 'COMPLETED'
  | 'CANCELLED'

export interface OrderItem {
  sku: string
  name: string
  quantity: number
  unit_price_vnd: number
}

export interface Order {
  id: string
  tenant_id?: string
  order_code: string
  customer_id: string
  customer_name?: string
  items: OrderItem[]
  items_json?: OrderItem[]
  total_amount_vnd: number
  status: OrderStatus
  sepay_reference?: string
  sepay_transaction_id?: string | null
  vietqr_url?: string | null
  sepay_qr_url?: string | null
  assigned_employee_id?: string | null
  created_at: string
}

export type WebhookSource = 'META_GRAPH' | 'SEPAY_IPN' | 'TELEGRAM_BOT'
export type WebhookEventStatus = 'PROCESSED' | 'PENDING' | 'FAILED'

export interface WebhookEventLog {
  id: string
  source: WebhookSource
  event_type: string
  summary: string
  status: WebhookEventStatus
  timestamp: string
}
