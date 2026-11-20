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
  similarity_score?: number | null
  similarity_distance?: number | null
  updated_at?: string
}

export interface Customer {
  id: string
  tenant_id?: string
  meta_psid: string
  full_name: string
  phone?: string | null
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

export interface OrderCreateItemPayload {
  sku: string
  quantity: number
}

export interface OrderCreatePayload {
  customer_id: string
  assigned_employee_id?: string | null
  items: OrderCreateItemPayload[]
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
  updated_at?: string
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

export interface WebhookLogResponse {
  id: number | string
  sepay_transaction_id?: string
  tenant_id?: string
  order_code?: string | null
  transfer_type?: string
  transfer_amount?: number
  account_number?: string | null
  sub_account?: string | null
  reference_code?: string | null
  processing_status?: string
  raw_payload?: Record<string, unknown>
  created_at?: string
  source?: WebhookSource
  event_type?: string
  summary?: string
  status?: WebhookEventStatus
  timestamp?: string
}

export interface ErpTelemetryMetrics {
  avg_rag_latency_ms?: number
  active_inventory_skus?: number
  vectorized_skus?: number
  pending_vietqr_orders?: number
  paid_and_dispatched_orders?: number
  total_orders?: number
  webhook_events_count?: number
}

export interface AuthUser {
  authenticated: boolean
  user_id?: string
  email: string
  tenant_id: string
  role: string
}

export interface LoginCredentials {
  email: string
  password: string
  tenant_id: string
}
