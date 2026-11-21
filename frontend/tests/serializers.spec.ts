import { createError } from 'h3'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  parseChangePasswordRequest,
  parseCompanyWritePayload,
  parseDemoTelegramNotifyRequest,
  parseErpWritePayload,
  parseLoginRequest,
  parseOrderTelegramNotifyRequest,
  parseSignupRequest,
  serializeAuthSessionResponse,
  serializeCompanyResponse,
  serializeErpResponse,
  serializeLogoutResponse,
  serializeTelegramDispatchResponse,
  serializeTokenPairResponse
} from '~/server/utils/serializers'

function expectHttpError(fn: () => unknown, expectedStatus: number, messagePattern?: RegExp) {
  let caught: unknown
  try {
    fn()
  } catch (err) {
    caught = err
  }
  expect(caught).toBeDefined()
  const errObj = caught as { statusCode?: number; statusMessage?: string; message?: string }
  expect(errObj.statusCode).toBe(expectedStatus)
  if (messagePattern) {
    expect(errObj.statusMessage || errObj.message || '').toMatch(messagePattern)
  }
}

describe('Nitro request & response serializers', () => {
  beforeEach(() => {
    vi.stubGlobal('createError', createError)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('validates login requests and rejects invalid payloads with 422', () => {
    expect(
      parseLoginRequest({
        email: '  Admin@Acme.IO ',
        password: ' secret-pass ',
        tenant_id: ' acme-tenant_1 '
      })
    ).toEqual({
      email: 'admin@acme.io',
      password: ' secret-pass ',
      tenant_id: 'acme-tenant_1'
    })

    expectHttpError(() => parseLoginRequest(null), 422, /JSON object/)
    expectHttpError(
      () => parseLoginRequest({ email: 'bad-email', password: 'pw', tenant_id: 'acme' }),
      422,
      /email format is invalid/
    )
    expectHttpError(
      () => parseLoginRequest({ email: 'ops@acme.io', password: '', tenant_id: 'acme' }),
      422,
      /password/
    )
    expectHttpError(
      () => parseLoginRequest({ email: 'ops@acme.io', password: 'pw', tenant_id: 'bad tenant!' }),
      422,
      /tenant_id format is invalid/
    )
    expectHttpError(
      () =>
        parseLoginRequest({
          email: 'ops@acme.io',
          password: 'pw',
          tenant_id: 'acme',
          role: 'superuser'
        }),
      422,
      /unexpected field "role"/
    )
  })

  it('validates signup requests with >= 8 char password and strict key allowlist', () => {
    expect(
      parseSignupRequest({
        email: 'NewUser@Tenant.vn',
        password: 'StrongPassword123',
        tenant_id: 'tenant-01'
      })
    ).toEqual({
      email: 'newuser@tenant.vn',
      password: 'StrongPassword123',
      tenant_id: 'tenant-01'
    })

    expectHttpError(
      () =>
        parseSignupRequest({
          email: 'user@tenant.vn',
          password: 'short',
          tenant_id: 'tenant-01'
        }),
      422,
      /at least 8 character/
    )
    expectHttpError(
      () =>
        parseSignupRequest({
          email: 'user@tenant.vn',
          password: 'StrongPassword123',
          tenant_id: 'tenant-01',
          permission_group: 'live_api'
        }),
      422,
      /unexpected field "permission_group"/
    )
  })

  it('validates demo notify-telegram and order notify-telegram payloads', () => {
    expect(
      parseDemoTelegramNotifyRequest({
        order_code: 'DEMO-ORD-8841',
        total_amount_vnd: 960000,
        items_summary: '2x Arabica Beans',
        customer_name: 'Pham Gia Huy',
        telegram_user_id: 'demo_ops_channel',
        sepay_reference: 'DEMO8841'
      })
    ).toEqual({
      order_code: 'DEMO-ORD-8841',
      total_amount_vnd: 960000,
      items_summary: '2x Arabica Beans',
      customer_name: 'Pham Gia Huy',
      telegram_user_id: 'demo_ops_channel',
      sepay_reference: 'DEMO8841'
    })

    expectHttpError(
      () =>
        parseDemoTelegramNotifyRequest({
          order_code: 'DEMO-ORD-8841',
          total_amount_vnd: -10,
          items_summary: '2x Arabica'
        }),
      422,
      /integer >= 0/
    )
    expectHttpError(
      () =>
        parseDemoTelegramNotifyRequest({
          order_code: 'DEMO-ORD-8841',
          total_amount_vnd: 100.5,
          items_summary: '2x Arabica'
        }),
      422,
      /integer >= 0/
    )
    expectHttpError(
      () =>
        parseDemoTelegramNotifyRequest({
          order_code: 'DEMO-ORD-8841',
          total_amount_vnd: 1000,
          items_summary: '2x Arabica',
          extra: true
        }),
      422,
      /unexpected field "extra"/
    )

    expect(parseOrderTelegramNotifyRequest(undefined)).toEqual({})
    expect(parseOrderTelegramNotifyRequest({ telegram_user_id: '  998877  ' })).toEqual({
      telegram_user_id: '998877'
    })
    expectHttpError(() => parseOrderTelegramNotifyRequest([]), 422, /JSON object/)
    expectHttpError(
      () => parseOrderTelegramNotifyRequest({ telegram_user_id: '123', spoofed: 'x' }),
      422,
      /unexpected field "spoofed"/
    )
  })

  it('validates ERP write payloads strictly and rejects invalid bodies with 422', () => {
    expectHttpError(() => parseErpWritePayload('orders', 'POST', undefined), 422, /Request body is required/)
    expectHttpError(() => parseErpWritePayload('orders', 'POST', '{bad-json'), 422, /valid JSON/)
    expectHttpError(
      () =>
        parseErpWritePayload(
          'orders',
          'POST',
          JSON.stringify({
            customer_id: 'cust-1',
            items: [{ sku: 'SKU-1', quantity: 2, unit_price_vnd: 1 }]
          })
        ),
      422,
      /unexpected field "unit_price_vnd"/
    )
    expectHttpError(
      () =>
        parseErpWritePayload(
          'orders',
          'POST',
          JSON.stringify({
            customer_id: 'cust-1',
            items: []
          })
        ),
      422,
      /non-empty array/
    )

    const validOrderBody = parseErpWritePayload(
      'orders',
      'POST',
      JSON.stringify({
        customer_id: 'cust-1',
        items: [{ sku: 'SKU-1', quantity: 2 }]
      })
    )
    expect(JSON.parse(validOrderBody!)).toEqual({
      customer_id: 'cust-1',
      items: [{ sku: 'SKU-1', quantity: 2 }]
    })

    expectHttpError(
      () =>
        parseErpWritePayload(
          'orders/ord-1/status',
          'PATCH',
          JSON.stringify({ status: 'HACKED' })
        ),
      422,
      /Invalid order status/
    )
    expect(
      JSON.parse(
        parseErpWritePayload(
          'orders/ord-1/status',
          'PATCH',
          JSON.stringify({ status: 'paid' })
        )!
      )
    ).toEqual({ status: 'PAID' })

    expectHttpError(
      () =>
        parseErpWritePayload(
          'inventory/search',
          'POST',
          JSON.stringify({ query: 'coffee', limit: 99 })
        ),
      422,
      /limit must be <= 25/
    )
  })

  it('validates token pair, auth session, logout, and telegram dispatch responses', () => {
    const livePair = serializeTokenPairResponse(
      {
        access_token: 'acc.jwt.tok',
        refresh_token: 'ref.jwt.tok',
        token_type: 'bearer',
        expires_in: 900,
        tenant_id: 'acme',
        role: 'admin',
        permission_group: 'live_api'
      },
      'live_api'
    )
    expect(livePair.permission_group).toBe('live_api')

    const demoPair = serializeTokenPairResponse(
      {
        access_token: 'demo.jwt.tok',
        refresh_token: 'demo.ref.tok',
        expires_in: 900,
        tenant_id: 'demo-sandbox',
        role: 'demo',
        permission_group: 'demo_api'
      },
      'demo_api'
    )
    expect(demoPair.permission_group).toBe('demo_api')

    expectHttpError(
      () =>
        serializeTokenPairResponse(
          {
            access_token: 'demo.jwt.tok',
            refresh_token: 'demo.ref.tok',
            expires_in: 900,
            tenant_id: 'demo-sandbox',
            role: 'demo',
            permission_group: 'demo_api'
          },
          'live_api'
        ),
      403,
      /Expected live_api session/
    )

    expectHttpError(() => serializeTokenPairResponse({ access_token: '' }), 502, /schema validation/)

    expect(
      serializeAuthSessionResponse({
        user_id: 'u-1',
        email: 'ops@acme.io',
        tenant_id: 'acme',
        role: 'admin',
        permission_group: 'live_api'
      })
    ).toEqual({
      authenticated: true,
      user_id: 'u-1',
      email: 'ops@acme.io',
      tenant_id: 'acme',
      role: 'admin',
      permission_group: 'live_api'
    })
    expectHttpError(() => serializeAuthSessionResponse({ tenant_id: 'acme' }), 502, /email is missing/)

    expect(serializeLogoutResponse({ status: 'logged_out', revoked: true })).toEqual({
      authenticated: false,
      logged_out: true,
      status: 'logged_out',
      revoked: true
    })
    expectHttpError(() => serializeLogoutResponse('bad-response'), 502, /logout response/)

    expect(
      serializeTelegramDispatchResponse({
        dispatched: true,
        simulated: true,
        telegram_user_id: 'demo_ops_channel',
        order_code: 'DEMO-ORD-8841',
        preview_text: 'Alert sent'
      })
    ).toMatchObject({
      dispatched: true,
      simulated: true,
      order_code: 'DEMO-ORD-8841'
    })
    expectHttpError(() => serializeTelegramDispatchResponse({ dispatched: 'yes' }), 502, /boolean/)
  })

  it('validates ERP upstream responses including soft-delete and restore schemas', () => {
    const deleted = serializeErpResponse('inventory/inv-1', 'DELETE', {
      resource: 'inventory',
      record_id: 'inv-1',
      tenant_id: 'acme',
      is_active: false,
      deactivated_at: '2026-11-20T09:00:00Z'
    })
    expect(deleted).toMatchObject({ resource: 'inventory', is_active: false })

    const restored = serializeErpResponse('customers/cust-1/restore', 'POST', {
      resource: 'customers',
      record_id: 'cust-1',
      tenant_id: 'acme',
      is_active: true,
      deactivated_at: null
    })
    expect(restored).toMatchObject({ resource: 'customers', is_active: true, deactivated_at: null })

    expectHttpError(
      () => serializeErpResponse('inventory', 'GET', { bad: true }),
      502,
      /inventory list must be an array/
    )
    expectHttpError(
      () => serializeErpResponse('orders', 'GET', [{ id: '1', order_code: 'O-1', status: 'PAID' }]),
      502,
      /total_amount_vnd/
    )
    expectHttpError(
      () => serializeErpResponse('metrics', 'GET', {}),
      502,
      /total_orders/
    )
  })

  it('validates identifier-based login, change-password, and multi-company AuthUser serialization', () => {
    // identifier-only login trims identifier and omits email
    expect(
      parseLoginRequest({
        identifier: '  staff_alice  ',
        password: 'TempPassword123',
        tenant_id: 'acme'
      })
    ).toEqual({
      identifier: 'staff_alice',
      password: 'TempPassword123',
      tenant_id: 'acme'
    })

    // both identifier and email provided
    expect(
      parseLoginRequest({
        identifier: 'staff_alice',
        email: 'Alice@Acme.IO',
        password: 'pw',
        tenant_id: 'acme'
      })
    ).toEqual({
      identifier: 'staff_alice',
      email: 'alice@acme.io',
      password: 'pw',
      tenant_id: 'acme'
    })

    // rejects missing both identifier and email
    expectHttpError(
      () => parseLoginRequest({ identifier: '   ', password: 'pw', tenant_id: 'acme' }),
      422,
      /identifier or email is required/
    )
    expectHttpError(
      () => parseLoginRequest({ identifier: 'a', password: 'pw', tenant_id: 'acme' }),
      422,
      /at least 2 character/
    )

    // change-password validation
    expect(
      parseChangePasswordRequest({
        current_password: ' old-pass ',
        new_password: ' NewStrongPass1! '
      })
    ).toEqual({
      current_password: ' old-pass ',
      new_password: ' NewStrongPass1! '
    })
    expectHttpError(() => parseChangePasswordRequest(null), 422, /JSON object/)
    expectHttpError(
      () => parseChangePasswordRequest({ current_password: '', new_password: 'NewPassword123' }),
      422,
      /current_password/
    )
    expectHttpError(
      () => parseChangePasswordRequest({ current_password: 'old', new_password: 'short' }),
      422,
      /at least 8 character/
    )
    expectHttpError(
      () =>
        parseChangePasswordRequest({
          current_password: 'old-password',
          new_password: 'NewPassword123',
          confirm_password: 'NewPassword123'
        }),
      422,
      /unexpected field "confirm_password"/
    )

    // TokenPair & AuthSession serialization with multi-company + forced password change fields
    const pair = serializeTokenPairResponse(
      {
        access_token: 'acc.jwt',
        refresh_token: 'ref.jwt',
        expires_in: 900,
        tenant_id: 'acme',
        role: 'manager',
        permission_group: 'live_api',
        user_id: 'u-mgr-1',
        email: 'mgr@acme.io',
        username: 'mgr_hcm',
        full_name: 'Tran Thi B',
        company_id: 'comp-1',
        employee_id: null,
        must_change_password: true
      },
      'live_api'
    )
    expect(pair).toMatchObject({
      user_id: 'u-mgr-1',
      email: 'mgr@acme.io',
      username: 'mgr_hcm',
      full_name: 'Tran Thi B',
      company_id: 'comp-1',
      employee_id: null,
      must_change_password: true
    })

    const session = serializeAuthSessionResponse({
      user_id: 'u-staff-1',
      email: 'staff@acme.io',
      username: 'staff_01',
      full_name: 'Nguyen Van Staff',
      tenant_id: 'acme',
      role: 'staff',
      permission_group: 'live_api',
      company_id: 'comp-1',
      employee_id: 'emp-1',
      must_change_password: true
    })
    expect(session).toEqual({
      authenticated: true,
      user_id: 'u-staff-1',
      email: 'staff@acme.io',
      username: 'staff_01',
      full_name: 'Nguyen Van Staff',
      tenant_id: 'acme',
      role: 'staff',
      permission_group: 'live_api',
      company_id: 'comp-1',
      employee_id: 'emp-1',
      must_change_password: true
    })
  })

  it('validates multi-company ERP write payloads and staff/my-work/dashboard/chats responses', () => {
    const orderWithCompany = JSON.parse(
      parseErpWritePayload(
        'orders',
        'POST',
        JSON.stringify({
          company_id: 'comp-1',
          customer_id: 'cust-1',
          assigned_employee_id: 'emp-1',
          items: [{ sku: 'SKU-1', quantity: 2 }]
        })
      )!
    )
    expect(orderWithCompany).toEqual({
      company_id: 'comp-1',
      customer_id: 'cust-1',
      assigned_employee_id: 'emp-1',
      items: [{ sku: 'SKU-1', quantity: 2 }]
    })

    const staffPayload = JSON.parse(
      parseErpWritePayload(
        'staff',
        'POST',
        JSON.stringify({
          full_name: '  Le Van Staff ',
          telegram_user_id: '  99887766 ',
          email: '  Staff01@Acme.IO '
        })
      )!
    )
    expect(staffPayload).toEqual({
      full_name: 'Le Van Staff',
      telegram_user_id: '99887766',
      email: 'staff01@acme.io'
    })
    expectHttpError(() => parseErpWritePayload('staff', 'POST', undefined), 422, /Request body is required/)
    expectHttpError(
      () =>
        parseErpWritePayload(
          'staff',
          'POST',
          JSON.stringify({ full_name: 'Le Van', telegram_user_id: '123', email: 'not-an-email' })
        ),
      422,
      /email format is invalid/
    )
    expectHttpError(
      () =>
        parseErpWritePayload(
          'staff',
          'POST',
          JSON.stringify({ full_name: 'Le Van', telegram_user_id: '123', role: 'admin' })
        ),
      422,
      /unexpected field "role"/
    )

    const chatReply = JSON.parse(
      parseErpWritePayload('chats/conv-1/reply', 'POST', JSON.stringify({ content: '  Hello!  ' }))!
    )
    expect(chatReply).toEqual({ content: 'Hello!' })
    expectHttpError(
      () => parseErpWritePayload('chats/conv-1/reply', 'POST', undefined),
      422,
      /Request body is required/
    )
    expectHttpError(
      () => parseErpWritePayload('chats/conv-1/reply', 'POST', JSON.stringify({ content: '   ' })),
      422,
      /at least 1 character/
    )

    // serializeErpResponse for my-work, staff, dashboard/manager, chats
    expect(
      serializeErpResponse('my-work', 'GET', [
        { id: 'ord-1', order_code: 'ORD-1', status: 'PAID', total_amount_vnd: 500000 }
      ])
    ).toHaveLength(1)
    expectHttpError(
      () => serializeErpResponse('my-work', 'GET', {}),
      502,
      /my-work list must be an array/
    )

    expect(
      serializeErpResponse('staff', 'GET', [
        { employee_id: 'emp-1', full_name: 'Staff A', telegram_user_id: '111' }
      ])
    ).toHaveLength(1)
    expect(
      serializeErpResponse('staff', 'POST', {
        employee_id: 'emp-1',
        user_id: 'u-1',
        username: 'staff_a',
        temporary_password: 'TempPass123!',
        full_name: 'Staff A'
      })
    ).toMatchObject({ username: 'staff_a' })
    expectHttpError(
      () =>
        serializeErpResponse('staff', 'POST', {
          employee_id: 'emp-1',
          user_id: 'u-1',
          username: 'staff_a',
          full_name: 'Staff A'
        }),
      502,
      /temporary_password/
    )

    const searchWithCompany = JSON.parse(
      parseErpWritePayload(
        'inventory/search',
        'POST',
        JSON.stringify({
          query: 'arabica beans',
          tenant_id: 'acme',
          company_id: 'comp-1',
          limit: 10,
          include_inactive: true
        })
      )!
    )
    expect(searchWithCompany).toEqual({
      query: 'arabica beans',
      tenant_id: 'acme',
      company_id: 'comp-1',
      limit: 10,
      include_inactive: true
    })

    expect(
      serializeErpResponse('dashboard/manager', 'GET', {
        tenant_id: 'acme',
        company_id: 'comp-1',
        company_name: 'Saigon Roastery',
        total_revenue_vnd: 1200000,
        paid_orders_count: 2,
        pending_orders_count: 1,
        total_orders_count: 3,
        active_customers_count: 4,
        active_staff_count: 2,
        open_chats_count: 1,
        daily_income: [{ date: '2026-11-21', revenue_vnd: 480000, orders_count: 1 }],
        staff_workload: [],
        recent_customers: []
      })
    ).toMatchObject({ total_revenue_vnd: 1200000, paid_orders_count: 2, total_orders_count: 3 })

    // Stale/wrong field names without canonical fields must fail with 502
    expectHttpError(
      () =>
        serializeErpResponse('dashboard/manager', 'GET', {
          total_revenue_vnd: 1200000,
          today_income_vnd: 480000,
          total_orders: 3,
          daily_income: []
        }),
      502,
      /managerDashboard\.paid_orders_count/
    )
    expectHttpError(
      () =>
        serializeErpResponse('dashboard/manager', 'GET', {
          total_revenue_vnd: 1200000,
          paid_orders_count: 2,
          pending_orders_count: 1,
          total_orders_count: 3,
          active_customers_count: 4,
          active_staff_count: 2,
          open_chats_count: 1,
          daily_income: [{ date: '2026-11-21', income_vnd: 480000, orders_count: 1 }],
          staff_workload: [],
          recent_customers: []
        }),
      502,
      /managerDashboard\.daily_income\.revenue_vnd/
    )
    expectHttpError(
      () =>
        serializeErpResponse('dashboard/manager', 'GET', {
          total_revenue_vnd: 1200000,
          paid_orders_count: 2,
          pending_orders_count: 1,
          total_orders_count: 3,
          active_customers_count: 4,
          active_staff_count: 2,
          open_chats_count: 1,
          daily_income: [],
          staff_workload: 'invalid',
          recent_customers: []
        }),
      502,
      /staff_workload must be an array/
    )

    expect(
      serializeErpResponse('chats', 'GET', [
        { id: 'c-1', channel: 'messenger', customer_name: 'Alice' }
      ])
    ).toHaveLength(1)
    expect(
      serializeErpResponse('chats/c-1/messages', 'GET', [
        { id: 'm-1', conversation_id: 'c-1', sender_type: 'customer', content: 'Hi' }
      ])
    ).toHaveLength(1)
    expect(
      serializeErpResponse('chats/c-1/reply', 'POST', {
        id: 'm-2',
        conversation_id: 'c-1',
        content: 'Hello!'
      })
    ).toMatchObject({ id: 'm-2' })
    expectHttpError(
      () => serializeErpResponse('chats/c-1/reply', 'POST', { id: 'm-2', conversation_id: 'c-1' }),
      502,
      /chatReply\.content/
    )
  })

  it('validates company, bank, and manager write payloads and responses', () => {
    const companyCreate = JSON.parse(
      parseCompanyWritePayload(
        '',
        'POST',
        JSON.stringify({
          name: 'Saigon Roastery',
          code: 'sgn',
          bank_code: 'vcb',
          bank_name: 'Vietcombank',
          bank_account_number: '1029384756',
          bank_account_name: 'CONG TY SAIGON',
          sepay_sub_account: 'SUB01',
          sepay_memo_prefix: 'SGN'
        })
      )!
    )
    expect(companyCreate).toEqual({
      name: 'Saigon Roastery',
      code: 'SGN',
      bank_code: 'VCB',
      bank_name: 'Vietcombank',
      bank_account_number: '1029384756',
      bank_account_name: 'CONG TY SAIGON',
      sepay_sub_account: 'SUB01',
      sepay_memo_prefix: 'SGN'
    })

    expectHttpError(() => parseCompanyWritePayload('', 'POST', undefined), 422, /Request body is required/)
    expectHttpError(
      () =>
        parseCompanyWritePayload(
          '',
          'POST',
          JSON.stringify({
            name: 'S',
            code: 'sg',
            bank_code: 'vcb',
            bank_account_number: '123',
            bank_account_name: 'AB'
          })
        ),
      422,
      /at least 2 character/
    )
    expectHttpError(
      () =>
        parseCompanyWritePayload(
          '',
          'POST',
          JSON.stringify({
            name: 'Saigon',
            code: 'sg',
            bank_code: 'vcb',
            bank_account_number: '123',
            bank_account_name: 'AB',
            tenant_id: 'forged'
          })
        ),
      422,
      /unexpected field "tenant_id"/
    )

    const bankUpdate = JSON.parse(
      parseCompanyWritePayload(
        'comp-1/bank',
        'PATCH',
        JSON.stringify({
          bank_code: 'mbb',
          bank_account_number: '000111222',
          bank_account_name: 'SAIGON ROASTERY'
        })
      )!
    )
    expect(bankUpdate).toEqual({
      bank_code: 'MBB',
      bank_account_number: '000111222',
      bank_account_name: 'SAIGON ROASTERY'
    })
    expectHttpError(
      () => parseCompanyWritePayload('comp-1/bank', 'PUT', undefined),
      422,
      /Request body is required/
    )

    const managerCreate = JSON.parse(
      parseCompanyWritePayload(
        'comp-1/managers',
        'POST',
        JSON.stringify({
          full_name: 'Manager One',
          email: 'Mgr1@Acme.IO',
          username: 'mgr_one',
          password: 'Password123!'
        })
      )!
    )
    expect(managerCreate).toEqual({
      full_name: 'Manager One',
      email: 'mgr1@acme.io',
      username: 'mgr_one',
      password: 'Password123!'
    })
    expectHttpError(
      () =>
        parseCompanyWritePayload(
          'comp-1/managers',
          'POST',
          JSON.stringify({ full_name: 'Manager One', password: 'short' })
        ),
      422,
      /at least 8 character/
    )
    expectHttpError(
      () =>
        parseCompanyWritePayload(
          'comp-1/managers',
          'POST',
          JSON.stringify({ full_name: 'Manager One', role: 'admin' })
        ),
      422,
      /unexpected field "role"/
    )

    // serializeCompanyResponse
    expect(
      serializeCompanyResponse('banks', 'GET', {
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
        sepay_linked_accounts: [],
        sepay_api_configured: false,
        bankhub_configured: false
      })
    ).toMatchObject({ sepay_api_configured: false, bankhub_configured: false })
    expectHttpError(() => serializeCompanyResponse('banks', 'GET', {}), 502, /banks array/)
    expectHttpError(
      () => serializeCompanyResponse('banks', 'GET', { banks: [], linked_accounts: [] }),
      502,
      /sepay_linked_accounts array/
    )
    expectHttpError(
      () =>
        serializeCompanyResponse('banks', 'GET', {
          banks: [],
          sepay_linked_accounts: [],
          sepay_api_v2_configured: true
        }),
      502,
      /bankCatalog\.sepay_api_configured/
    )

    const sampleCompany = {
      id: 'comp-1',
      name: 'Saigon Roastery',
      code: 'SGN',
      bank_code: 'VCB',
      bank_account_number: '1029384756'
    }
    expect(serializeCompanyResponse('', 'GET', [sampleCompany])).toEqual([sampleCompany])
    expect(serializeCompanyResponse('', 'POST', sampleCompany)).toEqual(sampleCompany)
    expect(serializeCompanyResponse('comp-1/bank', 'PATCH', sampleCompany)).toEqual(sampleCompany)
    expectHttpError(() => serializeCompanyResponse('', 'GET', [{ id: 'comp-1' }]), 502, /company\.name/)

    expect(
      serializeCompanyResponse('comp-1', 'DELETE', {
        resource: 'companies',
        record_id: 'comp-1',
        tenant_id: 'acme',
        is_active: false,
        deactivated_at: '2026-11-21T00:00:00Z'
      })
    ).toMatchObject({ is_active: false })

    expect(
      serializeCompanyResponse('comp-1/restore', 'POST', {
        resource: 'companies',
        record_id: 'comp-1',
        tenant_id: 'acme',
        is_active: true,
        deactivated_at: null
      })
    ).toMatchObject({ is_active: true })

    expect(
      serializeCompanyResponse('comp-1/managers', 'POST', {
        user_id: 'u-mgr-1',
        company_id: 'comp-1',
        username: 'mgr_one',
        email: 'mgr1@acme.io'
      })
    ).toMatchObject({ company_id: 'comp-1' })
    expectHttpError(
      () => serializeCompanyResponse('comp-1/managers', 'POST', { user_id: 'u-1' }),
      502,
      /managerCreate\.company_id/
    )
  })
})
