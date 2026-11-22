export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}
export async function request(path, options = {}) {
  const response = await fetch(path, { ...options, cache: 'no-store', signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000) });
  const data = await response.json();
  if (!response.ok) throw new ApiError(data.error?.message || 'The service is temporarily unavailable.', response.status);
  return data;
}
export function uploadPdf(file) {
  const body = new FormData(); body.append('file', file);
  return request('/api/runs', { method: 'POST', body });
}
export function getReport(session, signal) {
  return request(`/api/runs/${encodeURIComponent(session.run_id)}`, { headers: { Authorization: `Bearer ${session.access_token}` }, signal });
}
export function citationUrl(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
