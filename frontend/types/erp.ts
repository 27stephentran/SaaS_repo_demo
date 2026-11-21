export type PermissionGroup = 'live_api' | 'demo_api'

export type UserRole = 'admin' | 'manager' | 'staff' | 'demo' | string

export interface InventoryItem {
  id: string
  tenant_id?: string
  company_id?: string | null
  sku: string
  name: string
  description: string
  category?: string
  price_vnd: number
  stock_quantity: number
  has_embedding: boolean
  is_active?: boolean
  deactivated_at?: string | null
  similarity_score?: number | null
  similarity_distance?: number | null
  created_at?: string
  updated_at?: string
}

export interface InventoryCreatePayload {
  company_id?: string | null
  sku: string
  name: string
  description: string
  category?: string
  price_vnd: number
  stock_quantity: number
}

export interface Customer {
  id: string
  tenant_id?: string
  company_id?: string | null
  meta_psid: string
  full_name: string
  phone?: string | null
  is_active?: boolean
  deactivated_at?: string | null
  created_at: string
}

export interface Employee {
  id: string
  tenant_id?: string
  company_id?: string | null
  full_name: string
  role: string
  telegram_user_id: string
  is_active: boolean
  deactivated_at?: string | null
  created_at?: string
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
  company_id?: string | null
  customer_id: string
  assigned_employee_id?: string | null
  items: OrderCreateItemPayload[]
}

export interface Order {
  id: string
  tenant_id?: string
  company_id?: string | null
  company_name?: string
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
  is_active?: boolean
  deactivated_at?: string | null
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
  raw_payload?: Record<string, unknown>
}

export interface WebhookLogResponse {
  id: number | string
  sepay_transaction_id?: string
  tenant_id?: string
  company_id?: string | null
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
  tenant_id?: string
  company_id?: string | null
  avg_rag_latency_ms?: number | null
  active_inventory_skus?: number
  vectorized_skus?: number
  pending_vietqr_orders?: number
  paid_and_dispatched_orders?: number
  total_orders?: number
  webhook_events_count?: number
  orders_by_status?: Record<string, number>
}

export interface AuthUser {
  authenticated: boolean
  user_id?: string
  email: string
  username?: string | null
  full_name?: string
  tenant_id: string
  role: string
  permission_group: PermissionGroup
  company_id?: string | null
  employee_id?: string | null
  must_change_password?: boolean
}

export interface LoginCredentials {
  identifier?: string
  email?: string
  password: string
  tenant_id: string
}

export interface SignupCredentials {
  email: string
  password: string
  tenant_id: string
}

export interface ChangePasswordPayload {
  current_password: string
  new_password: string
}

export interface BankDirectoryEntry {
  code: string
  bin?: string
  name: string
  short_name: string
  vietqr_supported?: boolean
  sepay_supported?: boolean
}

export interface SePayLinkedBankAccount {
  id: string
  bank_code: string
  bank_short_name?: string
  account_number: string
  account_holder_name?: string
  accumulated?: number | null
  active?: boolean
}

export interface BankCatalogResponse {
  banks: BankDirectoryEntry[]
  sepay_linked_accounts: SePayLinkedBankAccount[]
  sepay_api_configured: boolean
  bankhub_configured: boolean
}

export interface Company {
  id: string
  tenant_id?: string
  name: string
  code: string
  bank_code: string
  bank_name: string
  bank_account_number: string
  bank_account_name: string
  sepay_sub_account?: string
  sepay_memo_prefix?: string
  sepay_bank_account_id?: string | null
  sepay_bankhub_company_xid?: string | null
  is_active: boolean
  deactivated_at?: string | null
  created_at?: string
  updated_at?: string
}

export interface CompanyCreatePayload {
  name: string
  code: string
  bank_code: string
  bank_name?: string
  bank_account_number: string
  bank_account_name: string
  sepay_sub_account?: string
  sepay_memo_prefix?: string
  sepay_bank_account_id?: string | null
}

export interface CompanyBankUpdatePayload {
  bank_code: string
  bank_name?: string
  bank_account_number: string
  bank_account_name: string
  sepay_sub_account?: string
  sepay_memo_prefix?: string
  sepay_bank_account_id?: string | null
}

export interface ManagerCreatePayload {
  full_name: string
  email?: string | null
  username?: string | null
  password?: string | null
}

export interface ManagerCreateResponse {
  user_id: string
  tenant_id: string
  company_id: string
  email: string
  username: string
  full_name: string
  role: string
  temporary_password?: string | null
  must_change_password: boolean
  created_at?: string
}

export interface StaffCreatePayload {
  full_name: string
  telegram_user_id: string
  email?: string | null
}

export interface StaffCreateResponse {
  employee_id: string
  user_id: string
  tenant_id: string
  company_id: string
  full_name: string
  telegram_user_id: string
  email: string
  username: string
  temporary_password: string
  role: string
  must_change_password: boolean
  created_at?: string
}

export interface StaffMemberSummary {
  employee_id: string
  user_id?: string | null
  tenant_id?: string
  company_id?: string | null
  full_name: string
  role: string
  telegram_user_id: string
  email?: string | null
  username?: string | null
  must_change_password?: boolean
  is_active: boolean
  active_orders_count: number
  completed_orders_count: number
  created_at?: string
}

export interface DailyIncomePoint {
  date: string
  revenue_vnd: number
  orders_count: number
}

export interface ManagerDashboardSummary {
  tenant_id: string
  company_id?: string | null
  company_name: string
  total_revenue_vnd: number
  paid_orders_count: number
  pending_orders_count: number
  total_orders_count: number
  active_customers_count: number
  active_staff_count: number
  open_chats_count: number
  daily_income: DailyIncomePoint[]
  staff_workload: StaffMemberSummary[]
  recent_customers: Customer[]
}

export type ChatChannel = 'messenger' | 'instagram' | 'telegram' | 'web' | 'other'

export interface ChannelConversation {
  id: string
  tenant_id?: string
  company_id?: string | null
  company_name?: string
  customer_id?: string | null
  channel: ChatChannel
  external_thread_id: string
  customer_name: string
  last_message_preview: string
  last_message_at: string
  unread_count: number
  status: string
  created_at?: string
  updated_at?: string
}

export interface ChannelMessage {
  id: string
  tenant_id?: string
  company_id?: string | null
  conversation_id: string
  channel: ChatChannel
  external_message_id?: string | null
  direction: 'inbound' | 'outbound'
  sender_type: 'customer' | 'ai_bot' | 'manager' | 'admin'
  sender_name: string
  content: string
  order_code?: string | null
  created_at: string
}

export interface TelegramDispatchResponse {
  dispatched: boolean
  simulated: boolean
  telegram_user_id: string
  order_code: string
  preview_text?: string | null
  error?: string | null
  status_code?: number | null
}

export interface SoftDeleteResponse {
  resource: string
  record_id: string
  tenant_id: string
  is_active: boolean
  deactivated_at: string
}

export interface RestoreResponse {
  resource: string
  record_id: string
  tenant_id: string
  is_active: boolean
  deactivated_at?: string | null
}

export interface DemoTelegramNotifyPayload {
  order_code: string
  total_amount_vnd: number
  items_summary: string
  customer_name?: string | null
  telegram_user_id?: string | null
  sepay_reference?: string | null
}
