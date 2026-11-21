<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import { useErpStore } from '~/stores/erp'

const erpStore = useErpStore()
const toast = useToast()

const form = reactive({
  tenant_id: '',
  identifier: '',
  password: ''
})

function resolvePostLoginRoute(role: string, mustChange?: boolean): string {
  if (mustChange) return '/change-password'
  if (role.toLowerCase() === 'staff') return '/my-work'
  return '/'
}

onMounted(async () => {
  erpStore.authError = null
  const session = await erpStore.fetchSession()
  if (session?.authenticated && session.permission_group === 'live_api') {
    await navigateTo(resolvePostLoginRoute(session.role, session.must_change_password))
  }
})

async function handleLogin(): Promise<void> {
  if (!form.tenant_id.trim() || !form.identifier.trim() || !form.password) {
    erpStore.authError = 'Tenant ID, username or email, and password are required.'
    return
  }
  try {
    const user = await erpStore.login({
      tenant_id: form.tenant_id.trim(),
      identifier: form.identifier.trim(),
      password: form.password
    })
    toast.add({
      title: 'Signed In',
      description: `Active workspace: ${user.tenant_id}`,
      color: 'emerald'
    })
    await navigateTo(resolvePostLoginRoute(user.role, user.must_change_password))
  } catch {
    // Error is surfaced cleanly via erpStore.authError
  }
}
</script>

<template>
  <UContainer class="min-h-screen flex flex-col items-center justify-center py-12 px-4">
    <div class="w-full max-w-md space-y-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-2">
          <div class="h-7 w-7 rounded-lg bg-emerald-600 flex items-center justify-center text-white font-bold text-sm">
            N
          </div>
          <span class="text-sm font-semibold text-gray-900 dark:text-slate-100">NexusERP</span>
        </div>
        <ThemeSwitcher />
      </div>

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 shadow-sm">
        <template #header>
          <div class="space-y-1">
            <h1 class="text-xl font-bold text-gray-900 dark:text-slate-100">
              Operator Sign In
            </h1>
            <p class="text-xs text-gray-500 dark:text-slate-400">
              Sign in with your username or email to access your company operations workspace.
            </p>
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="handleLogin">
          <UAlert
            v-if="erpStore.authError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="erpStore.authError"
          />

          <UFormGroup label="Workspace / Tenant ID" name="tenant_id" required>
            <UInput
              v-model="form.tenant_id"
              placeholder="e.g. acme-coffee"
              icon="i-heroicons-building-office-2"
              autocomplete="organization"
              required
            />
          </UFormGroup>

          <UFormGroup label="Username or Email" name="identifier" required>
            <UInput
              v-model="form.identifier"
              type="text"
              placeholder="operator@company.vn or staff.username"
              icon="i-heroicons-user"
              autocomplete="username"
              required
            />
          </UFormGroup>

          <UFormGroup label="Password" name="password" required>
            <UInput
              v-model="form.password"
              type="password"
              placeholder="••••••••"
              icon="i-heroicons-lock-closed"
              autocomplete="current-password"
              required
            />
          </UFormGroup>

          <div class="pt-2 space-y-2.5">
            <UButton
              type="submit"
              block
              color="emerald"
              variant="solid"
              :loading="erpStore.isAuthenticating"
            >
              Sign In to Workspace
            </UButton>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <UButton
                to="/signup"
                block
                color="gray"
                variant="outline"
              >
                Create Account
              </UButton>
              <UButton
                to="/demo"
                block
                color="amber"
                variant="soft"
                icon="i-heroicons-beaker"
              >
                Try Live Demo
              </UButton>
            </div>
          </div>
        </form>
      </UCard>
    </div>
  </UContainer>
</template>
