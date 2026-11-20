<script setup lang="ts">
import { onMounted, reactive } from 'vue'
import { useErpStore } from '~/stores/erp'

const erpStore = useErpStore()
const toast = useToast()

const form = reactive({
  email: '',
  password: '',
  tenant_id: 'default-tenant'
})

onMounted(async () => {
  const session = await erpStore.fetchSession()
  if (session?.authenticated) {
    await navigateTo('/')
  }
})

async function handleLogin(): Promise<void> {
  if (!form.email.trim() || !form.password) {
    return
  }
  try {
    const user = await erpStore.login({
      email: form.email,
      password: form.password,
      tenant_id: form.tenant_id || 'default-tenant'
    })
    toast.add({
      title: 'Operator Session Active',
      description: `Signed in as ${user.email} (${user.tenant_id})`,
      color: 'emerald'
    })
    await navigateTo('/')
  } catch {
    // Error message is surfaced via erpStore.authError
  }
}
</script>

<template>
  <UContainer class="min-h-screen flex items-center justify-center py-12">
    <UCard class="w-full max-w-md bg-slate-900/90 border border-slate-800">
      <template #header>
        <div class="space-y-1">
          <div class="flex items-center gap-2">
            <span class="inline-block h-2.5 w-2.5 rounded-full bg-emerald-400" />
            <span class="text-xs font-semibold uppercase tracking-widest text-emerald-400">
              AI-Native ERP Security Proxy
            </span>
          </div>
          <h1 class="text-xl font-bold text-white">
            Operator Sign In
          </h1>
          <p class="text-xs text-slate-400">
            Credentials are exchanged server-side via Nitro BFF into HttpOnly, SameSite=Strict session cookies.
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

        <UFormGroup label="Tenant ID" name="tenant_id">
          <UInput
            v-model="form.tenant_id"
            placeholder="default-tenant"
            icon="i-heroicons-building-office-2"
            autocomplete="organization"
            required
          />
        </UFormGroup>

        <UFormGroup label="Operator Email" name="email">
          <UInput
            v-model="form.email"
            type="email"
            placeholder="operator@company.vn"
            icon="i-heroicons-envelope"
            autocomplete="email"
            required
          />
        </UFormGroup>

        <UFormGroup label="Password" name="password">
          <UInput
            v-model="form.password"
            type="password"
            placeholder="••••••••"
            icon="i-heroicons-lock-closed"
            autocomplete="current-password"
            required
          />
        </UFormGroup>

        <div class="pt-2 space-y-2">
          <UButton
            type="submit"
            block
            color="emerald"
            variant="solid"
            :loading="erpStore.isAuthenticating"
          >
            Sign In to Control Plane
          </UButton>
          <UButton
            to="/"
            block
            color="gray"
            variant="ghost"
          >
            Continue in Offline / Demo Preview
          </UButton>
        </div>
      </form>
    </UCard>
  </UContainer>
</template>
