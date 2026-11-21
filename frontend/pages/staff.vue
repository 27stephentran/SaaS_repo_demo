<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useErpStore } from '~/stores/erp'
import type { StaffCreateResponse, StaffMemberSummary } from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const toast = useToast()

const searchQuery = ref<string>('')
const isCreateModalOpen = ref<boolean>(false)
const createError = ref<string | null>(null)
const lastCreatedStaff = ref<StaffCreateResponse | null>(null)

const staffDraft = reactive({
  full_name: '',
  telegram_user_id: '',
  email: ''
})

const columns = computed(() => {
  const base = [
    { key: 'full_name', label: 'Staff Member' },
    { key: 'telegram_user_id', label: 'Telegram ID' },
    { key: 'active_orders_count', label: 'Active Orders' },
    { key: 'completed_orders_count', label: 'Completed' },
    { key: 'is_active', label: 'Status' }
  ]
  if (erpStore.isAdmin) {
    base.splice(1, 0, { key: 'company_name', label: 'Company' })
  }
  return base
})

function companyNameFor(companyId?: string | null): string {
  if (!companyId) return '—'
  const match = erpStore.companies.find((c) => c.id === companyId)
  return match ? match.name : companyId.slice(0, 8)
}

onMounted(async () => {
  const tasks: Promise<unknown>[] = [erpStore.fetchStaff()]
  if (erpStore.isAdmin && erpStore.companies.length === 0) {
    tasks.push(erpStore.fetchCompanies())
  }
  await Promise.all(tasks)
})

watch(
  () => erpStore.selectedCompanyId,
  async () => {
    if (erpStore.isAdmin) {
      await erpStore.fetchStaff()
    }
  }
)

const filteredStaff = computed<StaffMemberSummary[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return erpStore.staffMembers
  return erpStore.staffMembers.filter(
    (s) =>
      s.full_name.toLowerCase().includes(q) ||
      (s.username || '').toLowerCase().includes(q) ||
      (s.email || '').toLowerCase().includes(q) ||
      s.telegram_user_id.toLowerCase().includes(q)
  )
})

function openCreateModal(): void {
  createError.value = null
  staffDraft.full_name = ''
  staffDraft.telegram_user_id = ''
  staffDraft.email = ''
  isCreateModalOpen.value = true
}

async function submitCreateStaff(): Promise<void> {
  createError.value = null
  if (staffDraft.full_name.trim().length < 2 || !staffDraft.telegram_user_id.trim()) {
    createError.value = 'Full name and Telegram user/chat ID are required.'
    return
  }

  try {
    const created = await erpStore.createStaff({
      full_name: staffDraft.full_name.trim(),
      telegram_user_id: staffDraft.telegram_user_id.trim(),
      ...(staffDraft.email.trim() ? { email: staffDraft.email.trim() } : {})
    })
    isCreateModalOpen.value = false
    lastCreatedStaff.value = created
    toast.add({
      title: 'Staff Account Created',
      description: `Credentials generated for ${created.full_name} (${created.username}).`,
      color: 'emerald'
    })
  } catch (err) {
    createError.value =
      err && typeof err === 'object' && 'data' in err && typeof (err as { data?: { detail?: unknown } }).data?.detail === 'string'
        ? (err as { data: { detail: string } }).data.detail
        : 'Unable to create staff account.'
  }
}

