import { defineStore } from 'pinia'
import { computed } from 'vue'
import { useErpStore } from '~/stores/erp'

export { useErpStore }

export const useInventoryStore = defineStore('inventory', () => {
  const erpStore = useErpStore()

  const items = computed(() => erpStore.inventory)
  const inventory = computed(() => erpStore.inventory)
  const isLoading = computed(() => erpStore.isLoadingInventory)
  const lastSearchQuery = computed(() => erpStore.lastSearchQuery)

  async function fetchInventory(): Promise<void> {
    await erpStore.fetchInventory()
  }

  async function searchInventorySemantic(query: string): Promise<void> {
    await erpStore.searchInventorySemantic(query)
  }

  return {
    items,
    inventory,
    isLoading,
    lastSearchQuery,
    fetchInventory,
    searchInventorySemantic
  }
})
