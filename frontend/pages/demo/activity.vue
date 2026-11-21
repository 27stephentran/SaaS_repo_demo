<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDemoStore } from '~/stores/demo'
import type { WebhookEventLog, WebhookEventStatus, WebhookSource } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const demoStore = useDemoStore()
const sourceFilter = ref<WebhookSource | 'ALL'>('ALL')
const selectedEvent = ref<WebhookEventLog | null>(null)
const isDetailOpen = ref<boolean>(false)

const filterOptions: { value: WebhookSource | 'ALL'; label: string }[] = [
  { value: 'ALL', label: 'All Events' },
  { value: 'SEPAY_IPN', label: 'Payments' },
  { value: 'META_GRAPH', label: 'Messaging' },
  { value: 'TELEGRAM_BOT', label: 'Staff Dispatch' }
]

const filteredEvents = computed<WebhookEventLog[]>(() => {
  if (sourceFilter.value === 'ALL') return demoStore.webhookFeed
  return demoStore.webhookFeed.filter((evt) => evt.source === sourceFilter.value)
})

function sourceBadgeColor(source: WebhookSource): 'emerald' | 'blue' | 'violet' {
  switch (source) {
    case 'SEPAY_IPN':
      return 'emerald'
    case 'META_GRAPH':
      return 'blue'
    case 'TELEGRAM_BOT':
      return 'violet'
  }
}

function statusBadgeColor(status: WebhookEventStatus): 'emerald' | 'amber' | 'red' {
  switch (status) {
    case 'PROCESSED':
      return 'emerald'
    case 'PENDING':
      return 'amber'
    case 'FAILED':
      return 'red'
  }
}

function openEventDetail(evt: WebhookEventLog): void {
  selectedEvent.value = evt
  isDetailOpen.value = true
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
        Sandbox Activity
      </h1>
      <p class="text-sm text-gray-500 dark:text-slate-400">
        Real-time log of sandbox transitions, order creation, and Telegram dispatches.
      </p>
    </div>

    <div class="flex flex-wrap items-center gap-1.5">
      <UButton
        v-for="opt in filterOptions"
        :key="opt.value"
        size="xs"
        :color="sourceFilter === opt.value ? 'emerald' : 'gray'"
        :variant="sourceFilter === opt.value ? 'solid' : 'soft'"
        @click="sourceFilter = opt.value"
      >
        {{ opt.label }}
      </UButton>
    </div>

    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <div v-if="filteredEvents.length === 0" class="py-10 text-center">
        <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No activity events</p>
      </div>

      <div v-else class="divide-y divide-gray-200 dark:divide-slate-800">
        <button
          v-for="evt in filteredEvents"
          :key="evt.id"
          type="button"
          class="w-full text-left py-3.5 first:pt-1 last:pb-1 flex items-center justify-between gap-4 hover:bg-gray-50/70 dark:hover:bg-slate-800/40 px-2 rounded-lg transition-colors"
          @click="openEventDetail(evt)"
        >
          <div class="min-w-0 space-y-1">
            <div class="flex items-center gap-2">
              <UBadge :color="sourceBadgeColor(evt.source)" variant="soft" size="xs">{{ evt.source }}</UBadge>
              <span class="text-xs font-mono text-gray-500 dark:text-slate-400">{{ evt.event_type }}</span>
            </div>
            <p class="text-sm text-gray-900 dark:text-slate-100 truncate">{{ evt.summary }}</p>
          </div>

          <div class="flex items-center gap-3 shrink-0">
            <UBadge :color="statusBadgeColor(evt.status)" variant="soft" size="xs">{{ evt.status }}</UBadge>
            <span class="text-xs text-gray-400 dark:text-slate-500 tabular-nums">
              {{ new Date(evt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }}
            </span>
          </div>
        </button>
      </div>
    </UCard>

    <USlideover v-model="isDetailOpen" :ui="{ width: 'max-w-md' }">
      <div v-if="selectedEvent" class="flex h-full flex-col bg-white dark:bg-slate-900">
        <div class="flex items-center justify-between border-b border-gray-200 dark:border-slate-800 px-6 py-4">
          <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">Event Details</h2>
          <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isDetailOpen = false" />
        </div>
        <div class="p-6 space-y-4">
          <div class="flex items-center gap-2">
            <UBadge :color="sourceBadgeColor(selectedEvent.source)" variant="soft" size="xs">
              {{ selectedEvent.source }}
            </UBadge>
            <UBadge :color="statusBadgeColor(selectedEvent.status)" variant="soft" size="xs">
              {{ selectedEvent.status }}
            </UBadge>
          </div>
          <div>
            <p class="text-xs text-gray-500 dark:text-slate-400">Event Type</p>
            <p class="text-sm font-mono text-gray-900 dark:text-slate-100">{{ selectedEvent.event_type }}</p>
          </div>
          <div>
            <p class="text-xs text-gray-500 dark:text-slate-400">Summary</p>
            <p class="text-sm text-gray-900 dark:text-slate-100">{{ selectedEvent.summary }}</p>
          </div>
          <div>
            <p class="text-xs text-gray-500 dark:text-slate-400">Timestamp</p>
            <p class="text-sm text-gray-900 dark:text-slate-100 tabular-nums">
              {{ new Date(selectedEvent.timestamp).toLocaleString() }}
            </p>
          </div>
        </div>
      </div>
    </USlideover>
  </div>
</template>
