<template>
  <div class="ci-shell" :class="{ 'is-embed': embedded }">
    <header v-if="!embedded" class="ci-topbar">
      <div class="tlogo">析</div>
      <div>
        <h1>解析控制台</h1>
        <div class="tsub">申报书智能修改系统 · PARSER CONSOLE</div>
      </div>
      <div class="grow"></div>
      <form class="ci-search hidden sm:flex" @submit.prevent="goSearch">
        <input v-model="navQuery" type="search" placeholder="搜索聊天记录" />
        <button type="submit" class="tbtn">搜索</button>
      </form>
      <a class="tbtn" href="/">打开系统</a>
      <a class="tbtn" href="/admin">管理后台</a>
    </header>

    <div v-else class="ci-embed-bar">
      <div>
        <h2 class="sec-title">解析控制台</h2>
        <p class="sec-desc">按电脑查看同步记录与检索；合并会话仍在「会话记录」</p>
      </div>
      <div class="grow"></div>
      <form class="ci-search" @submit.prevent="goSearch">
        <input v-model="navQuery" type="search" placeholder="搜索聊天记录" />
        <button type="submit" class="tbtn">搜索</button>
      </form>
    </div>

    <main class="ci-main">
      <router-view />
    </main>

    <div class="fixed bottom-4 right-4 z-[80] space-y-2 w-80 max-w-[calc(100vw-2rem)]">
      <div v-for="toast in toasts" :key="toast.id" class="ci-toast">
        <div class="t">{{ toast.title }}</div>
        <div class="m">{{ toast.message }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, provide, ref } from 'vue'
import { useRouter } from 'vue-router'
import { chatApi, type PresenceClient, type PresenceAlert } from './api'

const router = useRouter()
const navQuery = ref('')
const embedded = typeof window !== 'undefined' && window.self !== window.top

const online = ref<PresenceClient[]>([])
const toasts = ref<PresenceAlert[]>([])
const lastAlertId = ref(0)
let primed = false
let timer: ReturnType<typeof setInterval> | null = null

provide('presenceOnline', online)
provide('embedded', embedded)

onMounted(() => {
  if (embedded) document.body.classList.add('is-embed')
  if (!embedded && typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => undefined)
  }
  pollPresence()
  timer = setInterval(pollPresence, 4000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function pollPresence() {
  try {
    const { data } = await chatApi.getPresence(lastAlertId.value)
    online.value = data.online || []
    const alerts = data.alerts || []
    window.dispatchEvent(new CustomEvent('chatinsight-presence', { detail: { online: online.value, alerts } }))
    if (!primed) {
      primed = true
      if (alerts.length) lastAlertId.value = Math.max(...alerts.map((item) => item.id))
      return
    }
    if (alerts.length) {
      lastAlertId.value = Math.max(...alerts.map((item) => item.id))
      for (const alert of alerts) {
        pushToast(alert)
        desktopNotify(alert)
      }
    }
  } catch (e) {
    console.error(e)
  }
}

function pushToast(alert: PresenceAlert) {
  toasts.value = [...toasts.value, alert].slice(-4)
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== alert.id)
  }, 8000)
}

function desktopNotify(alert: PresenceAlert) {
  if (embedded) return
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  try {
    new Notification(alert.title, { body: alert.message })
  } catch {
    /* ignore */
  }
}

function goSearch() {
  const q = navQuery.value.trim()
  if (!q) return
  router.push({ name: 'search', query: { q } })
}
</script>
