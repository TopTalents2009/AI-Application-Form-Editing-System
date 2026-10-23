<template>
  <div>
    <div class="mb-5">
      <h1 class="text-[20px] font-bold text-[#1c2333] mb-1">聊天来源</h1>
      <p class="text-[13px] text-[#5b6577]">先选择电脑，再查看这台电脑同步过来的聊天列表</p>
      <p v-if="pushNotice" class="text-sm text-emerald-700 mt-2">{{ pushNotice }}</p>
    </div>

    <div class="ci-card mb-6 p-5">
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div class="min-w-0">
          <div class="text-sm font-medium text-slate-900 mb-1">最新同步助手</div>
          <p class="text-sm text-slate-500">
            版本 {{ latestAgentVersion }}<span v-if="agentSizeLabel"> · {{ agentSizeLabel }}</span>。
            下载后发给对方电脑运行，填写「同步与接口设置」里的 API 地址和令牌。
            需要卸掉时，把卸载程序发到同一台电脑运行即可。
          </p>
          <p v-if="latestAgentNotes" class="text-xs text-slate-500 mt-2 whitespace-pre-line">{{ latestAgentNotes }}</p>
        </div>
        <div class="flex flex-col items-end gap-2 shrink-0">
          <a
            v-if="agentPublished"
            :href="agentDownloadHref"
            class="text-sm px-3 py-2 rounded-lg bg-primary-600 text-white hover:bg-primary-700"
          >下载 WeComSyncAgent.exe</a>
          <a
            v-if="uninstallPublished"
            :href="uninstallDownloadHref"
            class="text-sm px-3 py-2 rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50"
          >下载卸载程序</a>
          <span v-if="!agentPublished" class="text-sm text-amber-700">尚未打包，请在分析机运行 parser\build_agent.bat</span>
        </div>
      </div>
    </div>

    <div
      v-if="loadError"
      class="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 flex items-center justify-between gap-3"
    >
      <span>{{ loadError }}</span>
      <button type="button" class="shrink-0 underline" @click="loadSources(true)">重试</button>
    </div>

    <details v-if="ingest" class="ci-card mb-6 p-5">
      <summary class="cursor-pointer text-sm font-medium text-slate-900">同步与接口设置</summary>
      <div class="space-y-5 mt-4">
      <div>
        <div class="text-sm font-medium text-slate-900 mb-1">局域网打开网页</div>
        <p class="text-sm text-slate-500 mb-3">
          手机或其它电脑连同一 WiFi / 网段后，浏览器打开下面的地址即可。不要用 127.0.0.1。
        </p>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="url in lanWebUrls"
            :key="url"
            type="button"
            class="text-sm bg-primary-50 text-primary-800 px-3 py-1.5 rounded-lg hover:bg-primary-100"
            @click="copyText(url)"
          >{{ url }}</button>
        </div>
        <p v-if="!lanWebUrls.length" class="text-sm text-amber-700">
          没有检测到局域网 IP。请确认网线/WiFi 已连接，并用管理员运行 scripts\open_lan_firewall.ps1 放行 5173 端口。
        </p>
        <p v-if="copied" class="text-xs text-emerald-600 mt-2">已复制 {{ copied }}</p>
      </div>
      <div>
        <div class="text-sm font-medium text-slate-900 mb-2">远端同步助手</div>
        <p class="text-sm text-slate-500 mb-3">
          把 <code class="bg-slate-100 px-1 rounded">WeComSyncAgent.exe</code> 装到对方电脑。
          助手填写的是 API 地址（8767），会同步全部群聊和单聊到这里。
          卸掉时运行 <code class="bg-slate-100 px-1 rounded">WeComSyncUninstall.exe</code>。
        </p>
        <div class="grid md:grid-cols-2 gap-3 text-sm">
          <div>
            <div class="text-xs text-slate-400 mb-1">同步助手地址</div>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="url in lanApiUrls"
                :key="url"
                type="button"
                class="bg-slate-100 px-2 py-1 rounded hover:bg-slate-200"
                @click="copyText(url)"
              >{{ url }}</button>
            </div>
          </div>
          <div>
            <div class="text-xs text-slate-400 mb-1">同步令牌</div>
            <button
              type="button"
              class="bg-slate-100 px-2 py-1 rounded break-all text-left hover:bg-slate-200"
              @click="copyText(ingest.token)"
            >{{ ingest.token }}</button>
          </div>
        </div>
        <div class="mt-4 pt-4 border-t border-slate-100">
          <div class="text-xs text-slate-400 mb-1">最新助手版本</div>
          <div class="flex flex-wrap items-center gap-2 text-sm">
            <span class="font-medium text-slate-900">{{ latestAgentVersion }}</span>
            <span
              v-if="agentPublished"
              class="text-[11px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700"
            >已打包，可推送</span>
            <span
              v-else
              class="text-[11px] px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700"
            >尚未打包</span>
          </div>
          <p v-if="latestAgentNotes" class="text-xs text-slate-500 mt-2 whitespace-pre-line">{{ latestAgentNotes }}</p>
          <p v-else-if="!agentPublished" class="text-xs text-amber-700 mt-2">
            运行 <code class="bg-slate-100 px-1 rounded">build_agent.bat</code> 后，才能向在线电脑推送更新。
          </p>
          <a
            v-if="agentPublished"
            :href="agentDownloadHref"
            class="inline-block mt-3 text-sm px-3 py-1.5 rounded-lg bg-primary-50 text-primary-800 hover:bg-primary-100"
          >下载最新客户端</a>
        </div>
      </div>
      <div v-if="ingest.read_api">
        <div class="text-sm font-medium text-slate-900 mb-1">开放读取 API</div>
        <p class="text-sm text-slate-500 mb-3">
          其他人用 API Key 调用 <code class="bg-slate-100 px-1 rounded">/v1</code> 只读聊天记录。
          请求头 <code class="bg-slate-100 px-1 rounded">X-API-Key</code>，或
          <code class="bg-slate-100 px-1 rounded">Authorization: Bearer</code>。
          可在 <code class="bg-slate-100 px-1 rounded">config.jsonc</code> 的
          <code class="bg-slate-100 px-1 rounded">open_api</code> 里增删 key。
        </p>
        <div class="grid md:grid-cols-2 gap-3 text-sm mb-3">
          <div>
            <div class="text-xs text-slate-400 mb-1">接口地址</div>
            <div class="flex flex-wrap gap-2">
              <button
                v-for="url in lanReadApiUrls"
                :key="url"
                type="button"
                class="bg-slate-100 px-2 py-1 rounded hover:bg-slate-200"
                @click="copyText(url)"
              >{{ url }}</button>
            </div>
          </div>
          <div>
            <div class="text-xs text-slate-400 mb-1">API Key</div>
            <div class="space-y-1">
              <button
                v-for="item in readApiKeys"
                :key="item.key"
                type="button"
                class="bg-slate-100 px-2 py-1 rounded break-all text-left hover:bg-slate-200 w-full"
                @click="copyText(item.key)"
              >
                <span v-if="item.name && item.name !== 'default'" class="text-slate-500 mr-1">{{ item.name }}</span>
                {{ item.key }}
              </button>
            </div>
          </div>
        </div>
        <div v-if="ingest.read_api.example" class="text-xs">
          <div class="text-slate-400 mb-1">调用示例（点击复制）</div>
          <button
            type="button"
            class="w-full text-left bg-slate-900 text-slate-100 px-3 py-2 rounded-lg font-mono break-all hover:bg-slate-800"
            @click="copyText(ingest.read_api.example || '')"
          >{{ ingest.read_api.example }}</button>
        </div>
      </div>
      </div>
    </details>

    <div v-if="loading" class="flex items-center justify-center py-20">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
    </div>

    <div v-else-if="!selectedSource" class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div
        v-for="source in sources"
        :key="source.id"
        class="ci-card p-5 hover:border-primary-300 cursor-pointer transition-shadow"
        @click="selectSource(source)"
      >
        <div class="flex items-center gap-4 mb-4">
          <div class="w-11 h-11 rounded-[11px] bg-primary-50 text-primary-500 flex items-center justify-center text-sm font-bold shrink-0">
            {{ source.kind === 'remote' ? '远' : '本' }}
          </div>
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
              <h3 class="text-[15px] font-semibold text-[#1c2333] break-words">{{ sourceTitle(source) }}</h3>
              <span
                v-if="isUpdating(source)"
                class="inline-flex items-center gap-1 shrink-0 text-sm font-medium text-amber-600"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
                更新中
              </span>
              <span
                v-else-if="isOnline(source)"
                class="inline-flex items-center gap-1 shrink-0 text-sm font-medium text-emerald-600"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
                在线
              </span>
            </div>
            <p class="text-xs text-slate-500 mt-1 break-all">{{ sourceSubtitle(source) }}</p>
          </div>
        </div>
        <p class="text-sm text-slate-500 mb-2">{{ source.session_count }} 个会话</p>
        <p class="text-xs text-slate-400">最近同步：{{ source.last_sync || '—' }}</p>
        <div class="mt-3 space-y-2" @click.stop>
          <p class="text-xs text-slate-500 break-all">
            助手 {{ sourceAgentVersion(source) }}
            <span v-if="isOutdated(source)" class="ml-1 text-amber-600">可更新</span>
          </p>
          <div class="flex flex-wrap gap-1.5">
            <button
              type="button"
              class="text-xs px-2 py-1 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="!canPushUpdate(source)"
              @click="pushUpdate(source)"
            >{{ updateButtonLabel(source) }}</button>
            <button
              type="button"
              class="text-xs px-2 py-1 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="!canSyncNow(source)"
              @click="syncNow(source)"
            >{{ syncNowLabel(source) }}</button>
            <button
              type="button"
              class="text-xs px-2 py-1 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="!isOnline(source) || logBusy"
              @click="requestLogs(source)"
            >回传日志</button>
            <button
              v-if="canDeleteSource(source)"
              type="button"
              class="text-xs px-2 py-1 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
              :disabled="Boolean(deleteBusy[source.id])"
              @click="deleteSource(source)"
            >{{ deleteBusy[source.id] ? '删除中' : '删除' }}</button>
          </div>
        </div>
      </div>
      <div v-if="!sources.length" class="col-span-full py-16 text-center text-slate-400">
        {{ loadError ? '电脑列表没有加载出来' : '还没有电脑数据。请在远端运行同步助手，或先在本机解密企业微信。' }}
      </div>
    </div>

    <div v-else class="flex flex-col h-[calc(100vh-var(--ci-chrome)-5rem)]">
      <div class="mb-4 shrink-0 space-y-3">
        <h2 class="text-xl font-semibold text-slate-900 flex flex-wrap items-center gap-x-2 gap-y-1">
          <span class="break-words">{{ sourceTitle(selectedSource) }}</span>
          <span
            v-if="isUpdating(selectedSource)"
            class="inline-flex items-center gap-1 text-sm font-medium text-amber-600"
          >
            <span class="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse"></span>
            更新中
          </span>
          <span
            v-else-if="isOnline(selectedSource)"
            class="inline-flex items-center gap-1 text-sm font-medium text-emerald-600"
          >
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            在线
          </span>
          <span class="text-xs font-normal text-slate-400 break-all">
            助手 {{ sourceAgentVersion(selectedSource) }}
            <span v-if="isOutdated(selectedSource)" class="ml-1 text-amber-600">可更新</span>
          </span>
          <span class="text-slate-400 font-normal">会话列表</span>
        </h2>
        <div class="flex flex-wrap items-center gap-2">
          <button
            type="button"
            class="text-sm px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!canPushUpdate(selectedSource)"
            @click="pushUpdate(selectedSource)"
          >{{ selectedSource ? updateButtonLabel(selectedSource) : '推送更新' }}</button>
          <button
            type="button"
            class="text-sm px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!canSyncNow(selectedSource)"
            @click="syncNow(selectedSource)"
          >{{ syncNowLabel(selectedSource) }}</button>
          <button
            type="button"
            class="text-sm px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="!isOnline(selectedSource) || logBusy"
            @click="requestLogs(selectedSource)"
          >回传日志</button>
          <button
            v-if="selectedSource && canDeleteSource(selectedSource)"
            type="button"
            class="text-sm px-3 py-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="Boolean(deleteBusy[selectedSource.id])"
            @click="deleteSource(selectedSource)"
          >{{ deleteBusy[selectedSource.id] ? '删除中' : '删除电脑' }}</button>
          <input
            v-model="sessionQuery"
            type="search"
            placeholder="筛选会话"
            class="text-sm border border-slate-200 rounded-lg px-3 py-1.5 w-36 focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          />
          <button @click="clearSelectedSource" class="text-sm text-slate-500 hover:text-slate-700">
            返回电脑列表
          </button>
        </div>
      </div>

      <div v-if="sessionsLoading" class="flex items-center justify-center py-10">
        <div class="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
      </div>

      <div
        v-else
        class="ci-card flex-1 min-h-0 overflow-y-auto"
      >
        <div class="divide-y divide-slate-100">
          <div
            v-for="session in visibleSessions"
            :key="session.username"
            class="flex items-center gap-4 px-5 py-3.5 hover:bg-[#f0f5fd] cursor-pointer transition-colors"
            @click="openChat(session)"
          >
            <div class="w-10 h-10 rounded-full bg-gradient-to-br from-primary-400 to-primary-600 flex items-center justify-center text-white text-sm font-medium shrink-0">
              {{ (session.display_name || session.username).charAt(0) }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span class="font-medium text-slate-900 break-words">{{ session.display_name || session.username }}</span>
                <span class="text-[11px] px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-500">
                  {{ session.session_type === 2 ? '群聊' : '单聊' }}
                </span>
                <span v-if="session.msg_count" class="text-xs text-slate-400">{{ session.msg_count }} 条</span>
                <span
                  v-if="isFreshSync(session)"
                  class="text-[11px] px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-700"
                >最新同步</span>
              </div>
              <p class="text-sm text-slate-500 break-words mt-0.5">{{ session.summary || session.username }}</p>
            </div>
            <div class="text-right shrink-0">
              <div v-if="session.synced_at" class="text-xs text-emerald-600">{{ session.synced_at }} 同步</div>
              <span class="text-xs text-slate-400">{{ session.last_time }}</span>
            </div>
          </div>
        </div>
        <div v-if="sessionError" class="py-10 text-center text-amber-700">{{ sessionError }}</div>
        <div v-else-if="!visibleSessions.length" class="py-10 text-center text-slate-400">
          {{ sessionQuery ? '没有匹配的会话' : '暂无会话数据' }}
        </div>
      </div>
    </div>

    <div
      v-if="logViewer"
      class="fixed inset-0 z-[90] bg-slate-900/40 flex items-center justify-center p-4"
      @click.self="closeLogViewer"
    >
      <div class="bg-white rounded-2xl shadow-xl w-full max-w-3xl max-h-[80vh] flex flex-col">
        <div class="px-5 py-4 border-b border-slate-200 flex items-center justify-between gap-3">
          <div class="min-w-0">
            <div class="font-semibold text-slate-900 truncate">
              {{ logViewer.source ? sourceTitle(logViewer.source) : '' }} 助手日志
            </div>
            <p class="text-xs text-slate-500 mt-0.5">{{ logViewer.detail || '最近回传的 agent.log，最新在最上面' }}</p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <a
              v-if="logViewer.status === 'ready' && logViewer.source"
              :href="chatApi.agentLogFileUrl(logViewer.source.id)"
              class="text-sm px-3 py-1.5 rounded-lg bg-primary-50 text-primary-800 hover:bg-primary-100"
            >下载</a>
            <button
              type="button"
              class="text-sm text-slate-500 hover:text-slate-700"
              @click="closeLogViewer"
            >关闭</button>
          </div>
        </div>
        <pre
          class="px-5 py-4 text-xs font-mono text-slate-700 whitespace-pre-wrap break-all overflow-auto flex-1"
        >{{ logViewer.text || (logBusy ? '正在等待助手回传…' : logViewer.detail) }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, onMounted, onUnmounted, ref, type Ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiErrorMessage, chatApi, PARSER_API, type Source, type Session, type IngestInfo, type PresenceClient } from '../api'

const SOURCE_KEY = 'chatinsight-source'

const router = useRouter()
const sources = ref<Source[]>([])
const sessions = ref<Session[]>([])
const sessionQuery = ref('')
const sessionError = ref('')
const selectedSource = ref<Source | null>(null)
const loadError = ref('')
const ingest = ref<IngestInfo | null>(null)
const copied = ref('')
const loading = ref(true)
const sessionsLoading = ref(false)
const online = inject<Ref<PresenceClient[]>>('presenceOnline', ref([]))
const logViewer = ref<{
  source: Source
  status: 'waiting' | 'ready' | 'error'
  text: string
  detail: string
} | null>(null)
const logBusy = ref(false)
const syncBusy = ref<Record<string, boolean>>({})
const pushBusy = ref<Record<string, boolean>>({})
const deleteBusy = ref<Record<string, boolean>>({})
const pushNotice = ref('')
let logSeq = 0
let timer: ReturnType<typeof setInterval> | null = null
let copiedTimer: ReturnType<typeof setTimeout> | null = null
let pushTimer: ReturnType<typeof setTimeout> | null = null

onMounted(async () => {
  await loadSources(true)
  timer = setInterval(() => loadSources(false), 8000)
  window.addEventListener('chatinsight-presence', onPresenceEvent)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (copiedTimer) clearTimeout(copiedTimer)
  if (pushTimer) clearTimeout(pushTimer)
  window.removeEventListener('chatinsight-presence', onPresenceEvent)
})

function isLoopbackUrl(url: string) {
  return /:\/\/(127\.0\.0\.1|localhost)(:|$)/i.test(url)
}

const lanWebUrls = computed(() => {
  const explicit = (ingest.value?.web_urls || []).filter((url) => !isLoopbackUrl(url))
  if (explicit.length) return explicit
  return (ingest.value?.urls || [])
    .filter((url) => !isLoopbackUrl(url))
    .map((url) => url.replace(/:8767\/?$/, ':5173'))
})
const lanApiUrls = computed(() => (ingest.value?.urls || []).filter((url) => !isLoopbackUrl(url)))
const lanReadApiUrls = computed(() => {
  const explicit = (ingest.value?.read_api?.urls || []).filter((url) => !isLoopbackUrl(url))
  if (explicit.length) return explicit
  return lanApiUrls.value.map((url) => url.replace(/\/?$/, '') + '/v1')
})
const readApiKeys = computed(() => {
  const info = ingest.value?.read_api
  if (!info) return []
  if (info.keys && info.keys.length) return info.keys
  return info.key ? [{ name: 'default', key: info.key }] : []
})
const latestAgentVersion = computed(() => {
  return ingest.value?.latest_agent_version
    || ingest.value?.agent_update?.version
    || '未知'
})
const agentPublished = computed(() => Boolean(ingest.value?.agent_update?.published))
const latestAgentNotes = computed(() => (ingest.value?.agent_update?.notes || '').trim())
const agentDownloadHref = computed(() => {
  const rel = ingest.value?.agent_update?.download_url || '/api/agent-exe'
  if (rel.startsWith('http://') || rel.startsWith('https://')) return rel
  return PARSER_API + rel.replace(/^\/api/, '')
})
const agentSizeLabel = computed(() => {
  const n = Number(ingest.value?.agent_update?.size || 0)
  if (!n) return ''
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB'
  if (n >= 1024) return Math.round(n / 1024) + ' KB'
  return n + ' B'
})
const uninstallPublished = computed(() => Boolean(ingest.value?.uninstall_published))
const uninstallDownloadHref = computed(() => {
  const rel = ingest.value?.uninstall_download_url || '/api/agent-uninstall-exe'
  if (rel.startsWith('http://') || rel.startsWith('https://')) return rel
  return PARSER_API + rel.replace(/^\/api/, '')
})

async function copyText(value: string) {
  const text = (value || '').trim()
  if (!text) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const input = document.createElement('textarea')
      input.value = text
      input.setAttribute('readonly', '')
      input.style.position = 'fixed'
      input.style.left = '-9999px'
      document.body.appendChild(input)
      input.select()
      document.execCommand('copy')
      input.remove()
    }
    copied.value = text
    if (copiedTimer) clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => {
      copied.value = ''
    }, 2000)
  } catch (e) {
    console.error(e)
  }
}

