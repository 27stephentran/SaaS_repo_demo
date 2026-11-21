<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useErpStore } from '~/stores/erp'
import type {
  Company,
  ManagerCreateResponse,
  SePayLinkedBankAccount
} from '~/types/erp'

definePageMeta({
  layout: 'dashboard'
})

const erpStore = useErpStore()
const toast = useToast()

const showArchived = ref<boolean>(false)
const searchQuery = ref<string>('')

const isCreateModalOpen = ref<boolean>(false)
const isBankModalOpen = ref<boolean>(false)
const isManagerModalOpen = ref<boolean>(false)

const createError = ref<string | null>(null)
const bankError = ref<string | null>(null)
const managerError = ref<string | null>(null)

const isSavingBank = ref<boolean>(false)
const isCreatingManager = ref<boolean>(false)
const mutatingCompanyIds = ref<Record<string, boolean>>({})

const activeCompanyForEdit = ref<Company | null>(null)
const lastCreatedManager = ref<ManagerCreateResponse | null>(null)
const selectedLinkedAccountId = ref<string>('')

const companyDraft = reactive({
  name: '',
  code: '',
  bank_code: '',
  bank_name: '',
  bank_account_number: '',
  bank_account_name: '',
  sepay_sub_account: '',
  sepay_memo_prefix: '',
  sepay_bank_account_id: ''
})

const bankDraft = reactive({
  bank_code: '',
  bank_name: '',
  bank_account_number: '',
  bank_account_name: '',
  sepay_sub_account: '',
  sepay_memo_prefix: '',
  sepay_bank_account_id: ''
})

const managerDraft = reactive({
  full_name: '',
  email: '',
  username: '',
  password: ''
})

async function loadCompaniesWorkspace(): Promise<void> {
  await Promise.all([
    erpStore.fetchCompanies(showArchived.value),
    erpStore.fetchBankCatalog()
  ])
}

onMounted(async () => {
  await loadCompaniesWorkspace()
})

watch(showArchived, async () => {
  await erpStore.fetchCompanies(showArchived.value)
})

const bankOptions = computed(() =>
  erpStore.bankCatalog.banks.map((b) => ({
    label: `${b.code} — ${b.short_name || b.name}`,
    value: b.code,
    bankName: b.name
  }))
)

const linkedAccountOptions = computed(() =>
  erpStore.bankCatalog.sepay_linked_accounts.map((acc) => ({
    label: `${acc.bank_code} · ${acc.account_number} (${acc.account_holder_name || acc.bank_short_name || 'SePay'})`,
    value: acc.id
  }))
)

const filteredCompanies = computed<Company[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return erpStore.companies.filter((company) => {
    if (!showArchived.value && company.is_active === false) {
      return false
    }
    if (!q) return true
    return (
      company.name.toLowerCase().includes(q) ||
      company.code.toLowerCase().includes(q) ||
      company.bank_code.toLowerCase().includes(q) ||
      company.bank_account_number.toLowerCase().includes(q)
    )
  })
})

function findBankName(code: string): string {
  const found = erpStore.bankCatalog.banks.find(
    (b) => b.code.toUpperCase() === code.toUpperCase()
  )
  return found?.name || code
}

function applyLinkedAccount(accountId: string, target: typeof companyDraft | typeof bankDraft): void {
  const acc: SePayLinkedBankAccount | undefined = erpStore.bankCatalog.sepay_linked_accounts.find(
    (a) => a.id === accountId
  )
  if (!acc) return
  target.bank_code = acc.bank_code
  target.bank_name = acc.bank_short_name || findBankName(acc.bank_code)
  target.bank_account_number = acc.account_number
  target.bank_account_name = acc.account_holder_name || target.bank_account_name
  target.sepay_bank_account_id = acc.id
}

function openCreateCompanyModal(): void {
  createError.value = null
  selectedLinkedAccountId.value = ''
  companyDraft.name = ''
  companyDraft.code = ''
  companyDraft.bank_code = bankOptions.value[0]?.value || ''
  companyDraft.bank_name = bankOptions.value[0]?.bankName || ''
  companyDraft.bank_account_number = ''
  companyDraft.bank_account_name = ''
  companyDraft.sepay_sub_account = ''
  companyDraft.sepay_memo_prefix = ''
  companyDraft.sepay_bank_account_id = ''
  isCreateModalOpen.value = true
}

