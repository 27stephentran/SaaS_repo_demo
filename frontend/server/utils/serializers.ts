import type {
  AuthUser,
  DemoTelegramNotifyPayload,
  PermissionGroup,
  RestoreResponse,
  SoftDeleteResponse,
  TelegramDispatchResponse
} from '~/types/erp'
import type { BackendTokenPairResponse } from '~/server/utils/backendProxy'

type JsonRecord = Record<string, unknown>

const TENANT_ID_PATTERN = /^[a-zA-Z0-9._-]+$/
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const ORDER_STATUSES = new Set([
  'PENDING_PAYMENT',
  'PAID',
  'FULFILLING',
  'COMPLETED',
  'CANCELLED'
])

function validationError(message: string): never {
  throw createError({
    statusCode: 422,
    statusMessage: message
  })
}

function upstreamSchemaError(message: string): never {
  throw createError({
    statusCode: 502,
    statusMessage: `Upstream response failed schema validation: ${message}`
  })
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function assertAllowedKeys(record: JsonRecord, allowed: readonly string[], context: string): void {
  for (const key of Object.keys(record)) {
    if (!allowed.includes(key)) {
      validationError(`${context}: unexpected field "${key}"`)
    }
  }
}

function readRequiredString(
  record: JsonRecord,
  key: string,
  opts: { min?: number; max?: number; trim?: boolean; pattern?: RegExp; label?: string } = {}
): string {
  const raw = record[key]
  if (typeof raw !== 'string') {
    validationError(`${opts.label ?? key} must be a string`)
  }
  const value = opts.trim === false ? raw : raw.trim()
  const min = opts.min ?? 1
  if (value.length < min) {
    validationError(`${opts.label ?? key} must be at least ${min} character(s)`)
  }
  if (opts.max !== undefined && value.length > opts.max) {
    validationError(`${opts.label ?? key} must be at most ${opts.max} character(s)`)
  }
  if (opts.pattern && !opts.pattern.test(value)) {
    validationError(`${opts.label ?? key} format is invalid`)
  }
  return value
}

function readOptionalString(
  record: JsonRecord,
  key: string,
  opts: { max?: number; trim?: boolean } = {}
): string | null | undefined {
  const raw = record[key]
  if (raw === undefined) return undefined
  if (raw === null) return null
  if (typeof raw !== 'string') {
    validationError(`${key} must be a string or null`)
  }
  const value = opts.trim === false ? raw : raw.trim()
  if (opts.max !== undefined && value.length > opts.max) {
    validationError(`${key} must be at most ${opts.max} character(s)`)
  }
  return value || null
}

function readNonNegativeInt(record: JsonRecord, key: string, min = 0): number {
  const raw = record[key]
  if (typeof raw !== 'number' || !Number.isInteger(raw) || raw < min) {
    validationError(`${key} must be an integer >= ${min}`)
  }
  return raw
}

function assertUpstreamString(record: JsonRecord, key: string, context: string): string {
  const value = record[key]
  if (typeof value !== 'string' || !value.trim()) {
    upstreamSchemaError(`${context}.${key} must be a non-empty string`)
  }
  return value
}

function assertUpstreamBoolean(record: JsonRecord, key: string, context: string): boolean {
  const value = record[key]
  if (typeof value !== 'boolean') {
    upstreamSchemaError(`${context}.${key} must be a boolean`)
  }
  return value
}

function assertUpstreamNumber(record: JsonRecord, key: string, context: string): number {
  const value = record[key]
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    upstreamSchemaError(`${context}.${key} must be a finite number`)
  }
  return value
}

export function parseLoginRequest(raw: unknown): {
  identifier?: string
  email?: string
  password: string
  tenant_id: string
} {
  if (!isRecord(raw)) {
    validationError('Login request body must be a JSON object')
  }
  assertAllowedKeys(raw, ['identifier', 'email', 'password', 'tenant_id'], 'login')

  const hasIdentifier = raw.identifier !== undefined && raw.identifier !== null && String(raw.identifier).trim() !== ''
  const hasEmail = raw.email !== undefined

  if (!hasIdentifier && !hasEmail) {
    validationError('identifier or email is required')
  }

  let email: string | undefined
  if (hasEmail) {
    email = readRequiredString(raw, 'email', {
      min: 3,
      max: 255,
      pattern: EMAIL_PATTERN,
      label: 'email'
    }).toLowerCase()
  }

  let identifier: string | undefined
  if (hasIdentifier) {
    identifier = readRequiredString(raw, 'identifier', {
      min: 2,
      max: 255,
      label: 'identifier'
    })
  }

  const password = readRequiredString(raw, 'password', {
    min: 1,
    max: 256,
    trim: false,
    label: 'password'
  })
  const tenant_id = readRequiredString(raw, 'tenant_id', {
    min: 1,
    max: 64,
    pattern: TENANT_ID_PATTERN,
    label: 'tenant_id'
  })

  if (identifier !== undefined) {
    return {
      identifier,
      ...(email !== undefined ? { email } : {}),
      password,
      tenant_id
    }
  }
  return { email: email!, password, tenant_id }
}