function onPresenceEvent() {
  loadSources(false)
}

async function loadSources(initial = false) {
  try {
    const [src, info] = await Promise.all([
      chatApi.getSources(),
      chatApi.getIngestInfo(),
    ])
    sources.value = src.data
    loadError.value = ''
    if (info) ingest.value = info.data
    if (!selectedSource.value) {
      const saved = sessionStorage.getItem(SOURCE_KEY)
      const found = sources.value.find((item) => item.id === saved)
      if (found) selectedSource.value = found
    }
    if (selectedSource.value) {
      const latest = sources.value.find((item) => item.id === selectedSource.value?.id)
      if (latest) selectedSource.value = latest
      else selectedSource.value = null
      if (selectedSource.value) await refreshSessions(false)
    }
  } catch (e) {
    console.error(e)
    loadError.value = apiErrorMessage(e, '电脑列表加载失败')
  } finally {
    if (initial) loading.value = false
  }
}

function sourceTitle(source: Source) {
  return source.operator_name || source.computer_name
}

function sourceSubtitle(source: Source) {
  if (source.operator_name && source.computer_name) {
    return `${source.computer_name} · ${sourceKindLabel(source)}`
  }
  return sourceKindLabel(source)
}

function sourceKindLabel(source: Source) {
  if (source.kind === 'remote') return '远端同步'
  if (source.kind === 'ssh') return 'SSH 直连'
  return '本机'
}

