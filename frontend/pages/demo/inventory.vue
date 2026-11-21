<script setup lang="ts">
import { computed, ref } from 'vue'
import { useDemoStore } from '~/stores/demo'
import type { InventoryItem } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const demoStore = useDemoStore()
const toast = useToast()

const searchQuery = ref<string>('')
const showArchived = ref<boolean>(false)

const columns = [
  { key: 'sku', label: 'SKU' },
  { key: 'name', label: 'Product' },
  { key: 'price_vnd', label: 'Unit Price' },
  { key: 'stock_quantity', label: 'Stock' },
  { key: 'is_active', label: 'Status' },
  { key: 'actions', label: '' }
]

const visibleItems = computed<InventoryItem[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return demoStore.inventory.filter((item) => {
    if (!showArchived.value && item.is_active === false) {
      return false
    }
    if (!q) return true
    return (
      item.sku.toLowerCase().includes(q) ||
      item.name.toLowerCase().includes(q) ||
      item.description.toLowerCase().includes(q) ||
      (item.category || '').toLowerCase().includes(q)
    )
  })
})

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

function handleArchive(item: InventoryItem): void {
  demoStore.softDeleteInventory(item.id)
  toast.add({
    title: 'Sandbox Product Archived',
    description: `${item.sku} marked inactive in demo.`,
    color: 'amber'
  })
}

function handleRestore(item: InventoryItem): void {
  demoStore.restoreInventory(item.id)
  toast.add({
    title: 'Sandbox Product Restored',
    description: `${item.sku} reactivated in demo.`,
    color: 'emerald'
  })
}
</script>

<template>
  <div class="space-y-5">
    <div>
      <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
        Sandbox Inventory
      </h1>
      <p class="text-sm text-gray-500 dark:text-slate-400">
        Filter products and test soft-delete Archive &amp; Restore flows.
      </p>
    </div>

    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="w-full sm:max-w-md">
        <UInput
          v-model="searchQuery"
          placeholder="Search SKU, name, or notes..."
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
      <div v-if="visibleItems.length === 0" class="py-10 text-center">
        <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No matching sandbox items</p>
      </div>

      <UTable v-else :rows="visibleItems" :columns="columns">
        <template #sku-data="{ row }">
          <span class="font-mono text-xs font-medium text-gray-900 dark:text-slate-100">{{ row.sku }}</span>
        </template>
        <template #name-data="{ row }">
          <div class="max-w-sm">
            <p class="font-medium text-gray-900 dark:text-slate-100 truncate">{{ row.name }}</p>
            <p class="text-xs text-gray-500 dark:text-slate-400 truncate">{{ row.category || 'General' }}</p>
          </div>
        </template>
        <template #price_vnd-data="{ row }">
          <span class="font-medium tabular-nums">{{ formatVnd(row.price_vnd) }}</span>
        </template>
        <template #stock_quantity-data="{ row }">
          <UBadge :color="row.stock_quantity <= 10 ? 'amber' : 'gray'" variant="soft" size="xs" class="tabular-nums">
            {{ row.stock_quantity }}
          </UBadge>
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
