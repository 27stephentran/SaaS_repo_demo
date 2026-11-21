<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useErpStore } from '~/stores/erp'

const erpStore = useErpStore()
const toast = useToast()

const localError = ref<string | null>(null)

const form = reactive({
  current_password: '',
  new_password: '',
  confirm_password: ''
})

const isForcedFirstLogin = computed<boolean>(() => Boolean(erpStore.currentUser?.must_change_password))

onMounted(() => {
  erpStore.authError = null
})

async function handleSubmit(): Promise<void> {
  localError.value = null
  erpStore.authError = null

  if (!form.current_password) {
    localError.value = 'Current password is required.'
    return
  }
  if (form.new_password.length < 8) {
    localError.value = 'New password must be at least 8 characters.'
    return
  }
  if (form.new_password !== form.confirm_password) {
    localError.value = 'New passwords do not match.'
    return
  }
  if (form.new_password === form.current_password) {
    localError.value = 'New password must be different from your current temporary password.'
    return
  }

  try {
    const updatedUser = await erpStore.changePassword({
      current_password: form.current_password,
      new_password: form.new_password
    })
    toast.add({
      title: 'Password Updated',
      description: 'Your account password has been updated securely.',
      color: 'emerald'
    })
    const role = (updatedUser.role || 'staff').toLowerCase()
    await navigateTo(role === 'staff' ? '/my-work' : '/')
  } catch {
    // Error surfaced via erpStore.authError
  }
}

async function handleSignOut(): Promise<void> {
  await erpStore.logout()
  await navigateTo('/login')
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
        <div class="flex items-center gap-2">
          <ThemeSwitcher />
          <UButton color="gray" variant="ghost" size="xs" icon="i-heroicons-arrow-right-on-rectangle" @click="handleSignOut">
            Sign Out
          </UButton>
        </div>
      </div>

      <UAlert
        v-if="isForcedFirstLogin"
        color="amber"
        variant="soft"
        icon="i-heroicons-shield-exclamation"
        title="First-Login Password Update Required"
        description="Your account was provisioned with a temporary password. Set a personal password (8+ characters) before accessing your workspace."
      />

      <UCard class="bg-white dark:bg-slate-900 border border-gray-200 dark:border-slate-800 shadow-sm">
        <template #header>
          <div class="space-y-1">
            <h1 class="text-xl font-bold text-gray-900 dark:text-slate-100">
              Update Password
            </h1>
            <p class="text-xs text-gray-500 dark:text-slate-400">
              Account: <span class="font-medium text-gray-700 dark:text-slate-200">{{ erpStore.currentUser?.username || erpStore.currentUser?.email }}</span>
              <span v-if="erpStore.currentUser?.tenant_id"> · {{ erpStore.currentUser.tenant_id }}</span>
            </p>
          </div>
        </template>

        <form class="space-y-4" @submit.prevent="handleSubmit">
          <UAlert
            v-if="localError || erpStore.authError"
            color="red"
            variant="soft"
            icon="i-heroicons-exclamation-triangle"
            :title="localError || erpStore.authError || ''"
          />

          <UFormGroup label="Current Password" name="current_password" required>
            <UInput
              v-model="form.current_password"
              type="password"
              placeholder="Enter current or temporary password"
              icon="i-heroicons-lock-closed"
              autocomplete="current-password"
              required
            />
          </UFormGroup>

          <UFormGroup label="New Password" name="new_password" hint="Minimum 8 characters" required>
            <UInput
              v-model="form.new_password"
              type="password"
              placeholder="At least 8 characters"
              icon="i-heroicons-key"
              autocomplete="new-password"
              required
            />
          </UFormGroup>

          <UFormGroup label="Confirm New Password" name="confirm_password" required>
            <UInput
              v-model="form.confirm_password"
              type="password"
              placeholder="Re-enter new password"
              icon="i-heroicons-check-circle"
              autocomplete="new-password"
              required
            />
          </UFormGroup>

          <div class="pt-2 flex flex-col gap-2">
            <UButton
              type="submit"
              block
              color="emerald"
              variant="solid"
              :loading="erpStore.isAuthenticating"
            >
              Save New Password &amp; Continue
            </UButton>
            <UButton
              v-if="!isForcedFirstLogin"
              to="/"
              block
              color="gray"
              variant="ghost"
            >
              Back to Workspace
            </UButton>
          </div>
        </form>
      </UCard>
    </div>
  </UContainer>
</template>
