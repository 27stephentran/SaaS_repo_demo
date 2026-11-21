const runtimeEnv = (
  globalThis as { process?: { env?: Record<string, string | undefined> } }
).process?.env

// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2024-11-01',
  devtools: { enabled: true },
  modules: ['@nuxt/ui', '@pinia/nuxt'],
  colorMode: {
    preference: 'system',
    fallback: 'light',
    classSuffix: ''
  },
  runtimeConfig: {
    apiBaseUrl:
      runtimeEnv?.NUXT_API_BASE_URL ||
      runtimeEnv?.NUXT_PUBLIC_API_BASE_URL ||
      runtimeEnv?.NUXT_PUBLIC_API_BASE ||
      '',
    isProduction: runtimeEnv?.NODE_ENV === 'production',
    allowedOrigins: runtimeEnv?.NUXT_ALLOWED_ORIGINS || '',
    public: {
      siteOrigin: runtimeEnv?.NUXT_PUBLIC_SITE_ORIGIN || '',
      apiBaseUrl:
        runtimeEnv?.NUXT_PUBLIC_API_BASE_URL ||
        runtimeEnv?.NUXT_PUBLIC_API_BASE ||
        '',
      apiBase:
        runtimeEnv?.NUXT_PUBLIC_API_BASE ||
        runtimeEnv?.NUXT_PUBLIC_API_BASE_URL ||
        ''
    }
  },
  typescript: {
    strict: true
  }
})
