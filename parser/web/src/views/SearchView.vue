<template>
  <div>
    <div class="flex items-start gap-3 min-w-0 mb-6">
      <router-link :to="backTo" class="text-slate-400 hover:text-slate-600 transition-colors mt-1.5 shrink-0">
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
        </svg>
      </router-link>
      <div class="min-w-0 flex-1">
        <h1 class="text-[20px] font-bold text-[#1c2333] mb-1">搜索聊天记录</h1>
        <p class="text-[13px] text-[#5b6577]">在服务端检索已同步和本机解密的消息</p>
      </div>
      <router-link
        :to="backTo"
        class="text-[13px] font-semibold text-[#46506a] bg-white border border-[#e2e8f2] px-3.5 py-2 rounded-[10px] hover:bg-[#f0f5fd] hover:text-primary-500 transition-colors shrink-0"
      >
        {{ backLabel }}
      </router-link>
    </div>

    <form class="flex gap-2 mb-6" @submit.prevent="runSearch">
      <input
        v-model="query"
        type="search"
        autofocus
        placeholder="消息、发送者、文件名；空格优先同时包含，没有结果再按任意词"
        class="flex-1 text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary-500/20 focus:border-primary-500"
      />
      <button
        type="submit"
        class="text-[13px] font-semibold text-white px-4 py-2 rounded-[10px] bg-gradient-to-r from-primary-500 to-primary-400 hover:from-primary-600 hover:to-primary-500"
      >
        搜索
      </button>
    </form>

    <div v-if="scopeText" class="text-xs text-slate-400 mb-4">{{ scopeText }}</div>

    <div v-if="loading" class="flex items-center justify-center py-16">
      <div class="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
    </div>

    <div v-else-if="errorText" class="py-16 text-center text-amber-700">{{ errorText }}</div>

    <div v-else-if="searched && !hits.length" class="py-16 text-center text-slate-400">
      没有匹配的消息
    </div>

    <div v-else class="ci-card overflow-hidden">
      <div
        v-for="hit in hits"
        :key="`${hit.source_id}-${hit.session_id}-${hit.message_id}-${hit.time_text}`"
        class="px-6 py-4 border-b border-slate-100 last:border-b-0 hover:bg-slate-50 cursor-pointer"
        @click="openHit(hit)"
      >
        <div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-400 mb-1">
          <span class="text-slate-600 font-medium break-words">{{ hit.session_name }}</span>
          <span v-if="hit.source_name" class="break-words">· {{ hit.source_name }}</span>
          <span class="ml-auto">{{ hit.time_text }}</span>
        </div>
        <div class="text-sm text-slate-900 break-words">
          <span class="font-medium">{{ hit.sender || '未知' }}：</span>
          <span>{{ hit.snippet || hit.text || hit.attachment_name }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiErrorMessage, chatApi, type SearchHit } from '../api'

const route = useRoute()
const router = useRouter()
const query = ref('')
const hits = ref<SearchHit[]>([])
const loading = ref(false)
const searched = ref(false)
const errorText = ref('')

const sourceId = computed(() => String(route.query.source_id || ''))
const sessionId = computed(() => String(route.query.session_id || ''))
const sessionName = computed(() => String(route.query.name || ''))

const backTo = computed(() => {
  if (sourceId.value && sessionId.value) {
    return {
      name: 'chat',
      params: { sourceId: sourceId.value, sessionId: sessionId.value },
      query: sessionName.value ? { name: sessionName.value } : {},
    }
  }
  return { name: 'home' }
})

const backLabel = computed(() => {
  if (sourceId.value && sessionId.value) return '返回会话'
  return '返回首页'
})

const scopeText = computed(() => {
  const parts = []
  if (sourceId.value) parts.push(`来源 ${sourceId.value}`)
  if (sessionName.value) parts.push(`会话 ${sessionName.value}`)
  else if (sessionId.value) parts.push(`会话 ${sessionId.value}`)
  return parts.join(' · ')
})

onMounted(() => {
  query.value = String(route.query.q || '')
  if (query.value.trim()) runSearch()
})

watch(() => route.query.q, (value) => {
  const next = String(value || '')
  if (next !== query.value) {
    query.value = next
    if (next.trim()) runSearch()
  }
})

async function runSearch() {
  const q = query.value.trim()
  if (!q) return
  loading.value = true
  searched.value = true
  errorText.value = ''
  router.replace({
    name: 'search',
    query: {
      q,
      ...(sourceId.value ? { source_id: sourceId.value } : {}),
      ...(sessionId.value ? { session_id: sessionId.value } : {}),
      ...(sessionName.value ? { name: sessionName.value } : {}),
    },
  })
  try {
    const { data } = await chatApi.searchMessages({
      q,
      source_id: sourceId.value || undefined,
      session_id: sessionId.value || undefined,
      limit: 80,
    })
    hits.value = data
  } catch (e) {
    console.error(e)
    hits.value = []
    errorText.value = apiErrorMessage(e, '搜索失败')
  } finally {
    loading.value = false
  }
}

function openHit(hit: SearchHit) {
  router.push({
    name: 'chat',
    params: {
      sourceId: hit.source_id,
      sessionId: hit.session_id,
    },
    query: { name: hit.session_name || hit.session_id },
  })
}
</script>