function presenceMatch(source: Source) {
  const list = online.value || []
  const computer = (source.computer_name || "").replace(/（本机）|（SSH）/g, "").trim()
  const host = (source.host || "").trim()
  return list.find((item) => {
    if (item.source_id && item.source_id === source.id) return true
    if (source.operator_name && item.operator_name && source.operator_name === item.operator_name) {
      return true
    }
    const name = (item.computer_name || "").trim()
    if (name && computer && (computer === name || computer.includes(name))) return true
    if (host && item.host && host === item.host) return true
    return false
  })
}

function isUpdating(source: Source) {
  return presenceMatch(source)?.status === "updating"
}

function isOnline(source: Source) {
  const hit = presenceMatch(source)
  return Boolean(hit) && hit?.status !== "updating"
}

function sourceAgentVersion(source: Source | null) {
  if (!source) return '版本未知'
  const fromSource = (source.agent_version || '').trim()
  if (fromSource) return fromSource
  const fromPresence = (presenceMatch(source)?.agent_version || '').trim()
  return fromPresence || '版本未知'
}

function isOutdated(source: Source | null) {
  if (!source) return false
  if (source.update_available) return true
  const latest = latestAgentVersion.value
  const current = sourceAgentVersion(source)
  if (!latest || latest === '未知' || current === '版本未知') return false
  return versionNewer(latest, current)
}

