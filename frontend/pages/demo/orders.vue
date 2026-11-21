<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useDemoStore } from '~/stores/demo'
import type { Order, OrderCreateItemPayload, OrderStatus } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const demoStore = useDemoStore()
const toast = useToast()

const viewMode = ref<'kanban' | 'list'>('kanban')
const searchQuery = ref<string>('')
const statusFilter = ref<OrderStatus | 'ALL'>('ALL')
const selectedOrderId = ref<string | null>(null)
const isDrawerOpen = ref<boolean>(false)
const isCreateModalOpen = ref<boolean>(false)
const createError = ref<string | null>(null)

const orderDraft = reactive<{
  customer_id: string
  assigned_employee_id: string
  items: OrderCreateItemPayload[]
}>({
  customer_id: '',
  assigned_employee_id: '',
  items: [{ sku: '', quantity: 1 }]
})

const statusChips: { value: OrderStatus | 'ALL'; label: string }[] = [
  { value: 'ALL', label: 'All' },
  { value: 'PENDING_PAYMENT', label: 'Pending Payment' },
  { value: 'PAID', label: 'Paid' },
  { value: 'FULFILLING', label: 'Fulfilling' },
  { value: 'COMPLETED', label: 'Completed' },
  { value: 'CANCELLED', label: 'Cancelled' }
]

const kanbanColumns: {
  key: OrderStatus
  title: string
  badgeColor: 'amber' | 'emerald' | 'blue' | 'primary' | 'red'
}[] = [
  { key: 'PENDING_PAYMENT', title: 'Pending Payment', badgeColor: 'amber' },
  { key: 'PAID', title: 'Paid', badgeColor: 'emerald' },
  { key: 'FULFILLING', title: 'Fulfilling', badgeColor: 'blue' },
  { key: 'COMPLETED', title: 'Completed', badgeColor: 'primary' },
  { key: 'CANCELLED', title: 'Cancelled', badgeColor: 'red' }
]

const listColumns = [
  { key: 'order_code', label: 'Order' },
  { key: 'customer_name', label: 'Customer' },
  { key: 'total_amount_vnd', label: 'Total (VND)' },
  { key: 'status', label: 'Status' },
  { key: 'created_at', label: 'Created' }
]

const filteredOrders = computed<Order[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return demoStore.orders.filter((order) => {
    if (statusFilter.value !== 'ALL' && order.status !== statusFilter.value) {
      return false
    }
    if (!q) return true
    return (
      order.order_code.toLowerCase().includes(q) ||
      (order.customer_name || '').toLowerCase().includes(q) ||
      (order.sepay_reference || '').toLowerCase().includes(q)
    )
  })
})

const visibleKanbanColumns = computed(() => {
  if (statusFilter.value === 'ALL') return kanbanColumns
  return kanbanColumns.filter((col) => col.key === statusFilter.value)
})

function ordersInColumn(status: OrderStatus): Order[] {
  return filteredOrders.value.filter((order) => order.status === status)
}

const selectedOrder = computed<Order | null>(
  () => demoStore.orders.find((order) => order.id === selectedOrderId.value) ?? null
)

const customerOptions = computed(() =>
  demoStore.customers
    .filter((customer) => customer.is_active !== false)
    .map((customer) => ({
      label: `${customer.full_name} (${customer.meta_psid})`,
      value: customer.id
    }))
)

const employeeOptions = computed(() => [
  { label: 'Unassigned (Auto-assign on Paid)', value: '' },
  ...demoStore.employees
    .filter((employee) => employee.is_active)
    .map((employee) => ({
      label: `${employee.full_name} (${employee.telegram_user_id})`,
      value: employee.id
    }))
])

const skuOptions = computed(() =>
  demoStore.inventory
    .filter((item) => item.is_active !== false)
    .map((item) => ({
      label: `${item.sku} — ${item.name} (${formatVnd(item.price_vnd)})`,
      value: item.sku
    }))
)

const estimatedDraftTotal = computed<number>(() =>
  orderDraft.items.reduce((sum, line) => {
    const matched = demoStore.inventory.find((item) => item.sku === line.sku)
    return sum + (matched?.price_vnd ?? 0) * Math.max(0, Number(line.quantity) || 0)
  }, 0)
)

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

function employeeName(employeeId?: string | null): string {
  if (!employeeId) return 'Unassigned'
  return demoStore.employees.find((emp) => emp.id === employeeId)?.full_name ?? 'Assigned'
}

function openOrderDrawer(order: Order): void {
  selectedOrderId.value = order.id
  isDrawerOpen.value = true
}