async function copyCredential(value: string, label: string): Promise<void> {
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    await navigator.clipboard.writeText(value)
    toast.add({
      title: 'Copied',
      description: `${label} copied to clipboard.`,
      color: 'emerald'
    })
  }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
          {{ erpStore.isAdmin ? 'Staff & Workload Overview' : 'Company Staff & Onboarding' }}
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          {{
            erpStore.isManager
              ? 'Create fulfillment staff accounts with auto-generated credentials and track work queues.'
              : 'Monitor staff fulfillment workloads across companies. Staff creation is performed by each company manager.'
          }}
        </p>
      </div>

      <div class="flex items-center gap-2">
        <UButton
          color="gray"
          variant="outline"
          size="sm"
          icon="i-heroicons-arrow-path"
          :loading="erpStore.isLoadingStaff"
          @click="erpStore.fetchStaff()"
        >
          Refresh
        </UButton>
        <UButton
          v-if="erpStore.isManager"
          color="emerald"
          variant="solid"
          size="sm"
          icon="i-heroicons-user-plus"
          @click="openCreateModal"
        >
          Create Staff
        </UButton>
      </div>
    </div>

    <!-- High-Visibility One-Time Credentials Card (Manager only after creation) -->
    <UCard
      v-if="lastCreatedStaff"
      class="bg-emerald-50/80 dark:bg-emerald-950/35 border-2 border-emerald-500 dark:border-emerald-500/60 shadow-sm"
    >
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div class="space-y-2.5 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <UBadge color="emerald" variant="solid" size="xs">One-Time Staff Credentials</UBadge>
            <span class="text-sm font-semibold text-gray-900 dark:text-slate-100">
              {{ lastCreatedStaff.full_name }}
            </span>
          </div>

          <UAlert
            color="amber"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            title="Shown Once — Staff Must Change Password on First Login"
            description="Copy and send these credentials to the staff member now. The temporary password will never be displayed again."
          />

          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
            <div class="rounded-lg bg-white dark:bg-slate-900 border border-emerald-200 dark:border-slate-700 p-3">
              <p class="text-[11px] font-medium text-gray-500 dark:text-slate-400">Login Username</p>
              <div class="flex items-center justify-between gap-2 mt-1">
                <span class="font-mono text-sm font-bold text-gray-900 dark:text-slate-100">
                  {{ lastCreatedStaff.username }}
                </span>
                <UButton
                  color="gray"
                  variant="ghost"
                  size="xs"
                  icon="i-heroicons-clipboard-document"
                  @click="copyCredential(lastCreatedStaff.username, 'Username')"
                />
              </div>
            </div>

            <div class="rounded-lg bg-white dark:bg-slate-900 border border-emerald-200 dark:border-slate-700 p-3">
              <p class="text-[11px] font-medium text-gray-500 dark:text-slate-400">Temporary Password</p>
              <div class="flex items-center justify-between gap-2 mt-1">
                <span class="font-mono text-sm font-bold text-emerald-700 dark:text-emerald-300">
                  {{ lastCreatedStaff.temporary_password }}
                </span>
                <UButton
                  color="gray"
                  variant="ghost"
                  size="xs"
                  icon="i-heroicons-clipboard-document"
                  @click="copyCredential(lastCreatedStaff.temporary_password, 'Temporary password')"
                />
              </div>
            </div>

            <div class="rounded-lg bg-white dark:bg-slate-900 border border-emerald-200 dark:border-slate-700 p-3">
              <p class="text-[11px] font-medium text-gray-500 dark:text-slate-400">Tenant &amp; Email</p>
              <p class="font-mono text-xs text-gray-700 dark:text-slate-300 mt-1 truncate">
                {{ lastCreatedStaff.tenant_id }}
              </p>
              <p class="font-mono text-xs text-gray-500 dark:text-slate-400 truncate">
                {{ lastCreatedStaff.email }}
              </p>
            </div>
          </div>
        </div>

        <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-x-mark" @click="lastCreatedStaff = null">
          Done
        </UButton>
      </div>
    </UCard>

    <div class="w-full sm:max-w-xs">
      <UInput
        v-model="searchQuery"
        placeholder="Search staff name, username, Telegram..."
        icon="i-heroicons-magnifying-glass"
        size="sm"
      />
    </div>

    <UAlert
      v-if="erpStore.staffError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="erpStore.staffError"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="erpStore.fetchStaff()">Retry</UButton>
      </template>
    </UAlert>

    <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800">
      <div v-if="erpStore.isLoadingStaff" class="space-y-3 py-4">
        <div v-for="n in 4" :key="n" class="h-10 rounded bg-gray-100 dark:bg-slate-800 animate-pulse" />
      </div>

      <div v-else-if="filteredStaff.length === 0" class="py-10 text-center">
        <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No staff members found</p>
        <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
          {{
            erpStore.isManager
              ? 'Create a staff account to auto-generate login credentials and route Telegram fulfillment tasks.'
              : 'No staff records match the current company filter.'
          }}
        </p>
        <div v-if="erpStore.isManager" class="mt-4">
          <UButton color="emerald" variant="solid" size="xs" icon="i-heroicons-user-plus" @click="openCreateModal">
            Create First Staff Member
          </UButton>
        </div>
      </div>

      <UTable v-else :rows="filteredStaff" :columns="columns">
        <template #full_name-data="{ row }">
          <div>
            <p class="font-medium text-gray-900 dark:text-slate-100">{{ row.full_name }}</p>
            <p class="text-xs text-gray-500 dark:text-slate-400 font-mono">
              {{ row.username || row.email || '—' }}
            </p>
          </div>
        </template>
        <template #company_name-data="{ row }">
          <UBadge color="blue" variant="subtle" size="xs">
            {{ companyNameFor(row.company_id) }}
          </UBadge>
        </template>
        <template #telegram_user_id-data="{ row }">
          <span class="font-mono text-xs text-gray-700 dark:text-slate-300">{{ row.telegram_user_id }}</span>
        </template>
        <template #active_orders_count-data="{ row }">
          <UBadge color="blue" variant="soft" size="xs" class="tabular-nums">{{ row.active_orders_count }}</UBadge>
        </template>
        <template #completed_orders_count-data="{ row }">
          <UBadge color="emerald" variant="soft" size="xs" class="tabular-nums">{{ row.completed_orders_count }}</UBadge>
        </template>
        <template #is_active-data="{ row }">
          <div class="flex items-center gap-1.5">
            <UBadge :color="row.is_active ? 'emerald' : 'gray'" variant="soft" size="xs">
              {{ row.is_active ? 'Active' : 'Inactive' }}
            </UBadge>
            <UBadge v-if="row.must_change_password" color="amber" variant="subtle" size="xs">
              Temp Password
            </UBadge>
          </div>
        </template>
      </UTable>
    </UCard>

    <!-- Manager Create Staff Modal -->
    <UModal v-model="isCreateModalOpen">
      <UCard class="bg-white dark:bg-slate-900">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">Create Staff Account</h2>
            <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isCreateModalOpen = false" />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitCreateStaff">
          <UAlert v-if="createError" color="red" variant="soft" icon="i-heroicons-exclamation-triangle" :title="createError" />

          <UFormGroup label="Full Name" required>
            <UInput v-model="staffDraft.full_name" placeholder="Nguyen Van Minh" required />
          </UFormGroup>

          <UFormGroup label="Telegram User / Chat ID" required help="Used for instant order fulfillment alerts.">
            <UInput v-model="staffDraft.telegram_user_id" placeholder="123456789" required />
          </UFormGroup>

          <UFormGroup label="Work Email (Optional)" help="If omitted, a workspace email and username are auto-generated.">
            <UInput v-model="staffDraft.email" type="email" placeholder="minh@company.vn" />
          </UFormGroup>

          <div class="rounded-lg bg-gray-50 dark:bg-slate-950/60 border border-gray-200 dark:border-slate-800 p-3 text-xs text-gray-600 dark:text-slate-400">
            A secure username and one-time temporary password will be generated automatically. The staff member will be required to change their password on first sign-in.
          </div>

          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="isCreateModalOpen = false">Cancel</UButton>
            <UButton type="submit" color="emerald" variant="solid" :loading="erpStore.isCreatingStaff">
              Create Staff &amp; Generate Credentials
            </UButton>
          </div>
        </form>
      </UCard>
    </UModal>
  </div>
</template>
