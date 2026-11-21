import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useErpStore } from '~/stores/erp'
import type {
  AuthUser,
  BankCatalogResponse,
  ChannelConversation,
  ChannelMessage,
  Company,
  ManagerCreateResponse,
  ManagerDashboardSummary,
  Order,
  RestoreResponse,
  SoftDeleteResponse,
  StaffCreateResponse,
  StaffMemberSummary
} from '~/types/erp'

function makeCompany(overrides: Partial<Company> = {}): Company {
  return {
    id: 'comp-1111-1111-1111-111111111111',
    tenant_id: 'acme',
    name: 'Saigon Roastery',
    code: 'SGN',
    bank_code: 'VCB',
    bank_name: 'Vietcombank',
    bank_account_number: '1029384756',
    bank_account_name: 'SAIGON ROASTERY CO',
    sepay_sub_account: 'SUB01',
    sepay_memo_prefix: 'SGN',
    is_active: true,
    deactivated_at: null,
    ...overrides
  }
}

function makeOrder(overrides: Partial<Order> = {}): Order {
  return {
    id: 'ord-1',
    tenant_id: 'acme',
    company_id: 'comp-1111-1111-1111-111111111111',
    order_code: 'ORD-SGN-001',
    customer_id: 'cust-1',
    items: [{ sku: 'SKU-1', name: 'Arabica', quantity: 1, unit_price_vnd: 480000 }],
    total_amount_vnd: 480000,
    status: 'PAID',
    created_at: '2026-11-21T01:00:00Z',
    ...overrides
  }
}

