<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { isValidOrderTransition, useErpStore } from '~/stores/erp'
import type { Employee, Order, OrderCreateItemPayload, OrderStatus, WebhookSource } from '~/types/erp'

const erpStore = useErpStore()
const toast = useToast()

const searchQuery = ref<string>('')
const selectedTab = ref<number>(0)
const expandedQrOrderId = ref<string | null>(null)
const isCreateOrderOpen = ref<boolean>(false)
const createOrderError = ref<string | null>(null)
let pollTimer: ReturnType<typeof setInterval> | null = null

const orderDraft = reactive<{
  customer_id: string
  assigned_employee_id: string
  items: OrderCreateItemPayload[]
}>({
  customer_id: '',
  assigned_employee_id: '',
  items: [{ sku: '', quantity: 1 }]
})

const tabs = [
  { label: 'Fulfillment Kanban & Inventory', icon: 'i-heroicons-squares-2x2' },
  { label: 'Omnichannel Webhook & Staff Dispatch', icon: 'i-heroicons-bolt' }
]

const inventoryColumns = [
  { key: 'sku', label: 'SKU' },
  { key: 'name', label: 'Product & Category' },
  { key: 'price_vnd', label: 'Unit Price (VND)' },
  { key: 'stock_quantity', label: 'Stock' },
  { key: 'has_embedding', label: 'pgvector (<=>)' }
]

const kanbanColumns: {
  key: OrderStatus
  title: string
  badgeColor: 'amber' | 'emerald' | 'blue' | 'primary' | 'red'
  nextStatus?: OrderStatus
  nextLabel?: string
}[] = [
  {
    key: 'PENDING_PAYMENT',
    title: 'Awaiting SePay VietQR',
    badgeColor: 'amber'
  },
  {
    key: 'PAID',
    title: 'Paid & Telegram Dispatched',
    badgeColor: 'emerald',
    nextStatus: 'FULFILLING',
    nextLabel: 'Start Fulfillment'
  },
  {
    key: 'FULFILLING',
    title: 'Fulfilling in Warehouse',
    badgeColor: 'blue',
    nextStatus: 'COMPLETED',
    nextLabel: 'Mark Completed'
  },
  {
    key: 'COMPLETED',
    title: 'Completed Orders',
    badgeColor: 'primary'
  },
  {
    key: 'CANCELLED',
    title: 'Cancelled Orders',
    badgeColor: 'red'
  }
]

const embeddedSkuCount = computed<number>(
  () => erpStore.metrics?.vectorized_skus ?? erpStore.inventory.filter((item) => item.has_embedding).length
)

const activeSkuCount = computed<number>(
  () => erpStore.metrics?.active_inventory_skus ?? erpStore.inventory.length
)

const pendingVietQrCount = computed<number>(
  () => erpStore.metrics?.pending_vietqr_orders ?? erpStore.ordersByStatus.PENDING_PAYMENT.length
)

const paidAndDispatchedCount = computed<number>(
  () =>
    erpStore.metrics?.paid_and_dispatched_orders ??
    (erpStore.ordersByStatus.PAID.length +
      erpStore.ordersByStatus.FULFILLING.length +
      erpStore.ordersByStatus.COMPLETED.length)
)

const customerOptions = computed(() =>
  erpStore.customers.map((customer) => ({
    label: `${customer.full_name} (${customer.meta_psid})`,
    value: customer.id
  }))
)

const employeeOptions = computed(() => [
  { label: 'Unassigned (Auto-dispatch on PAID)', value: '' },
  ...erpStore.employees.map((employee) => ({
    label: `${employee.full_name} (${employee.telegram_user_id})`,
    value: employee.id
  }))
])

const inventorySkuOptions = computed(() =>
  erpStore.inventory.map((item) => ({
    label: `${item.sku} — ${item.name} (${formatVnd(item.price_vnd)})`,
    value: item.sku
  }))
)

const estimatedDraftTotalVnd = computed<number>(() => {
  return orderDraft.items.reduce((sum, line) => {
    const matched = erpStore.inventory.find((inv) => inv.sku === line.sku)
    const unitPrice = matched?.price_vnd ?? 0
    return sum + unitPrice * Math.max(0, Number(line.quantity) || 0)
  }, 0)
})

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

