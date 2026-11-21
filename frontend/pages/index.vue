<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { OrderStatus, WebhookSource } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const isInitializing = ref<boolean>(true)
const loadError = ref<string | null>(null)

async function loadOverview(): Promise<void> {
  if (erpStore.isStaff) {
    await navigateTo('/my-work')
    return
  }
  isInitializing.value = true
  loadError.value = null
  try {
    await Promise.all([
      erpStore.fetchManagerDashboard(),
      erpStore.fetchMetrics(),
      erpStore.fetchOrders(),
      erpStore.fetchInventory(),
      erpStore.fetchWebhookLogs()
    ])
  } catch {
    loadError.value = 'Unable to load workspace overview. Please retry.'
  } finally {
    isInitializing.value = false
  }
}

onMounted(async () => {
  await loadOverview()
})

watch(
  () => erpStore.selectedCompanyId,
  async () => {
    if (erpStore.isAdmin) {
      await loadOverview()
    }
  }
)

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

const totalRevenueVnd = computed<number>(() => {
  if (erpStore.managerDashboard) {
    return erpStore.managerDashboard.total_revenue_vnd
  }
  return erpStore.orders
    .filter((o) => o.status === 'PAID' || o.status === 'FULFILLING' || o.status === 'COMPLETED')
    .reduce((sum, o) => sum + o.total_amount_vnd, 0)
})

const todayIncomeVnd = computed<number>(() => {
  const series = erpStore.managerDashboard?.daily_income ?? []
  if (series.length === 0) return 0
  const todayIso = new Date().toISOString().slice(0, 10)
  const todayEntry = series.find((d) => d.date === todayIso)
  if (todayEntry) return todayEntry.revenue_vnd
  return series[series.length - 1]?.revenue_vnd ?? 0
})

const totalOrdersCount = computed<number>(
  () => erpStore.managerDashboard?.total_orders_count ?? erpStore.metrics?.total_orders ?? erpStore.orders.length
)

const activeCustomersCount = computed<number>(
  () => erpStore.managerDashboard?.active_customers_count ?? erpStore.customers.length
)

const openChatsCount = computed<number>(
  () => erpStore.managerDashboard?.open_chats_count ?? 0
)

const unreadChatsCount = computed<number>(() =>
  erpStore.chats.reduce((sum, c) => sum + (c.unread_count || 0), 0)
)

const dailyIncomeSeries = computed(() => erpStore.managerDashboard?.daily_income ?? [])

const maxDailyIncome = computed<number>(() => {
  const values = dailyIncomeSeries.value.map((d) => d.revenue_vnd)
  return Math.max(1, ...values)
})

function barHeightPct(revenue: number): number {
  if (revenue <= 0) return 6
  return Math.max(10, Math.round((revenue / maxDailyIncome.value) * 100))
}

const staffWorkSummary = computed(() => (erpStore.managerDashboard?.staff_workload ?? []).slice(0, 5))

const pipelineStages: { key: OrderStatus; label: string; barClass: string }[] = [
  { key: 'PENDING_PAYMENT', label: 'Awaiting Payment', barClass: 'bg-amber-500' },
  { key: 'PAID', label: 'Paid', barClass: 'bg-emerald-500' },
  { key: 'FULFILLING', label: 'Fulfilling', barClass: 'bg-blue-500' },
  { key: 'COMPLETED', label: 'Completed', barClass: 'bg-teal-500' },
  { key: 'CANCELLED', label: 'Cancelled', barClass: 'bg-rose-500' }
]

function stageCount(status: OrderStatus): number {
  return erpStore.ordersByStatus[status].length
}

function stagePct(status: OrderStatus): number {
  const total = Math.max(1, erpStore.orders.length)
  return Math.round((stageCount(status) / total) * 100)
}