function versionNewer(remote: string, local: string) {
  const a = (remote || '').split('.').map((n) => parseInt(n, 10)).filter((n) => !Number.isNaN(n))
  const b = (local || '').split('.').map((n) => parseInt(n, 10)).filter((n) => !Number.isNaN(n))
  const len = Math.max(a.length, b.length)
  for (let i = 0; i < len; i += 1) {
    const left = a[i] || 0
    const right = b[i] || 0
    if (left > right) return true
    if (left < right) return false
  }
  return false
}

function canSyncNow(source: Source | null) {
  if (!source || !isOnline(source) || isUpdating(source)) return false
  return !syncBusy.value[source.id]
}

function syncNowLabel(source: Source | null) {
  if (source && syncBusy.value[source.id]) return '通知中'
  return '立即同步'
}

async function syncNow(source: Source | null) {
  if (!source || !canSyncNow(source)) return
  syncBusy.value = { ...syncBusy.value, [source.id]: true }
  try {
    const { data } = await chatApi.requestSyncNow(source.id)
    showPushNotice(data.detail || `已通知 ${sourceTitle(source)} 立即同步`)
  } catch (err) {
    showPushNotice(axiosDetail(err, '立即同步失败'))
  } finally {
    const next = { ...syncBusy.value }
    delete next[source.id]
    syncBusy.value = next
  }
}

