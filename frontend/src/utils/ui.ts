export function toast(message: string, kind = 'success') {
  window.dispatchEvent(new CustomEvent('cx-toast', { detail: { message, kind } }))
}

export function formatDate(value?: string | null, opts?: Intl.DateTimeFormatOptions) {
  if (!value) return 'Not scheduled'
  return new Intl.DateTimeFormat(undefined, opts || { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value))
}
