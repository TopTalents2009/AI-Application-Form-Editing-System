import axios from 'axios'

/** 同源反代前缀：开发走 Vite → 8767/api，生产走 3777 /parser-api。 */
export const PARSER_API = '/parser-api'

const api = axios.create({
  baseURL: PARSER_API,
  timeout: 30000,
})

export interface Platform {
  name: string
  display_name: string
  detected: boolean
  data_dir: string
}

export interface Session {
  username: string
  display_name: string
  session_type: number
  summary: string
  last_time: string
  unread: number
  msg_count: number
  synced_at?: string
}

export interface Message {
  time_text: string
  sender: string
  sender_id: string
  text: string
  msg_type: number
  msg_type_label: string
  hour?: number
  message_id?: number
  has_attachment?: boolean
  attachment_name?: string
  media_url?: string
}

export interface Contact {
  user_id: string
  nickname: string
  remark: string
}

export interface Stats {
  total_messages: number
  unique_senders: number
  sender_stats: Record<string, number>
  hourly_distribution: Record<string, number>
  msg_type_distribution: Record<string, number>
  time_range: { start?: string; end?: string }
  top_senders: { name: string; count: number }[]
  activity_timeline: { date: string; count: number }[]
}

export interface Source {
  id: string
  kind: string
  computer_name: string
  operator_name?: string
  username: string
  account_id: string
  host: string
  last_sync: string
  session_count: number
  platform: string
  agent_version?: string
  update_available?: boolean
}

export interface ReadApiInfo {
  enabled: boolean
  key: string
  keys?: { name: string; key: string }[]
  urls: string[]
  example?: string
}

export interface AgentUpdateInfo {
  version: string
  sha256?: string
  size?: number
  url?: string
  download_url?: string
  notes?: string
  changelog?: { version: string; notes: string[] }[]
  published?: boolean
}

export interface IngestInfo {
  token: string
  port: number
  urls: string[]
  web_port?: number
  web_urls?: string[]
  read_api?: ReadApiInfo
  agent_update?: AgentUpdateInfo
  latest_agent_version?: string
  uninstall_published?: boolean
  uninstall_download_url?: string
}

export interface PresenceClient {
  source_id: string
  operator_name: string
  computer_name: string
  host: string
  last_seen: string
  status?: string
  agent_version?: string
}

export interface PresenceAlert {
  id: number
  kind: string
  title: string
  message: string
  time: string
  source_id?: string
}

export interface PresenceSnapshot {
  online: PresenceClient[]
  alerts: PresenceAlert[]
}

export interface SearchHit {
  source_id: string
  source_name: string
  session_id: string
  session_name: string
  time_text: string
  sender: string
  sender_id: string
  text: string
  snippet: string
  message_id: number
  msg_type: number
  attachment_name: string
  media_url: string
}

export interface AgentLogInfo {
  ok?: boolean
  source_id?: string
  job_id?: string
  status: string
  detail?: string
  text?: string
  size?: number
  filename?: string
}

export function apiErrorMessage(err: unknown, fallback = '请求失败') {
  if (axios.isAxiosError(err)) {
    if (!err.response) return '无法连接接口，请确认分析服务已启动'
    const detail = (err.response.data as { detail?: string } | undefined)?.detail
    if (detail) return String(detail)
  }
  return fallback
}

export const chatApi = {
  getPlatforms: () => api.get<Platform[]>('/platforms'),
  getSources: () => api.get<Source[]>('/sources'),
  deleteSource: (sourceId: string) =>
    api.delete<{ ok: boolean; source_id: string }>(`/sources/${encodeURIComponent(sourceId)}`),
  getIngestInfo: () => api.get<IngestInfo>('/ingest/info'),
  getPresence: (since = 0) =>
    api.get<PresenceSnapshot>('/presence', { params: { since } }),
  requestAgentLog: (sourceId: string) =>
    api.post<AgentLogInfo>(`/sources/${encodeURIComponent(sourceId)}/agent-log`),
  getAgentLog: (sourceId: string, jobId = '') =>
    api.get<AgentLogInfo>(`/sources/${encodeURIComponent(sourceId)}/agent-log`, {
      params: jobId ? { job_id: jobId } : undefined,
      validateStatus: (status) => status < 500,
    }),
  agentLogFileUrl: (sourceId: string) =>
    `${PARSER_API}/sources/${encodeURIComponent(sourceId)}/agent-log/file`,
  agentExeUrl: () => `${PARSER_API}/agent-exe`,
  requestSyncNow: (sourceId: string) =>
    api.post<{ ok: boolean; job_id: string; status: string; detail?: string }>(
      `/sources/${encodeURIComponent(sourceId)}/sync-now`,
    ),
  pushAgentUpdate: (sourceId: string) =>
    api.post<{ ok: boolean; job_id: string; status: string; version?: string; detail?: string }>(
      `/sources/${encodeURIComponent(sourceId)}/agent-update`,
    ),
  searchMessages: (params: {
    q: string
    source_id?: string
    session_id?: string
    limit?: number
  }) => api.get<SearchHit[]>('/search', { params, timeout: 120000 }),
  getSourceSessions: (sourceId: string, limit = 1000) =>
    api.get<Session[]>(`/sources/${encodeURIComponent(sourceId)}/sessions`, { params: { limit } }),
  getSourceMessages: (sourceId: string, sessionId: string, params?: {
    start_date?: string
    end_date?: string
    limit?: number
  }) => api.get<Message[]>(
    `/sources/${encodeURIComponent(sourceId)}/messages/${encodeURIComponent(sessionId)}`,
    { params },
  ),
  getSourceStats: (sourceId: string, sessionId: string, params?: {
    start_date?: string
    end_date?: string
  }) => api.get<Stats>(
    `/sources/${encodeURIComponent(sourceId)}/stats/${encodeURIComponent(sessionId)}`,
    { params },
  ),
  getSessions: (platform: string, limit = 100) =>
    api.get<Session[]>(`/${platform}/sessions`, { params: { limit } }),
  getMessages: (platform: string, sessionId: string, params?: {
    start_date?: string
    end_date?: string
    limit?: number
  }) => api.get<Message[]>(`/${platform}/messages/${encodeURIComponent(sessionId)}`, { params }),
  getContacts: (platform: string) =>
    api.get<Contact[]>(`/${platform}/contacts`),
  getStats: (platform: string, sessionId: string, params?: {
    start_date?: string
    end_date?: string
  }) => api.get<Stats>(`/${platform}/stats/${encodeURIComponent(sessionId)}`, { params }),
  getGroups: (platform: string) =>
    api.get<Session[]>(`/${platform}/groups`),
}

export default api