const attentionItems = computed(() => {
  const pendingOrders = erpStore.ordersByStatus.PENDING_PAYMENT.slice(0, 3).map((order) => ({
    id: `ord-${order.id}`,
    title: `${order.order_code} awaiting payment`,
    subtitle: `${order.customer_name || 'Customer'} · ${formatVnd(order.total_amount_vnd)}`,
    to: '/orders',
    badge: 'Payment',
    color: 'amber' as const
  }))

  const lowStockItems = erpStore.inventory
    .filter((item) => item.is_active !== false && item.stock_quantity <= 10)
    .slice(0, 3)
    .map((item) => ({
      id: `inv-${item.id}`,
      title: `Low stock: ${item.name}`,
      subtitle: `${item.sku} · ${item.stock_quantity} remaining`,
      to: '/inventory',
      badge: 'Restock',
      color: 'red' as const
    }))

  return [...pendingOrders, ...lowStockItems].slice(0, 5)
})

const recentActivity = computed(() => erpStore.webhookFeed.slice(0, 5))

function sourceLabel(source: WebhookSource): string {
  switch (source) {
    case 'SEPAY_IPN':
      return 'Payment'
    case 'META_GRAPH':
      return 'Channel'
    case 'TELEGRAM_BOT':
      return 'Dispatch'
  }
}

function sourceColor(source: WebhookSource): 'emerald' | 'blue' | 'violet' {
  switch (source) {
    case 'SEPAY_IPN':
      return 'emerald'
    case 'META_GRAPH':
      return 'blue'
    case 'TELEGRAM_BOT':
      return 'violet'
  }
}
</script>