export function parseChangePasswordRequest(raw: unknown): {
  current_password: string
  new_password: string
} {
  if (!isRecord(raw)) {
    validationError('Change password request body must be a JSON object')
  }
  assertAllowedKeys(raw, ['current_password', 'new_password'], 'changePassword')
  const current_password = readRequiredString(raw, 'current_password', {
    min: 1,
    max: 256,
    trim: false,
    label: 'current_password'
  })
  const new_password = readRequiredString(raw, 'new_password', {
    min: 8,
    max: 256,
    trim: false,
    label: 'new_password'
  })
  return { current_password, new_password }
}

export function parseSignupRequest(raw: unknown): {
  email: string
  password: string
  tenant_id: string
} {
  if (!isRecord(raw)) {
    validationError('Signup request body must be a JSON object')
  }
  assertAllowedKeys(raw, ['email', 'password', 'tenant_id'], 'signup')
  const email = readRequiredString(raw, 'email', {
    min: 3,
    max: 255,
    pattern: EMAIL_PATTERN,
    label: 'email'
  }).toLowerCase()
  const password = readRequiredString(raw, 'password', {
    min: 8,
    max: 256,
    trim: false,
    label: 'password'
  })
  const tenant_id = readRequiredString(raw, 'tenant_id', {
    min: 1,
    max: 64,
    pattern: TENANT_ID_PATTERN,
    label: 'tenant_id'
  })
  return { email, password, tenant_id }
}

export function parseDemoTelegramNotifyRequest(raw: unknown): DemoTelegramNotifyPayload {
  if (!isRecord(raw)) {
    validationError('Demo Telegram notification payload must be a JSON object')
  }
  assertAllowedKeys(
    raw,
    ['order_code', 'total_amount_vnd', 'items_summary', 'customer_name', 'telegram_user_id', 'sepay_reference'],
    'demo.notify-telegram'
  )

  return {
    order_code: readRequiredString(raw, 'order_code', { min: 2, max: 64 }),
    total_amount_vnd: readNonNegativeInt(raw, 'total_amount_vnd', 0),
    items_summary: readRequiredString(raw, 'items_summary', { min: 1, max: 500 }),
    customer_name: readOptionalString(raw, 'customer_name', { max: 255 }) ?? null,
    telegram_user_id: readOptionalString(raw, 'telegram_user_id', { max: 64 }) ?? null,
    sepay_reference: readOptionalString(raw, 'sepay_reference', { max: 128 }) ?? null
  }
}

export function parseOrderTelegramNotifyRequest(raw: unknown): {
  telegram_user_id?: string | null
} {
  if (raw === undefined || raw === null) {
    return {}
  }
  if (!isRecord(raw)) {
    validationError('Order Telegram notify payload must be a JSON object')
  }
  assertAllowedKeys(raw, ['telegram_user_id'], 'orders.notify-telegram')
  const telegram_user_id = readOptionalString(raw, 'telegram_user_id', { max: 64 })
  return telegram_user_id !== undefined ? { telegram_user_id } : {}
}

