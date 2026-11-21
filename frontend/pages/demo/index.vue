<script setup lang="ts">
import { computed } from 'vue'
import { useDemoStore } from '~/stores/demo'
import type { OrderStatus, WebhookSource } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const demoStore = useDemoStore()
const toast = useToast()

const totalOrdersCount = computed(() => demoStore.orders.length)
const pendingPaymentCount = computed(() => demoStore.ordersByStatus.PENDING_PAYMENT.length)
const paidAndFulfillingCount = computed(
  () => demoStore.ordersByStatus.PAID.length + demoStore.ordersByStatus.FULFILLING.length
)
const activeCatalogCount = computed(
  () => demoStore.inventory.filter((item) => item.is_active !== false).length
)

const pipelineStages: { key: OrderStatus; label: string; barClass: string }[] = [
  { key: 'PENDING_PAYMENT', label: 'Awaiting Payment', barClass: 'bg-amber-500' },
  { key: 'PAID', label: 'Paid', barClass: 'bg-emerald-500' },
  { key: 'FULFILLING', label: 'Fulfilling', barClass: 'bg-blue-500' },
  { key: 'COMPLETED', label: 'Completed', barClass: 'bg-teal-500' },
  { key: 'CANCELLED', label: 'Cancelled', barClass: 'bg-rose-500' }
]

function stageCount(status: OrderStatus): number {
  return demoStore.ordersByStatus[status].length
}

function stagePct(status: OrderStatus): number {
  const total = Math.max(1, demoStore.orders.length)
  return Math.round((stageCount(status) / total) * 100)
}

const attentionItems = computed(() => {
  const pendingOrders = demoStore.ordersByStatus.PENDING_PAYMENT.slice(0, 3).map((order) => ({
    id: `ord-${order.id}`,
    title: `${order.order_code} awaiting payment`,
    subtitle: `${order.customer_name || 'Customer'} · ${order.total_amount_vnd.toLocaleString('vi-VN')} ₫`,
    to: '/demo/orders',
    badge: 'Payment',
    color: 'amber' as const
  }))

  const lowStockItems = demoStore.inventory
    .filter((item) => item.is_active !== false && item.stock_quantity <= 10)
    .slice(0, 3)
    .map((item) => ({
      id: `inv-${item.id}`,
      title: `Low stock: ${item.name}`,
      subtitle: `${item.sku} · ${item.stock_quantity} remaining`,
      to: '/demo/inventory',
      badge: 'Restock',
      color: 'red' as const
    }))

  return [...pendingOrders, ...lowStockItems].slice(0, 5)
})

const recentActivity = computed(() => demoStore.webhookFeed.slice(0, 5))

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

async function triggerSampleTelegram(): Promise<void> {
  const sampleOrder = demoStore.orders[0]
  if (!sampleOrder) return
  try {
    const res = await demoStore.triggerTelegramNotification(sampleOrder)
    toast.add({
      title: res.dispatched ? 'Demo Telegram Alert Sent' : 'Telegram Dispatch Failed',
      description: res.simulated
        ? `Simulated preview for ${sampleOrder.order_code}`
        : `Dispatched live alert for ${sampleOrder.order_code}`,
      color: res.dispatched ? 'emerald' : 'amber'
    })
  } catch {
    toast.add({
      title: 'Telegram Dispatch Error',
      description: demoStore.telegramError || 'Unable to trigger demo Telegram alert.',
      color: 'red'
    })
  }
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
          Sandbox Overview
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Interactive preview environment. All changes reset automatically when you reload.
        </p>
      </div>
      <div class="flex items-center gap-2">
        <UButton
          color="primary"
          variant="soft"
          size="sm"
          icon="i-heroicons-paper-airplane"
          :loading="demoStore.isNotifyingTelegram"
          @click="triggerSampleTelegram"
        >
          Test Telegram Alert
        </UButton>
        <UButton
          to="/demo/orders"
          color="emerald"
          variant="solid"
          size="sm"
          icon="i-heroicons-clipboard-document-list"
        >
          Open Orders
        </UButton>
      </div>
    </div>

    <UAlert
      v-if="demoStore.telegramError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="demoStore.telegramError"
    />

    <UAlert
      v-else-if="demoStore.lastTelegramResult"
      color="emerald"
      variant="soft"
      icon="i-heroicons-check-circle"
      :title="`Telegram Alert (${demoStore.lastTelegramResult.order_code}): ${demoStore.lastTelegramResult.simulated ? 'Simulated' : 'Delivered Live'}`"
      :description="demoStore.lastTelegramResult.preview_text || `Target: ${demoStore.lastTelegramResult.telegram_user_id}`"
    />

    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Total Orders</p>
        <p class="mt-2 text-3xl font-bold text-gray-900 dark:text-slate-100 tabular-nums">
          {{ totalOrdersCount }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">Sandbox orders</p>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Awaiting Payment</p>
        <p class="mt-2 text-3xl font-bold text-amber-600 dark:text-amber-400 tabular-nums">
          {{ pendingPaymentCount }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">Open VietQR invoices</p>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Paid &amp; Fulfilling</p>
        <p class="mt-2 text-3xl font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">
          {{ paidAndFulfillingCount }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">Ready for dispatch</p>
      </UCard>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Active Catalog SKUs</p>
        <p class="mt-2 text-3xl font-bold text-gray-900 dark:text-slate-100 tabular-nums">
          {{ activeCatalogCount }}
        </p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">Available in sandbox</p>
      </UCard>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">Order Pipeline</h2>
            <NuxtLink to="/demo/orders" class="text-xs font-medium text-emerald-600 dark:text-emerald-400 hover:underline">
              View board →
            </NuxtLink>
          </div>
        </template>

        <div class="space-y-3.5">
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

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">Needs Attention</h2>
            <UBadge color="amber" variant="soft" size="xs">{{ attentionItems.length }} items</UBadge>
          </div>
        </template>

        <div class="divide-y divide-gray-100 dark:divide-slate-800">
          <NuxtLink
            v-for="item in attentionItems"
            :key="item.id"
            :to="item.to"
            class="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0 hover:opacity-80 transition-opacity"
          >
            <div class="min-w-0">
              <p class="text-sm font-medium text-gray-900 dark:text-slate-100 truncate">{{ item.title }}</p>
              <p class="text-xs text-gray-500 dark:text-slate-400 truncate">{{ item.subtitle }}</p>
            </div>
            <UBadge :color="item.color" variant="soft" size="xs">{{ item.badge }}</UBadge>
          </NuxtLink>
        </div>
      </UCard>
    </div>

    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <template #header>
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-semibold text-gray-900 dark:text-slate-100">Recent Activity</h2>
          <NuxtLink to="/demo/activity" class="text-xs font-medium text-emerald-600 dark:text-emerald-400 hover:underline">
            All activity →
          </NuxtLink>
        </div>
      </template>

      <div class="divide-y divide-gray-100 dark:divide-slate-800">
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
