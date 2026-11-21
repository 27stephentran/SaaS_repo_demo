<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { Order, OrderStatus } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const toast = useToast()

const statusFilter = ref<'ALL' | 'OPEN' | 'FULFILLING' | 'COMPLETED'>('ALL')
const selectedOrderId = ref<string | null>(null)
const isDrawerOpen = ref<boolean>(false)

onMounted(async () => {
  await erpStore.fetchMyWork()
})

const selectedOrder = computed<Order | null>(
  () => erpStore.myWorkOrders.find((o) => o.id === selectedOrderId.value) ?? null
)

const filteredWork = computed<Order[]>(() => {
  return erpStore.myWorkOrders.filter((order) => {
    if (statusFilter.value === 'OPEN') {
      return order.status === 'PAID' || order.status === 'PENDING_PAYMENT'
    }
    if (statusFilter.value === 'FULFILLING') {
      return order.status === 'FULFILLING'
    }
    if (statusFilter.value === 'COMPLETED') {
      return order.status === 'COMPLETED'
    }
    return true
  })
})

const paidCount = computed(() => erpStore.myWorkOrders.filter((o) => o.status === 'PAID').length)
const inProgressCount = computed(() => erpStore.myWorkOrders.filter((o) => o.status === 'FULFILLING').length)
const completedCount = computed(() => erpStore.myWorkOrders.filter((o) => o.status === 'COMPLETED').length)

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

function statusColor(status: OrderStatus): 'amber' | 'emerald' | 'blue' | 'primary' | 'red' {
  switch (status) {
    case 'PENDING_PAYMENT':
      return 'amber'
    case 'PAID':
      return 'emerald'
    case 'FULFILLING':
      return 'blue'
    case 'COMPLETED':
      return 'primary'
    case 'CANCELLED':
      return 'red'
  }
}

function openOrderDetail(order: Order): void {
  selectedOrderId.value = order.id
  isDrawerOpen.value = true
}

