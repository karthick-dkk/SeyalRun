import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'

export interface UserInfo {
  id: string
  username: string
  name: string
  email: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('pravesh_token') || '')
  const user = ref<UserInfo | null>(null)
  const loading = ref(false)

  const isAuthenticated = computed(() => !!token.value && !!user.value)

  async function login(jmsToken: string) {
    loading.value = true
    try {
      const resp = await axios.get('/api/v1/me', {
        headers: { Authorization: `Bearer ${jmsToken}` },
      })
      user.value = {
        id: resp.data.id,
        username: resp.data.username,
        name: resp.data.name,
        email: resp.data.email,
      }
      token.value = jmsToken
      localStorage.setItem('pravesh_token', jmsToken)
    } finally {
      loading.value = false
    }
  }

  async function checkToken() {
    if (!token.value) return false
    try {
      const resp = await axios.get('/api/v1/me', {
        headers: { Authorization: `Bearer ${token.value}` },
      })
      user.value = {
        id: resp.data.id,
        username: resp.data.username,
        name: resp.data.name,
        email: resp.data.email,
      }
      return true
    } catch {
      logout()
      return false
    }
  }

  function setUser(userInfo: UserInfo, jmsToken: string) {
    user.value = userInfo
    token.value = jmsToken
    localStorage.setItem('pravesh_token', jmsToken)
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('pravesh_token')
  }

  return { token, user, loading, isAuthenticated, login, checkToken, setUser, logout }
})
