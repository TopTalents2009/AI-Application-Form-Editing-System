import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('../views/HomeView.vue'),
    },
    {
      path: '/search',
      name: 'search',
      component: () => import('../views/SearchView.vue'),
    },
    {
      path: '/chat/:sourceId/:sessionId',
      name: 'chat',
      component: () => import('../views/ChatView.vue'),
    },
    {
      path: '/stats/:sourceId/:sessionId',
      name: 'stats',
      component: () => import('../views/StatsView.vue'),
    },
  ],
})

export default router
