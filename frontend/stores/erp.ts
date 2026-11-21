import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type {
  AuthUser,
  BankCatalogResponse,
  ChangePasswordPayload,
  ChannelConversation,
  ChannelMessage,
  Company,
  CompanyBankUpdatePayload,
  CompanyCreatePayload,
  Customer,
  Employee,
  ErpTelemetryMetrics,
  InventoryCreatePayload,
  InventoryItem,
  LoginCredentials,
  ManagerCreatePayload,
  ManagerCreateResponse,
  ManagerDashboardSummary,
  Order,
  OrderCreatePayload,
  OrderStatus,
  RestoreResponse,
  SignupCredentials,
  SoftDeleteResponse,
  StaffCreatePayload,
  StaffCreateResponse,
  StaffMemberSummary,
  TelegramDispatchResponse,
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

function extractHttpStatus(err: unknown): number | undefined {
  if (!err || typeof err !== 'object') return undefined
  const record = err as { statusCode?: number; status?: number; response?: { status?: number } }
  return record.statusCode ?? record.status ?? record.response?.status
}

function extractErrorMessage(err: unknown, fallback: string): string {
  if (!err || typeof err !== 'object') return fallback
  const record = err as {
    data?: { detail?: unknown; message?: unknown; statusMessage?: unknown }
    statusMessage?: unknown
    message?: unknown
  }
  if (typeof record.data?.detail === 'string' && record.data.detail.trim()) {
    return record.data.detail
  }
  if (typeof record.data?.message === 'string' && record.data.message.trim()) {
    return record.data.message
  }
  if (typeof record.data?.statusMessage === 'string' && record.data.statusMessage.trim()) {
    return record.data.statusMessage
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
    is_active: item.is_active ?? true,
    deactivated_at: item.deactivated_at ?? null,
    similarity_score: computedScore
  }
}

function normalizeCustomer(customer: Customer): Customer {
  return {
    ...customer,
    is_active: customer.is_active ?? true,
    deactivated_at: customer.deactivated_at ?? null
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
    sepay_reference: sepayReference,
    is_active: order.is_active ?? true,
    deactivated_at: order.deactivated_at ?? null
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
      timestamp: log.timestamp,
      raw_payload: log.raw_payload
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
    timestamp: log.created_at || log.timestamp || new Date().toISOString(),
    raw_payload: log.raw_payload
  }
}

export const useErpStore = defineStore('erp', () => {
  const currentUser = ref<AuthUser | null>(null)
  const isAuthenticated = computed<boolean>(() => Boolean(currentUser.value?.authenticated))
  const isLiveAuthenticated = computed<boolean>(
    () => Boolean(currentUser.value?.authenticated && currentUser.value?.permission_group === 'live_api')
  )
  const userRole = computed<string>(() => (currentUser.value?.role || '').toLowerCase())
  const isAdmin = computed<boolean>(() => isLiveAuthenticated.value && userRole.value === 'admin')
  const isManager = computed<boolean>(() => isLiveAuthenticated.value && userRole.value === 'manager')
  const isStaff = computed<boolean>(() => isLiveAuthenticated.value && userRole.value === 'staff')
  const mustChangePassword = computed<boolean>(() => Boolean(currentUser.value?.must_change_password))

  const requiresAuth = ref<boolean>(false)
  const isAuthenticating = ref<boolean>(false)
  const authError = ref<string | null>(null)

  const selectedCompanyId = ref<string | null>(null)
  const companies = ref<Company[]>([])
  const bankCatalog = ref<BankCatalogResponse>({
    banks: [],
    sepay_linked_accounts: [],
    sepay_api_configured: false,
    bankhub_configured: false
  })
  const staffMembers = ref<StaffMemberSummary[]>([])
  const myWorkOrders = ref<Order[]>([])
  const managerDashboard = ref<ManagerDashboardSummary | null>(null)
  const chats = ref<ChannelConversation[]>([])
  const activeChatMessages = ref<ChannelMessage[]>([])

  const inventory = ref<InventoryItem[]>([])
  const orders = ref<Order[]>([])
  const customers = ref<Customer[]>([])
  const employees = ref<Employee[]>([])
  const webhookFeed = ref<WebhookEventLog[]>([])
  const metrics = ref<ErpTelemetryMetrics | null>(null)

  const isLoadingCompanies = ref<boolean>(false)
  const isLoadingBanks = ref<boolean>(false)
  const isCreatingCompany = ref<boolean>(false)
  const isLoadingStaff = ref<boolean>(false)
  const isCreatingStaff = ref<boolean>(false)
  const isLoadingMyWork = ref<boolean>(false)
  const isLoadingDashboard = ref<boolean>(false)
  const isLoadingChats = ref<boolean>(false)
  const isLoadingChatMessages = ref<boolean>(false)
  const isSendingChatReply = ref<boolean>(false)

  const isLoadingInventory = ref<boolean>(false)
  const isLoadingOrders = ref<boolean>(false)
  const isLoadingCustomers = ref<boolean>(false)
  const isLoadingEmployees = ref<boolean>(false)
  const isLoadingWebhooks = ref<boolean>(false)
  const isCreatingOrder = ref<boolean>(false)
  const isCreatingInventory = ref<boolean>(false)
  const updatingOrderIds = ref<Record<string, boolean>>({})
  const mutatingInventoryIds = ref<Record<string, boolean>>({})
  const mutatingCustomerIds = ref<Record<string, boolean>>({})
  const notifyingOrderIds = ref<Record<string, boolean>>({})

  const companiesError = ref<string | null>(null)
  const staffError = ref<string | null>(null)
  const myWorkError = ref<string | null>(null)
  const dashboardError = ref<string | null>(null)
  const chatsError = ref<string | null>(null)

  const inventoryError = ref<string | null>(null)
  const ordersError = ref<string | null>(null)
  const customersError = ref<string | null>(null)
  const webhookError = ref<string | null>(null)
  const lastSearchQuery = ref<string>('')
  const avgRagLatencyMs = ref<number>(0)

  const activeCompanyId = computed<string | null>(() => {
    if (isAdmin.value) {
      return selectedCompanyId.value
    }
    return currentUser.value?.company_id ?? null
  })

  const activeCompanyName = computed<string | null>(() => {
    const id = activeCompanyId.value
    if (!id) return null
    const matched = companies.value.find((c) => c.id === id)
    if (matched) return matched.name
    if (managerDashboard.value?.company_id === id && managerDashboard.value.company_name) {
      return managerDashboard.value.company_name
    }
    return id.slice(0, 8)
  })

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

  function buildCompanyQuery(extra: Record<string, string | boolean | undefined> = {}): string {
    const params = new URLSearchParams()
    for (const [key, val] of Object.entries(extra)) {
      if (val !== undefined && val !== false && val !== '') {
        params.set(key, String(val))
      }
    }
    if (isAdmin.value && selectedCompanyId.value) {
      params.set('company_id', selectedCompanyId.value)
    }
    const qs = params.toString()
    return qs ? `?${qs}` : ''
  }

  function setSelectedCompanyId(companyId: string | null): void {
    selectedCompanyId.value = companyId || null
  }

  function clearWorkspaceState(): void {
    selectedCompanyId.value = null
    companies.value = []
    staffMembers.value = []
    myWorkOrders.value = []
    managerDashboard.value = null
    chats.value = []
    activeChatMessages.value = []
    inventory.value = []
    orders.value = []
    customers.value = []
    employees.value = []
    webhookFeed.value = []
    metrics.value = null
    companiesError.value = null
    staffError.value = null
    myWorkError.value = null
    dashboardError.value = null
    chatsError.value = null
    inventoryError.value = null
    ordersError.value = null
    customersError.value = null
    webhookError.value = null
  }

  function markUnauthorizedIf401(err: unknown): boolean {
    if (extractHttpStatus(err) === 401) {
      currentUser.value = null
      requiresAuth.value = true
      return true
    }
    return false
  }

  async function fetchSession(requestHeaders?: Record<string, string>): Promise<AuthUser | null> {
    try {
      const data = await $fetch<AuthUser>('/api/auth/me', {
        ...(requestHeaders ? { headers: requestHeaders } : {})
      })
      currentUser.value = {
        ...data,
        authenticated: true
      }
      requiresAuth.value = data.permission_group !== 'live_api'
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
      const tenantId = credentials.tenant_id.trim()
      if (!tenantId) {
        throw new Error('Tenant ID is required')
      }
      const identifierVal = credentials.identifier?.trim()
      const emailVal = credentials.email?.trim()
      if (!identifierVal && !emailVal) {
        throw new Error('Username or email is required')
      }
      const data = await $fetch<AuthUser>('/api/auth/login', {
        method: 'POST',
        body: {
          ...(identifierVal ? { identifier: identifierVal } : {}),
          ...(emailVal ? { email: emailVal } : {}),
          password: credentials.password,
          tenant_id: tenantId
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

  async function changePassword(payload: ChangePasswordPayload): Promise<AuthUser> {
    isAuthenticating.value = true
    authError.value = null
    try {
      const data = await $fetch<AuthUser>('/api/auth/change-password', {
        method: 'POST',
        body: {
          current_password: payload.current_password,
          new_password: payload.new_password
        }
      })
      currentUser.value = {
        ...data,
        authenticated: true,
        must_change_password: false
      }
      return currentUser.value
    } catch (err) {
      authError.value = extractErrorMessage(err, 'Unable to update password')
      throw err
    } finally {
      isAuthenticating.value = false
    }
  }

  async function signup(credentials: SignupCredentials): Promise<AuthUser> {
    isAuthenticating.value = true
    authError.value = null
    try {
      const tenantId = credentials.tenant_id.trim()
      if (!tenantId) {
        throw new Error('Tenant ID is required')
      }
      const data = await $fetch<AuthUser>('/api/auth/signup', {
        method: 'POST',
        body: {
          email: credentials.email.trim(),
          password: credentials.password,
          tenant_id: tenantId
        }
      })
      currentUser.value = {
        ...data,
        authenticated: true
      }
      requiresAuth.value = false
      return currentUser.value
    } catch (err) {
      authError.value = extractErrorMessage(err, 'Unable to create operator account')
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
      clearWorkspaceState()
    }
  }

  async function fetchInventory(includeInactive = false): Promise<void> {
    isLoadingInventory.value = true
    inventoryError.value = null
    try {
      const query = buildCompanyQuery({ include_inactive: includeInactive || undefined })
      const data = await $fetch<InventoryItem[]>(`/api/erp/inventory${query}`)
      inventory.value = Array.isArray(data) ? data.map(normalizeInventoryItem) : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        inventoryError.value = extractErrorMessage(err, 'Unable to load inventory catalog')
      }
    } finally {
      isLoadingInventory.value = false
    }
  }

  async function createInventoryItem(payload: InventoryCreatePayload): Promise<InventoryItem> {
    isCreatingInventory.value = true
    try {
      const effectiveCompanyId = payload.company_id || (isAdmin.value ? selectedCompanyId.value : null)
      const created = await $fetch<InventoryItem>('/api/erp/inventory', {
        method: 'POST',
        body: {
          ...(effectiveCompanyId ? { company_id: effectiveCompanyId } : {}),
          sku: payload.sku.trim(),
          name: payload.name.trim(),
          description: payload.description.trim(),
          category: payload.category?.trim() || 'General Merchandise',
          price_vnd: Math.max(0, Math.floor(Number(payload.price_vnd))),
          stock_quantity: Math.max(0, Math.floor(Number(payload.stock_quantity)))
        }
      })
      const normalized = normalizeInventoryItem(created)
      inventory.value.unshift(normalized)
      return normalized
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      isCreatingInventory.value = false
    }
  }

  async function softDeleteInventory(itemId: string): Promise<SoftDeleteResponse> {
    mutatingInventoryIds.value[itemId] = true
    try {
      const result = await $fetch<SoftDeleteResponse>(`/api/erp/inventory/${itemId}`, {
        method: 'DELETE'
      })
      const target = inventory.value.find((item) => item.id === itemId)
      if (target) {
        target.is_active = false
        target.deactivated_at = result.deactivated_at
      }
      return result
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete mutatingInventoryIds.value[itemId]
    }
  }

  async function restoreInventory(itemId: string): Promise<RestoreResponse> {
    mutatingInventoryIds.value[itemId] = true
    try {
      const result = await $fetch<RestoreResponse>(`/api/erp/inventory/${itemId}/restore`, {
        method: 'POST'
      })
      const target = inventory.value.find((item) => item.id === itemId)
      if (target) {
        target.is_active = true
        target.deactivated_at = null
      }
      return result
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete mutatingInventoryIds.value[itemId]
    }
  }

  async function searchInventorySemantic(query: string, includeInactive = false): Promise<void> {
    const trimmed = query.trim()
    lastSearchQuery.value = trimmed
    if (!trimmed) {
      await fetchInventory(includeInactive)
      return
    }

    isLoadingInventory.value = true
    inventoryError.value = null
    try {
      const data = await $fetch<InventoryItem[]>('/api/erp/inventory/search', {
        method: 'POST',
        body: {
          query: trimmed,
          ...(currentUser.value?.tenant_id ? { tenant_id: currentUser.value.tenant_id } : {}),
          ...(isAdmin.value && selectedCompanyId.value ? { company_id: selectedCompanyId.value } : {}),
          limit: 10,
          include_inactive: includeInactive
        }
      })
      inventory.value = Array.isArray(data) ? data.map(normalizeInventoryItem) : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        inventoryError.value = extractErrorMessage(err, 'Semantic inventory search failed')
      }
    } finally {
      isLoadingInventory.value = false
    }
  }

  async function fetchOrders(): Promise<void> {
    isLoadingOrders.value = true
    ordersError.value = null
    try {
      const query = buildCompanyQuery()
      const data = await $fetch<Order[]>(`/api/erp/orders${query}`)
      orders.value = Array.isArray(data) ? data.map(normalizeOrder) : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        ordersError.value = extractErrorMessage(err, 'Unable to load orders')
      }
    } finally {
      isLoadingOrders.value = false
    }
  }

  async function fetchCustomers(includeInactive = false): Promise<void> {
    isLoadingCustomers.value = true
    customersError.value = null
    try {
      const query = buildCompanyQuery({ include_inactive: includeInactive || undefined })
      const data = await $fetch<Customer[]>(`/api/erp/customers${query}`)
      customers.value = Array.isArray(data) ? data.map(normalizeCustomer) : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        customersError.value = extractErrorMessage(err, 'Unable to load customer directory')
      }
    } finally {
      isLoadingCustomers.value = false
    }
  }

  async function softDeleteCustomer(customerId: string): Promise<SoftDeleteResponse> {
    mutatingCustomerIds.value[customerId] = true
    try {
      const result = await $fetch<SoftDeleteResponse>(`/api/erp/customers/${customerId}`, {
        method: 'DELETE'
      })
      const target = customers.value.find((customer) => customer.id === customerId)
      if (target) {
        target.is_active = false
        target.deactivated_at = result.deactivated_at
      }
      return result
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete mutatingCustomerIds.value[customerId]
    }
  }

  async function restoreCustomer(customerId: string): Promise<RestoreResponse> {
    mutatingCustomerIds.value[customerId] = true
    try {
      const result = await $fetch<RestoreResponse>(`/api/erp/customers/${customerId}/restore`, {
        method: 'POST'
      })
      const target = customers.value.find((customer) => customer.id === customerId)
      if (target) {
        target.is_active = true
        target.deactivated_at = null
      }
      return result
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete mutatingCustomerIds.value[customerId]
    }
  }

  async function fetchEmployees(includeInactive = false): Promise<void> {
    isLoadingEmployees.value = true
    try {
      const query = buildCompanyQuery({ include_inactive: includeInactive || undefined })
      const data = await $fetch<Employee[]>(`/api/erp/employees${query}`)
      employees.value = Array.isArray(data) ? data : []
    } catch (err) {
      markUnauthorizedIf401(err)
    } finally {
      isLoadingEmployees.value = false
    }
  }

  async function softDeleteEmployee(employeeId: string): Promise<SoftDeleteResponse> {
    const result = await $fetch<SoftDeleteResponse>(`/api/erp/employees/${employeeId}`, {
      method: 'DELETE'
    })
    const target = employees.value.find((employee) => employee.id === employeeId)
    if (target) {
      target.is_active = false
      target.deactivated_at = result.deactivated_at
    }
    return result
  }

  async function restoreEmployee(employeeId: string): Promise<RestoreResponse> {
    const result = await $fetch<RestoreResponse>(`/api/erp/employees/${employeeId}/restore`, {
      method: 'POST'
    })
    const target = employees.value.find((employee) => employee.id === employeeId)
    if (target) {
      target.is_active = true
      target.deactivated_at = null
    }
    return result
  }

  async function fetchWebhookLogs(): Promise<void> {
    isLoadingWebhooks.value = true
    webhookError.value = null
    try {
      const query = buildCompanyQuery()
      const data = await $fetch<WebhookLogResponse[]>(`/api/erp/webhooks/logs${query}`)
      webhookFeed.value = Array.isArray(data) ? data.map(normalizeWebhookLog) : []
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
      const query = buildCompanyQuery()
      const data = await $fetch<ErpTelemetryMetrics>(`/api/erp/metrics${query}`)
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

  async function createOrder(payload: OrderCreatePayload): Promise<Order> {
    isCreatingOrder.value = true
    try {
      const effectiveCompanyId = payload.company_id || (isAdmin.value ? selectedCompanyId.value : null)
      const requestBody: OrderCreatePayload = {
        ...(effectiveCompanyId ? { company_id: effectiveCompanyId } : {}),
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
      const firstActive = employees.value.find((emp) => emp.is_active)
      target.assigned_employee_id = firstActive?.id ?? employees.value[0]?.id ?? null
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

  async function notifyOrderTelegram(
    orderId: string,
    telegramUserId?: string | null
  ): Promise<TelegramDispatchResponse> {
    notifyingOrderIds.value[orderId] = true
    try {
      return await $fetch<TelegramDispatchResponse>(`/api/erp/orders/${orderId}/notify-telegram`, {
        method: 'POST',
        body: telegramUserId ? { telegram_user_id: telegramUserId } : {}
      })
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete notifyingOrderIds.value[orderId]
    }
  }

  async function fetchCompanies(includeInactive = false): Promise<void> {
    isLoadingCompanies.value = true
    companiesError.value = null
    try {
      const query = includeInactive ? '?include_inactive=true' : ''
      const data = await $fetch<Company[]>(`/api/companies${query}`)
      companies.value = Array.isArray(data) ? data : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        companiesError.value = extractErrorMessage(err, 'Unable to load companies')
      }
    } finally {
      isLoadingCompanies.value = false
    }
  }

  async function fetchBankCatalog(): Promise<void> {
    isLoadingBanks.value = true
    try {
      const data = await $fetch<BankCatalogResponse>('/api/companies/banks')
      if (data && Array.isArray(data.banks)) {
        bankCatalog.value = {
          banks: data.banks,
          sepay_linked_accounts: Array.isArray(data.sepay_linked_accounts)
            ? data.sepay_linked_accounts
            : [],
          sepay_api_configured: Boolean(data.sepay_api_configured),
          bankhub_configured: Boolean(data.bankhub_configured)
        }
      }
    } catch (err) {
      markUnauthorizedIf401(err)
    } finally {
      isLoadingBanks.value = false
    }
  }

  async function createCompany(payload: CompanyCreatePayload): Promise<Company> {
    isCreatingCompany.value = true
    try {
      const created = await $fetch<Company>('/api/companies', {
        method: 'POST',
        body: payload
      })
      companies.value.unshift(created)
      return created
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      isCreatingCompany.value = false
    }
  }

  async function updateCompanyBank(companyId: string, payload: CompanyBankUpdatePayload): Promise<Company> {
    try {
      const updated = await $fetch<Company>(`/api/companies/${companyId}/bank`, {
        method: 'PATCH',
        body: payload
      })
      const idx = companies.value.findIndex((c) => c.id === companyId)
      if (idx !== -1) {
        companies.value[idx] = updated
      }
      return updated
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    }
  }

  async function softDeleteCompany(companyId: string): Promise<SoftDeleteResponse> {
    try {
      const res = await $fetch<SoftDeleteResponse>(`/api/companies/${companyId}`, {
        method: 'DELETE'
      })
      const target = companies.value.find((c) => c.id === companyId)
      if (target) {
        target.is_active = false
        target.deactivated_at = res.deactivated_at
      }
      return res
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    }
  }

  async function restoreCompany(companyId: string): Promise<RestoreResponse> {
    try {
      const res = await $fetch<RestoreResponse>(`/api/companies/${companyId}/restore`, {
        method: 'POST'
      })
      const target = companies.value.find((c) => c.id === companyId)
      if (target) {
        target.is_active = true
        target.deactivated_at = null
      }
      return res
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    }
  }

  async function createManager(companyId: string, payload: ManagerCreatePayload): Promise<ManagerCreateResponse> {
    try {
      return await $fetch<ManagerCreateResponse>(`/api/companies/${companyId}/managers`, {
        method: 'POST',
        body: payload
      })
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    }
  }

  async function fetchStaff(): Promise<void> {
    isLoadingStaff.value = true
    staffError.value = null
    try {
      const query = buildCompanyQuery()
      const data = await $fetch<StaffMemberSummary[]>(`/api/erp/staff${query}`)
      staffMembers.value = Array.isArray(data) ? data : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        staffError.value = extractErrorMessage(err, 'Unable to load staff roster')
      }
    } finally {
      isLoadingStaff.value = false
    }
  }

  async function createStaff(payload: StaffCreatePayload): Promise<StaffCreateResponse> {
    isCreatingStaff.value = true
    try {
      const created = await $fetch<StaffCreateResponse>('/api/erp/staff', {
        method: 'POST',
        body: payload
      })
      await fetchStaff()
      return created
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      isCreatingStaff.value = false
    }
  }

  async function fetchMyWork(): Promise<void> {
    isLoadingMyWork.value = true
    myWorkError.value = null
    try {
      const data = await $fetch<Order[]>('/api/erp/my-work')
      myWorkOrders.value = Array.isArray(data) ? data.map(normalizeOrder) : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        myWorkError.value = extractErrorMessage(err, 'Unable to load assigned work orders')
      }
    } finally {
      isLoadingMyWork.value = false
    }
  }

  async function updateMyWorkStatus(orderId: string, status: OrderStatus): Promise<void> {
    const target = myWorkOrders.value.find((o) => o.id === orderId)
    if (!target) return
    const previousStatus = target.status
    if (!isValidOrderTransition(previousStatus, status)) {
      throw new Error(`Invalid order transition: ${previousStatus} -> ${status}`)
    }
    updatingOrderIds.value[orderId] = true
    target.status = status
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
      markUnauthorizedIf401(err)
      throw err
    } finally {
      delete updatingOrderIds.value[orderId]
    }
  }

  async function fetchManagerDashboard(): Promise<void> {
    isLoadingDashboard.value = true
    dashboardError.value = null
    try {
      const query = buildCompanyQuery()
      const data = await $fetch<ManagerDashboardSummary>(`/api/erp/dashboard/manager${query}`)
      managerDashboard.value = data
    } catch (err) {
      const status = extractHttpStatus(err)
      if (status !== 404 && !markUnauthorizedIf401(err)) {
        dashboardError.value = extractErrorMessage(err, 'Unable to load analytics dashboard')
      }
    } finally {
      isLoadingDashboard.value = false
    }
  }

  async function fetchChats(channel?: string): Promise<void> {
    isLoadingChats.value = true
    chatsError.value = null
    try {
      const query = buildCompanyQuery({
        channel: channel && channel !== 'all' ? channel : undefined
      })
      const data = await $fetch<ChannelConversation[]>(`/api/erp/chats${query}`)
      chats.value = Array.isArray(data) ? data : []
    } catch (err) {
      if (!markUnauthorizedIf401(err)) {
        chatsError.value = extractErrorMessage(err, 'Unable to load omnichannel chats')
      }
    } finally {
      isLoadingChats.value = false
    }
  }

  async function fetchChatMessages(conversationId: string): Promise<void> {
    isLoadingChatMessages.value = true
    try {
      const data = await $fetch<ChannelMessage[]>(`/api/erp/chats/${conversationId}/messages`)
      activeChatMessages.value = Array.isArray(data) ? data : []
      const conv = chats.value.find((c) => c.id === conversationId)
      if (conv) {
        conv.unread_count = 0
      }
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      isLoadingChatMessages.value = false
    }
  }

  async function replyToChat(conversationId: string, content: string): Promise<ChannelMessage> {
    isSendingChatReply.value = true
    try {
      const msg = await $fetch<ChannelMessage>(`/api/erp/chats/${conversationId}/reply`, {
        method: 'POST',
        body: { content: content.trim() }
      })
      activeChatMessages.value.push(msg)
      const conv = chats.value.find((c) => c.id === conversationId)
      if (conv) {
        conv.last_message_preview = msg.content
        conv.last_message_at = msg.created_at
      }
      return msg
    } catch (err) {
      markUnauthorizedIf401(err)
      throw err
    } finally {
      isSendingChatReply.value = false
    }
  }

  return {
    currentUser,
    isAuthenticated,
    isLiveAuthenticated,
    userRole,
    isAdmin,
    isManager,
    isStaff,
    mustChangePassword,
    requiresAuth,
    isAuthenticating,
    authError,
    selectedCompanyId,
    activeCompanyId,
    activeCompanyName,
    companies,
    bankCatalog,
    staffMembers,
    myWorkOrders,
    managerDashboard,
    chats,
    activeChatMessages,
    inventory,
    orders,
    customers,
    employees,
    webhookFeed,
    metrics,
    isLoadingCompanies,
    isLoadingBanks,
    isCreatingCompany,
    isLoadingStaff,
    isCreatingStaff,
    isLoadingMyWork,
    isLoadingDashboard,
    isLoadingChats,
    isLoadingChatMessages,
    isSendingChatReply,
    isLoadingInventory,
    isLoadingOrders,
    isLoadingCustomers,
    isLoadingEmployees,
    isLoadingWebhooks,
    isCreatingOrder,
    isCreatingInventory,
    updatingOrderIds,
    mutatingInventoryIds,
    mutatingCustomerIds,
    notifyingOrderIds,
    companiesError,
    staffError,
    myWorkError,
    dashboardError,
    chatsError,
    inventoryError,
    ordersError,
    customersError,
    webhookError,
    lastSearchQuery,
    avgRagLatencyMs,
    ordersByStatus,
    setSelectedCompanyId,
    fetchSession,
    login,
    signup,
    changePassword,
    logout,
    fetchCompanies,
    fetchBankCatalog,
    createCompany,
    updateCompanyBank,
    softDeleteCompany,
    restoreCompany,
    createManager,
    fetchStaff,
    createStaff,
    fetchMyWork,
    updateMyWorkStatus,
    fetchManagerDashboard,
    fetchChats,
    fetchChatMessages,
    replyToChat,
    fetchInventory,
    createInventoryItem,
    softDeleteInventory,
    restoreInventory,
    fetchOrders,
    fetchCustomers,
    softDeleteCustomer,
    restoreCustomer,
    fetchEmployees,
    softDeleteEmployee,
    restoreEmployee,
    fetchWebhookLogs,
    fetchMetrics,
    searchInventorySemantic,
    createOrder,
    updateOrderStatus,
    notifyOrderTelegram
  }
})
