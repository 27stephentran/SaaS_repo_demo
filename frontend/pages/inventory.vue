<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { InventoryItem } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const toast = useToast()

const searchQuery = ref<string>('')
const showArchived = ref<boolean>(false)
const isCreateOpen = ref<boolean>(false)
const createError = ref<string | null>(null)

const productDraft = reactive({
  sku: '',
  name: '',
  category: 'Coffee Beans',
  description: '',
  price_vnd: 0,
  stock_quantity: 0
})

const columns = [
  { key: 'sku', label: 'SKU' },
  { key: 'name', label: 'Product' },
  { key: 'price_vnd', label: 'Unit Price' },
  { key: 'stock_quantity', label: 'Stock' },
  { key: 'is_active', label: 'Status' },
  { key: 'actions', label: '' }
]

async function refreshCatalog(): Promise<void> {
  if (searchQuery.value.trim()) {
    await erpStore.searchInventorySemantic(searchQuery.value, showArchived.value)
  } else {
    await erpStore.fetchInventory(showArchived.value)
  }
}

onMounted(async () => {
  await erpStore.fetchInventory(showArchived.value)
})

watch(showArchived, async () => {
  await refreshCatalog()
})

watch(
  () => erpStore.selectedCompanyId,
  async () => {
    if (erpStore.isAdmin) {
      await refreshCatalog()
    }
  }
)

const visibleItems = computed<InventoryItem[]>(() => {
  if (showArchived.value) {
    return erpStore.inventory
  }
  return erpStore.inventory.filter((item) => item.is_active !== false)
})

function formatVnd(amount: number): string {
  return `${amount.toLocaleString('vi-VN')} ₫`
}

async function handleSearch(): Promise<void> {
  await erpStore.searchInventorySemantic(searchQuery.value, showArchived.value)
}

async function handleClearSearch(): Promise<void> {
  searchQuery.value = ''
  await erpStore.fetchInventory(showArchived.value)
}

function openCreateModal(): void {
  createError.value = null
  productDraft.sku = ''
  productDraft.name = ''
  productDraft.category = 'Coffee Beans'
  productDraft.description = ''
  productDraft.price_vnd = 0
  productDraft.stock_quantity = 10
  isCreateOpen.value = true
}

async function submitCreateProduct(): Promise<void> {
  createError.value = null
  if (!productDraft.sku.trim() || !productDraft.name.trim() || productDraft.description.trim().length < 3) {
    createError.value = 'SKU, product name, and description (min 3 chars) are required.'
    return
  }
  try {
    const created = await erpStore.createInventoryItem({
      sku: productDraft.sku,
      name: productDraft.name,
      category: productDraft.category,
      description: productDraft.description,
      price_vnd: productDraft.price_vnd,
      stock_quantity: productDraft.stock_quantity
    })
    isCreateOpen.value = false
    toast.add({
      title: 'Product Added',
      description: `${created.sku} added to catalog.`,
      color: 'emerald'
    })
  } catch {
    createError.value = 'Unable to create product. Check for duplicate SKU and retry.'
  }
}

async function handleArchive(item: InventoryItem): Promise<void> {
  try {
    await erpStore.softDeleteInventory(item.id)
    toast.add({
      title: 'Product Archived',
      description: `${item.sku} deactivated and excluded from active catalog.`,
      color: 'amber'
    })
  } catch {
    toast.add({
      title: 'Archive Failed',
      description: `Could not archive ${item.sku}.`,
      color: 'red'
    })
  }
}

async function handleRestore(item: InventoryItem): Promise<void> {
  try {
    await erpStore.restoreInventory(item.id)
    toast.add({
      title: 'Product Restored',
      description: `${item.sku} is active again.`,
      color: 'emerald'
    })
  } catch {
    toast.add({
      title: 'Restore Failed',
      description: `Could not restore ${item.sku}.`,
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
          Inventory
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Search product catalog naturally, monitor stock levels, and archive discontinued SKUs.
        </p>
      </div>

      <UButton
        color="emerald"
        variant="solid"
        size="sm"
        icon="i-heroicons-plus"
        @click="openCreateModal"
      >
        Add Product
      </UButton>
    </div>

    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <form class="flex items-center gap-2 w-full sm:max-w-md" @submit.prevent="handleSearch">
        <UInput
          v-model="searchQuery"
          placeholder="Natural language search (e.g. floral arabica beans)..."
          icon="i-heroicons-magnifying-glass"
          size="sm"
          class="flex-1"
        />
        <UButton type="submit" color="gray" variant="outline" size="sm" :loading="erpStore.isLoadingInventory">
          Search
        </UButton>
        <UButton
          v-if="searchQuery"
          type="button"
          color="gray"
          variant="ghost"
          size="sm"
          @click="handleClearSearch"
        >
          Clear
        </UButton>
      </form>

      <div class="flex items-center gap-2.5">
        <UToggle v-model="showArchived" />
        <span class="text-xs font-medium text-gray-600 dark:text-slate-300">Show Archived</span>
      </div>
    </div>

    <UAlert
      v-if="erpStore.inventoryError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="erpStore.inventoryError"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="refreshCatalog">
          Retry
        </UButton>
      </template>
    </UAlert>

    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <div v-if="erpStore.isLoadingInventory" class="space-y-3 py-4">
        <div v-for="n in 4" :key="n" class="h-10 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
      </div>

      <div v-else-if="visibleItems.length === 0" class="py-10 text-center">
        <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No inventory items found</p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
          Add a new product SKU or toggle Show Archived to view inactive items.
        </p>
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
          <UBadge
            :color="row.stock_quantity <= 10 ? 'amber' : 'gray'"
            variant="soft"
            size="xs"
            class="tabular-nums"
          >
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
              :loading="Boolean(erpStore.mutatingInventoryIds[row.id])"
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
              :loading="Boolean(erpStore.mutatingInventoryIds[row.id])"
              @click="handleRestore(row)"
            >
              Restore
            </UButton>
          </div>
        </template>
      </UTable>
    </UCard>

    <UModal v-model="isCreateOpen">
      <UCard class="bg-white dark:bg-slate-900">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">Add Product SKU</h2>
            <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isCreateOpen = false" />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitCreateProduct">
          <UAlert
            v-if="createError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="createError"
          />

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="SKU" required>
              <UInput v-model="productDraft.sku" placeholder="COFFEE-ROAST-1KG" required />
            </UFormGroup>
            <UFormGroup label="Category">
              <UInput v-model="productDraft.category" placeholder="Coffee Beans" />
            </UFormGroup>
          </div>

          <UFormGroup label="Product Name" required>
            <UInput v-model="productDraft.name" placeholder="Specialty Highland Arabica (1kg)" required />
          </UFormGroup>

          <UFormGroup label="Description" required>
            <UTextarea v-model="productDraft.description" placeholder="Tasting notes, origin, and packaging details" required />
          </UFormGroup>

          <div class="grid grid-cols-2 gap-3">
            <UFormGroup label="Unit Price (VND)" required>
              <UInput v-model.number="productDraft.price_vnd" type="number" min="0" required />
            </UFormGroup>
            <UFormGroup label="Initial Stock" required>
              <UInput v-model.number="productDraft.stock_quantity" type="number" min="0" required />
            </UFormGroup>
          </div>

          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="isCreateOpen = false">Cancel</UButton>
            <UButton type="submit" color="emerald" variant="solid" :loading="erpStore.isCreatingInventory">
              Save Product
            </UButton>
          </div>
        </form>
      </UCard>
    </UModal>
  </div>
</template>