function openEditBankModal(company: Company): void {
  bankError.value = null
  activeCompanyForEdit.value = company
  selectedLinkedAccountId.value = ''
  bankDraft.bank_code = company.bank_code
  bankDraft.bank_name = company.bank_name || findBankName(company.bank_code)
  bankDraft.bank_account_number = company.bank_account_number
  bankDraft.bank_account_name = company.bank_account_name
  bankDraft.sepay_sub_account = company.sepay_sub_account || ''
  bankDraft.sepay_memo_prefix = company.sepay_memo_prefix || ''
  bankDraft.sepay_bank_account_id = company.sepay_bank_account_id || ''
  isBankModalOpen.value = true
}

function openCreateManagerModal(company: Company): void {
  managerError.value = null
  activeCompanyForEdit.value = company
  managerDraft.full_name = ''
  managerDraft.email = ''
  managerDraft.username = ''
  managerDraft.password = ''
  isManagerModalOpen.value = true
}

async function submitCreateCompany(): Promise<void> {
  createError.value = null
  if (
    !companyDraft.name.trim() ||
    !companyDraft.code.trim() ||
    !companyDraft.bank_code.trim() ||
    !companyDraft.bank_account_number.trim() ||
    !companyDraft.bank_account_name.trim()
  ) {
    createError.value = 'Company name, code, bank, account number, and account holder name are required.'
    return
  }

  try {
    const created = await erpStore.createCompany({
      name: companyDraft.name.trim(),
      code: companyDraft.code.trim().toUpperCase(),
      bank_code: companyDraft.bank_code.trim().toUpperCase(),
      bank_name: companyDraft.bank_name.trim() || findBankName(companyDraft.bank_code),
      bank_account_number: companyDraft.bank_account_number.trim(),
      bank_account_name: companyDraft.bank_account_name.trim(),
      sepay_sub_account: companyDraft.sepay_sub_account.trim(),
      sepay_memo_prefix: companyDraft.sepay_memo_prefix.trim(),
      sepay_bank_account_id: companyDraft.sepay_bank_account_id.trim() || null
    })
    isCreateModalOpen.value = false
    toast.add({
      title: 'Company Provisioned',
      description: `${created.name} (${created.code}) linked to ${created.bank_code}.`,
      color: 'emerald'
    })
  } catch (err) {
    createError.value =
      err && typeof err === 'object' && 'data' in err && typeof (err as { data?: { detail?: unknown } }).data?.detail === 'string'
        ? (err as { data: { detail: string } }).data.detail
        : 'Unable to create company. Verify unique code and bank details.'
  }
}

async function submitUpdateBank(): Promise<void> {
  if (!activeCompanyForEdit.value) return
  bankError.value = null
  if (!bankDraft.bank_code.trim() || !bankDraft.bank_account_number.trim() || !bankDraft.bank_account_name.trim()) {
    bankError.value = 'Bank code, account number, and account holder name are required.'
    return
  }
  isSavingBank.value = true
  try {
    const updated = await erpStore.updateCompanyBank(activeCompanyForEdit.value.id, {
      bank_code: bankDraft.bank_code.trim().toUpperCase(),
      bank_name: bankDraft.bank_name.trim() || findBankName(bankDraft.bank_code),
      bank_account_number: bankDraft.bank_account_number.trim(),
      bank_account_name: bankDraft.bank_account_name.trim(),
      sepay_sub_account: bankDraft.sepay_sub_account.trim(),
      sepay_memo_prefix: bankDraft.sepay_memo_prefix.trim(),
      sepay_bank_account_id: bankDraft.sepay_bank_account_id.trim() || null
    })
    isBankModalOpen.value = false
    toast.add({
      title: 'Bank Configuration Updated',
      description: `${updated.name} now routes VietQR to ${updated.bank_code} ${updated.bank_account_number}.`,
      color: 'emerald'
    })
  } catch {
    bankError.value = 'Unable to update company banking details.'
  } finally {
    isSavingBank.value = false
  }
}

