export type Health = { status: 'ok'; service: string }

export async function getHealth(signal: AbortSignal): Promise<Health> {
  const response = await fetch('/api/health', {
    signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
  })
  if (!response.ok) throw new Error(`Backend returned HTTP ${response.status}`)
  const body: unknown = await response.json()
  if (typeof body !== 'object' || body === null || !('status' in body) ||
    body.status !== 'ok' || !('service' in body) || typeof body.service !== 'string') {
    throw new Error('Unexpected backend response')
  }
  return { status: 'ok', service: body.service }
}
