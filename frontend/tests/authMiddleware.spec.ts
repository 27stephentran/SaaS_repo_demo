import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useErpStore } from '~/stores/erp'
import type { AuthUser } from '~/types/erp'

type MiddlewareFn = (to: { path: string }) => Promise<unknown>

async function loadAuthMiddleware(options?: {
  mockStore?: {
    currentUser: AuthUser | null
    fetchSession: ReturnType<typeof vi.fn>
  }
}): Promise<{
  middleware: MiddlewareFn
  navigateToMock: ReturnType<typeof vi.fn>
}> {
  vi.resetModules()
  const navigateToMock = vi.fn((path: string) => ({ redirect: path }))
  vi.stubGlobal('defineNuxtRouteMiddleware', (fn: MiddlewareFn) => fn)
  vi.stubGlobal('navigateTo', navigateToMock)
  vi.stubGlobal('useRequestHeaders', () => ({}))

  if (options?.mockStore) {
    vi.doMock('~/stores/erp', () => ({
      useErpStore: () => options.mockStore
    }))
  } else {
    vi.doUnmock('~/stores/erp')
  }

  const mod = await import('~/middleware/auth.global')
  return {
    middleware: mod.default as unknown as MiddlewareFn,
    navigateToMock
  }
}

describe('auth.global.ts route guard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.doUnmock('~/stores/erp')
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('permits public and demo routes (/login, /signup, /demo, /demo/*) without calling session API', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('$fetch', fetchMock)

    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const publicPaths = ['/login', '/signup', '/demo', '/demo/sandbox', '/demo/orders']

    for (const path of publicPaths) {
      const result = await middleware({ path })
      expect(result).toBeUndefined()
    }

    expect(navigateToMock).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('blocks unauthenticated users from protected ERP routes and redirects to /login', async () => {
    const fetchMock = vi.fn().mockRejectedValue({ statusCode: 401 })
    vi.stubGlobal('$fetch', fetchMock)

    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const protectedPaths = ['/', '/orders', '/inventory', '/customers', '/activity']

    for (const path of protectedPaths) {
      const result = await middleware({ path })
      expect(result).toEqual({ redirect: '/login' })
    }

    expect(navigateToMock).toHaveBeenCalledTimes(protectedPaths.length)
    expect(navigateToMock).toHaveBeenCalledWith('/login')
  })

  it('blocks demo_api-only users from protected ERP routes and redirects to /login', async () => {
    const demoSession: AuthUser = {
      authenticated: true,
      user_id: 'demo-user-01',
      email: 'demo@sandbox.local',
      tenant_id: 'demo-sandbox',
      role: 'demo',
      permission_group: 'demo_api'
    }
    const fetchMock = vi.fn().mockResolvedValue(demoSession)
    vi.stubGlobal('$fetch', fetchMock)

    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const store = useErpStore()
    store.currentUser = { ...demoSession }

    const protectedPaths = ['/', '/orders', '/inventory', '/customers', '/activity']

    for (const path of protectedPaths) {
      const result = await middleware({ path })
      expect(result).toEqual({ redirect: '/login' })
    }

    expect(navigateToMock).toHaveBeenCalledTimes(protectedPaths.length)
    expect(navigateToMock).toHaveBeenCalledWith('/login')
  })

  it('permits live_api authenticated operators across all protected ERP routes', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('$fetch', fetchMock)

    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      user_id: 'user-1',
      email: 'ops@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api'
    }

    const protectedPaths = ['/', '/orders', '/inventory', '/customers', '/activity']
    for (const path of protectedPaths) {
      const result = await middleware({ path })
      expect(result).toBeUndefined()
    }

    expect(navigateToMock).not.toHaveBeenCalled()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('hydrates session via stubbed useErpStore.fetchSession for live vs demo routing', async () => {
    const liveSession: AuthUser = {
      authenticated: true,
      user_id: 'user-2',
      email: 'lead@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api'
    }
    const demoSession: AuthUser = {
      authenticated: true,
      user_id: 'demo-user-01',
      email: 'demo@sandbox.local',
      tenant_id: 'demo-sandbox',
      role: 'demo',
      permission_group: 'demo_api'
    }

    const fetchSessionMock = vi
      .fn()
      .mockResolvedValueOnce(liveSession)
      .mockResolvedValueOnce(demoSession)

    const { middleware, navigateToMock } = await loadAuthMiddleware({
      mockStore: {
        currentUser: null,
        fetchSession: fetchSessionMock
      }
    })

    // Cold-start live user allowed on protected route after fetchSession
    expect(await middleware({ path: '/orders' })).toBeUndefined()
    expect(fetchSessionMock).toHaveBeenCalledTimes(1)
    expect(navigateToMock).not.toHaveBeenCalled()

    // Cold-start demo_api user redirected to /login on protected route
    expect(await middleware({ path: '/inventory' })).toEqual({ redirect: '/login' })
    expect(fetchSessionMock).toHaveBeenCalledTimes(2)
    expect(navigateToMock).toHaveBeenCalledWith('/login')

    // /login, /signup, and /demo remain accessible without calling fetchSession
    expect(await middleware({ path: '/login' })).toBeUndefined()
    expect(await middleware({ path: '/signup' })).toBeUndefined()
    expect(await middleware({ path: '/demo' })).toBeUndefined()
    expect(await middleware({ path: '/demo/orders' })).toBeUndefined()
    expect(fetchSessionMock).toHaveBeenCalledTimes(2)
  })

  it('forces redirect to /change-password when must_change_password === true across roles', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('$fetch', fetchMock)

    const roles = ['admin', 'manager', 'staff'] as const
    const blockedPaths = [
      '/',
      '/orders',
      '/my-work',
      '/companies',
      '/staff',
      '/chats',
      '/customers',
      '/inventory',
      '/activity'
    ]

    for (const role of roles) {
      const { middleware, navigateToMock } = await loadAuthMiddleware()
      const store = useErpStore()
      store.currentUser = {
        authenticated: true,
        user_id: `u-${role}`,
        email: `${role}@acme.io`,
        tenant_id: 'acme',
        role,
        permission_group: 'live_api',
        must_change_password: true
      }

      for (const path of blockedPaths) {
        const result = await middleware({ path })
        expect(result).toEqual({ redirect: '/change-password' })
      }
      expect(navigateToMock).toHaveBeenCalledTimes(blockedPaths.length)

      // /change-password itself must be allowed without redirect loop
      expect(await middleware({ path: '/change-password' })).toBeUndefined()
      expect(navigateToMock).toHaveBeenCalledTimes(blockedPaths.length)
    }

    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('redirects staff users away from restricted routes to /my-work and allows /my-work', async () => {
    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      user_id: 'u-staff-1',
      email: 'staff@acme.io',
      tenant_id: 'acme',
      role: 'staff',
      permission_group: 'live_api',
      company_id: 'comp-1',
      employee_id: 'emp-1',
      must_change_password: false
    }

    const restrictedPaths = [
      '/companies',
      '/companies/comp-1',
      '/staff',
      '/chats',
      '/chats/conv-1',
      '/customers',
      '/orders',
      '/inventory',
      '/activity'
    ]

    for (const path of restrictedPaths) {
      const result = await middleware({ path })
      expect(result).toEqual({ redirect: '/my-work' })
    }
    expect(navigateToMock).toHaveBeenCalledTimes(restrictedPaths.length)

    expect(await middleware({ path: '/my-work' })).toBeUndefined()
    expect(await middleware({ path: '/change-password' })).toBeUndefined()
    expect(await middleware({ path: '/' })).toBeUndefined()
    expect(navigateToMock).toHaveBeenCalledTimes(restrictedPaths.length)
  })

  it('blocks manager role from /companies routes (redirects to /) while allowing /staff, /chats, /orders', async () => {
    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      user_id: 'u-mgr-1',
      email: 'mgr@acme.io',
      tenant_id: 'acme',
      role: 'manager',
      permission_group: 'live_api',
      company_id: 'comp-1',
      must_change_password: false
    }

    expect(await middleware({ path: '/companies' })).toEqual({ redirect: '/' })
    expect(await middleware({ path: '/companies/new' })).toEqual({ redirect: '/' })
    expect(navigateToMock).toHaveBeenCalledTimes(2)
    expect(navigateToMock).toHaveBeenCalledWith('/')

    const allowedForManager = ['/', '/staff', '/chats', '/customers', '/orders', '/inventory', '/activity', '/my-work']
    for (const path of allowedForManager) {
      expect(await middleware({ path })).toBeUndefined()
    }
    expect(navigateToMock).toHaveBeenCalledTimes(2)
  })

  it('allows admin role on /companies and all protected routes', async () => {
    const { middleware, navigateToMock } = await loadAuthMiddleware()
    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      user_id: 'u-admin-1',
      email: 'admin@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api',
      must_change_password: false
    }

    for (const path of ['/companies', '/companies/comp-1', '/staff', '/chats', '/orders', '/my-work']) {
      expect(await middleware({ path })).toBeUndefined()
    }
    expect(navigateToMock).not.toHaveBeenCalled()
  })
})