function openCreateModal(): void {
  createError.value = null
  orderDraft.customer_id = customerOptions.value[0]?.value ?? ''
  orderDraft.assigned_employee_id = ''
  orderDraft.items = [{ sku: skuOptions.value[0]?.value ?? '', quantity: 1 }]
  isCreateModalOpen.value = true
}

function addLineItem(): void {
  orderDraft.items.push({ sku: skuOptions.value[0]?.value ?? '', quantity: 1 })
}

function removeLineItem(index: number): void {
  if (orderDraft.items.length > 1) {
    orderDraft.items.splice(index, 1)
  }
}

function submitCreateOrder(): void {
  createError.value = null
  try {
    const created = demoStore.createOrder({
      customer_id: orderDraft.customer_id,
      assigned_employee_id: orderDraft.assigned_employee_id || null,
      items: orderDraft.items
    })
    isCreateModalOpen.value = false
    toast.add({
      title: `Demo Order ${created.order_code} Created`,
      description: `Total ${formatVnd(created.total_amount_vnd)}`,
      color: 'emerald'
    })
    openOrderDrawer(created)
  } catch (err) {
    createError.value = err instanceof Error ? err.message : 'Unable to create demo order'
  }
}

function handleTransition(order: Order, nextStatus: OrderStatus): void {
  try {
    demoStore.updateOrderStatus(order.id, nextStatus)
    toast.add({
      title: `${order.order_code} Updated`,
      description: `Status moved to ${nextStatus} in sandbox.`,
      color: 'emerald'
    })
  } catch (err) {
    toast.add({
      title: 'Invalid Transition',
      description: err instanceof Error ? err.message : 'Transition not permitted.',
      color: 'red'
    })
  }
}

