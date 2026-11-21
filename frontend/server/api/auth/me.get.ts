import {
  forwardAuthorizedRequest,
  forwardDemoAuthorizedRequest,
  getAuthCookies,
  getBackendBaseUrl,
  getDemoAuthCookie
} from '~/server/utils/backendProxy'
import { serializeAuthSessionResponse } from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  const backendBase = getBackendBaseUrl()
  const { accessToken, refreshToken } = getAuthCookies(event)

  if (accessToken || refreshToken) {
    const principal = await forwardAuthorizedRequest(event, `${backendBase}/api/v1/auth/me`, {
      method: 'GET'
    })
    return serializeAuthSessionResponse(principal)
  }

  const demoAccessToken = getDemoAuthCookie(event)
  if (demoAccessToken) {
    const principal = await forwardDemoAuthorizedRequest(event, `${backendBase}/api/v1/auth/me`, {
      method: 'GET'
    })
    return serializeAuthSessionResponse(principal)
  }

  throw createError({
    statusCode: 401,
    statusMessage: 'Authentication required'
  })
})
