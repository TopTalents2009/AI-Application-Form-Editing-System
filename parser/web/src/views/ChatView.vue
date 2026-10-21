<template>
  <div class="flex flex-col h-[calc(100vh-var(--ci-chrome)-2.5rem)]">
    <div class="flex flex-col gap-3 mb-4">
      <div class="flex items-start gap-3 min-w-0">
        <router-link to="/" class="text-slate-400 hover:text-slate-600 transition-colors mt-1">
          <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
          </svg>
        </router-link>
        <div class="min-w-0 flex-1">
          <h1 class="text-xl font-semibold text-slate-900 break-words">{{ sessionTitle }}</h1>
          <p v-if="sessionTitle !== sessionId" class="text-xs text-slate-400 break-all">{{ sessionId }}</p>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <form class="flex items-center gap-2" @submit.prevent="searchInSession">
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索本会话"
            class="text-sm border border-slate-200 rounded-lg px-3 py-1.5 w-36 focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
          />
          <button
            type="submit"
            class="text-[13px] font-semibold text-white px-4 py-1.5 rounded-[10px] bg-gradient-to-r from-primary-500 to-primary-400 hover:from-primary-600 hover:to-primary-500"
          >
            搜索
          </button>
        </form>
        <input
          v-model="startDate"
          type="date"
          class="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
        />
        <span class="text-slate-400">~</span>
        <input
          v-model="endDate"
          type="date"
          class="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
        />
        <button
          @click="loadMessages"
          class="text-[13px] font-semibold text-white px-4 py-1.5 rounded-[10px] bg-gradient-to-r from-primary-500 to-primary-400 hover:from-primary-600 hover:to-primary-500"
        >
          查询
        </button>
        <router-link
          :to="{ name: 'stats', params: { sourceId, sessionId }, query: route.query }"
          class="text-[13px] font-semibold text-[#46506a] bg-white border border-[#e2e8f2] px-3.5 py-1.5 rounded-[10px] hover:bg-[#f0f5fd] hover:text-primary-500"
        >
          统计
        </router-link>
      </div>
    </div>

    <div
      v-if="loadError"
      class="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 flex items-center justify-between gap-3"
    >
      <span>{{ loadError }}</span>
      <button type="button" class="shrink-0 underline" @click="loadMessages">重试</button>
    </div>

    <div
      ref="listEl"
      class="ci-card flex-1 min-h-0 overflow-y-auto p-4 space-y-3"
    >
      <div v-if="loading" class="flex items-center justify-center py-10">
        <div class="animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
      </div>

      <div v-else-if="!messages.length" class="flex items-center justify-center py-10 text-slate-400">
        {{ loadError ? '消息没有加载出来' : '暂无消息' }}
      </div>

      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="flex gap-3 group"
      >
        <div class="w-8 h-8 rounded-full bg-gradient-to-br from-slate-200 to-slate-300 flex items-center justify-center text-xs font-medium text-slate-600 shrink-0 mt-0.5">
          {{ (msg.sender || '?').charAt(0) }}
        </div>
        <div class="flex-1 min-w-0">
          <div class="flex items-baseline gap-2">
            <span class="text-sm font-medium text-slate-900">{{ msg.sender }}</span>
            <span class="text-xs text-slate-400">{{ msg.time_text }}</span>
          </div>
          <p
            v-if="captionText(msg, i)"
            class="text-sm text-slate-700 mt-0.5 whitespace-pre-wrap break-words"
          >{{ captionText(msg, i) }}</p>
          <div v-if="currentImageSrc(msg, i)" class="mt-2">
            <button
              type="button"
              class="block text-left"
              @click="previewUrl = currentImageSrc(msg, i)"
            >
              <img
                :src="currentImageSrc(msg, i)"
                :alt="fileName(msg) || '图片'"
                referrerpolicy="no-referrer"
                loading="lazy"
                class="max-w-xs max-h-72 rounded-xl border border-slate-200 object-contain bg-slate-50 cursor-zoom-in hover:opacity-95"
                @error="onImgError(msg, i)"
              />
            </button>
          </div>
          <div
            v-else-if="showFileCard(msg, i)"
            class="mt-2 max-w-sm flex items-center gap-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2"
          >
            <div class="w-10 h-10 rounded-lg bg-white border border-slate-200 flex items-center justify-center text-[10px] font-semibold text-slate-500 shrink-0">
              {{ fileExt(fileName(msg)) }}
            </div>
            <div class="min-w-0 flex-1">
              <div class="text-sm font-medium text-slate-900 break-all">{{ fileName(msg) || '聊天文件' }}</div>
              <div class="text-xs text-slate-400">{{ fileStatusText(msg) }}</div>
            </div>
            <button
              type="button"
              class="shrink-0 text-sm px-3 py-1.5 rounded-lg bg-primary-600 text-white hover:bg-primary-700 disabled:bg-slate-300 disabled:text-slate-500"
              :disabled="!msg.message_id || ['loading', 'waiting'].includes(downloadState[downloadKey(msg)] || '')"
              @click="downloadFile(msg)"
            >
              {{ downloadLabel(msg) }}
            </button>
          </div>
        </div>
      </div>
      <div ref="endEl"></div>
    </div>

    <Teleport to="body">
      <div
        v-if="previewUrl"
        class="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center p-6"
        @click="previewUrl = ''"
      >
        <img
          :src="previewUrl"
          alt="预览"
          referrerpolicy="no-referrer"
          class="max-w-[92vw] max-h-[92vh] rounded-lg shadow-2xl object-contain"
          @click.stop
        />
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage, chatApi, PARSER_API, type Message } from '../api'

