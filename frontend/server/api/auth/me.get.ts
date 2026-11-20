import {
  forwardAuthorizedRequest,
  getBackendBaseUrl,
  type BackendPrincipalResponse
} from '~/server/utils/backendProxy'

export default defineEventHandler(async (event) => {
  const backendBase = getBackendBaseUrl()
  const principal = (await forwardAuthorizedRequest(event, `${backendBase}/api/v1/auth/me`, {
    method: 'GET'
  })) as BackendPrincipalResponse

  return {
    authenticated: true,
    user_id: principal.user_id,
    email: principal.email,
    tenant_id: principal.tenant_id,
    role: principal.role
  }
})