async function submitCreateManager(): Promise<void> {
  if (!activeCompanyForEdit.value) return
  managerError.value = null
  if (managerDraft.full_name.trim().length < 2) {
    managerError.value = 'Manager full name is required.'
    return
  }
  if (managerDraft.password && managerDraft.password.length < 8) {
    managerError.value = 'Optional password must be at least 8 characters.'
    return
  }
  isCreatingManager.value = true
  try {
    const res = await erpStore.createManager(activeCompanyForEdit.value.id, {
      full_name: managerDraft.full_name.trim(),
      ...(managerDraft.email.trim() ? { email: managerDraft.email.trim() } : {}),
      ...(managerDraft.username.trim() ? { username: managerDraft.username.trim() } : {}),
      ...(managerDraft.password ? { password: managerDraft.password } : {})
    })
    isManagerModalOpen.value = false
    lastCreatedManager.value = res
    toast.add({
      title: 'Manager Provisioned',
      description: `${res.full_name} (${res.username}) assigned to ${activeCompanyForEdit.value.name}.`,
      color: 'emerald'
    })
  } catch (err) {
    managerError.value =
      err && typeof err === 'object' && 'data' in err && typeof (err as { data?: { detail?: unknown } }).data?.detail === 'string'
        ? (err as { data: { detail: string } }).data.detail
        : 'Unable to provision manager account.'
  } finally {
    isCreatingManager.value = false
  }
}

async function toggleArchiveCompany(company: Company): Promise<void> {
  mutatingCompanyIds.value[company.id] = true
  try {
    if (company.is_active !== false) {
      await erpStore.softDeleteCompany(company.id)
      toast.add({ title: 'Company Archived', description: `${company.name} deactivated.`, color: 'amber' })
    } else {
      await erpStore.restoreCompany(company.id)
      toast.add({ title: 'Company Restored', description: `${company.name} reactivated.`, color: 'emerald' })
    }
  } catch {
    toast.add({ title: 'Action Failed', description: `Could not update ${company.name}.`, color: 'red' })
  } finally {
    delete mutatingCompanyIds.value[company.id]
  }
}

async function copyText(value: string, label: string): Promise<void> {
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    await navigator.clipboard.writeText(value)
    toast.add({ title: 'Copied', description: `${label} copied to clipboard.`, color: 'emerald' })
  }
}
</script>

