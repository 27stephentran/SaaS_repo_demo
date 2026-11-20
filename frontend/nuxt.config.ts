const runtimeEnv = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env

// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2024-11-01',
  devtools: { enabled: true },
  modules: ['@nuxt/ui', '@pinia/nuxt'],
  runtimeConfig: {
    apiBaseUrl:
      runtimeEnv?.NUXT_API_BASE_URL ||
      runtimeEnv?.NUXT_PUBLIC_API_BASE_URL ||
      runtimeEnv?.NUXT_PUBLIC_API_BASE ||
      'http://localhost:8000',
    isProduction: runtimeEnv?.NODE_ENV === 'production',
    public: {
      apiBaseUrl:
        runtimeEnv?.NUXT_PUBLIC_API_BASE_URL ||
        runtimeEnv?.NUXT_PUBLIC_API_BASE ||
        'http://localhost:8000',
      apiBase:
        runtimeEnv?.NUXT_PUBLIC_API_BASE ||
        runtimeEnv?.NUXT_PUBLIC_API_BASE_URL ||
        'http://localhost:8000'
    }
  },
  typescript: {
    strict: true
  }
})