function formatEventTimeUtc(timestamp: string): string {
  const parsed = new Date(timestamp)
  if (Number.isNaN(parsed.getTime())) {
    return timestamp
  }
  return `${parsed.toISOString().slice(11, 19)}Z`
}

function getEmployeeById(employeeId?: string | null): Employee | undefined {
  if (!employeeId) return undefined
  return erpStore.employees.find((emp) => emp.id === employeeId)
}

function isSafeHttpUrl(url?: string | null): url is string {
  return Boolean(url && /^https?:\/\//i.test(url))
}

function getWebhookBadgeColor(source: WebhookSource): 'blue' | 'emerald' | 'violet' {
  switch (source) {
    case 'META_GRAPH':
      return 'blue'
    case 'SEPAY_IPN':
      return 'emerald'
    case 'TELEGRAM_BOT':
      return 'violet'
  }
}

async function handleSemanticSearch(): Promise<void> {
  await erpStore.searchInventorySemantic(searchQuery.value)
}

async function handleResetSearch(): Promise<void> {
  searchQuery.value = ''
  await erpStore.fetchInventory()
}

async function handleLogout(): Promise<void> {
  await erpStore.logout()
  toast.add({
    title: 'Signed Out',
    description: 'Operator session cookies cleared.',
    color: 'gray'
  })
}

async function handleRefreshTelemetry(): Promise<void> {
  await Promise.all([erpStore.fetchWebhookLogs(), erpStore.fetchMetrics()])
}

function openCreateOrderModal(): void {
  createOrderError.value = null
  orderDraft.customer_id = erpStore.customers[0]?.id ?? ''
  orderDraft.assigned_employee_id = ''
  orderDraft.items = [
    {
      sku: erpStore.inventory[0]?.sku ?? '',
      quantity: 1
    }
  ]
  isCreateOrderOpen.value = true
}

function addOrderLineItem(): void {
  orderDraft.items.push({
    sku: erpStore.inventory[0]?.sku ?? '',
    quantity: 1
  })
}

function removeOrderLineItem(index: number): void {
  if (orderDraft.items.length <= 1) return
  orderDraft.items.splice(index, 1)
}

async function submitCreateOrder(): Promise<void> {
  createOrderError.value = null
  const validItems = orderDraft.items
    .filter((item) => item.sku.trim().length > 0 && Number(item.quantity) >= 1)
    .map((item) => ({
      sku: item.sku.trim(),
      quantity: Math.floor(Number(item.quantity))
    }))

  if (!orderDraft.customer_id.trim()) {
    createOrderError.value = 'Please select or enter a customer ID.'
    return
  }
  if (validItems.length === 0) {
    createOrderError.value = 'Add at least one SKU with quantity >= 1.'
    return
  }

  try {
    const created = await erpStore.createOrder({
      customer_id: orderDraft.customer_id.trim(),
      assigned_employee_id: orderDraft.assigned_employee_id.trim() || null,
      items: validItems
    })
    isCreateOrderOpen.value = false
    toast.add({
      title: `Order ${created.order_code} Created`,
      description: `Server-authoritative total: ${formatVnd(created.total_amount_vnd)}`,
      color: 'emerald'
    })
  } catch (err) {
    const detail =
      err && typeof err === 'object' && 'data' in err && (err as { data?: { detail?: unknown } }).data?.detail
    createOrderError.value =
      typeof detail === 'string' && detail.trim()
        ? detail
        : 'Unable to create order. Ensure you are signed in and SKUs have sufficient stock.'
  }
}

async function advanceOrder(order: Order, nextStatus: OrderStatus): Promise<void> {
  if (!isValidOrderTransition(order.status, nextStatus)) {
    toast.add({
      title: 'Invalid State Transition',
      description: `Transition ${order.status} -> ${nextStatus} is not permitted.`,
      color: 'red'
    })
    return
  }

  try {
    await erpStore.updateOrderStatus(order.id, nextStatus)
    toast.add({
      title: `Order ${order.order_code} -> ${nextStatus}`,
      description:
        nextStatus === 'PAID'
          ? `SePay VietQR IPN verified (${order.sepay_reference}). Dispatched to Telegram staff.`
          : `Order status updated to ${nextStatus}.`,
      color: nextStatus === 'CANCELLED' ? 'amber' : 'emerald'
    })
  } catch {
    toast.add({
      title: `Failed to update ${order.order_code}`,
      description: `Could not transition order from ${order.status} to ${nextStatus}.`,
      color: 'red'
    })
  }
}

function toggleQrPreview(orderId: string): void {
  expandedQrOrderId.value = expandedQrOrderId.value === orderId ? null : orderId
}

onMounted(async () => {
  await erpStore.fetchSession()
  await Promise.all([
    erpStore.fetchInventory(),
    erpStore.fetchOrders(),
    erpStore.fetchCustomers(),
    erpStore.fetchEmployees(),
    erpStore.fetchWebhookLogs(),
    erpStore.fetchMetrics()
  ])
  pollTimer = setInterval(() => {
    if (pendingVietQrCount.value > 0) {
      void erpStore.fetchOrders()
      void erpStore.fetchWebhookLogs()
    }
  }, 10000)
})

onUnmounted(() => {
  if (pollTimer !== null) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<template>
  <UContainer class="py-8 space-y-8">
    <!-- Top Header & Operator Session Bar -->
    <div class="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4 border-b border-slate-800 pb-6">
      <div>
        <div class="flex items-center gap-2.5">
          <span class="inline-block h-3 w-3 rounded-full bg-emerald-400 animate-pulse" />
          <span class="text-xs font-semibold uppercase tracking-widest text-emerald-400">
            AI-Native Omnichannel ERP Control Plane
          </span>
        </div>
        <h1 class="mt-1 text-2xl sm:text-3xl font-bold tracking-tight text-white">
          Social Commerce RAG &bull; SePay VietQR &bull; Telegram Dispatch
        </h1>
        <p class="mt-1 text-sm text-slate-400">
          Real-time inventory vector search, automated VietQR bank webhook reconciliation, and instant staff fulfillment alerts.
        </p>
      </div>

      <div class="flex flex-col items-start lg:items-end gap-3">
        <div class="flex flex-wrap items-center gap-2">
          <UBadge color="emerald" variant="subtle" size="md">
            FastAPI Async Core
          </UBadge>
          <UBadge color="cyan" variant="subtle" size="md">
            Supabase pgvector (&lt;=&gt;)
          </UBadge>
          <UBadge color="violet" variant="subtle" size="md">
            Gemini 1.5 Flash/Pro RAG
          </UBadge>
          <UBadge color="amber" variant="subtle" size="md">
            SePay VietQR Sandbox
          </UBadge>
        </div>

        <div class="flex flex-wrap items-center gap-2">
          <template v-if="erpStore.isAuthenticated && erpStore.currentUser">
            <UBadge color="emerald" variant="soft" size="sm">
              {{ erpStore.currentUser.email }} &bull; {{ erpStore.currentUser.role }} ({{ erpStore.currentUser.tenant_id }})
            </UBadge>
            <UButton
              size="xs"
              color="gray"
              variant="outline"
              icon="i-heroicons-arrow-right-on-rectangle"
              @click="handleLogout"
            >
              Sign Out
            </UButton>
          </template>
          <template v-else>
            <UBadge color="amber" variant="soft" size="sm">
              Demo Preview Mode
            </UBadge>
            <UButton
              to="/login"
              size="xs"
              color="emerald"
              variant="soft"
              icon="i-heroicons-lock-closed"
            >
              Operator Sign In
            </UButton>
          </template>
        </div>
      </div>
    </div>

    <!-- Non-blocking Auth Prompt when API returns 401 -->
    <UAlert
      v-if="erpStore.requiresAuth && !erpStore.isAuthenticated"
      color="amber"
      variant="soft"
      icon="i-heroicons-shield-exclamation"
      title="Operator authentication required for live backend mutations"
      description="Viewing local preview data. Sign in via /login to connect to live tenant ERP endpoints with HttpOnly session cookies."
    >
      <template #actions>
        <UButton to="/login" size="xs" color="amber" variant="solid">
          Sign In
        </UButton>
      </template>
    </UAlert>

    <!-- 4 KPI Metric Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <UCard class="bg-slate-900/80 border border-slate-800">
        <div class="text-xs font-medium uppercase tracking-wider text-slate-400">
          Active Inventory SKUs
        </div>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-3xl font-bold text-white">
            {{ activeSkuCount }}
          </span>
          <UBadge color="cyan" variant="soft" size="xs">
            {{ embeddedSkuCount }} / {{ activeSkuCount }} Vectorized
          </UBadge>
        </div>
        <p class="mt-2 text-xs text-slate-400">
          768-dim Gemini embeddings indexed via <code class="text-cyan-300">pgvector</code>
        </p>
      </UCard>

      <UCard class="bg-slate-900/80 border border-slate-800">
        <div class="text-xs font-medium uppercase tracking-wider text-slate-400">
          Awaiting VietQR Payment
        </div>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-3xl font-bold text-amber-400">
            {{ pendingVietQrCount }}
          </span>
          <UBadge color="amber" variant="soft" size="xs">
            SePay IPN Listener
          </UBadge>
        </div>
        <p class="mt-2 text-xs text-slate-400">
          Dynamic QR generated in Messenger/IG DM checkout
        </p>
      </UCard>

      <UCard class="bg-slate-900/80 border border-slate-800">
        <div class="text-xs font-medium uppercase tracking-wider text-slate-400">
          Paid &amp; Dispatched to Staff
        </div>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-3xl font-bold text-emerald-400">
            {{ paidAndDispatchedCount }}
          </span>
          <UBadge color="emerald" variant="soft" size="xs">
            {{ erpStore.employees.length }} Active Telegram Staff
          </UBadge>
        </div>
        <p class="mt-2 text-xs text-slate-400">
          Instant push via <code class="text-emerald-300">telegram_user_id</code>
        </p>
      </UCard>

      <UCard class="bg-slate-900/80 border border-slate-800">
        <div class="text-xs font-medium uppercase tracking-wider text-slate-400">
          Avg Gemini RAG Reply Latency
        </div>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-3xl font-bold text-violet-400">
            {{ erpStore.avgRagLatencyMs }}ms
          </span>
          <UBadge color="violet" variant="soft" size="xs">
            Cosine &lt;=&gt; Top-K
          </UBadge>
        </div>
        <p class="mt-2 text-xs text-slate-400">
          Zero-hallucination grounded stock &amp; price lookup
        </p>
      </UCard>
    </div>

    <!-- Navigation Tabs -->
    <UTabs v-model="selectedTab" :items="tabs" class="w-full" />

    <!-- Tab 0: Kanban Board & Semantic Inventory -->
    <div v-if="selectedTab === 0" class="space-y-8">
      <!-- 5-Column Orders Fulfillment Kanban Board -->
      <section class="space-y-4">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div>
            <h2 class="text-lg font-semibold text-white">
              5-Column Orders Fulfillment Kanban Board
            </h2>
            <p class="text-xs text-slate-400">
              Enforces strict order state transitions from SePay VietQR verification to warehouse handover or cancellation.
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <div class="flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
              <span>Dispatch Roster:</span>
              <UBadge
                v-for="emp in erpStore.employees"
                :key="emp.id"
                color="gray"
                variant="subtle"
                size="xs"
              >
                {{ emp.full_name }} ({{ emp.telegram_user_id }})
              </UBadge>
            </div>
            <UButton
              size="xs"
              color="emerald"
              variant="solid"
              icon="i-heroicons-plus"
              @click="openCreateOrderModal"
            >
              Create Order
            </UButton>
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5 gap-4">
          <UCard
            v-for="col in kanbanColumns"
            :key="col.key"
            class="bg-slate-900/90 border border-slate-800 flex flex-col justify-between"
          >
            <template #header>
              <div class="flex items-center justify-between gap-2">
                <span class="text-sm font-semibold text-white">
                  {{ col.title }}
                </span>
                <UBadge :color="col.badgeColor" variant="subtle" size="xs">
                  {{ erpStore.ordersByStatus[col.key].length }}
                </UBadge>
              </div>
            </template>

            <div class="space-y-3 min-h-[220px]">
              <div
                v-for="order in erpStore.ordersByStatus[col.key]"
                :key="order.id"
                class="rounded-lg border border-slate-800 bg-slate-950/80 p-3.5 space-y-2.5"
              >
                <div class="flex items-center justify-between">
                  <span class="font-mono text-xs font-bold text-emerald-400">
                    {{ order.order_code }}
                  </span>
                  <span class="font-mono text-xs text-slate-400">
                    {{ order.sepay_reference }}
                  </span>
                </div>

                <div class="text-sm font-medium text-white">
                  {{ order.customer_name }}
                </div>

                <ul class="space-y-1 text-xs text-slate-300">
                  <li v-for="item in order.items" :key="item.sku">
                    {{ item.quantity }}x {{ item.name }}
                  </li>
                </ul>

                <div class="flex items-center justify-between pt-2 border-t border-slate-800/80">
                  <span class="text-xs text-slate-400">Total VietQR:</span>
                  <span class="text-sm font-bold text-white">
                    {{ formatVnd(order.total_amount_vnd) }}
                  </span>
                </div>

                <div
                  v-if="order.status === 'PENDING_PAYMENT' && isSafeHttpUrl(order.vietqr_url)"
                  class="space-y-2"
                >
                  <UButton
                    block
                    size="xs"
                    color="amber"
                    variant="outline"
                    icon="i-heroicons-qr-code"
                    @click="toggleQrPreview(order.id)"
                  >
                    {{ expandedQrOrderId === order.id ? 'Hide SePay VietQR' : 'View SePay VietQR' }}
                  </UButton>
                  <div
                    v-if="expandedQrOrderId === order.id"
                    class="rounded-md bg-white p-2 flex flex-col items-center gap-1"
                  >
                    <img
                      :src="order.vietqr_url"
                      :alt="`VietQR for ${order.order_code}`"
                      class="w-40 h-40 object-contain"
                    >
                    <span class="text-[11px] font-mono text-slate-700">
                      Memo: {{ order.order_code }}
                    </span>
                  </div>
                </div>

                <div
                  v-if="order.assigned_employee_id"
                  class="flex items-center justify-between text-xs text-indigo-300 bg-indigo-950/40 px-2.5 py-1.5 rounded"
                >
                  <span>Telegram Assignee:</span>
                  <span class="font-mono">
                    {{ getEmployeeById(order.assigned_employee_id)?.full_name }}
                    ({{ getEmployeeById(order.assigned_employee_id)?.telegram_user_id }})
                  </span>
                </div>

                <div class="pt-1 space-y-1.5">
                  <div
                    v-if="col.key === 'PENDING_PAYMENT'"
                    class="flex items-center justify-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-950/30 px-2.5 py-1.5 text-xs font-medium text-amber-300"
                  >
                    <UIcon name="i-heroicons-clock" class="w-3.5 h-3.5 shrink-0" />
                    <span>Awaiting SePay Webhook</span>
                  </div>

                  <UButton
                    v-if="col.nextStatus && col.nextLabel"
                    block
                    size="xs"
                    :color="col.badgeColor"
                    variant="soft"
                    :disabled="!isValidOrderTransition(order.status, col.nextStatus) || Boolean(erpStore.updatingOrderIds[order.id])"
                    :loading="Boolean(erpStore.updatingOrderIds[order.id])"
                    @click="advanceOrder(order, col.nextStatus)"
                  >
                    {{ col.nextLabel }}
                  </UButton>

                  <UButton
                    v-if="isValidOrderTransition(order.status, 'CANCELLED')"
                    block
                    size="xs"
                    color="red"
                    variant="ghost"
                    :disabled="Boolean(erpStore.updatingOrderIds[order.id])"
                    :loading="Boolean(erpStore.updatingOrderIds[order.id])"
                    @click="advanceOrder(order, 'CANCELLED')"
                  >
                    Cancel Order
                  </UButton>

                  <UButton
                    v-if="!col.nextStatus && !isValidOrderTransition(order.status, 'CANCELLED')"
                    block
                    size="xs"
                    color="gray"
                    variant="soft"
                    disabled
                  >
                    Terminal State ({{ order.status }})
                  </UButton>
                </div>
              </div>

              <div
                v-if="erpStore.ordersByStatus[col.key].length === 0"
                class="flex items-center justify-center h-36 rounded-lg border border-dashed border-slate-800 text-xs text-slate-500"
              >
                No orders in {{ col.key }}
              </div>
            </div>
          </UCard>
        </div>
      </section>

      <!-- Semantic pgvector Inventory Search & Datatable -->
      <UCard class="bg-slate-900/90 border border-slate-800">
        <template #header>
          <div class="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div>
              <h2 class="text-lg font-semibold text-white">
                Semantic <code class="text-cyan-400">pgvector</code> Cosine (<code class="text-cyan-400">&lt;=&gt;</code>) Inventory Search
              </h2>
              <p class="text-xs text-slate-400">
                Test the exact vector retrieval pipeline Gemini 1.5 uses when answering customer Messenger/Instagram DMs.
              </p>
            </div>

            <form class="flex items-center gap-2 w-full md:w-auto" @submit.prevent="handleSemanticSearch">
              <UInput
                v-model="searchQuery"
                icon="i-heroicons-magnifying-glass"
                placeholder="e.g. high altitude citrus coffee or pour-over copper..."
                class="w-full md:w-80"
              />
              <UButton
                type="submit"
                color="cyan"
                variant="solid"
                :loading="erpStore.isLoadingInventory"
              >
                Vector Search
              </UButton>
              <UButton
                v-if="erpStore.lastSearchQuery"
                color="gray"
                variant="ghost"
                @click="handleResetSearch"
              >
                Reset
              </UButton>
            </form>
          </div>
        </template>

        <UTable
          :rows="erpStore.inventory"
          :columns="inventoryColumns"
          :loading="erpStore.isLoadingInventory"
        >
          <template #sku-data="{ row }">
            <span class="font-mono text-xs font-semibold text-cyan-300">
              {{ row.sku }}
            </span>
          </template>

          <template #name-data="{ row }">
            <div>
              <div class="font-medium text-white">{{ row.name }}</div>
              <div class="text-xs text-slate-400">{{ row.description }}</div>
            </div>
          </template>

          <template #price_vnd-data="{ row }">
            <span class="font-mono text-sm font-semibold text-emerald-400">
              {{ formatVnd(row.price_vnd) }}
            </span>
          </template>

          <template #stock_quantity-data="{ row }">
            <UBadge
              :color="row.stock_quantity > 10 ? 'emerald' : 'amber'"
              variant="subtle"
              size="xs"
            >
              {{ row.stock_quantity }} in stock
            </UBadge>
          </template>

          <template #has_embedding-data="{ row }">
            <div class="flex items-center gap-2">
              <UBadge
                :color="row.has_embedding ? 'cyan' : 'gray'"
                variant="soft"
                size="xs"
              >
                {{ row.has_embedding ? '768-d Indexed' : 'Pending' }}
              </UBadge>
              <span
                v-if="row.similarity_score != null"
                class="font-mono text-xs text-cyan-300"
              >
                score: {{ row.similarity_score.toFixed(2) }}
              </span>
            </div>
          </template>
        </UTable>
      </UCard>
    </div>

    <!-- Tab 1: Live Omnichannel Webhook & Staff Dispatch Feed -->
    <div v-else class="space-y-6">
      <UCard class="bg-slate-900/90 border border-slate-800">
        <template #header>
          <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 class="text-lg font-semibold text-white">
                Live Omnichannel Webhook &amp; Staff Dispatch Feed
              </h2>
              <p class="text-xs text-slate-400">
                Real-time telemetry from <code>/api/erp/webhooks/logs</code> and <code>/api/erp/metrics</code>.
              </p>
            </div>
            <div class="flex items-center gap-2">
              <UBadge color="emerald" variant="subtle">
                {{ erpStore.webhookFeed.length }} Events Logged
              </UBadge>
              <UButton
                size="xs"
                color="gray"
                variant="outline"
                icon="i-heroicons-arrow-path"
                :loading="erpStore.isLoadingWebhooks"
                @click="handleRefreshTelemetry"
              >
                Refresh
              </UButton>
            </div>
          </div>
        </template>

        <div class="space-y-3">
          <UAlert
            v-if="erpStore.webhookError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="erpStore.webhookError"
          />

          <div
            v-if="erpStore.isLoadingWebhooks"
            class="flex items-center justify-center h-36 rounded-lg border border-slate-800 text-xs text-slate-400"
          >
            Loading live webhook telemetry...
          </div>

          <div
            v-else-if="erpStore.webhookFeed.length === 0"
            class="flex flex-col items-center justify-center h-40 rounded-lg border border-dashed border-slate-800 text-center px-4 gap-1"
          >
            <span class="text-sm font-medium text-slate-300">
              No webhook events recorded yet
            </span>
            <span class="text-xs text-slate-500">
              Inbound SePay VietQR IPN, Meta Graph DM, and Telegram dispatch events will appear here once received.
            </span>
          </div>

          <template v-else>
            <div
              v-for="event in erpStore.webhookFeed"
              :key="event.id"
              class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/75 p-4"
            >
              <div class="space-y-1">
                <div class="flex items-center gap-2">
                  <UBadge :color="getWebhookBadgeColor(event.source)" variant="soft" size="xs">
                    {{ event.source }}
                  </UBadge>
                  <span class="font-mono text-xs text-slate-300">
                    {{ event.event_type }}
                  </span>
                  <UBadge
                    :color="event.status === 'FAILED' ? 'red' : event.status === 'PENDING' ? 'amber' : 'emerald'"
                    variant="subtle"
                    size="xs"
                  >
                    {{ event.status }}
                  </UBadge>
                </div>
                <p class="text-sm text-slate-200">
                  {{ event.summary }}
                </p>
              </div>

              <div class="font-mono text-xs text-slate-400 shrink-0">
                {{ formatEventTimeUtc(event.timestamp) }}
              </div>
            </div>
          </template>
        </div>
      </UCard>
    </div>

    <!-- Server-Authoritative Order Creation Modal -->
    <UModal v-model="isCreateOrderOpen">
      <UCard class="bg-slate-900 border border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-base font-semibold text-white">
                Create Authoritative ERP Order
              </h3>
              <p class="text-xs text-slate-400">
                Submits only customer ID and SKU quantities; unit prices &amp; SePay VietQR totals are computed server-side.
              </p>
            </div>
            <UButton
              color="gray"
              variant="ghost"
              icon="i-heroicons-x-mark"
              @click="isCreateOrderOpen = false"
            />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitCreateOrder">
          <UAlert
            v-if="createOrderError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="createOrderError"
          />

          <UFormGroup label="Customer" name="customer_id" required>
            <USelect
              v-model="orderDraft.customer_id"
              :options="customerOptions"
              option-attribute="label"
              value-attribute="value"
            />
          </UFormGroup>

          <UFormGroup label="Assigned Fulfillment Staff (Optional)" name="assigned_employee_id">
            <USelect
              v-model="orderDraft.assigned_employee_id"
              :options="employeeOptions"
              option-attribute="label"
              value-attribute="value"
            />
          </UFormGroup>

          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-slate-300">Line Items (SKU &amp; Quantity)</span>
              <UButton
                size="xs"
                color="cyan"
                variant="soft"
                icon="i-heroicons-plus"
                @click="addOrderLineItem"
              >
                Add SKU
              </UButton>
            </div>

            <div
              v-for="(line, index) in orderDraft.items"
              :key="index"
              class="flex items-center gap-2"
            >
              <USelect
                v-model="line.sku"
                :options="inventorySkuOptions"
                option-attribute="label"
                value-attribute="value"
                class="flex-1"
              />
              <UInput
                v-model.number="line.quantity"
                type="number"
                min="1"
                class="w-24"
              />
              <UButton
                v-if="orderDraft.items.length > 1"
                size="xs"
                color="red"
                variant="ghost"
                icon="i-heroicons-trash"
                @click="removeOrderLineItem(index)"
              />
            </div>
          </div>

          <div class="flex items-center justify-between rounded-md bg-slate-950/80 border border-slate-800 px-3 py-2 text-xs">
            <span class="text-slate-400">Catalog Preview Estimate:</span>
            <span class="font-mono font-semibold text-emerald-400">
              {{ formatVnd(estimatedDraftTotalVnd) }}
            </span>
          </div>

          <div class="flex items-center justify-end gap-2 pt-2">
            <UButton
              color="gray"
              variant="ghost"
              @click="isCreateOrderOpen = false"
            >
              Cancel
            </UButton>
            <UButton
              type="submit"
              color="emerald"
              variant="solid"
              :loading="erpStore.isCreatingOrder"
            >
              Submit Order
            </UButton>
          </div>
        </form>
      </UCard>
    </UModal>
  </UContainer>
</template>
