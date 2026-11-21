<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useErpStore } from '~/stores/erp'

const erpStore = useErpStore()
const toast = useToast()

const localError = ref<string | null>(null)

const form = reactive({
  tenant_id: '',
  email: '',
  password: '',
  confirmPassword: ''
})

onMounted(async () => {
  erpStore.authError = null
  const session = await erpStore.fetchSession()
  if (session?.authenticated && session.permission_group === 'live_api') {
    await navigateTo('/')
  }
})

async function handleSignup(): Promise<void> {
  localError.value = null
  erpStore.authError = null

  if (!form.tenant_id.trim()) {
    localError.value = 'Workspace / Tenant ID is required.'
    return
  }
  if (!form.email.trim()) {
    localError.value = 'Operator email is required.'
    return
  }
  if (form.password.length < 8) {
    localError.value = 'Password must be at least 8 characters.'
    return
  }
  if (form.password !== form.confirmPassword) {
    localError.value = 'Passwords do not match.'
    return
  }

  try {
    const user = await erpStore.signup({
      tenant_id: form.tenant_id.trim(),
      email: form.email.trim(),
      password: form.password
    })
    toast.add({
      title: 'Workspace Account Created',
      description: `Signed in to ${user.tenant_id} as ${user.email}`,
      color: 'emerald'
    })
    await navigateTo('/')
  } catch {
    // Error surfaced via erpStore.authError
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
              Create New Workspace
            </h1>
            <p class="text-xs text-gray-500 dark:text-slate-400">
              Provision a new tenant workspace as its founding administrator. To join an existing workspace, request an account from your workspace admin and sign in.
            </p>
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="handleSignup">
          <UAlert
            v-if="localError || erpStore.authError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="localError || erpStore.authError || ''"
          />

          <UFormGroup
            label="New Workspace / Tenant ID"
            name="tenant_id"
            hint="Letters, numbers, dashes"
            help="Choose a unique ID for your new workspace. Existing workspace IDs cannot be claimed via self-serve signup."
            required
          >
            <UInput
              v-model="form.tenant_id"
              placeholder="e.g. acme-coffee"
              icon="i-heroicons-building-office-2"
              autocomplete="organization"
              required
            />
          </UFormGroup>

          <UFormGroup label="Work Email" name="email" required>
            <UInput
              v-model="form.email"
              type="email"
              placeholder="operator@company.vn"
              icon="i-heroicons-envelope"
              autocomplete="email"
              required
            />
          </UFormGroup>

          <UFormGroup label="Password" name="password" hint="Minimum 8 characters" required>
            <UInput
              v-model="form.password"
              type="password"
              placeholder="At least 8 characters"
              icon="i-heroicons-lock-closed"
              autocomplete="new-password"
              required
            />
          </UFormGroup>

          <UFormGroup label="Confirm Password" name="confirm_password" required>
            <UInput
              v-model="form.confirmPassword"
              type="password"
              placeholder="Re-enter password"
              icon="i-heroicons-check-circle"
              autocomplete="new-password"
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
              Create Operator Account
            </UButton>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <UButton
                to="/login"
                block
                color="gray"
                variant="outline"
              >
                Back to Sign In
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