const route = useRoute()
const router = useRouter()
const platform = 'wecom'
const sourceId = route.params.sourceId as string
const sessionId = route.params.sessionId as string

const messages = ref<Message[]>([])
const listEl = ref<HTMLElement | null>(null)
const endEl = ref<HTMLElement | null>(null)
const loading = ref(true)
const loadError = ref('')
const sessionTitle = computed(() => String(route.query.name || sessionId))
const startDate = ref('')
const endDate = ref('')
const keyword = ref('')
const previewUrl = ref('')
const imgFallback = ref<Record<number, number>>({})
const downloadState = ref<Record<string, 'loading' | 'waiting' | 'done' | 'error' | 'missing' | 'timeout'>>({})

const IMAGE_HOST_RE = /https?:\/\/(?:wework\.qpic\.cn|wx\.qlogo\.cn|mmbiz\.qpic\.cn|pic\.weixin\.qq\.com)\S+/gi
const FILE_NAME_RE = /([^\s[\]/\\]+\.(?:zip|rar|7z|pdf|xlsx?|docx?|pptx?|png|jpe?g|gif|webp|bmp|mp4|mov|txt|csv|md))$/i
const FILE_TYPES = new Set([14, 15, 16, 20])
const ARCHIVE_EXT_RE = /\.(zip|rar|7z|pdf|xlsx?|docx?|pptx?|mp4|mov|txt|csv|md)$/i
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|bmp)$/i

onMounted(() => loadMessages())

function attachmentUrl(messageId: number) {
  return `${PARSER_API}/${platform}/attachments/${messageId}`
}

function proxyUrl(url: string) {
  return `${PARSER_API}/media/proxy?url=${encodeURIComponent(url)}`
}

function fileName(msg: Message) {
  if (msg.attachment_name) return msg.attachment_name
  const matched = (msg.text || '').match(FILE_NAME_RE)
  return matched ? matched[1] : ''
}

function fileExt(name: string) {
  const ext = (name.split('.').pop() || 'FILE').toUpperCase()
  return ext.slice(0, 4)
}

function downloadKey(msg: Message) {
  return String(msg.message_id || fileName(msg) || msg.time_text)
}

function isImagePreview(msg: Message) {
  const name = fileName(msg).toLowerCase()
  if (ARCHIVE_EXT_RE.test(name)) return false
  if (msg.media_url) return true
  if (sourceId === 'local' && msg.has_attachment && msg.message_id && (IMAGE_EXT_RE.test(name) || msg.msg_type === 4)) {
    return true
  }
  return msg.msg_type === 4 && urlsFromText(msg.text).length > 0
}

function showFileCard(msg: Message, index: number) {
  if (currentImageSrc(msg, index)) return false
  return !!fileName(msg) || FILE_TYPES.has(msg.msg_type) || (!!msg.has_attachment && !isImagePreview(msg))
}

function urlsFromText(text: string) {
  return (text || '').match(IMAGE_HOST_RE) || []
}

function imageCandidates(msg: Message) {
  if (!isImagePreview(msg)) return []
  const out: string[] = []
  const seen = new Set<string>()
  const push = (url?: string) => {
    if (!url || seen.has(url)) return
    seen.add(url)
    out.push(url)
  }
  const name = fileName(msg).toLowerCase()
  if (sourceId === 'local' && msg.has_attachment && msg.message_id && (IMAGE_EXT_RE.test(name) || msg.msg_type === 4)) {
    push(attachmentUrl(msg.message_id))
  }
  const remote = [msg.media_url, ...urlsFromText(msg.text)].filter(Boolean) as string[]
  for (const url of remote) {
    push(url)
    push(proxyUrl(url))
  }
  return out
}

