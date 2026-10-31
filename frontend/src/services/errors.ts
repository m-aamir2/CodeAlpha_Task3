import axios from 'axios'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function errorMessage(error: unknown, fallback: string, field?: string): string {
  if (axios.isAxiosError<unknown>(error)) {
    const payload = error.response?.data
    if (isRecord(payload)) {
      if (typeof payload.detail === 'string') return payload.detail
      if (field && typeof payload[field] === 'string') return payload[field] as string
      if (field && Array.isArray(payload[field])) {
        const fieldMessages = payload[field].filter((item): item is string => typeof item === 'string')
        if (fieldMessages.length) return fieldMessages.join(' ')
      }
      const messages = Object.values(payload).flatMap(value => Array.isArray(value) ? value : [value]).filter((value): value is string => typeof value === 'string')
      if (messages.length) return messages.join(' ')
    }
    if (!error.response) {
      if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') return 'The request timed out. Check your connection and try again.'
      if (error.code === 'ERR_NETWORK') return 'ConnectX could not reach the server. Check your connection and try again.'
    }
    if (error.response?.status === 429) return 'Too many requests. Wait a moment, then try again.'
    if (error.response && error.response.status >= 500) return 'ConnectX is temporarily unavailable. Your session is still active; please retry shortly.'
    return error.message || fallback
  }
  return error instanceof Error ? error.message || fallback : fallback
}

export function errorStatus(error: unknown): number | undefined {
  return axios.isAxiosError<unknown>(error) ? error.response?.status : undefined
}

export function errorName(error: unknown): string | undefined {
  return error instanceof Error ? error.name : undefined
}