async function handleNotifyTelegram(order: Order): Promise<void> {
  try {
    const res = await demoStore.triggerTelegramNotification(order)
    toast.add({
      title: res.dispatched ? 'Telegram Alert Dispatched' : 'Telegram Dispatch Failed',
      description: res.simulated
        ? `Simulated preview for ${order.order_code}`
        : `Sent live alert for ${order.order_code}`,
      color: res.dispatched ? 'emerald' : 'amber'
    })
  } catch {
    toast.add({
      title: 'Telegram Dispatch Error',
      description: demoStore.telegramError || 'Unable to send demo Telegram alert.',
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
          Sandbox Orders
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Create orders, test stage transitions, and trigger real Telegram alerts via the demo API.
        </p>
      </div>

      <div class="flex items-center gap-2">
        <div class="inline-flex rounded-lg border border-gray-200 dark:border-slate-700 bg-white dark:bg-slate-900 p-0.5">
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors"
            :class="viewMode === 'kanban' ? 'bg-emerald-600 text-white' : 'text-gray-600 dark:text-slate-300'"
            @click="viewMode = 'kanban'"
          >
            <UIcon name="i-heroicons-view-columns" class="h-3.5 w-3.5" />
            Kanban
          </button>
          <button
            type="button"
            class="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors"
            :class="viewMode === 'list' ? 'bg-emerald-600 text-white' : 'text-gray-600 dark:text-slate-300'"
            @click="viewMode = 'list'"
          >
            <UIcon name="i-heroicons-list-bullet" class="h-3.5 w-3.5" />
            List
          </button>
        </div>

        <UButton color="emerald" variant="solid" size="sm" icon="i-heroicons-plus" @click="openCreateModal">
          New Order
        </UButton>
      </div>
    </div>

    <div class="flex flex-col md:flex-row md:items-center justify-between gap-3">
      <div class="w-full md:max-w-xs">
        <UInput
          v-model="searchQuery"
          placeholder="Search order code or customer..."
          icon="i-heroicons-magnifying-glass"
          size="sm"
        />
      </div>

      <div class="flex flex-wrap items-center gap-1.5">
        <UButton
          v-for="chip in statusChips"
          :key="chip.value"
          size="xs"
          :color="statusFilter === chip.value ? 'emerald' : 'gray'"
          :variant="statusFilter === chip.value ? 'solid' : 'soft'"
          @click="statusFilter = chip.value"
        >
          {{ chip.label }}
        </UButton>
      </div>
    </div>

    <div
      v-if="viewMode === 'kanban'"
      class="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-4 items-start"
    >
      <div
        v-for="col in visibleKanbanColumns"
        :key="col.key"
        class="rounded-xl border border-gray-200 dark:border-slate-800 bg-gray-100/70 dark:bg-slate-900/50 p-3 space-y-3"
      >
        <div class="flex items-center justify-between px-1">
          <span class="text-xs font-semibold text-gray-700 dark:text-slate-200">{{ col.title }}</span>
          <UBadge :color="col.badgeColor" variant="soft" size="xs">
            {{ ordersInColumn(col.key).length }}
          </UBadge>
        </div>

        <div class="space-y-2.5">
          <button
            v-for="order in ordersInColumn(col.key)"
            :key="order.id"
            type="button"
            class="w-full text-left rounded-lg border border-gray-200 dark:border-slate-800 bg-white dark:bg-slate-900 p-3 shadow-sm hover:border-emerald-500/60 transition-colors space-y-1.5"
            @click="openOrderDrawer(order)"
          >
            <div class="flex items-center justify-between gap-2">
              <span class="text-xs font-semibold text-gray-900 dark:text-slate-100 tabular-nums">
                {{ order.order_code }}
              </span>
              <span class="text-xs font-semibold text-emerald-600 dark:text-emerald-400 tabular-nums">
                {{ formatVnd(order.total_amount_vnd) }}
              </span>
            </div>
            <p class="text-xs text-gray-600 dark:text-slate-300 truncate">
              {{ order.customer_name || order.customer_id }}
            </p>
            <p class="text-[11px] text-gray-400 dark:text-slate-500 truncate">
              {{ employeeName(order.assigned_employee_id) }}
            </p>
          </button>

          <p
            v-if="ordersInColumn(col.key).length === 0"
            class="py-6 text-center text-xs text-gray-400 dark:text-slate-500"
          >
            Empty
          </p>
        </div>
      </div>
    </div>

    <UCard v-else class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <UTable :rows="filteredOrders" :columns="listColumns" class="cursor-pointer" @select="openOrderDrawer">
        <template #order_code-data="{ row }">
          <span class="font-semibold text-gray-900 dark:text-slate-100 tabular-nums">{{ row.order_code }}</span>
        </template>
        <template #total_amount_vnd-data="{ row }">
          <span class="font-medium tabular-nums">{{ formatVnd(row.total_amount_vnd) }}</span>
        </template>
        <template #status-data="{ row }">
          <UBadge :color="statusColor(row.status)" variant="soft" size="xs">{{ row.status }}</UBadge>
        </template>
        <template #created_at-data="{ row }">
          <span class="text-xs text-gray-500 dark:text-slate-400 tabular-nums">
            {{ new Date(row.created_at).toLocaleDateString() }}
          </span>
        </template>
      </UTable>
    </UCard>

    <OrderDetailSlideover
      v-model="isDrawerOpen"
      :order="selectedOrder"
      :employees="demoStore.employees"
      :is-notifying="demoStore.isNotifyingTelegram"
      @transition="handleTransition"
      @notify-telegram="handleNotifyTelegram"
    />

    <UModal v-model="isCreateModalOpen">
      <UCard class="bg-white dark:bg-slate-900">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">Create Sandbox Order</h2>
            <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isCreateModalOpen = false" />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitCreateOrder">
          <UAlert
            v-if="createError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="createError"
          />

          <UFormGroup label="Customer" required>
            <USelectMenu
              v-model="orderDraft.customer_id"
              :options="customerOptions"
              value-attribute="value"
              option-attribute="label"
            />
          </UFormGroup>

          <UFormGroup label="Assigned Staff">
            <USelectMenu
              v-model="orderDraft.assigned_employee_id"
              :options="employeeOptions"
              value-attribute="value"
              option-attribute="label"
            />
          </UFormGroup>

          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-xs font-medium text-gray-700 dark:text-slate-300">Line Items</span>
              <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-plus" @click="addLineItem">
                Add Item
              </UButton>
            </div>
            <div v-for="(line, idx) in orderDraft.items" :key="idx" class="flex items-center gap-2">
              <USelectMenu
                v-model="line.sku"
                :options="skuOptions"
                value-attribute="value"
                option-attribute="label"
                class="flex-1"
              />
              <UInput v-model.number="line.quantity" type="number" min="1" class="w-20" />
              <UButton
                v-if="orderDraft.items.length > 1"
                color="red"
                variant="ghost"
                icon="i-heroicons-trash"
                size="xs"
                @click="removeLineItem(idx)"
              />
            </div>
          </div>

          <div class="flex items-center justify-between border-t border-gray-200 dark:border-slate-800 pt-3 text-sm">
            <span class="text-gray-500 dark:text-slate-400">Estimated Total</span>
            <span class="font-semibold text-gray-900 dark:text-slate-100 tabular-nums">
              {{ formatVnd(estimatedDraftTotal) }}
            </span>
          </div>

          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="isCreateModalOpen = false">Cancel</UButton>
            <UButton type="submit" color="emerald" variant="solid">Create Sandbox Order</UButton>
          </div>
        </form>
      </UCard>
    </UModal>
  </div>
</template>
