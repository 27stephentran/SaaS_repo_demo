<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { Customer } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const toast = useToast()

const searchQuery = ref<string>('')
const showArchived = ref<boolean>(false)

const columns = [
  { key: 'full_name', label: 'Customer' },
  { key: 'meta_psid', label: 'Channel ID' },
  { key: 'phone', label: 'Phone' },
  { key: 'is_active', label: 'Status' },
  { key: 'created_at', label: 'Joined' },
  { key: 'actions', label: '' }
]

async function loadCustomers(): Promise<void> {
  await erpStore.fetchCustomers(showArchived.value)
}

onMounted(async () => {
  await loadCustomers()
})

watch(showArchived, async () => {
  await loadCustomers()
})

watch(
  () => erpStore.selectedCompanyId,
  async () => {
    if (erpStore.isAdmin) {
      await loadCustomers()
    }
  }
)

const filteredCustomers = computed<Customer[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return erpStore.customers.filter((customer) => {
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

async function handleArchive(customer: Customer): Promise<void> {
  try {
    await erpStore.softDeleteCustomer(customer.id)
    toast.add({
      title: 'Customer Archived',
      description: `${customer.full_name} deactivated cleanly.`,
      color: 'amber'
    })
  } catch {
    toast.add({
      title: 'Archive Failed',
      description: `Could not archive ${customer.full_name}.`,
      color: 'red'
    })
  }
}

async function handleRestore(customer: Customer): Promise<void> {
  try {
    await erpStore.restoreCustomer(customer.id)
    toast.add({
      title: 'Customer Restored',
      description: `${customer.full_name} is active again.`,
      color: 'emerald'
    })
  } catch {
    toast.add({
      title: 'Restore Failed',
      description: `Could not restore ${customer.full_name}.`,
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
          Customers
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Omnichannel customer directory linked to Messenger and Instagram conversations.
        </p>
      </div>
      <UButton color="gray" variant="outline" size="sm" icon="i-heroicons-arrow-path" :loading="erpStore.isLoadingCustomers" @click="loadCustomers">
        Refresh
      </UButton>
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

    <UAlert
      v-if="erpStore.customersError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="erpStore.customersError"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="loadCustomers">
          Retry
        </UButton>
      </template>
    </UAlert>

    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <div v-if="erpStore.isLoadingCustomers" class="space-y-3 py-4">
        <div v-for="n in 4" :key="n" class="h-10 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
      </div>

      <div v-else-if="filteredCustomers.length === 0" class="py-10 text-center">
        <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No customers found</p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
          Customers appear automatically as omnichannel orders arrive, or toggle Show Archived.
        </p>
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
        <template #created_at-data="{ row }">
          <span class="text-xs text-gray-500 dark:text-slate-400 tabular-nums">
            {{ new Date(row.created_at).toLocaleDateString() }}
          </span>
        </template>
        <template #actions-data="{ row }">
          <div class="flex justify-end">
            <UButton
              v-if="row.is_active !== false"
              color="amber"
              variant="ghost"
              size="xs"
              icon="i-heroicons-archive-box"
              :loading="Boolean(erpStore.mutatingCustomerIds[row.id])"
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
              :loading="Boolean(erpStore.mutatingCustomerIds[row.id])"
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
