<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { Order, OrderStatus, WebhookSource } from '~/types/erp'

const erpStore = useErpStore()
const toast = useToast()

const searchQuery = ref<string>('')
const selectedTab = ref<number>(0)

const tabs = [
  { label: 'Fulfillment Kanban & Inventory', icon: 'i-heroicons-squares-2x2' },
  { label: 'Omnichannel Webhook & Staff Dispatch', icon: 'i-heroicons-bolt' }
]

const inventoryColumns = [
  { key: 'sku', label: 'SKU' },
  { key: 'name', label: 'Product & Category' },
  { key: 'price_vnd', label: 'Unit Price (VND)' },
  { key: 'stock_quantity', label: 'Stock' },
  { key: 'has_embedding', label: 'pgvector (<->)' }
]

const kanbanColumns: {
  key: OrderStatus
  title: string
  badgeColor: 'amber' | 'emerald' | 'blue' | 'primary'
  nextStatus?: OrderStatus
  nextLabel?: string
}[] = [
  {
    key: 'PENDING_PAYMENT',
    title: 'Awaiting SePay VietQR',
    badgeColor: 'amber',
    nextStatus: 'PAID',
    nextLabel: 'Simulate SePay IPN (Mark PAID)'
  },
  {
    key: 'PAID',
    title: 'Paid & Telegram Dispatched',
    badgeColor: 'emerald',
    nextStatus: 'FULFILLING',
    nextLabel: 'Start Packing (FULFILLING)'
  },
  {
    key: 'FULFILLING',
    title: 'Fulfilling in Warehouse',
    badgeColor: 'blue',
    nextStatus: 'COMPLETED',
    nextLabel: 'Complete Handover'
  },
  {
    key: 'COMPLETED',
    title: 'Completed Orders',
    badgeColor: 'primary'
  }
]

const embeddedSkuCount = computed<number>(
  () => erpStore.inventory.filter((item) => item.has_embedding).length
)

const pendingVietQrCount = computed<number>(
  () => erpStore.ordersByStatus.PENDING_PAYMENT.length
)

const paidAndDispatchedCount = computed<number>(
  () =>
    erpStore.ordersByStatus.PAID.length +
    erpStore.ordersByStatus.FULFILLING.length
)

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

function getEmployeeById(employeeId?: string) {
  if (!employeeId) return undefined
  return erpStore.employees.find((emp) => emp.id === employeeId)
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

async function advanceOrder(order: Order, nextStatus: OrderStatus): Promise<void> {
  await erpStore.updateOrderStatus(order.id, nextStatus)
  toast.add({
    title: `Order ${order.order_code} -> ${nextStatus}`,
    description:
      nextStatus === 'PAID'
        ? `SePay VietQR IPN verified (${order.sepay_reference}). Dispatched to Telegram staff.`
        : `Order status updated to ${nextStatus}.`,
    color: 'emerald'
  })
}

onMounted(async () => {
  await Promise.all([
    erpStore.fetchInventory(),
    erpStore.fetchOrders(),
    erpStore.fetchEmployees()
  ])
})
</script>

<template>
  <UContainer class="py-8 space-y-8">
    <!-- Top Header & System Architecture Status Badges -->
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

      <div class="flex flex-wrap items-center gap-2">
        <UBadge color="emerald" variant="subtle" size="md">
          FastAPI Async Core
        </UBadge>
        <UBadge color="cyan" variant="subtle" size="md">
          Supabase pgvector (&lt;-&gt;)
        </UBadge>
        <UBadge color="violet" variant="subtle" size="md">
          Gemini 1.5 Flash/Pro RAG
        </UBadge>
        <UBadge color="blue" variant="subtle" size="md">
          Meta Graph v18.0
        </UBadge>
        <UBadge color="amber" variant="subtle" size="md">
          SePay VietQR Sandbox
        </UBadge>
        <UBadge color="indigo" variant="subtle" size="md">
          Telegram Staff Bot
        </UBadge>
      </div>
    </div>

    <!-- 4 KPI Metric Cards -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <UCard class="bg-slate-900/80 border border-slate-800">
        <div class="text-xs font-medium uppercase tracking-wider text-slate-400">
          Active Inventory SKUs
        </div>
        <div class="mt-2 flex items-baseline justify-between">
          <span class="text-3xl font-bold text-white">
            {{ erpStore.inventory.length }}
          </span>
          <UBadge color="cyan" variant="soft" size="xs">
            {{ embeddedSkuCount }} / {{ erpStore.inventory.length }} Vectorized
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
            Cosine &lt;-&gt; Top-K
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
      <!-- 4-Column Orders Fulfillment Kanban Board -->
      <section class="space-y-4">
        <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div>
            <h2 class="text-lg font-semibold text-white">
              4-Column Orders Fulfillment Kanban Board
            </h2>
            <p class="text-xs text-slate-400">
              Advance orders from SePay VietQR verification to Telegram staff warehouse fulfillment.
            </p>
          </div>
          <div class="flex items-center gap-2 text-xs text-slate-400">
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
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          <UCard
            v-for="col in kanbanColumns"
            :key="col.key"
            class="bg-slate-900/90 border border-slate-800 flex flex-col justify-between"
          >
            <template #header>
              <div class="flex items-center justify-between">
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
                  v-if="order.assigned_employee_id"
                  class="flex items-center justify-between text-xs text-indigo-300 bg-indigo-950/40 px-2.5 py-1.5 rounded"
                >
                  <span>Telegram Assignee:</span>
                  <span class="font-mono">
                    {{ getEmployeeById(order.assigned_employee_id)?.full_name }}
                    ({{ getEmployeeById(order.assigned_employee_id)?.telegram_user_id }})
                  </span>
                </div>

                <div v-if="col.nextStatus && col.nextLabel" class="pt-1">
                  <UButton
                    block
                    size="xs"
                    :color="col.badgeColor"
                    variant="soft"
                    :loading="erpStore.isUpdatingOrder"
                    @click="advanceOrder(order, col.nextStatus)"
                  >
                    {{ col.nextLabel }}
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
                Semantic <code class="text-cyan-400">pgvector</code> Cosine (<code class="text-cyan-400">&lt;-&gt;</code>) Inventory Search
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
                v-if="row.similarity_score !== undefined"
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
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-lg font-semibold text-white">
                Live Omnichannel Webhook &amp; Staff Dispatch Feed
              </h2>
              <p class="text-xs text-slate-400">
                End-to-end event trace: Meta Graph v18.0 DM &rarr; Gemini 1.5 RAG &rarr; SePay VietQR IPN &rarr; Telegram Bot Staff Alert.
              </p>
            </div>
            <UBadge color="emerald" variant="subtle">
              {{ erpStore.webhookFeed.length }} Events Logged
            </UBadge>
          </div>
        </template>

        <div class="space-y-3">
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
                <UBadge color="emerald" variant="subtle" size="xs">
                  {{ event.status }}
                </UBadge>
              </div>
              <p class="text-sm text-slate-200">
                {{ event.summary }}
              </p>
            </div>

            <div class="font-mono text-xs text-slate-400 shrink-0">
              {{ new Date(event.timestamp).toLocaleTimeString() }}
            </div>
          </div>
        </div>
      </UCard>
    </div>
  </UContainer>
</template>