describe('useErpStore multi-company, RBAC helpers, staff, chats, and manager dashboard', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('computes role helpers, mustChangePassword, activeCompanyId, and activeCompanyName accurately', () => {
    const store = useErpStore()

    expect(store.isAdmin).toBe(false)
    expect(store.isManager).toBe(false)
    expect(store.isStaff).toBe(false)
    expect(store.mustChangePassword).toBe(false)
    expect(store.activeCompanyId).toBeNull()
    expect(store.activeCompanyName).toBeNull()

    // Demo session must never elevate to live role helpers
    store.currentUser = {
      authenticated: true,
      email: 'demo@sandbox.local',
      tenant_id: 'demo-sandbox',
      role: 'admin',
      permission_group: 'demo_api'
    }
    expect(store.isLiveAuthenticated).toBe(false)
    expect(store.isAdmin).toBe(false)

    // Live admin uses selectedCompanyId
    store.currentUser = {
      authenticated: true,
      user_id: 'u-admin',
      email: 'admin@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api',
      must_change_password: false
    }
    expect(store.isAdmin).toBe(true)
    expect(store.isManager).toBe(false)
    expect(store.isStaff).toBe(false)

    store.companies = [makeCompany({ id: 'comp-1111-1111-1111-111111111111', name: 'Saigon Roastery' })]
    store.setSelectedCompanyId('comp-1111-1111-1111-111111111111')
    expect(store.activeCompanyId).toBe('comp-1111-1111-1111-111111111111')
    expect(store.activeCompanyName).toBe('Saigon Roastery')

    // Fallback to managerDashboard.company_name, then 8-char ID prefix
    store.setSelectedCompanyId('comp-2222-2222-2222-222222222222')
    expect(store.activeCompanyName).toBe('comp-222')
    store.managerDashboard = {
      tenant_id: 'acme',
      company_id: 'comp-2222-2222-2222-222222222222',
      company_name: 'Hanoi Flagship',
      total_revenue_vnd: 0,
      paid_orders_count: 0,
      pending_orders_count: 0,
      total_orders_count: 0,
      active_customers_count: 0,
      active_staff_count: 0,
      open_chats_count: 0,
      daily_income: [],
      staff_workload: [],
      recent_customers: []
    }
    expect(store.activeCompanyName).toBe('Hanoi Flagship')

    // Manager uses currentUser.company_id regardless of selectedCompanyId
    store.currentUser = {
      authenticated: true,
      user_id: 'u-mgr',
      email: 'mgr@acme.io',
      tenant_id: 'acme',
      role: 'manager',
      permission_group: 'live_api',
      company_id: 'comp-1111-1111-1111-111111111111',
      must_change_password: true
    }
    expect(store.isAdmin).toBe(false)
    expect(store.isManager).toBe(true)
    expect(store.isStaff).toBe(false)
    expect(store.mustChangePassword).toBe(true)
    expect(store.activeCompanyId).toBe('comp-1111-1111-1111-111111111111')

    // Staff role
    store.currentUser = {
      authenticated: true,
      user_id: 'u-staff',
      email: 'staff@acme.io',
      tenant_id: 'acme',
      role: 'staff',
      permission_group: 'live_api',
      company_id: 'comp-1111-1111-1111-111111111111',
      employee_id: 'emp-1',
      must_change_password: false
    }
    expect(store.isStaff).toBe(true)
    expect(store.isManager).toBe(false)
  })

  it('appends selectedCompanyId query parameters for admin across ERP endpoints and omits for non-admin', async () => {
    const fetchMock = vi.fn().mockResolvedValue([])
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      email: 'admin@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api'
    }
    store.setSelectedCompanyId('comp-1')

    await store.fetchInventory(true)
    await store.fetchOrders()
    await store.fetchCustomers(true)
    await store.fetchEmployees(true)
    await store.fetchWebhookLogs()
    await store.fetchMetrics()
    await store.fetchStaff()
    await store.fetchManagerDashboard()
    await store.fetchChats('messenger')
    await store.fetchChats('all')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/erp/inventory?include_inactive=true&company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/erp/orders?company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/erp/customers?include_inactive=true&company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(4, '/api/erp/employees?include_inactive=true&company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(5, '/api/erp/webhooks/logs?company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(6, '/api/erp/metrics?company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(7, '/api/erp/staff?company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(8, '/api/erp/dashboard/manager?company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(9, '/api/erp/chats?channel=messenger&company_id=comp-1')
    expect(fetchMock).toHaveBeenNthCalledWith(10, '/api/erp/chats?company_id=comp-1')

    fetchMock.mockClear()
    store.currentUser.role = 'manager'
    await store.fetchOrders()
    await store.fetchStaff()
    await store.fetchChats('telegram')

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/erp/orders')
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/erp/staff')
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/erp/chats?channel=telegram')

    // searchInventorySemantic forwards company_id for admin and omits for non-admin
    fetchMock.mockClear()
    store.currentUser.role = 'admin'
    await store.searchInventorySemantic('arabica', true)
    expect(fetchMock).toHaveBeenCalledWith('/api/erp/inventory/search', {
      method: 'POST',
      body: {
        query: 'arabica',
        tenant_id: 'acme',
        company_id: 'comp-1',
        limit: 10,
        include_inactive: true
      }
    })
  })

  it('passes company_id cleanly on createOrder and createInventoryItem', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeOrder())
      .mockResolvedValueOnce(makeOrder({ company_id: 'comp-override' }))
      .mockResolvedValueOnce({
        id: 'inv-1',
        sku: 'SKU-1',
        name: 'Coffee Beans',
        description: 'Fresh roast',
        price_vnd: 250000,
        stock_quantity: 10,
        has_embedding: true
      })
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      email: 'admin@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api'
    }
    store.setSelectedCompanyId('comp-admin-selected')

    // Admin without explicit company_id falls back to selectedCompanyId
    await store.createOrder({
      customer_id: 'cust-1',
      assigned_employee_id: 'emp-1',
      items: [{ sku: 'SKU-1', quantity: 1 }]
    })
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/erp/orders', {
      method: 'POST',
      body: {
        company_id: 'comp-admin-selected',
        customer_id: 'cust-1',
        assigned_employee_id: 'emp-1',
        items: [{ sku: 'SKU-1', quantity: 1 }]
      }
    })

    // Explicit payload.company_id overrides selectedCompanyId
    await store.createOrder({
      company_id: 'comp-override',
      customer_id: 'cust-1',
      items: [{ sku: 'SKU-1', quantity: 2 }]
    })
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/erp/orders', {
      method: 'POST',
      body: {
        company_id: 'comp-override',
        customer_id: 'cust-1',
        items: [{ sku: 'SKU-1', quantity: 2 }]
      }
    })

    // createInventoryItem uses selectedCompanyId for admin
    await store.createInventoryItem({
      sku: 'SKU-1',
      name: 'Coffee Beans',
      description: 'Fresh roast',
      price_vnd: 250000,
      stock_quantity: 10
    })
    expect(fetchMock).toHaveBeenNthCalledWith(3, '/api/erp/inventory', {
      method: 'POST',
      body: {
        company_id: 'comp-admin-selected',
        sku: 'SKU-1',
        name: 'Coffee Beans',
        description: 'Fresh roast',
        category: 'General Merchandise',
        price_vnd: 250000,
        stock_quantity: 10
      }
    })
  })

  it('supports company CRUD, bank catalog/update, and manager provisioning actions', async () => {
    const company = makeCompany()
    const updatedCompany = makeCompany({ bank_code: 'MBB', bank_account_number: '99887766' })
    const catalog: BankCatalogResponse = {
      banks: [
        {
          code: 'VCB',
          bin: '970436',
          name: 'Vietcombank',
          short_name: 'VCB',
          vietqr_supported: true,
          sepay_supported: true
        }
      ],
      sepay_linked_accounts: [
        {
          id: 'bank-acc-1',
          bank_code: 'VCB',
          bank_short_name: 'VCB',
          account_number: '1029384756',
          account_holder_name: 'SAIGON ROASTERY CO',
          active: true
        }
      ],
      sepay_api_configured: true,
      bankhub_configured: false
    }
    const softDeleted: SoftDeleteResponse = {
      resource: 'companies',
      record_id: company.id,
      tenant_id: 'acme',
      is_active: false,
      deactivated_at: '2026-11-21T02:00:00Z'
    }
    const restored: RestoreResponse = {
      resource: 'companies',
      record_id: company.id,
      tenant_id: 'acme',
      is_active: true,
      deactivated_at: null
    }
    const mgrRes: ManagerCreateResponse = {
      user_id: 'u-mgr-1',
      tenant_id: 'acme',
      company_id: company.id,
      email: 'mgr@acme.io',
      username: 'mgr_sgn',
      full_name: 'Manager SGN',
      role: 'manager',
      temporary_password: 'TempMgrPass123!',
      must_change_password: true
    }

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce([company])
      .mockResolvedValueOnce(catalog)
      .mockResolvedValueOnce(company)
      .mockResolvedValueOnce(updatedCompany)
      .mockResolvedValueOnce(softDeleted)
      .mockResolvedValueOnce(restored)
      .mockResolvedValueOnce(mgrRes)
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    await store.fetchCompanies(true)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/companies?include_inactive=true')
    expect(store.companies).toEqual([company])

    await store.fetchBankCatalog()
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/companies/banks')
    expect(store.bankCatalog.sepay_api_configured).toBe(true)
    expect(store.bankCatalog.bankhub_configured).toBe(false)
    expect(store.bankCatalog.sepay_linked_accounts).toHaveLength(1)

    store.companies = []
    const created = await store.createCompany({
      name: 'Saigon Roastery',
      code: 'SGN',
      bank_code: 'VCB',
      bank_account_number: '1029384756',
      bank_account_name: 'SAIGON ROASTERY CO'
    })
    expect(created.id).toBe(company.id)
    expect(store.companies[0]).toEqual(company)

    const bankUpdated = await store.updateCompanyBank(company.id, {
      bank_code: 'MBB',
      bank_account_number: '99887766',
      bank_account_name: 'SAIGON ROASTERY CO'
    })
    expect(bankUpdated.bank_code).toBe('MBB')
    expect(store.companies[0]?.bank_account_number).toBe('99887766')

    await store.softDeleteCompany(company.id)
    expect(store.companies[0]?.is_active).toBe(false)

    await store.restoreCompany(company.id)
    expect(store.companies[0]?.is_active).toBe(true)
    expect(store.companies[0]?.deactivated_at).toBeNull()

    const createdMgr = await store.createManager(company.id, {
      full_name: 'Manager SGN',
      email: 'mgr@acme.io'
    })
    expect(createdMgr).toEqual(mgrRes)
    expect(fetchMock).toHaveBeenNthCalledWith(7, `/api/companies/${company.id}/managers`, {
      method: 'POST',
      body: { full_name: 'Manager SGN', email: 'mgr@acme.io' }
    })
  })

  it('supports login with identifier, changePassword, and logout workspace cleanup', async () => {
    const initialUser: AuthUser = {
      authenticated: true,
      user_id: 'u-staff-1',
      email: 'staff1@acme.io',
      username: 'staff_01',
      full_name: 'Staff One',
      tenant_id: 'acme',
      role: 'staff',
      permission_group: 'live_api',
      company_id: 'comp-1',
      employee_id: 'emp-1',
      must_change_password: true
    }
    const updatedUser: AuthUser = {
      ...initialUser,
      must_change_password: false
    }

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(initialUser)
      .mockResolvedValueOnce(updatedUser)
      .mockResolvedValueOnce({ logged_out: true })
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()

    await expect(
      store.login({ identifier: '', password: 'pw', tenant_id: 'acme' })
    ).rejects.toThrow(/Username or email is required/)

    const loggedIn = await store.login({
      identifier: ' staff_01 ',
      password: 'TempPassword123',
      tenant_id: ' acme '
    })
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/auth/login', {
      method: 'POST',
      body: {
        identifier: 'staff_01',
        password: 'TempPassword123',
        tenant_id: 'acme'
      }
    })
    expect(loggedIn.must_change_password).toBe(true)
    expect(store.mustChangePassword).toBe(true)

    const changed = await store.changePassword({
      current_password: 'TempPassword123',
      new_password: 'NewStrongPassword123'
    })
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/auth/change-password', {
      method: 'POST',
      body: {
        current_password: 'TempPassword123',
        new_password: 'NewStrongPassword123'
      }
    })
    expect(changed.must_change_password).toBe(false)
    expect(store.mustChangePassword).toBe(false)

    // Populate workspace state before logout
    store.selectedCompanyId = 'comp-1'
    store.companies = [makeCompany()]
    store.myWorkOrders = [makeOrder()]
    store.chats = [
      {
        id: 'c-1',
        channel: 'messenger',
        external_thread_id: 't-1',
        customer_name: 'Alice',
        last_message_preview: 'Hi',
        last_message_at: '2026-11-21T00:00:00Z',
        unread_count: 1,
        status: 'open'
      }
    ]

    await store.logout()
    expect(store.currentUser).toBeNull()
    expect(store.requiresAuth).toBe(true)
    expect(store.selectedCompanyId).toBeNull()
    expect(store.companies).toEqual([])
    expect(store.myWorkOrders).toEqual([])
    expect(store.chats).toEqual([])
  })

  it('supports createStaff, fetchManagerDashboard, fetchChats, replyToChat, and staff my-work actions', async () => {
    const staffSummary: StaffMemberSummary = {
      employee_id: 'emp-1',
      user_id: 'u-staff-1',
      tenant_id: 'acme',
      company_id: 'comp-1',
      full_name: 'Le Van Staff',
      username: 'levanstaff',
      telegram_user_id: '998877',
      role: 'staff',
      is_active: true,
      must_change_password: true,
      active_orders_count: 1,
      completed_orders_count: 1
    }
    const staffCreated: StaffCreateResponse = {
      employee_id: 'emp-1',
      user_id: 'u-staff-1',
      tenant_id: 'acme',
      company_id: 'comp-1',
      full_name: 'Le Van Staff',
      telegram_user_id: '998877',
      email: 'staff@acme.io',
      username: 'levanstaff',
      temporary_password: 'TempPassword123!',
      role: 'staff',
      must_change_password: true
    }
    const dashboard: ManagerDashboardSummary = {
      tenant_id: 'acme',
      company_id: 'comp-1',
      company_name: 'Saigon Roastery',
      total_revenue_vnd: 2400000,
      paid_orders_count: 3,
      pending_orders_count: 1,
      total_orders_count: 5,
      active_customers_count: 4,
      active_staff_count: 2,
      open_chats_count: 2,
      daily_income: [{ date: '2026-11-21', revenue_vnd: 960000, orders_count: 2 }],
      staff_workload: [staffSummary],
      recent_customers: []
    }
    const conv: ChannelConversation = {
      id: 'conv-1',
      channel: 'messenger',
      external_thread_id: 'psid-1',
      customer_name: 'Nguyen Thi C',
      last_message_preview: 'Is coffee in stock?',
      last_message_at: '2026-11-21T03:00:00Z',
      unread_count: 2,
      status: 'open'
    }
    const inboundMsg: ChannelMessage = {
      id: 'msg-1',
      conversation_id: 'conv-1',
      channel: 'messenger',
      direction: 'inbound',
      sender_type: 'customer',
      sender_name: 'Nguyen Thi C',
      content: 'Is coffee in stock?',
      created_at: '2026-11-21T03:00:00Z'
    }
    const replyMsg: ChannelMessage = {
      id: 'msg-2',
      conversation_id: 'conv-1',
      channel: 'messenger',
      direction: 'outbound',
      sender_type: 'manager',
      sender_name: 'Manager',
      content: 'Yes, ready to ship!',
      created_at: '2026-11-21T03:05:00Z'
    }

    const myOrder = makeOrder({ id: 'ord-mw-1', status: 'PAID' })
    const updatedOrder = makeOrder({ id: 'ord-mw-1', status: 'FULFILLING' })

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(staffCreated)
      .mockResolvedValueOnce([staffSummary])
      .mockResolvedValueOnce(dashboard)
      .mockResolvedValueOnce([conv])
      .mockResolvedValueOnce([inboundMsg])
      .mockResolvedValueOnce(replyMsg)
      .mockResolvedValueOnce([myOrder])
      .mockResolvedValueOnce(updatedOrder)
      .mockRejectedValueOnce({ statusCode: 500, statusMessage: 'Update failed' })
    vi.stubGlobal('$fetch', fetchMock)

    const store = useErpStore()
    store.currentUser = {
      authenticated: true,
      email: 'mgr@acme.io',
      tenant_id: 'acme',
      role: 'manager',
      permission_group: 'live_api',
      company_id: 'comp-1'
    }

    const createdStaffRes = await store.createStaff({
      full_name: 'Le Van Staff',
      telegram_user_id: '998877',
      email: 'staff@acme.io'
    })
    expect(createdStaffRes.temporary_password).toBe('TempPassword123!')
    expect(store.staffMembers).toEqual([staffSummary])
    expect(store.isCreatingStaff).toBe(false)

    await store.fetchManagerDashboard()
    expect(store.managerDashboard?.total_revenue_vnd).toBe(2400000)
    expect(store.managerDashboard?.paid_orders_count).toBe(3)
    expect(store.managerDashboard?.daily_income[0]?.revenue_vnd).toBe(960000)
    expect(store.dashboardError).toBeNull()

    await store.fetchChats('messenger')
    expect(store.chats).toHaveLength(1)
    expect(store.chats[0]?.unread_count).toBe(2)

    await store.fetchChatMessages('conv-1')
    expect(store.activeChatMessages).toEqual([inboundMsg])
    expect(store.chats[0]?.unread_count).toBe(0)

    const sent = await store.replyToChat('conv-1', '  Yes, ready to ship!  ')
    expect(fetchMock).toHaveBeenNthCalledWith(6, '/api/erp/chats/conv-1/reply', {
      method: 'POST',
      body: { content: 'Yes, ready to ship!' }
    })
    expect(sent.id).toBe('msg-2')
    expect(store.activeChatMessages).toHaveLength(2)
    expect(store.chats[0]?.last_message_preview).toBe('Yes, ready to ship!')

    await store.fetchMyWork()
    expect(store.myWorkOrders).toHaveLength(1)

    await store.updateMyWorkStatus('ord-mw-1', 'FULFILLING')
    expect(store.myWorkOrders[0]?.status).toBe('FULFILLING')

    // Invalid transition blocked before network call
    await expect(store.updateMyWorkStatus('ord-mw-1', 'PAID')).rejects.toThrow(/Invalid order transition/)

    // Server failure rolls back optimistic status
    await expect(store.updateMyWorkStatus('ord-mw-1', 'COMPLETED')).rejects.toMatchObject({ statusCode: 500 })
    expect(store.myWorkOrders[0]?.status).toBe('FULFILLING')
    expect(store.updatingOrderIds['ord-mw-1']).toBeUndefined()
  })
})