function canPushUpdate(source: Source | null) {
  if (!source) return false
  if (!agentPublished.value) return false
  if (!isOnline(source) || isUpdating(source)) return false
  return !pushBusy.value[source.id]
}

function updateButtonLabel(source: Source) {
  if (pushBusy.value[source.id]) return '推送中'
  if (isUpdating(source)) return '更新中'
  if (isOutdated(source)) return '推送更新'
  return '推送更新'
}

async function pushUpdate(source: Source | null) {
  if (!source || !canPushUpdate(source)) return
  pushBusy.value = { ...pushBusy.value, [source.id]: true }
  try {
    const { data } = await chatApi.pushAgentUpdate(source.id)
    showPushNotice(data.detail || `已通知 ${sourceTitle(source)} 更新到 ${data.version || latestAgentVersion.value}`)
  } catch (err) {
    showPushNotice(axiosDetail(err, '推送失败'))
  } finally {
    const next = { ...pushBusy.value }
    delete next[source.id]
    pushBusy.value = next
  }
}

function canDeleteSource(source: Source | null) {
  if (!source) return false
  if (source.kind === 'local' || source.id === 'local') return false
  return true
}

async function deleteSource(source: Source | null) {
  if (!source || !canDeleteSource(source) || deleteBusy.value[source.id]) return
  const title = sourceTitle(source)
  const ok = window.confirm(
    `确定删除「${title}」？\n会从分析机去掉这台电脑的聊天记录。助手如果还在运行，下次心跳会再次出现。`,
  )
  if (!ok) return
  deleteBusy.value = { ...deleteBusy.value, [source.id]: true }
  try {
    await chatApi.deleteSource(source.id)
    sources.value = sources.value.filter((item) => item.id !== source.id)
    if (selectedSource.value?.id === source.id) {
      clearSelectedSource()
    }
    showPushNotice(`已删除 ${title}`)
  } catch (err) {
    showPushNotice(axiosDetail(err, '删除失败'))
  } finally {
    const next = { ...deleteBusy.value }
    delete next[source.id]
    deleteBusy.value = next
  }
}