function currentImageSrc(msg: Message, index: number) {
  const list = imageCandidates(msg)
  return list[imgFallback.value[index] || 0] || ''
}

function onImgError(msg: Message, index: number) {
  const list = imageCandidates(msg)
  const next = (imgFallback.value[index] || 0) + 1
  if (next < list.length) {
    imgFallback.value = { ...imgFallback.value, [index]: next }
  }
}

function captionText(msg: Message, index: number) {
  let text = (msg.text || '').trim()
  text = text.replace(IMAGE_HOST_RE, '').replace(/[ \t]+/g, ' ').trim()
  if (currentImageSrc(msg, index) || showFileCard(msg, index)) {
    text = text.replace(/^\[.*?\]\s*/, '')
    if (!text || text === fileName(msg)) {
      return ''
    }
  }
  return text
}

function fileStatusText(msg: Message) {
  const state = downloadState.value[downloadKey(msg)]
  if (state === 'loading') return '正在下载…'
  if (state === 'waiting') return '正在等待远端回传…'
  if (state === 'done') return '已保存到本机'
  if (state === 'missing') return '对方电脑未缓存该文件'
  if (state === 'timeout') return '等待超时，请确认助手在线'
  if (state === 'error') return sourceId === 'local' ? '文件不在本机缓存' : '下载失败'
  return '默认同步不下载，点击获取'
}

function downloadLabel(msg: Message) {
  const state = downloadState.value[downloadKey(msg)]
  if (state === 'loading' || state === 'waiting') return '下载中'
  if (state === 'done') return '已下载'
  if (state === 'error' || state === 'missing' || state === 'timeout') return '重试'
  return '下载'
}

function sleep(ms: number) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

async function readErrorDetail(res: Response) {
  const raw = await res.text()
  try {
    const data = JSON.parse(raw)
    if (data?.detail) return String(data.detail)
  } catch {
    /* ignore */
  }
  return raw
}

async function downloadFile(msg: Message) {
  if (!msg.message_id) return
  const key = downloadKey(msg)
  downloadState.value = {
    ...downloadState.value,
    [key]: sourceId === 'local' ? 'loading' : 'waiting',
  }
  try {
    const params = new URLSearchParams()
    if (sourceId !== 'local') params.set('session_id', sessionId)
    const url = `${PARSER_API}/sources/${encodeURIComponent(sourceId)}/attachments/${msg.message_id}?${params}`
    const deadline = Date.now() + 90_000
    while (true) {
      const res = await fetch(url)
      if (res.status === 202) {
        downloadState.value = { ...downloadState.value, [key]: 'waiting' }
        if (Date.now() > deadline) {
          downloadState.value = { ...downloadState.value, [key]: 'timeout' }
          return
        }
        await sleep(2000)
        continue
      }
      if (res.status === 404) {
        await readErrorDetail(res)
        downloadState.value = {
          ...downloadState.value,
          [key]: sourceId === 'local' ? 'error' : 'missing',
        }
        return
      }
      if (!res.ok) throw new Error(await readErrorDetail(res))
      const blob = await res.blob()
      const objectUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objectUrl
      link.download = fileName(msg) || 'chat-file'
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(objectUrl)
      downloadState.value = { ...downloadState.value, [key]: 'done' }
      return
    }
  } catch {
    downloadState.value = { ...downloadState.value, [key]: 'error' }
  }
}

function chronological(rows: Message[]) {
  return [...rows].sort((a, b) => {
    const byTime = (a.time_text || '').localeCompare(b.time_text || '')
    if (byTime) return byTime
    return (a.message_id || 0) - (b.message_id || 0)
  })
}

function scrollToLatest() {
  const el = listEl.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

async function loadMessages() {
  loading.value = true
  loadError.value = ''
  imgFallback.value = {}
  previewUrl.value = ''
  downloadState.value = {}
  try {
    const params: Record<string, any> = { limit: 5000 }
    if (startDate.value) params.start_date = startDate.value
    if (endDate.value) params.end_date = endDate.value
    const { data } = await chatApi.getSourceMessages(sourceId, sessionId, params)
    messages.value = chronological(data)
  } catch (e) {
    console.error('Failed to load messages:', e)
    messages.value = []
    loadError.value = apiErrorMessage(e, '消息加载失败')
  } finally {
    loading.value = false
  }
  await nextTick()
  endEl.value?.scrollIntoView({ block: 'end' })
  scrollToLatest()
  requestAnimationFrame(scrollToLatest)
}

function searchInSession() {
  const q = keyword.value.trim()
  if (!q) return
  router.push({
    name: 'search',
    query: {
      q,
      source_id: sourceId,
      session_id: sessionId,
      ...(route.query.name ? { name: String(route.query.name) } : {}),
    },
  })
}
</script>