export function parseErpWritePayload(
  cleanPath: string,
  method: string,
  rawBody: string | undefined
): string | undefined {
  if (!rawBody || !rawBody.trim()) {
    if (
      (method === 'POST' &&
        (cleanPath === 'orders' ||
          cleanPath === 'inventory' ||
          cleanPath === 'inventory/search' ||
          cleanPath === 'staff' ||
          /^chats\/[^/]+\/reply$/.test(cleanPath))) ||
      (method === 'PATCH' && /^orders\/[^/]+\/status$/.test(cleanPath))
    ) {
      validationError(`Request body is required for ${method} /api/erp/${cleanPath}`)
    }
    return undefined
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(rawBody) as unknown
  } catch {
    validationError('Request body must be valid JSON')
  }

  if (!isRecord(parsed)) {
    validationError('Request body must be a JSON object')
  }

  if (method === 'POST' && cleanPath === 'orders') {
    assertAllowedKeys(
      parsed,
      ['tenant_id', 'company_id', 'customer_id', 'assigned_employee_id', 'items'],
      'orders.create'
    )
    const customer_id = readRequiredString(parsed, 'customer_id', { min: 1, max: 64 })
    const assigned_employee_id = readOptionalString(parsed, 'assigned_employee_id', { max: 64 })
    const tenant_id = readOptionalString(parsed, 'tenant_id', { max: 64 })
    const company_id = readOptionalString(parsed, 'company_id', { max: 64 })
    const rawItems = parsed.items
    if (!Array.isArray(rawItems) || rawItems.length === 0) {
      validationError('orders.create: items must be a non-empty array')
    }
    const items = rawItems.map((entry, idx) => {
      if (!isRecord(entry)) {
        validationError(`orders.create: items[${idx}] must be an object`)
      }
      assertAllowedKeys(entry, ['sku', 'quantity'], `orders.create.items[${idx}]`)
      return {
        sku: readRequiredString(entry, 'sku', { min: 1, max: 64 }),
        quantity: readNonNegativeInt(entry, 'quantity', 1)
      }
    })
    return JSON.stringify({
      ...(tenant_id ? { tenant_id } : {}),
      ...(company_id ? { company_id } : {}),
      customer_id,
      ...(assigned_employee_id ? { assigned_employee_id } : {}),
      items
    })
  }

  if (method === 'PATCH' && /^orders\/[^/]+\/status$/.test(cleanPath)) {
    assertAllowedKeys(parsed, ['status'], 'orders.status')
    const rawStatus = readRequiredString(parsed, 'status', { min: 2, max: 32 }).toUpperCase()
    const status = rawStatus === 'IN_PROGRESS' ? 'FULFILLING' : rawStatus
    if (!ORDER_STATUSES.has(status)) {
      validationError(`Invalid order status: ${status}`)
    }
    return JSON.stringify({ status })
  }

  if (method === 'POST' && /^orders\/[^/]+\/notify-telegram$/.test(cleanPath)) {
    return JSON.stringify(parseOrderTelegramNotifyRequest(parsed))
  }

  if (method === 'POST' && cleanPath === 'inventory') {
    assertAllowedKeys(
      parsed,
      ['tenant_id', 'company_id', 'sku', 'name', 'description', 'category', 'price_vnd', 'stock_quantity'],
      'inventory.create'
    )
    const tenant_id = readOptionalString(parsed, 'tenant_id', { max: 64 })
    const company_id = readOptionalString(parsed, 'company_id', { max: 64 })
    const category = readOptionalString(parsed, 'category', { max: 128 })
    return JSON.stringify({
      ...(tenant_id ? { tenant_id } : {}),
      ...(company_id ? { company_id } : {}),
      sku: readRequiredString(parsed, 'sku', { min: 2, max: 64 }),
      name: readRequiredString(parsed, 'name', { min: 2, max: 255 }),
      description: readRequiredString(parsed, 'description', { min: 3, max: 4000 }),
      ...(category ? { category } : {}),
      price_vnd: readNonNegativeInt(parsed, 'price_vnd', 0),
      stock_quantity: readNonNegativeInt(parsed, 'stock_quantity', 0)
    })
  }

  if (method === 'POST' && cleanPath === 'inventory/search') {
    assertAllowedKeys(parsed, ['query', 'tenant_id', 'company_id', 'limit', 'include_inactive'], 'inventory.search')
    const query = readRequiredString(parsed, 'query', { min: 1, max: 500 })
    const tenant_id = readOptionalString(parsed, 'tenant_id', { max: 64 })
    const company_id = readOptionalString(parsed, 'company_id', { max: 64 })
    const limit = parsed.limit !== undefined ? readNonNegativeInt(parsed, 'limit', 1) : 5
    if (limit > 25) {
      validationError('inventory.search: limit must be <= 25')
    }
    const include_inactive = parsed.include_inactive === true
    return JSON.stringify({
      query,
      ...(tenant_id ? { tenant_id } : {}),
      ...(company_id ? { company_id } : {}),
      limit,
      include_inactive
    })
  }

  if (method === 'POST' && cleanPath === 'staff') {
    assertAllowedKeys(parsed, ['full_name', 'telegram_user_id', 'email'], 'staff.create')
    const full_name = readRequiredString(parsed, 'full_name', { min: 2, max: 255 })
    const telegram_user_id = readRequiredString(parsed, 'telegram_user_id', { min: 1, max: 64 })
    const email = readOptionalString(parsed, 'email', { max: 255 })
    if (email && !EMAIL_PATTERN.test(email)) {
      validationError('email format is invalid')
    }
    return JSON.stringify({
      full_name,
      telegram_user_id,
      ...(email ? { email: email.toLowerCase() } : {})
    })
  }

  if (method === 'POST' && /^chats\/[^/]+\/reply$/.test(cleanPath)) {
    assertAllowedKeys(parsed, ['content'], 'chats.reply')
    const content = readRequiredString(parsed, 'content', { min: 1, max: 4000 })
    return JSON.stringify({ content })
  }

  return JSON.stringify(parsed)
}

