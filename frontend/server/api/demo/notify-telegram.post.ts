import {
  assertSameOriginCsrf,
  forwardDemoAuthorizedRequest,
  getBackendBaseUrl
} from '~/server/utils/backendProxy'
import {
  parseDemoTelegramNotifyRequest,
  serializeTelegramDispatchResponse
} from '~/server/utils/serializers'

export default defineEventHandler(async (event) => {
  assertSameOriginCsrf(event)

  const rawBody = await readBody<unknown>(event).catch(() => null)
  const payload = parseDemoTelegramNotifyRequest(rawBody)

  const backendBase = getBackendBaseUrl()
  const upstreamData = await forwardDemoAuthorizedRequest(
    event,
    `${backendBase}/api/v1/demo/notify-telegram`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    }
  )

  return serializeTelegramDispatchResponse(upstreamData)
})
