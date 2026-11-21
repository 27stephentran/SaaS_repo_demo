<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useDemoStore } from '~/stores/demo'
import { useErpStore } from '~/stores/erp'

const route = useRoute()
const erpStore = useErpStore()
const demoStore = useDemoStore()
const toast = useToast()

const isMobileNavOpen = ref(false)

const isDemoMode = computed<boolean>(
  () => route.path === '/demo' || route.path.startsWith('/demo/')
)

const basePath = computed<string>(() => (isDemoMode.value ? '/demo' : ''))

interface NavItem {
  label: string
  to: string
  icon: string
}

const navItems = computed<NavItem[]>(() => {
  if (isDemoMode.value) {
    return [
      { label: 'Overview', to: '/demo', icon: 'i-heroicons-chart-bar-square' },
      { label: 'Orders', to: '/demo/orders', icon: 'i-heroicons-clipboard-document-list' },
      { label: 'Inventory', to: '/demo/inventory', icon: 'i-heroicons-cube' },
      { label: 'Customers', to: '/demo/customers', icon: 'i-heroicons-users' },
      { label: 'Activity', to: '/demo/activity', icon: 'i-heroicons-bolt' }
    ]
  }

  if (erpStore.isStaff) {
    return [
      { label: 'My Work', to: '/my-work', icon: 'i-heroicons-clipboard-document-check' }
    ]
  }

  if (erpStore.isManager) {
    return [
      { label: 'Overview', to: '/', icon: 'i-heroicons-chart-bar-square' },
      { label: 'Staff', to: '/staff', icon: 'i-heroicons-identification' },
      { label: 'Omnichannel Chats', to: '/chats', icon: 'i-heroicons-chat-bubble-left-right' },
      { label: 'Orders', to: '/orders', icon: 'i-heroicons-clipboard-document-list' },
      { label: 'Inventory', to: '/inventory', icon: 'i-heroicons-cube' },
      { label: 'Customers', to: '/customers', icon: 'i-heroicons-users' },
      { label: 'Activity', to: '/activity', icon: 'i-heroicons-bolt' }
    ]
  }

  return [
    { label: 'Overview', to: basePath.value || '/', icon: 'i-heroicons-chart-bar-square' },
    { label: 'Companies & Bank Setup', to: '/companies', icon: 'i-heroicons-building-office-2' },
    { label: 'Orders', to: '/orders', icon: 'i-heroicons-clipboard-document-list' },
    { label: 'Inventory', to: '/inventory', icon: 'i-heroicons-cube' },
    { label: 'Customers', to: '/customers', icon: 'i-heroicons-users' },
    { label: 'Staff & Work', to: '/staff', icon: 'i-heroicons-identification' },
    { label: 'Omnichannel Chats', to: '/chats', icon: 'i-heroicons-chat-bubble-left-right' },
    { label: 'Activity', to: '/activity', icon: 'i-heroicons-bolt' }
  ]
})

const activeSectionTitle = computed<string>(() => {
  const path = route.path
  if (path.endsWith('/companies')) return 'Companies & Bank Setup'
  if (path.endsWith('/staff')) return erpStore.isAdmin ? 'Staff & Work' : 'Staff'
  if (path.endsWith('/chats')) return 'Omnichannel Chats'
  if (path.endsWith('/my-work')) return 'My Work'
  if (path.endsWith('/change-password')) return 'Change Password'
  if (path.endsWith('/orders')) return 'Orders'
  if (path.endsWith('/inventory')) return 'Inventory'
  if (path.endsWith('/customers')) return 'Customers'
  if (path.endsWith('/activity')) return 'Activity'
  return 'Overview'
})

const companySwitcherOptions = computed(() => [
  { label: 'All Companies', value: '' },
  ...erpStore.companies
    .filter((c) => c.is_active !== false)
    .map((c) => ({
      label: `${c.name} (${c.code})`,
      value: c.id
    }))
])

const selectedCompanyModel = computed({
  get: () => erpStore.selectedCompanyId || '',
  set: (value: string) => {
    erpStore.setSelectedCompanyId(value || null)
  }
})

const operatorDisplayName = computed<string>(
  () =>
    erpStore.currentUser?.full_name ||
    erpStore.currentUser?.username ||
    erpStore.currentUser?.email ||
    'Operator'
)

const operatorMenuItems = computed(() => [
  [
    {
      label: operatorDisplayName.value,
      slot: 'account',
      disabled: true
    }
  ],
  [
    {
      label: 'Change Password',
      icon: 'i-heroicons-key',
      click: async () => {
        await navigateTo('/change-password')
      }
    },
    {
      label: 'Sign Out',
      icon: 'i-heroicons-arrow-right-on-rectangle',
      click: handleSignOut
    }
  ]
])

onMounted(async () => {
  if (isDemoMode.value) {
    demoStore.resetDemoState()
    return
  }
  if (erpStore.isAdmin && erpStore.companies.length === 0) {
    await erpStore.fetchCompanies()
  }
})