export function serializeTokenPairResponse(
  raw: unknown,
  expectedPermissionGroup?: PermissionGroup
): BackendTokenPairResponse {
  if (!isRecord(raw)) {
    upstreamSchemaError('token pair payload is not an object')
  }
  const access_token = assertUpstreamString(raw, 'access_token', 'tokenPair')
  const refresh_token = assertUpstreamString(raw, 'refresh_token', 'tokenPair')
  const token_type = typeof raw.token_type === 'string' ? raw.token_type : 'bearer'
  const expires_in = assertUpstreamNumber(raw, 'expires_in', 'tokenPair')
  const tenant_id = assertUpstreamString(raw, 'tenant_id', 'tokenPair')
  const role = typeof raw.role === 'string' ? raw.role : null

  const rawGroup = raw.permission_group
  const permission_group: PermissionGroup =
    rawGroup === 'live_api' || rawGroup === 'demo_api'
      ? rawGroup
      : expectedPermissionGroup ?? (role === 'demo' ? 'demo_api' : 'live_api')

  if (expectedPermissionGroup && permission_group !== expectedPermissionGroup) {
    throw createError({
      statusCode: 403,
      statusMessage: `Forbidden: Expected ${expectedPermissionGroup} session, received ${permission_group}`
    })
  }

  return {
    access_token,
    refresh_token,
    token_type,
    expires_in,
    tenant_id,
    role,
    permission_group,
    ...(typeof raw.user_id === 'string' ? { user_id: raw.user_id } : {}),
    ...(typeof raw.email === 'string' ? { email: raw.email } : {}),
    ...(typeof raw.username === 'string' || raw.username === null ? { username: raw.username } : {}),
    ...(typeof raw.full_name === 'string' ? { full_name: raw.full_name } : {}),
    ...(typeof raw.company_id === 'string' || raw.company_id === null ? { company_id: raw.company_id } : {}),
    ...(typeof raw.employee_id === 'string' || raw.employee_id === null ? { employee_id: raw.employee_id } : {}),
    ...(typeof raw.must_change_password === 'boolean' ? { must_change_password: raw.must_change_password } : {})
  }
}

export function serializeAuthSessionResponse(
  raw: unknown,
  fallbackEmail?: string
): AuthUser {
  if (!isRecord(raw)) {
    upstreamSchemaError('auth principal payload is not an object')
  }
  const tenant_id = assertUpstreamString(raw, 'tenant_id', 'authMe')
  const role = typeof raw.role === 'string' && raw.role.trim() ? raw.role : 'staff'
  const email =
    typeof raw.email === 'string' && raw.email.trim()
      ? raw.email
      : fallbackEmail ?? ''
  if (!email) {
    upstreamSchemaError('authMe.email is missing')
  }
  const rawGroup = raw.permission_group
  const permission_group: PermissionGroup =
    rawGroup === 'live_api' || rawGroup === 'demo_api'
      ? rawGroup
      : role === 'demo'
        ? 'demo_api'
        : 'live_api'

  return {
    authenticated: true,
    ...(typeof raw.user_id === 'string' ? { user_id: raw.user_id } : {}),
    email,
    ...(typeof raw.username === 'string' || raw.username === null ? { username: raw.username } : {}),
    ...(typeof raw.full_name === 'string' && raw.full_name ? { full_name: raw.full_name } : {}),
    tenant_id,
    role,
    permission_group,
    ...(typeof raw.company_id === 'string' || raw.company_id === null ? { company_id: raw.company_id } : {}),
    ...(typeof raw.employee_id === 'string' || raw.employee_id === null ? { employee_id: raw.employee_id } : {}),
    ...(typeof raw.must_change_password === 'boolean' ? { must_change_password: raw.must_change_password } : {})
  }
}

export function serializeLogoutResponse(raw: unknown): {
  authenticated: false
  logged_out: true
  status: 'logged_out'
  revoked: boolean
} {
  if (raw !== null && raw !== undefined && !isRecord(raw)) {
    upstreamSchemaError('logout response is invalid')
  }
  return {
    authenticated: false,
    logged_out: true,
    status: 'logged_out',
    revoked: true
  }
}