<template>
  <div class="space-y-5">
    <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 class="text-2xl font-bold tracking-tight text-gray-900 dark:text-slate-100">
          Companies &amp; SePay Bank Setup
        </h1>
        <p class="text-sm text-gray-500 dark:text-slate-400">
          Provision isolated companies, bind SePay VietQR receiving bank accounts, and assign company managers.
        </p>
      </div>

      <div class="flex items-center gap-2">
        <UButton
          color="gray"
          variant="outline"
          size="sm"
          icon="i-heroicons-arrow-path"
          :loading="erpStore.isLoadingCompanies"
          @click="loadCompaniesWorkspace"
        >
          Refresh
        </UButton>
        <UButton
          v-if="erpStore.isAdmin"
          color="emerald"
          variant="solid"
          size="sm"
          icon="i-heroicons-plus"
          @click="openCreateCompanyModal"
        >
          New Company
        </UButton>
      </div>
    </div>

    <!-- One-Time Manager Credential Card -->
    <UCard
      v-if="lastCreatedManager"
      class="bg-emerald-50/70 dark:bg-emerald-950/30 border-2 border-emerald-500/70 dark:border-emerald-500/50"
    >
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        <div class="space-y-2">
          <div class="flex items-center gap-2">
            <UBadge color="emerald" variant="solid" size="xs">One-Time Manager Credentials</UBadge>
            <span class="text-xs font-medium text-emerald-800 dark:text-emerald-200">
              {{ lastCreatedManager.full_name }}
            </span>
          </div>
          <p class="text-xs text-amber-800 dark:text-amber-300 font-medium">
            Save or share these credentials now. Temporary passwords cannot be retrieved after dismissing this notice.
          </p>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1">
            <div class="rounded-lg bg-white dark:bg-slate-900 border border-emerald-200 dark:border-slate-700 p-2.5">
              <p class="text-[11px] text-gray-500 dark:text-slate-400">Username</p>
              <div class="flex items-center justify-between gap-2 mt-0.5">
                <span class="font-mono text-sm font-semibold text-gray-900 dark:text-slate-100">{{ lastCreatedManager.username }}</span>
                <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-clipboard" @click="copyText(lastCreatedManager.username, 'Username')" />
              </div>
            </div>
            <div class="rounded-lg bg-white dark:bg-slate-900 border border-emerald-200 dark:border-slate-700 p-2.5">
              <p class="text-[11px] text-gray-500 dark:text-slate-400">Email</p>
              <div class="flex items-center justify-between gap-2 mt-0.5">
                <span class="font-mono text-xs font-medium text-gray-900 dark:text-slate-100 truncate">{{ lastCreatedManager.email }}</span>
                <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-clipboard" @click="copyText(lastCreatedManager.email, 'Email')" />
              </div>
            </div>
            <div v-if="lastCreatedManager.temporary_password" class="rounded-lg bg-white dark:bg-slate-900 border border-emerald-200 dark:border-slate-700 p-2.5">
              <p class="text-[11px] text-gray-500 dark:text-slate-400">Temporary Password</p>
              <div class="flex items-center justify-between gap-2 mt-0.5">
                <span class="font-mono text-sm font-semibold text-emerald-700 dark:text-emerald-300">{{ lastCreatedManager.temporary_password }}</span>
                <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-clipboard" @click="copyText(lastCreatedManager.temporary_password, 'Temporary password')" />
              </div>
            </div>
          </div>
        </div>
        <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-x-mark" @click="lastCreatedManager = null">
          Dismiss
        </UButton>
      </div>
    </UCard>

    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
      <div class="w-full sm:max-w-xs">
        <UInput
          v-model="searchQuery"
          placeholder="Search company, code, or bank..."
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
      v-if="erpStore.companiesError"
      color="red"
      variant="soft"
      icon="i-heroicons-exclamation-triangle"
      :title="erpStore.companiesError"
    >
      <template #actions>
        <UButton color="red" variant="ghost" size="xs" @click="loadCompaniesWorkspace">Retry</UButton>
      </template>
    </UAlert>

    <div v-if="erpStore.isLoadingCompanies" class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div v-for="n in 4" :key="n" class="h-44 rounded-xl bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 animate-pulse" />
    </div>

    <UCard
      v-else-if="filteredCompanies.length === 0"
      class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 text-center py-10"
    >
      <p class="text-sm font-medium text-gray-900 dark:text-slate-100">No companies configured yet</p>
      <p class="mt-1 text-xs text-gray-500 dark:text-slate-400">
        Create your first company and connect its SePay receiving bank account to isolate orders, inventory, and staff.
      </p>
      <div v-if="erpStore.isAdmin" class="mt-4">
        <UButton color="emerald" variant="solid" size="sm" icon="i-heroicons-plus" @click="openCreateCompanyModal">
          Create First Company
        </UButton>
      </div>
    </UCard>

    <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <UCard
        v-for="company in filteredCompanies"
        :key="company.id"
        class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800"
      >
        <div class="space-y-4">
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100 truncate">
                  {{ company.name }}
                </h2>
                <UBadge color="gray" variant="soft" size="xs" class="font-mono">
                  {{ company.code }}
                </UBadge>
              </div>
              <p class="text-xs text-gray-500 dark:text-slate-400 font-mono mt-0.5 truncate">
                ID: {{ company.id }}
              </p>
            </div>
            <UBadge :color="company.is_active === false ? 'gray' : 'emerald'" variant="soft" size="xs">
              {{ company.is_active === false ? 'Archived' : 'Active' }}
            </UBadge>
          </div>

          <div class="rounded-lg border border-gray-200 dark:border-slate-800 bg-gray-50 dark:bg-slate-950/60 p-3 space-y-1.5 text-xs">
            <div class="flex items-center justify-between">
              <span class="text-gray-500 dark:text-slate-400">SePay Receiving Bank</span>
              <span class="font-semibold text-gray-900 dark:text-slate-100">
                {{ company.bank_code }} · {{ company.bank_name || findBankName(company.bank_code) }}
              </span>
            </div>
            <div class="flex items-center justify-between">
              <span class="text-gray-500 dark:text-slate-400">Account Number</span>
              <span class="font-mono font-medium text-gray-900 dark:text-slate-100">{{ company.bank_account_number }}</span>
            </div>
            <div class="flex items-center justify-between">
              <span class="text-gray-500 dark:text-slate-400">Account Holder</span>
              <span class="font-medium text-gray-900 dark:text-slate-100">{{ company.bank_account_name }}</span>
            </div>
            <div v-if="company.sepay_sub_account || company.sepay_memo_prefix" class="flex items-center justify-between">
              <span class="text-gray-500 dark:text-slate-400">Sub-Account / Memo Prefix</span>
              <span class="font-mono text-gray-700 dark:text-slate-300">
                {{ company.sepay_sub_account || '—' }} / {{ company.sepay_memo_prefix || '—' }}
              </span>
            </div>
          </div>

          <div v-if="erpStore.isAdmin" class="flex flex-wrap items-center justify-between gap-2 pt-1">
            <div class="flex flex-wrap items-center gap-2">
              <UButton
                size="xs"
                color="emerald"
                variant="soft"
                icon="i-heroicons-user-plus"
                @click="openCreateManagerModal(company)"
              >
                Add Manager
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="outline"
                icon="i-heroicons-building-library"
                @click="openEditBankModal(company)"
              >
                Edit Bank
              </UButton>
              <UButton
                size="xs"
                :color="erpStore.selectedCompanyId === company.id ? 'blue' : 'gray'"
                :variant="erpStore.selectedCompanyId === company.id ? 'solid' : 'ghost'"
                @click="erpStore.setSelectedCompanyId(erpStore.selectedCompanyId === company.id ? null : company.id)"
              >
                {{ erpStore.selectedCompanyId === company.id ? 'Scoped Active' : 'Focus Scope' }}
              </UButton>
            </div>
            <UButton
              size="xs"
              :color="company.is_active === false ? 'emerald' : 'amber'"
              variant="ghost"
              :loading="Boolean(mutatingCompanyIds[company.id])"
              @click="toggleArchiveCompany(company)"
            >
              {{ company.is_active === false ? 'Restore' : 'Archive' }}
            </UButton>
          </div>
        </div>
      </UCard>
    </div>

    <!-- Create Company Modal -->
    <UModal v-model="isCreateModalOpen">
      <UCard class="bg-white dark:bg-slate-900">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">Create Company &amp; SePay Bank</h2>
            <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isCreateModalOpen = false" />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitCreateCompany">
          <UAlert v-if="createError" color="red" variant="soft" icon="i-heroicons-exclamation-triangle" :title="createError" />

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="Company Name" required>
              <UInput v-model="companyDraft.name" placeholder="Saigon Roastery Co." required />
            </UFormGroup>
            <UFormGroup label="Company Code" required>
              <UInput v-model="companyDraft.code" placeholder="SGN-ROAST" required />
            </UFormGroup>
          </div>

          <UFormGroup
            v-if="linkedAccountOptions.length > 0"
            label="Quick-Fill from SePay Linked Accounts"
            help="Optional: select an existing SePay API v2 linked bank account."
          >
            <USelectMenu
              v-model="selectedLinkedAccountId"
              :options="linkedAccountOptions"
              value-attribute="value"
              option-attribute="label"
              placeholder="Choose SePay linked account..."
              @update:model-value="(val: string) => applyLinkedAccount(val, companyDraft)"
            />
          </UFormGroup>

          <UFormGroup label="Bank (VietQR / SePay)" required>
            <USelectMenu
              v-model="companyDraft.bank_code"
              :options="bankOptions"
              value-attribute="value"
              option-attribute="label"
              searchable
              searchable-placeholder="Search Vietnam bank..."
              placeholder="Select bank"
              @update:model-value="(val: string) => { companyDraft.bank_name = findBankName(val) }"
            />
          </UFormGroup>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="Bank Account Number" required>
              <UInput v-model="companyDraft.bank_account_number" placeholder="0123456789" required />
            </UFormGroup>
            <UFormGroup label="Account Holder Name" required>
              <UInput v-model="companyDraft.bank_account_name" placeholder="CONG TY TNHH SAIGON ROAST" required />
            </UFormGroup>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="SePay Sub-Account (Optional)">
              <UInput v-model="companyDraft.sepay_sub_account" placeholder="VA001" />
            </UFormGroup>
            <UFormGroup label="Payment Memo Prefix (Optional)">
              <UInput v-model="companyDraft.sepay_memo_prefix" placeholder="SGN" />
            </UFormGroup>
          </div>

          <div class="flex justify-end gap-2 pt-2">
            <UButton color="gray" variant="ghost" @click="isCreateModalOpen = false">Cancel</UButton>
            <UButton type="submit" color="emerald" variant="solid" :loading="erpStore.isCreatingCompany">
              Create Company
            </UButton>
          </div>
        </form>
      </UCard>
    </UModal>

    <!-- Edit Bank Modal -->
    <UModal v-model="isBankModalOpen">
      <UCard class="bg-white dark:bg-slate-900">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">
              Edit Bank — {{ activeCompanyForEdit?.name }}
            </h2>
            <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isBankModalOpen = false" />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitUpdateBank">
          <UAlert v-if="bankError" color="red" variant="soft" icon="i-heroicons-exclamation-triangle" :title="bankError" />

          <UFormGroup label="Bank (VietQR / SePay)" required>
            <USelectMenu
              v-model="bankDraft.bank_code"
              :options="bankOptions"
              value-attribute="value"
              option-attribute="label"
              searchable
              searchable-placeholder="Search Vietnam bank..."
              @update:model-value="(val: string) => { bankDraft.bank_name = findBankName(val) }"
            />
          </UFormGroup>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="Bank Account Number" required>
              <UInput v-model="bankDraft.bank_account_number" required />
            </UFormGroup>
            <UFormGroup label="Account Holder Name" required>
              <UInput v-model="bankDraft.bank_account_name" required />
            </UFormGroup>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="SePay Sub-Account">
              <UInput v-model="bankDraft.sepay_sub_account" />
            </UFormGroup>
            <UFormGroup label="Payment Memo Prefix">
              <UInput v-model="bankDraft.sepay_memo_prefix" />
            </UFormGroup>
          </div>

          <div class="flex justify-end gap-2 pt-2">
            <UButton color="gray" variant="ghost" @click="isBankModalOpen = false">Cancel</UButton>
            <UButton type="submit" color="emerald" variant="solid" :loading="isSavingBank">
              Save Bank Settings
            </UButton>
          </div>
        </form>
      </UCard>
    </UModal>

    <!-- Create Manager Modal -->
    <UModal v-model="isManagerModalOpen">
      <UCard class="bg-white dark:bg-slate-900">
        <template #header>
          <div class="flex items-center justify-between">
            <h2 class="text-base font-semibold text-gray-900 dark:text-slate-100">
              Assign Manager — {{ activeCompanyForEdit?.name }}
            </h2>
            <UButton color="gray" variant="ghost" icon="i-heroicons-x-mark" @click="isManagerModalOpen = false" />
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="submitCreateManager">
          <UAlert v-if="managerError" color="red" variant="soft" icon="i-heroicons-exclamation-triangle" :title="managerError" />

          <UFormGroup label="Manager Full Name" required>
            <UInput v-model="managerDraft.full_name" placeholder="Tran Thi Manager" required />
          </UFormGroup>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <UFormGroup label="Email (Optional)">
              <UInput v-model="managerDraft.email" type="email" placeholder="manager@company.vn" />
            </UFormGroup>
            <UFormGroup label="Username (Optional)">
              <UInput v-model="managerDraft.username" placeholder="Auto-generated if blank" />
            </UFormGroup>
          </div>

          <UFormGroup label="Temporary Password (Optional)" help="Leave blank to auto-generate a secure one-time password.">
            <UInput v-model="managerDraft.password" type="password" placeholder="Auto-generate (recommended)" />
          </UFormGroup>

          <div class="flex justify-end gap-2 pt-2">
            <UButton color="gray" variant="ghost" @click="isManagerModalOpen = false">Cancel</UButton>
            <UButton type="submit" color="emerald" variant="solid" :loading="isCreatingManager">
              Provision Manager
            </UButton>
          </div>
        </form>
      </UCard>
    </UModal>
  </div>
</template>
