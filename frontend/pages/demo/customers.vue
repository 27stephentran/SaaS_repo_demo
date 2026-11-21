<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDemoStore } from '~/stores/demo'
import type { Customer } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const demoStore = useDemoStore()
const toast = useToast()

const searchQuery = ref<string>('')
const showArchived = ref<boolean>(false)

const columns = [
  { key: 'full_name', label: 'Customer' },
  { key: 'meta_psid', label: 'Channel ID' },
  { key: 'phone', label: 'Phone' },
  { key: 'is_active', label: 'Status' },
  { key: 'actions', label: '' }
]

const filteredCustomers = computed<Customer[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return demoStore.customers.filter((customer) => {
    if (!showArchived.value && customer.is_active === false) {
      return false
    }
    if (!q) return true
    return (
      customer.full_name.toLowerCase().includes(q) ||
      customer.meta_psid.toLowerCase().includes(q) ||
      (customer.phone || '').toLowerCase().includes(q)
    )
  })
})

function handleArchive(customer: Customer): void {
  demoStore.softDeleteCustomer(customer.id)
  toast.add({
    title: 'Sandbox Customer Archived',
    description: `${customer.full_name} deactivated in demo.`,
    color: 'amber'
  })
}

function handleRestore(customer: Customer): void {
  demoStore.restoreCustomer(customer.id)
  toast.add({
    title: 'Sandbox Customer Restored',
    description: `${customer.full_name} reactivated in demo.`,
    color: 'emerald'
  })
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
        Sandbox Customers
      </h1>
      <p class="text-sm text-gray-500 dark:text-slate-400">
        Inspect omnichannel customer profiles and test Archive / Restore flows.
      </p>
    </div>

    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="w-full sm:max-w-xs">
        <UInput
          v-model="searchQuery"
          placeholder="Search name, PSID, or phone..."
          icon="i-heroicons-magnifying-glass"
          size="sm"
        />
      </div>

      <div class="flex items-center gap-2.5">
        <UToggle v-model="showArchived" />
        <span class="text-xs font-medium text-gray-600 dark:text-slate-300">Show Archived</span>
      </div>
    </div>

    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <div v-if="filteredCustomers.length === 0" class="py-10 text-center">
        <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No matching customers</p>
      </div>

      <UTable v-else :rows="filteredCustomers" :columns="columns">
        <template #full_name-data="{ row }">
          <span class="font-medium text-gray-900 dark:text-slate-100">{{ row.full_name }}</span>
        </template>
        <template #meta_psid-data="{ row }">
          <span class="font-mono text-xs text-gray-600 dark:text-slate-300">{{ row.meta_psid }}</span>
        </template>
        <template #phone-data="{ row }">
          <span class="text-xs text-gray-600 dark:text-slate-300 tabular-nums">{{ row.phone || '—' }}</span>
        </template>
        <template #is_active-data="{ row }">
          <UBadge :color="row.is_active === false ? 'gray' : 'emerald'" variant="soft" size="xs">
            {{ row.is_active === false ? 'Archived' : 'Active' }}
          </UBadge>
        </template>
        <template #actions-data="{ row }">
          <div class="flex justify-end">
            <UButton
              v-if="row.is_active !== false"
              color="amber"
              variant="ghost"
              size="xs"
              icon="i-heroicons-archive-box"
              @click="handleArchive(row)"
            >
              Archive
            </UButton>
            <UButton
              v-else
              color="emerald"
              variant="soft"
              size="xs"
              icon="i-heroicons-arrow-uturn-left"
              @click="handleRestore(row)"
            >
              Restore
            </UButton>
          </div>
        </template>
      </UTable>
    </UCard>
  </div>
</template>