export function serializeTelegramDispatchResponse(raw: unknown): TelegramDispatchResponse {
  if (!isRecord(raw)) {
    upstreamSchemaError('TelegramDispatchResponse is not an object')
  }
  return {
    dispatched: assertUpstreamBoolean(raw, 'dispatched', 'telegramDispatch'),
    simulated: assertUpstreamBoolean(raw, 'simulated', 'telegramDispatch'),
    telegram_user_id: typeof raw.telegram_user_id === 'string' ? raw.telegram_user_id : '',
    order_code: assertUpstreamString(raw, 'order_code', 'telegramDispatch'),
    preview_text: typeof raw.preview_text === 'string' ? raw.preview_text : null,
    error: typeof raw.error === 'string' ? raw.error : null,
    status_code: typeof raw.status_code === 'number' ? raw.status_code : null
  }
}

function serializeSoftDeleteResponse(raw: unknown): SoftDeleteResponse {
  if (!isRecord(raw)) {
    upstreamSchemaError('SoftDeleteResponse is not an object')
  }
  return {
    resource: assertUpstreamString(raw, 'resource', 'softDelete'),
    record_id: assertUpstreamString(raw, 'record_id', 'softDelete'),
    tenant_id: assertUpstreamString(raw, 'tenant_id', 'softDelete'),
    is_active: assertUpstreamBoolean(raw, 'is_active', 'softDelete'),
    deactivated_at: assertUpstreamString(raw, 'deactivated_at', 'softDelete')
  }
}

function serializeRestoreResponse(raw: unknown): RestoreResponse {
  if (!isRecord(raw)) {
    upstreamSchemaError('RestoreResponse is not an object')
  }
  return {
    resource: assertUpstreamString(raw, 'resource', 'restore'),
    record_id: assertUpstreamString(raw, 'record_id', 'restore'),
    tenant_id: assertUpstreamString(raw, 'tenant_id', 'restore'),
    is_active: assertUpstreamBoolean(raw, 'is_active', 'restore'),
    deactivated_at: typeof raw.deactivated_at === 'string' ? raw.deactivated_at : null
  }
}

