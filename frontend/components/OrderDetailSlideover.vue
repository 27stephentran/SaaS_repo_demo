<script setup lang="ts">
import { computed } from 'vue'
import { ALLOWED_ORDER_TRANSITIONS } from '~/stores/erp'
import type { Employee, Order, OrderStatus } from '~/types/erp'

const props = defineProps<{
  modelValue: boolean
  order: Order | null
  employees: Employee[]
  isUpdating?: boolean
  isNotifying?: boolean
  hideTelegram?: boolean
  hideCancel?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  transition: [order: Order, nextStatus: OrderStatus]
  'notify-telegram': [order: Order]
}>()

const isOpen = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value)
})

const assignedEmployee = computed<Employee | undefined>(() => {
  if (!props.order?.assigned_employee_id) return undefined
  return props.employees.find((emp) => emp.id === props.order?.assigned_employee_id)
})

const allowedTransitions = computed<readonly OrderStatus[]>(() => {
  if (!props.order) return []
  const transitions = ALLOWED_ORDER_TRANSITIONS[props.order.status] ?? []
  return props.hideCancel ? transitions.filter((s) => s !== 'CANCELLED') : transitions
})

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

function statusLabel(status: OrderStatus): string {
  switch (status) {
    case 'PENDING_PAYMENT':
      return 'Pending Payment'
    case 'PAID':
      return 'Paid'
    case 'FULFILLING':
      return 'Fulfilling'
    case 'COMPLETED':
      return 'Completed'
    case 'CANCELLED':
      return 'Cancelled'
  }
}

function transitionActionLabel(status: OrderStatus): string {
  switch (status) {
    case 'PAID':
      return 'Mark Paid'
    case 'FULFILLING':
      return 'Start Fulfillment'
    case 'COMPLETED':
      return 'Complete Order'
    case 'CANCELLED':
      return 'Cancel Order'
    default:
      return status
  }
}

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

function isSafeHttpUrl(url?: string | null): url is string {
  return Boolean(url && /^https?:\/\//i.test(url))
}
</script>

<template>
  <USlideover v-model="isOpen" :ui="{ width: 'max-w-lg' }">
    <div v-if="order" class="flex h-full flex-col bg-white dark:bg-slate-900">
      <div class="flex items-start justify-between border-b border-gray-200 dark:border-slate-800 px-6 py-4">
        <div class="space-y-1">
          <div class="flex items-center gap-2.5">
            <h2 class="text-lg font-semibold text-gray-900 dark:text-slate-100 tabular-nums">
              {{ order.order_code }}
            </h2>
            <UBadge :color="statusColor(order.status)" variant="soft" size="xs">
              {{ statusLabel(order.status) }}
            </UBadge>
            <UBadge v-if="order.company_name" color="blue" variant="subtle" size="xs">
              {{ order.company_name }}
            </UBadge>
          </div>
          <p class="text-xs text-gray-500 dark:text-slate-400">
            Created {{ new Date(order.created_at).toLocaleString() }}
          </p>
        </div>
        <UButton
          color="gray"
          variant="ghost"
          icon="i-heroicons-x-mark"
          aria-label="Close order details"
          @click="isOpen = false"
        />
      </div>

      <div class="flex-1 overflow-y-auto px-6 py-5 space-y-6">
        <div class="grid grid-cols-2 gap-4 rounded-lg border border-gray-200 dark:border-slate-800 bg-gray-50 dark:bg-slate-950/50 p-4">
          <div>
            <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Customer</p>
            <p class="mt-0.5 text-sm font-medium text-gray-900 dark:text-slate-100">
              {{ order.customer_name || order.customer_id }}
            </p>
          </div>
          <div>
            <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Order Total</p>
            <p class="mt-0.5 text-sm font-semibold text-gray-900 dark:text-slate-100 tabular-nums">
              {{ formatVnd(order.total_amount_vnd) }}
            </p>
          </div>
          <div>
            <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Payment Reference</p>
            <p class="mt-0.5 text-xs font-mono text-gray-700 dark:text-slate-300">
              {{ order.sepay_reference || order.order_code }}
            </p>
          </div>
          <div>
            <p class="text-xs font-medium text-gray-500 dark:text-slate-400">Assigned Staff</p>
            <p class="mt-0.5 text-sm text-gray-900 dark:text-slate-100">
              {{ assignedEmployee ? `${assignedEmployee.full_name}` : 'Unassigned' }}
            </p>
            <p v-if="assignedEmployee" class="text-xs text-gray-500 dark:text-slate-400">
              Telegram: {{ assignedEmployee.telegram_user_id }}
            </p>
          </div>
        </div>

        <div>
          <h3 class="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-slate-400 mb-3">
            Order Line Items
          </h3>
          <div class="divide-y divide-gray-200 dark:divide-slate-800 rounded-lg border border-gray-200 dark:border-slate-800">
            <div
              v-for="item in order.items"
              :key="item.sku"
              class="flex items-center justify-between px-4 py-3 text-sm"
            >
              <div class="min-w-0 pr-3">
                <p class="font-medium text-gray-900 dark:text-slate-100 truncate">{{ item.name }}</p>
                <p class="text-xs text-gray-500 dark:text-slate-400 font-mono">
                  {{ item.sku }} × {{ item.quantity }}
                </p>
              </div>
              <div class="text-right shrink-0 tabular-nums">
                <p class="font-medium text-gray-900 dark:text-slate-100">
                  {{ formatVnd(item.unit_price_vnd * item.quantity) }}
                </p>
                <p class="text-xs text-gray-500 dark:text-slate-400">
                  {{ formatVnd(item.unit_price_vnd) }} ea
                </p>
              </div>
            </div>
          </div>
        </div>

        <div
          v-if="isSafeHttpUrl(order.vietqr_url || order.sepay_qr_url)"
          class="rounded-lg border border-gray-200 dark:border-slate-800 p-4 space-y-3"
        >
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-semibold uppercase tracking-wider text-gray-500 dark:text-slate-400">
              VietQR Payment Link
            </h3>
            <span class="text-xs font-mono text-emerald-600 dark:text-emerald-400">
              {{ order.sepay_reference || order.order_code }}
            </span>
          </div>
          <div class="flex items-center justify-center rounded-lg bg-white p-3 border border-gray-100">
            <img
              :src="order.vietqr_url || order.sepay_qr_url || ''"
              :alt="`VietQR for ${order.order_code}`"
              class="h-44 w-44 object-contain"
            >
          </div>
        </div>
      </div>

      <div class="border-t border-gray-200 dark:border-slate-800 px-6 py-4 space-y-3 bg-gray-50/70 dark:bg-slate-950/60">
        <div class="flex flex-wrap items-center gap-2">
          <UButton
            v-for="nextStatus in allowedTransitions"
            :key="nextStatus"
            :color="nextStatus === 'CANCELLED' ? 'red' : 'emerald'"
            :variant="nextStatus === 'CANCELLED' ? 'soft' : 'solid'"
            size="sm"
            :loading="isUpdating"
            @click="emit('transition', order, nextStatus)"
          >
            {{ transitionActionLabel(nextStatus) }}
          </UButton>
        </div>

        <UButton
          v-if="!hideTelegram"
          block
          color="primary"
          variant="soft"
          icon="i-heroicons-paper-airplane"
          :loading="isNotifying"
          @click="emit('notify-telegram', order)"
        >
          Send Telegram Alert
        </UButton>
      </div>
    </div>
  </USlideover>
</template>