function showPushNotice(text: string) {
  pushNotice.value = text
  if (pushTimer) clearTimeout(pushTimer)
  pushTimer = setTimeout(() => {
    pushNotice.value = ''
  }, 4000)
}

function newestLogFirst(text: string) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  while (lines.length && lines[lines.length - 1] === '') lines.pop()
  return lines.reverse().join('\n')
}

function axiosDetail(err: unknown, fallback: string) {
  return apiErrorMessage(err, fallback)
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function closeLogViewer() {
  logSeq += 1
  logBusy.value = false
  logViewer.value = null
}

async function requestLogs(source: Source | null) {
  if (!source || logBusy.value) return
  if (!isOnline(source)) {
    logViewer.value = {
      source,
      status: 'error',
      text: '',
      detail: '助手离线，无法回传日志',
    }
    return
  }
  const seq = ++logSeq
  logBusy.value = true
  logViewer.value = {
    source,
    status: 'waiting',
    text: '',
    detail: '已通知助手，正在等待回传…',
  }
  try {
    const created = await chatApi.requestAgentLog(source.id)
    if (seq !== logSeq) return
    const jobId = created.data.job_id || ''
    const deadline = Date.now() + 90_000
    while (seq === logSeq) {
      const res = await chatApi.getAgentLog(source.id, jobId)
      if (seq !== logSeq) return
      if (res.status === 202) {
        if (Date.now() > deadline) {
          logViewer.value = {
            source,
            status: 'error',
            text: '',
            detail: '等待超时，请确认助手在线且已更新到支持回传日志的版本',
          }
          return
        }
        await sleep(2000)
        continue
      }
      if (res.status >= 400) {
        const detail = (res.data as { detail?: string })?.detail || '回传失败'
        logViewer.value = { source, status: 'error', text: '', detail: String(detail) }
        return
      }
      logViewer.value = {
        source,
        status: 'ready',
        text: res.data.text ? newestLogFirst(res.data.text) : '（日志为空）',
        detail: (res.data.filename || 'agent.log') + '，最新在最上面',
      }
      return
    }
  } catch (err) {
    if (seq !== logSeq) return
    logViewer.value = {
      source,
      status: 'error',
      text: '',
      detail: axiosDetail(err, '回传失败'),
    }
  } finally {
    if (seq === logSeq) logBusy.value = false
  }
}

function isFreshSync(session: Session) {
  if (!session.synced_at) return false
  let newest = ''
  for (const item of sessions.value) {
    if ((item.synced_at || '') > newest) newest = item.synced_at || ''
  }
  return Boolean(newest) && session.synced_at === newest
}

function byLastTime(a: Session, b: Session) {
  const byTime = (b.last_time || '').localeCompare(a.last_time || '')
  if (byTime) return byTime
  return (a.display_name || a.username || '').localeCompare(b.display_name || b.username || '')
}

const visibleSessions = computed(() => {
  const q = sessionQuery.value.trim().toLowerCase()
  if (!q) return sessions.value
  return sessions.value.filter((session) => {
    const name = (session.display_name || '').toLowerCase()
    const id = (session.username || '').toLowerCase()
    return name.includes(q) || id.includes(q)
  })
})

function clearSelectedSource() {
  selectedSource.value = null
  sessionQuery.value = ''
  sessionStorage.removeItem(SOURCE_KEY)
}

async function selectSource(source: Source) {
  selectedSource.value = source
  sessionQuery.value = ''
  sessionStorage.setItem(SOURCE_KEY, source.id)
  await refreshSessions(true)
}

async function refreshSessions(showLoading: boolean) {
  if (!selectedSource.value) return
  if (showLoading) sessionsLoading.value = true
  try {
    const { data } = await chatApi.getSourceSessions(selectedSource.value.id)
    sessions.value = [...data].sort(byLastTime)
    sessionError.value = ''
  } catch (e) {
    console.error(e)
    sessionError.value = apiErrorMessage(e, '会话列表加载失败')
    if (showLoading) sessions.value = []
  } finally {
    sessionsLoading.value = false
  }
}

function openChat(session: Session) {
  if (!selectedSource.value) return
  router.push({
    name: 'chat',
    params: {
      sourceId: selectedSource.value.id,
      sessionId: session.username,
    },
    query: { name: session.display_name || session.username },
  })
}
</script>