export function serializeErpResponse(cleanPath: string, method: string, raw: unknown): unknown {
  if (method === 'DELETE' && /^(inventory|customers|employees)\/[^/]+$/.test(cleanPath)) {
    return serializeSoftDeleteResponse(raw)
  }

  if (method === 'POST' && /^(inventory|customers|employees)\/[^/]+\/restore$/.test(cleanPath)) {
    return serializeRestoreResponse(raw)
  }

  if (method === 'POST' && /^orders\/[^/]+\/notify-telegram$/.test(cleanPath)) {
    return serializeTelegramDispatchResponse(raw)
  }

  if ((method === 'GET' && cleanPath === 'inventory') || cleanPath === 'inventory/search') {
    if (!Array.isArray(raw)) {
      upstreamSchemaError('inventory list must be an array')
    }
    for (const item of raw) {
      if (!isRecord(item)) upstreamSchemaError('inventory item must be an object')
      assertUpstreamString(item, 'id', 'inventoryItem')
      assertUpstreamString(item, 'sku', 'inventoryItem')
      assertUpstreamString(item, 'name', 'inventoryItem')
      assertUpstreamNumber(item, 'price_vnd', 'inventoryItem')
      assertUpstreamNumber(item, 'stock_quantity', 'inventoryItem')
    }
    return raw
  }

  if (method === 'POST' && cleanPath === 'inventory') {
    if (!isRecord(raw)) upstreamSchemaError('created inventory item must be an object')
    assertUpstreamString(raw, 'id', 'inventoryItem')
    assertUpstreamString(raw, 'sku', 'inventoryItem')
    assertUpstreamString(raw, 'name', 'inventoryItem')
    return raw
  }

  if (method === 'GET' && cleanPath === 'orders') {
    if (!Array.isArray(raw)) upstreamSchemaError('orders list must be an array')
    for (const order of raw) {
      if (!isRecord(order)) upstreamSchemaError('order item must be an object')
      assertUpstreamString(order, 'id', 'order')
      assertUpstreamString(order, 'order_code', 'order')
      assertUpstreamString(order, 'status', 'order')
      assertUpstreamNumber(order, 'total_amount_vnd', 'order')
    }
    return raw
  }

  if (
    (method === 'POST' && cleanPath === 'orders') ||
    (method === 'PATCH' && /^orders\/[^/]+\/status$/.test(cleanPath))
  ) {
    if (!isRecord(raw)) upstreamSchemaError('order response must be an object')
    assertUpstreamString(raw, 'id', 'order')
    assertUpstreamString(raw, 'order_code', 'order')
    assertUpstreamString(raw, 'status', 'order')
    assertUpstreamNumber(raw, 'total_amount_vnd', 'order')
    return raw
  }

  if (method === 'GET' && cleanPath === 'customers') {
    if (!Array.isArray(raw)) upstreamSchemaError('customers list must be an array')
    for (const customer of raw) {
      if (!isRecord(customer)) upstreamSchemaError('customer item must be an object')
      assertUpstreamString(customer, 'id', 'customer')
      assertUpstreamString(customer, 'meta_psid', 'customer')
      assertUpstreamString(customer, 'full_name', 'customer')
    }
    return raw
  }

  if (method === 'GET' && cleanPath === 'employees') {
    if (!Array.isArray(raw)) upstreamSchemaError('employees list must be an array')
    for (const emp of raw) {
      if (!isRecord(emp)) upstreamSchemaError('employee item must be an object')
      assertUpstreamString(emp, 'id', 'employee')
      assertUpstreamString(emp, 'full_name', 'employee')
      assertUpstreamString(emp, 'telegram_user_id', 'employee')
    }
    return raw
  }

  if (method === 'GET' && cleanPath === 'webhooks/logs') {
    if (!Array.isArray(raw)) upstreamSchemaError('webhook logs must be an array')
    for (const entry of raw) {
      if (!isRecord(entry) || (typeof entry.id !== 'string' && typeof entry.id !== 'number')) {
        upstreamSchemaError('webhook log entry must be an object with id')
      }
    }
    return raw
  }

  if (method === 'GET' && cleanPath === 'metrics') {
    if (!isRecord(raw)) upstreamSchemaError('metrics response must be an object')
    assertUpstreamNumber(raw, 'total_orders', 'metrics')
    return raw
  }

  if (method === 'GET' && cleanPath === 'my-work') {
    if (!Array.isArray(raw)) upstreamSchemaError('my-work list must be an array')
    for (const order of raw) {
      if (!isRecord(order)) upstreamSchemaError('my-work order must be an object')
      assertUpstreamString(order, 'id', 'myWorkOrder')
      assertUpstreamString(order, 'order_code', 'myWorkOrder')
      assertUpstreamString(order, 'status', 'myWorkOrder')
      assertUpstreamNumber(order, 'total_amount_vnd', 'myWorkOrder')
    }
    return raw
  }

  if (method === 'GET' && cleanPath === 'staff') {
    if (!Array.isArray(raw)) upstreamSchemaError('staff list must be an array')
    for (const member of raw) {
      if (!isRecord(member)) upstreamSchemaError('staff member must be an object')
      assertUpstreamString(member, 'employee_id', 'staffMember')
      assertUpstreamString(member, 'full_name', 'staffMember')
      assertUpstreamString(member, 'telegram_user_id', 'staffMember')
    }
    return raw
  }

  if (method === 'POST' && cleanPath === 'staff') {
    if (!isRecord(raw)) upstreamSchemaError('staff create response must be an object')
    assertUpstreamString(raw, 'employee_id', 'staffCreate')
    assertUpstreamString(raw, 'user_id', 'staffCreate')
    assertUpstreamString(raw, 'username', 'staffCreate')
    assertUpstreamString(raw, 'temporary_password', 'staffCreate')
    assertUpstreamString(raw, 'full_name', 'staffCreate')
    return raw
  }

  if (method === 'GET' && cleanPath === 'dashboard/manager') {
    if (!isRecord(raw)) upstreamSchemaError('manager dashboard response must be an object')
    assertUpstreamNumber(raw, 'total_revenue_vnd', 'managerDashboard')
    assertUpstreamNumber(raw, 'paid_orders_count', 'managerDashboard')
    assertUpstreamNumber(raw, 'pending_orders_count', 'managerDashboard')
    assertUpstreamNumber(raw, 'total_orders_count', 'managerDashboard')
    assertUpstreamNumber(raw, 'active_customers_count', 'managerDashboard')
    assertUpstreamNumber(raw, 'active_staff_count', 'managerDashboard')
    assertUpstreamNumber(raw, 'open_chats_count', 'managerDashboard')
    if (!Array.isArray(raw.daily_income)) {
      upstreamSchemaError('managerDashboard.daily_income must be an array')
    }
    for (const point of raw.daily_income) {
      if (!isRecord(point)) upstreamSchemaError('managerDashboard.daily_income item must be an object')
      assertUpstreamString(point, 'date', 'managerDashboard.daily_income')
      assertUpstreamNumber(point, 'revenue_vnd', 'managerDashboard.daily_income')
      assertUpstreamNumber(point, 'orders_count', 'managerDashboard.daily_income')
    }
    if (!Array.isArray(raw.staff_workload)) {
      upstreamSchemaError('managerDashboard.staff_workload must be an array')
    }
    if (!Array.isArray(raw.recent_customers)) {
      upstreamSchemaError('managerDashboard.recent_customers must be an array')
    }
    return raw
  }

  if (method === 'GET' && cleanPath === 'chats') {
    if (!Array.isArray(raw)) upstreamSchemaError('chats list must be an array')
    for (const conv of raw) {
      if (!isRecord(conv)) upstreamSchemaError('conversation must be an object')
      assertUpstreamString(conv, 'id', 'conversation')
      assertUpstreamString(conv, 'channel', 'conversation')
      assertUpstreamString(conv, 'customer_name', 'conversation')
    }
    return raw
  }

  if (method === 'GET' && /^chats\/[^/]+\/messages$/.test(cleanPath)) {
    if (!Array.isArray(raw)) upstreamSchemaError('chat messages must be an array')
    for (const msg of raw) {
      if (!isRecord(msg)) upstreamSchemaError('chat message must be an object')
      assertUpstreamString(msg, 'id', 'chatMessage')
      assertUpstreamString(msg, 'conversation_id', 'chatMessage')
      assertUpstreamString(msg, 'sender_type', 'chatMessage')
      assertUpstreamString(msg, 'content', 'chatMessage')
    }
    return raw
  }

  if (method === 'POST' && /^chats\/[^/]+\/reply$/.test(cleanPath)) {
    if (!isRecord(raw)) upstreamSchemaError('chat reply response must be an object')
    assertUpstreamString(raw, 'id', 'chatReply')
    assertUpstreamString(raw, 'conversation_id', 'chatReply')
    assertUpstreamString(raw, 'content', 'chatReply')
    return raw
  }

  return raw
}