watch(
  () => erpStore.isAdmin,
  async (admin) => {
    if (admin && !isDemoMode.value && erpStore.companies.length === 0) {
      await erpStore.fetchCompanies()
    }
  }
)

function isActiveNav(targetPath: string): boolean {
  if (targetPath === '/' || targetPath === '/demo') {
    return route.path === targetPath
  }
  return route.path === targetPath || route.path.startsWith(`${targetPath}/`)
}

function handleResetSandbox(): void {
  demoStore.resetDemoState()
  toast.add({
    title: 'Sandbox Reset',
    description: 'Demo orders, catalog, and customer records restored to initial snapshot.',
    color: 'emerald'
  })
}

async function handleSignOut(): Promise<void> {
  await erpStore.logout()
  toast.add({
    title: 'Signed Out',
    description: 'Operator session ended cleanly.',
    color: 'gray'
  })
  await navigateTo('/login')
}
</script>

<template>
  <div class="min-h-screen flex bg-gray-50 dark:bg-slate-950 text-gray-900 dark:text-slate-100">
    <!-- Persistent Desktop Sidebar -->
    <aside class="hidden lg:flex lg:w-64 lg:flex-col lg:fixed lg:inset-y-0 border-r border-gray-200 dark:border-slate-800 bg-white dark:bg-slate-900">
      <div class="h-14 flex items-center gap-2.5 px-5 border-b border-gray-200 dark:border-slate-800">
        <div class="h-7 w-7 rounded-lg bg-emerald-600 flex items-center justify-center text-white font-bold text-sm">
          N
        </div>
        <div class="min-w-0">
          <p class="text-sm font-semibold tracking-tight text-gray-900 dark:text-slate-100 truncate">
            NexusERP
          </p>
          <p class="text-[11px] text-gray-500 dark:text-slate-400 truncate">
            {{ isDemoMode ? 'Interactive Sandbox' : 'Operations Control' }}
          </p>
        </div>
      </div>

      <nav class="flex-1 px-3 py-4 space-y-1 overflow-y-auto" aria-label="Main navigation">
        <NuxtLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
          :class="
            isActiveNav(item.to)
              ? 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300'
              : 'text-gray-600 dark:text-slate-400 hover:bg-gray-100 dark:hover:bg-slate-800/70 hover:text-gray-900 dark:hover:text-slate-100'
          "
        >
          <UIcon :name="item.icon" class="h-4 w-4 shrink-0" />
          <span class="truncate">{{ item.label }}</span>
        </NuxtLink>
      </nav>

      <div class="p-3 border-t border-gray-200 dark:border-slate-800 space-y-2">
        <NuxtLink
          v-if="isDemoMode"
          to="/login"
          class="flex items-center justify-center gap-2 w-full rounded-lg border border-gray-200 dark:border-slate-700 px-3 py-2 text-xs font-medium text-gray-700 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-slate-800 transition-colors"
        >
          <UIcon name="i-heroicons-arrow-right-on-rectangle" class="h-4 w-4" />
          <span>Operator Sign In</span>
        </NuxtLink>
        <div v-else class="px-2 py-1 space-y-1">
          <div class="flex items-center justify-between text-xs text-gray-500 dark:text-slate-400">
            <span>Role</span>
            <UBadge color="emerald" variant="subtle" size="xs" class="uppercase">
              {{ erpStore.currentUser?.role || 'staff' }}
            </UBadge>
          </div>
          <div
            v-if="!erpStore.isAdmin && erpStore.activeCompanyName"
            class="flex items-center justify-between text-[11px] text-gray-500 dark:text-slate-400"
          >
            <span>Company</span>
            <span class="font-medium text-gray-700 dark:text-slate-200 truncate max-w-[130px]">
              {{ erpStore.activeCompanyName }}
            </span>
          </div>
        </div>
      </div>
    </aside>

    <!-- Mobile Sidebar Drawer -->
    <USlideover v-model="isMobileNavOpen" side="left" :ui="{ width: 'max-w-xs' }">
      <div class="flex h-full flex-col bg-white dark:bg-slate-900">
        <div class="h-14 flex items-center justify-between px-4 border-b border-gray-200 dark:border-slate-800">
          <span class="text-sm font-semibold text-gray-900 dark:text-slate-100">NexusERP</span>
          <UButton
            color="gray"
            variant="ghost"
            icon="i-heroicons-x-mark"
            aria-label="Close navigation"
            @click="isMobileNavOpen = false"
          />
        </div>
        <div v-if="!isDemoMode && erpStore.isAdmin" class="p-3 border-b border-gray-200 dark:border-slate-800">
          <p class="text-[11px] font-medium text-gray-500 dark:text-slate-400 mb-1.5">Company Scope</p>
          <USelectMenu
            v-model="selectedCompanyModel"
            :options="companySwitcherOptions"
            value-attribute="value"
            option-attribute="label"
            size="xs"
          />
        </div>
        <nav class="flex-1 p-3 space-y-1 overflow-y-auto">
          <NuxtLink
            v-for="item in navItems"
            :key="item.to"
            :to="item.to"
            class="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors"
            :class="
              isActiveNav(item.to)
                ? 'bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300'
                : 'text-gray-600 dark:text-slate-400 hover:bg-gray-100 dark:hover:bg-slate-800/70'
            "
            @click="isMobileNavOpen = false"
          >
            <UIcon :name="item.icon" class="h-4 w-4 shrink-0" />
            <span>{{ item.label }}</span>
          </NuxtLink>
        </nav>
      </div>
    </USlideover>

    <!-- Main Workspace Shell -->
    <div class="flex-1 lg:pl-64 flex flex-col min-w-0">
      <header class="sticky top-0 z-20 h-14 border-b border-gray-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur px-4 sm:px-6 flex items-center justify-between gap-3">
        <div class="flex items-center gap-3 min-w-0">
          <UButton
            class="lg:hidden"
            color="gray"
            variant="ghost"
            icon="i-heroicons-bars-3"
            aria-label="Open navigation"
            @click="isMobileNavOpen = true"
          />
          <div class="flex items-center gap-2 text-sm truncate">
            <span class="text-gray-400 dark:text-slate-500 hidden sm:inline">
              {{ isDemoMode ? 'Demo Workspace' : 'Operations' }}
            </span>
            <span class="text-gray-300 dark:text-slate-600 hidden sm:inline">/</span>
            <span class="font-semibold text-gray-900 dark:text-slate-100 truncate">
              {{ activeSectionTitle }}
            </span>
          </div>
        </div>

        <div class="flex items-center gap-2.5 shrink-0">
          <!-- Admin Global Company Switcher -->
          <div v-if="!isDemoMode && erpStore.isAdmin" class="hidden sm:block w-52">
            <USelectMenu
              v-model="selectedCompanyModel"
              :options="companySwitcherOptions"
              value-attribute="value"
              option-attribute="label"
              size="xs"
              icon="i-heroicons-building-office"
              placeholder="All Companies"
            />
          </div>

          <!-- Manager / Staff Locked Company Badge -->
          <UBadge
            v-else-if="!isDemoMode && erpStore.activeCompanyName"
            color="blue"
            variant="subtle"
            size="sm"
            class="hidden sm:inline-flex"
          >
            <UIcon name="i-heroicons-lock-closed" class="h-3 w-3 mr-1" />
            {{ erpStore.activeCompanyName }}
          </UBadge>

          <UBadge
            :color="isDemoMode ? 'amber' : 'emerald'"
            variant="subtle"
            size="sm"
          >
            {{ isDemoMode ? 'Sandbox Demo' : (erpStore.currentUser?.tenant_id || 'Workspace') }}
          </UBadge>

          <ThemeSwitcher />

          <template v-if="isDemoMode">
            <UButton
              color="gray"
              variant="ghost"
              size="xs"
              icon="i-heroicons-arrow-path"
              @click="handleResetSandbox"
            >
              <span class="hidden sm:inline">Reset</span>
            </UButton>
            <UButton
              to="/login"
              color="emerald"
              variant="soft"
              size="xs"
            >
              Operator Sign In
            </UButton>
          </template>

          <UDropdown v-else :items="operatorMenuItems" :popper="{ placement: 'bottom-end' }">
            <UButton
              color="gray"
              variant="ghost"
              size="sm"
              icon="i-heroicons-user-circle"
              trailing-icon="i-heroicons-chevron-down"
            >
              <span class="hidden md:inline max-w-[140px] truncate">
                {{ operatorDisplayName }}
              </span>
            </UButton>
            <template #account="{ item }">
              <div class="text-left">
                <p class="text-xs font-medium text-gray-900 dark:text-slate-100 truncate">
                  {{ item.label }}
                </p>
                <p class="text-[11px] text-gray-500 dark:text-slate-400">
                  Tenant: {{ erpStore.currentUser?.tenant_id }} · {{ erpStore.currentUser?.role }}
                </p>
              </div>
            </template>
          </UDropdown>
        </div>
      </header>

      <div
        v-if="isDemoMode"
        class="border-b border-amber-200 dark:border-amber-900/60 bg-amber-50/80 dark:bg-amber-950/30 px-4 sm:px-6 py-2 flex flex-wrap items-center justify-between gap-2 text-xs text-amber-800 dark:text-amber-200"
      >
        <div class="flex items-center gap-2">
          <UIcon name="i-heroicons-beaker" class="h-4 w-4 shrink-0" />
          <span>
            <strong>Sandbox Demo (Resets on Reload):</strong> Explore orders, catalog archiving, and real Telegram notifications without affecting live tenant data.
          </span>
        </div>
        <NuxtLink to="/signup" class="font-semibold underline hover:no-underline">
          Create Live Account →
        </NuxtLink>
      </div>

      <main class="flex-1 p-4 sm:p-6 max-w-7xl w-full mx-auto">
        <slot />
      </main>
    </div>
  </div>
</template>