<template>
  <div v-if="erpStore.isStaff" class="py-12 text-center space-y-3">
    <p class="text-sm text-gray-600 dark:text-slate-300">Redirecting to your work queue...</p>
    <UButton to="/my-work" color="emerald" size="sm">Open My Work</UButton>
  </div>

  <div v-else class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
          {{ erpStore.isAdmin ? 'Multi-Company Analytics Hub' : 'Company Analytics Overview' }}
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Revenue performance, daily income trends, staff fulfillment, and omnichannel inbox status.
        </p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <UButton
          color="gray"
          variant="outline"
          size="sm"
          icon="i-heroicons-arrow-path"
          :loading="isInitializing"
          @click="loadOverview"
        >
          Refresh
        </UButton>
        <UButton
          to="/chats"
          color="blue"
          variant="soft"
          size="sm"
          icon="i-heroicons-chat-bubble-left-right"
        >
          Chats
          <UBadge v-if="unreadChatsCount > 0" color="emerald" variant="solid" size="xs" class="ml-1">
            {{ unreadChatsCount }}
          </UBadge>
        </UButton>
        <UButton
          to="/orders"
          color="emerald"
          variant="solid"
          size="sm"
          icon="i-heroicons-clipboard-document-list"
        >
          Manage Orders
        </UButton>
      </div>
    </div>

    <UAlert
      v-if="loadError || erpStore.dashboardError || erpStore.ordersError || erpStore.inventoryError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="loadError || erpStore.dashboardError || erpStore.ordersError || erpStore.inventoryError || ''"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="loadOverview">
          Retry
        </UButton>
      </template>
    </UAlert>

    <!-- 4 Executive Analytics KPIs -->
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Total Revenue</p>
        <div v-if="isInitializing" class="mt-2 h-8 w-28 rounded bg-gray-200 dark:bg-slate-800 animate-pulse" />
        <p v-else class="mt-2 text-2xl font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">
          {{ formatVnd(totalRevenueVnd) }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">Verified &amp; active order volume</p>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Today's Income</p>
        <div v-if="isInitializing" class="mt-2 h-8 w-24 rounded bg-gray-200 dark:bg-slate-800 animate-pulse" />
        <p v-else class="mt-2 text-2xl font-bold text-gray-900 dark:text-slate-100 tabular-nums">
          {{ formatVnd(todayIncomeVnd) }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">{{ totalOrdersCount }} total orders tracked</p>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Active Customers</p>
        <div v-if="isInitializing" class="mt-2 h-8 w-16 rounded bg-gray-200 dark:bg-slate-800 animate-pulse" />
        <p v-else class="mt-2 text-2xl font-bold text-gray-900 dark:text-slate-100 tabular-nums">
          {{ activeCustomersCount }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
          <NuxtLink to="/customers" class="text-emerald-600 dark:text-emerald-400 hover:underline">View directory →</NuxtLink>
        </p>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Omnichannel Inbox</p>
        <div v-if="isInitializing" class="mt-2 h-8 w-16 rounded bg-gray-200 dark:bg-slate-800 animate-pulse" />
        <p v-else class="mt-2 text-2xl font-bold text-blue-600 dark:text-blue-400 tabular-nums">
          {{ openChatsCount }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
          {{ unreadChatsCount }} unread ·
          <NuxtLink to="/chats" class="text-blue-600 dark:text-blue-400 hover:underline">Open inbox →</NuxtLink>
        </p>
      </UCard>
    </div>

    <!-- Daily Income Chart & Staff Work Summary -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">
              Daily Income Breakdown
            </h2>
            <span class="text-xs text-gray-500 dark:text-slate-400">Recent days</span>
          </div>
        </template>

        <div v-if="isInitializing" class="h-44 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
        <div v-else-if="dailyIncomeSeries.length === 0" class="py-10 text-center text-sm text-gray-500 dark:text-slate-400">
          Daily income telemetry will populate as paid orders are recorded.
        </div>
        <div v-else class="space-y-4">
          <div class="h-40 flex items-end gap-2 pt-4 px-1">
            <div
              v-for="point in dailyIncomeSeries"
              :key="point.date"
              class="flex-1 flex flex-col items-center gap-1.5 min-w-0"
            >
              <span class="text-[10px] font-medium text-gray-600 dark:text-slate-300 tabular-nums truncate max-w-full">
                {{ point.revenue_vnd > 0 ? `${Math.round(point.revenue_vnd / 1000)}k` : '0' }}
              </span>
              <div class="w-full flex-1 flex items-end justify-center">
                <div
                  class="w-full max-w-[34px] rounded-t-md bg-emerald-500 dark:bg-emerald-400 transition-all duration-300"
                  :style="{ height: `${barHeightPct(point.revenue_vnd)}%` }"
                  :title="`${point.date}: ${formatVnd(point.revenue_vnd)} (${point.orders_count} orders)`"
                />
              </div>
              <span class="text-[10px] text-gray-500 dark:text-slate-400 tabular-nums truncate">
                {{ point.date.slice(5) }}
              </span>
            </div>
          </div>
        </div>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">
              Active Staff Work
            </h2>
            <NuxtLink to="/staff" class="text-xs font-medium text-emerald-600 dark:text-emerald-400 hover:underline">
              Manage staff →
            </NuxtLink>
          </div>
        </template>

        <div v-if="isInitializing" class="space-y-3">
          <div v-for="n in 3" :key="n" class="h-10 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
        </div>
        <div v-else-if="staffWorkSummary.length === 0" class="py-10 text-center text-sm text-gray-500 dark:text-slate-400">
          No staff workload metrics available yet.
        </div>
        <div v-else class="divide-y divide-gray-100 dark:divide-slate-800">
          <div
            v-for="member in staffWorkSummary"
            :key="member.employee_id"
            class="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0"
          >
            <div class="min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-slate-100 truncate">
                {{ member.full_name }}
              </p>
              <p class="text-xs text-gray-500 dark:text-slate-400 font-mono truncate">
                {{ member.username || member.telegram_user_id }}
              </p>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <UBadge color="blue" variant="soft" size="xs" class="tabular-nums">
                {{ member.active_orders_count }} active
              </UBadge>
              <UBadge color="emerald" variant="soft" size="xs" class="tabular-nums">
                {{ member.completed_orders_count }} done
              </UBadge>
            </div>
          </div>
        </div>
      </UCard>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Order Pipeline Summary -->
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">
              Order Pipeline
            </h2>
            <NuxtLink to="/orders" class="text-xs font-medium text-emerald-600 dark:text-emerald-400 hover:underline">
              View board →
            </NuxtLink>
          </div>
        </template>

        <div v-if="isInitializing" class="space-y-3">
          <div v-for="n in 4" :key="n" class="h-6 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
        </div>
        <div v-else-if="erpStore.orders.length === 0" class="py-8 text-center text-sm text-gray-500 dark:text-slate-400">
          No orders recorded yet for this scope.
        </div>
        <div v-else class="space-y-3.5">
          <div v-for="stage in pipelineStages" :key="stage.key" class="space-y-1.5">
            <div class="flex items-center justify-between text-xs">
              <span class="font-medium text-gray-700 dark:text-slate-300">{{ stage.label }}</span>
              <span class="font-semibold text-gray-900 dark:text-slate-100 tabular-nums">
                {{ stageCount(stage.key) }}
              </span>
            </div>
            <div class="h-2 w-full rounded-full bg-gray-100 dark:bg-slate-800 overflow-hidden">
              <div
                class="h-full rounded-full transition-all duration-300"
                :class="stage.barClass"
                :style="{ width: `${stagePct(stage.key)}%` }"
              />
            </div>
          </div>
        </div>
      </UCard>

      <!-- Needs Attention -->
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">
              Needs Attention
            </h2>
            <UBadge v-if="attentionItems.length > 0" color="amber" variant="soft" size="xs">
              {{ attentionItems.length }} items
            </UBadge>
          </div>
        </template>

        <div v-if="isInitializing" class="space-y-3">
          <div v-for="n in 3" :key="n" class="h-12 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
        </div>
        <div v-else-if="attentionItems.length === 0" class="py-8 text-center text-sm text-gray-500 dark:text-slate-400">
          All orders and stock levels are healthy.
        </div>
        <div v-else class="divide-y divide-gray-100 dark:divide-slate-800">
          <NuxtLink
            v-for="item in attentionItems"
            :key="item.id"
            :to="item.to"
            class="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0 hover:opacity-80 transition-opacity"
          >
            <div class="min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-slate-100 truncate">
                {{ item.title }}
              </p>
              <p class="text-xs text-gray-500 dark:text-slate-400 truncate">
                {{ item.subtitle }}
              </p>
            </div>
            <UBadge :color="item.color" variant="soft" size="xs">
              {{ item.badge }}
            </UBadge>
          </NuxtLink>
        </div>
      </UCard>
    </div>

    <!-- Recent Activity -->
    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <template #header>
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">
            Recent Activity
          </h2>
          <NuxtLink to="/activity" class="text-xs font-medium text-emerald-600 dark:text-emerald-400 hover:underline">
            All activity →
          </NuxtLink>
        </div>
      </template>

      <div v-if="isInitializing" class="space-y-2.5">
        <div v-for="n in 3" :key="n" class="h-10 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
      </div>
      <div v-else-if="recentActivity.length === 0" class="py-8 text-center text-sm text-gray-500 dark:text-slate-400">
        No webhook or dispatch activity recorded yet.
      </div>
      <div v-else class="divide-y divide-gray-100 dark:divide-slate-800">
        <div
          v-for="evt in recentActivity"
          :key="evt.id"
          class="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0 text-sm"
        >
          <div class="flex items-center gap-3 min-w-0">
            <UBadge :color="sourceColor(evt.source)" variant="soft" size="xs">
              {{ sourceLabel(evt.source) }}
            </UBadge>
            <span class="text-gray-700 dark:text-slate-200 truncate">{{ evt.summary }}</span>
          </div>
          <span class="text-xs text-gray-400 dark:text-slate-500 shrink-0 tabular-nums">
            {{ new Date(evt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}
          </span>
        </div>
      </div>
    </UCard>
  </div>
</template>