export function parseCompanyWritePayload(
  cleanPath: string,
  method: string,
  rawBody: string | undefined
): string | undefined {
  if (!rawBody || !rawBody.trim()) {
    if (
      (method === 'POST' && (cleanPath === '' || /^[^/]+\/managers$/.test(cleanPath))) ||
      ((method === 'PATCH' || method === 'PUT') && /^[^/]+\/bank$/.test(cleanPath))
    ) {
      validationError(`Request body is required for ${method} /api/companies/${cleanPath}`)
    }
    return undefined
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(rawBody) as unknown
  } catch {
    validationError('Request body must be valid JSON')
  }

  if (!isRecord(parsed)) {
    validationError('Request body must be a JSON object')
  }

  if (method === 'POST' && cleanPath === '') {
    assertAllowedKeys(
      parsed,
      [
        'name',
        'code',
        'bank_code',
        'bank_name',
        'bank_account_number',
        'bank_account_name',
        'sepay_sub_account',
        'sepay_memo_prefix',
        'sepay_bank_account_id'
      ],
      'companies.create'
    )
    const bank_name = readOptionalString(parsed, 'bank_name', { max: 128 })
    const sepay_sub_account = readOptionalString(parsed, 'sepay_sub_account', { max: 64 })
    const sepay_memo_prefix = readOptionalString(parsed, 'sepay_memo_prefix', { max: 32 })
    const sepay_bank_account_id = readOptionalString(parsed, 'sepay_bank_account_id', { max: 128 })
    return JSON.stringify({
      name: readRequiredString(parsed, 'name', { min: 2, max: 255 }),
      code: readRequiredString(parsed, 'code', { min: 2, max: 32 }).toUpperCase(),
      bank_code: readRequiredString(parsed, 'bank_code', { min: 2, max: 32 }).toUpperCase(),
      ...(bank_name ? { bank_name } : {}),
      bank_account_number: readRequiredString(parsed, 'bank_account_number', { min: 3, max: 64 }),
      bank_account_name: readRequiredString(parsed, 'bank_account_name', { min: 2, max: 255 }),
      ...(sepay_sub_account !== undefined ? { sepay_sub_account: sepay_sub_account ?? '' } : {}),
      ...(sepay_memo_prefix !== undefined ? { sepay_memo_prefix: sepay_memo_prefix ?? '' } : {}),
      ...(sepay_bank_account_id !== undefined ? { sepay_bank_account_id } : {})
    })
  }

  if ((method === 'PATCH' || method === 'PUT') && /^[^/]+\/bank$/.test(cleanPath)) {
    assertAllowedKeys(
      parsed,
      [
        'bank_code',
        'bank_name',
        'bank_account_number',
        'bank_account_name',
        'sepay_sub_account',
        'sepay_memo_prefix',
        'sepay_bank_account_id'
      ],
      'companies.bank'
    )
    const bank_name = readOptionalString(parsed, 'bank_name', { max: 128 })
    const sepay_sub_account = readOptionalString(parsed, 'sepay_sub_account', { max: 64 })
    const sepay_memo_prefix = readOptionalString(parsed, 'sepay_memo_prefix', { max: 32 })
    const sepay_bank_account_id = readOptionalString(parsed, 'sepay_bank_account_id', { max: 128 })
    return JSON.stringify({
      bank_code: readRequiredString(parsed, 'bank_code', { min: 2, max: 32 }).toUpperCase(),
      ...(bank_name ? { bank_name } : {}),
      bank_account_number: readRequiredString(parsed, 'bank_account_number', { min: 3, max: 64 }),
      bank_account_name: readRequiredString(parsed, 'bank_account_name', { min: 2, max: 255 }),
      ...(sepay_sub_account !== undefined ? { sepay_sub_account: sepay_sub_account ?? '' } : {}),
      ...(sepay_memo_prefix !== undefined ? { sepay_memo_prefix: sepay_memo_prefix ?? '' } : {}),
      ...(sepay_bank_account_id !== undefined ? { sepay_bank_account_id } : {})
    })
  }

  if (method === 'POST' && /^[^/]+\/managers$/.test(cleanPath)) {
    assertAllowedKeys(parsed, ['full_name', 'email', 'username', 'password'], 'companies.managers')
    const full_name = readRequiredString(parsed, 'full_name', { min: 2, max: 255 })
    const email = readOptionalString(parsed, 'email', { max: 255 })
    if (email && !EMAIL_PATTERN.test(email)) {
      validationError('email format is invalid')
    }
    const username = readOptionalString(parsed, 'username', { max: 64 })
    const password = readOptionalString(parsed, 'password', { max: 256, trim: false })
    if (password && password.length < 8) {
      validationError('password must be at least 8 character(s)')
    }
    return JSON.stringify({
      full_name,
      ...(email ? { email: email.toLowerCase() } : {}),
      ...(username ? { username } : {}),
      ...(password ? { password } : {})
    })
  }

  return JSON.stringify(parsed)
}