async function handleStatusAction(order: Order, nextStatus: OrderStatus): Promise<void> {
  try {
    await erpStore.updateMyWorkStatus(order.id, nextStatus)
    toast.add({
      title: `${order.order_code} Updated`,
      description: `Moved to ${nextStatus === 'FULFILLING' ? 'In Progress' : nextStatus}.`,
      color: 'emerald'
    })
  } catch {
    toast.add({
      title: 'Update Failed',
      description: 'Could not update work order status.',
      color: 'red'
    })
  }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
          My Assigned Work
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Fulfill orders assigned directly to you. Start processing paid orders and mark them completed upon handoff.
        </p>
      </div>

      <UButton
        color="gray"
        variant="outline"
        size="sm"
        icon="i-heroicons-arrow-path"
        :loading="erpStore.isLoadingMyWork"
        @click="erpStore.fetchMyWork()"
      >
        Refresh
      </UButton>
    </div>

    <!-- Staff KPIs -->
    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Ready to Start (Paid)</p>
        <p class="mt-1.5 text-2xl font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">{{ paidCount }}</p>
      </UCard>
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">In Progress</p>
        <p class="mt-1.5 text-2xl font-bold text-blue-600 dark:text-blue-400 tabular-nums">{{ inProgressCount }}</p>
      </UCard>
      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
        <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Completed</p>
        <p class="mt-1.5 text-2xl font-bold text-gray-900 dark:text-slate-100 tabular-nums">{{ completedCount }}</p>
      </UCard>
    </div>

    <!-- Filter Chips -->
    <div class="flex flex-wrap items-center gap-1.5">
      <UButton
        size="xs"
        :color="statusFilter === 'ALL' ? 'emerald' : 'gray'"
        :variant="statusFilter === 'ALL' ? 'solid' : 'soft'"
        @click="statusFilter = 'ALL'"
      >
        All ({{ erpStore.myWorkOrders.length }})
      </UButton>
      <UButton
        size="xs"
        :color="statusFilter === 'OPEN' ? 'emerald' : 'gray'"
        :variant="statusFilter === 'OPEN' ? 'solid' : 'soft'"
        @click="statusFilter = 'OPEN'"
      >
        Ready / Open
      </UButton>
      <UButton
        size="xs"
        :color="statusFilter === 'FULFILLING' ? 'emerald' : 'gray'"
        :variant="statusFilter === 'FULFILLING' ? 'solid' : 'soft'"
        @click="statusFilter = 'FULFILLING'"
      >
        In Progress
      </UButton>
      <UButton
        size="xs"
        :color="statusFilter === 'COMPLETED' ? 'emerald' : 'gray'"
        :variant="statusFilter === 'COMPLETED' ? 'solid' : 'soft'"
        @click="statusFilter = 'COMPLETED'"
      >
        Completed
      </UButton>
    </div>

    <UAlert
      v-if="erpStore.myWorkError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="erpStore.myWorkError"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="erpStore.fetchMyWork()">Retry</UButton>
      </template>
    </UAlert>

    <div v-if="erpStore.isLoadingMyWork" class="space-y-3">
      <div v-for="n in 3" :key="n" class="h-24 rounded-xl bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 animate-pulse" />
    </div>

    <UCard
      v-else-if="filteredWork.length === 0"
      class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 text-center py-10"
    >
      <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No assigned work orders</p>
      <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
        When your manager or automated checkout assigns an order to you, it will appear in this queue.
      </p>
    </UCard>

    <div v-else class="space-y-3">
      <UCard
        v-for="order in filteredWork"
        :key="order.id"
        class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800"
      >
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div class="space-y-1.5 min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="font-mono text-sm font-bold text-gray-900 dark:text-slate-100">
                {{ order.order_code }}
              </span>
              <UBadge :color="statusColor(order.status)" variant="soft" size="xs">
                {{ order.status === 'FULFILLING' ? 'In Progress' : order.status }}
              </UBadge>
              <UBadge v-if="order.company_name" color="blue" variant="subtle" size="xs">
                {{ order.company_name }}
              </UBadge>
            </div>
            <p class="text-sm text-gray-700 dark:text-slate-200">
              Customer: <span class="font-medium">{{ order.customer_name || order.customer_id }}</span> ·
              <span class="font-semibold text-emerald-600 dark:text-emerald-400 tabular-nums">
                {{ formatVnd(order.total_amount_vnd) }}
              </span>
            </p>
            <p class="text-xs text-gray-500 dark:text-slate-400 truncate">
              Items: {{ order.items.map((i) => `${i.quantity}x ${i.name || i.sku}`).join(', ') || 'See details' }}
            </p>
          </div>

          <div class="flex flex-wrap items-center gap-2 shrink-0">
            <UButton
              v-if="order.status === 'PAID'"
              color="blue"
              variant="solid"
              size="xs"
              icon="i-heroicons-play"
              :loading="Boolean(erpStore.updatingOrderIds[order.id])"
              @click="handleStatusAction(order, 'FULFILLING')"
            >
              Start Work / In Progress
            </UButton>
            <UButton
              v-if="order.status === 'FULFILLING'"
              color="emerald"
              variant="solid"
              size="xs"
              icon="i-heroicons-check-circle"
              :loading="Boolean(erpStore.updatingOrderIds[order.id])"
              @click="handleStatusAction(order, 'COMPLETED')"
            >
              Mark Completed
            </UButton>
            <UButton
              color="gray"
              variant="outline"
              size="xs"
              icon="i-heroicons-eye"
              @click="openOrderDetail(order)"
            >
              Details
            </UButton>
          </div>
        </div>
      </UCard>
    </div>

    <OrderDetailSlideover
      v-model="isDrawerOpen"
      :order="selectedOrder"
      :employees="[]"
      :is-updating="Boolean(selectedOrder && erpStore.updatingOrderIds[selectedOrder.id])"
      hide-telegram
      hide-cancel
      @transition="handleStatusAction"
    />
  </div>
</template>
