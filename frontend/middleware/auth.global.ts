import { useErpStore } from '~/stores/erp'
import type { AuthUser } from '~/types/erp'

function isPublicOrDemoRoute(path: string): boolean {
  if (path === '/login' || path === '/signup' || path === '/demo') {
    return true
  }
  return path.startsWith('/demo/')
}

function matchesRoutePrefix(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`)
}

const STAFF_RESTRICTED_PREFIXES = [
  '/companies',
  '/staff',
  '/chats',
  '/customers',
  '/orders',
  '/inventory',
  '/activity'
]

export default defineNuxtRouteMiddleware(async (to) => {
  if (isPublicOrDemoRoute(to.path)) {
    return
  }

  const erpStore = useErpStore()
  let session: AuthUser | null =
    erpStore.currentUser?.authenticated && erpStore.currentUser.permission_group === 'live_api'
      ? erpStore.currentUser
      : null

  if (!session) {
    const requestHeaders = import.meta.server ? useRequestHeaders(['cookie']) : undefined
    session = await erpStore.fetchSession(requestHeaders)
  }

  if (!session || !session.authenticated || session.permission_group !== 'live_api') {
    return navigateTo('/login')
  }

  if (session.must_change_password === true) {
    if (to.path !== '/change-password') {
      return navigateTo('/change-password')
    }
    return
  }

  const role = (session.role || 'staff').toLowerCase()

  if (role === 'staff') {
    if (STAFF_RESTRICTED_PREFIXES.some((prefix) => matchesRoutePrefix(to.path, prefix))) {
      return navigateTo('/my-work')
    }
    return
  }

  if (role !== 'admin' && matchesRoutePrefix(to.path, '/companies')) {
    return navigateTo('/')
  }
})