export function serializeCompanyResponse(cleanPath: string, method: string, raw: unknown): unknown {
  if (method === 'GET' && cleanPath === 'banks') {
    if (!isRecord(raw) || !Array.isArray(raw.banks)) {
      upstreamSchemaError('banks catalog response must contain banks array')
    }
    if (!Array.isArray(raw.sepay_linked_accounts)) {
      upstreamSchemaError('banks catalog response must contain sepay_linked_accounts array')
    }
    assertUpstreamBoolean(raw, 'sepay_api_configured', 'bankCatalog')
    assertUpstreamBoolean(raw, 'bankhub_configured', 'bankCatalog')
    return raw
  }

  if (method === 'GET' && cleanPath === '') {
    if (!Array.isArray(raw)) upstreamSchemaError('companies list must be an array')
    for (const company of raw) {
      if (!isRecord(company)) upstreamSchemaError('company item must be an object')
      assertUpstreamString(company, 'id', 'company')
      assertUpstreamString(company, 'name', 'company')
      assertUpstreamString(company, 'code', 'company')
      assertUpstreamString(company, 'bank_code', 'company')
      assertUpstreamString(company, 'bank_account_number', 'company')
    }
    return raw
  }

  if (
    (method === 'POST' && cleanPath === '') ||
    ((method === 'PATCH' || method === 'PUT') && /^[^/]+\/bank$/.test(cleanPath))
  ) {
    if (!isRecord(raw)) upstreamSchemaError('company response must be an object')
    assertUpstreamString(raw, 'id', 'company')
    assertUpstreamString(raw, 'name', 'company')
    assertUpstreamString(raw, 'code', 'company')
    assertUpstreamString(raw, 'bank_code', 'company')
    assertUpstreamString(raw, 'bank_account_number', 'company')
    return raw
  }

  if (method === 'DELETE' && /^[^/]+$/.test(cleanPath)) {
    return serializeSoftDeleteResponse(raw)
  }

  if (method === 'POST' && /^[^/]+\/restore$/.test(cleanPath)) {
    return serializeRestoreResponse(raw)
  }

  if (method === 'POST' && /^[^/]+\/managers$/.test(cleanPath)) {
    if (!isRecord(raw)) upstreamSchemaError('manager create response must be an object')
    assertUpstreamString(raw, 'user_id', 'managerCreate')
    assertUpstreamString(raw, 'company_id', 'managerCreate')
    assertUpstreamString(raw, 'username', 'managerCreate')
    assertUpstreamString(raw, 'email', 'managerCreate')
    return raw
  }

  return raw
}
