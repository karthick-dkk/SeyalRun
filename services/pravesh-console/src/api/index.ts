import { api } from './client'
import type {
  ModuleInfo, Playbook, Template, Job, AlertRule, AlertHistory,
  NotificationChannel, PaginatedResponse, TaskDefinition, VariableDefinition,
} from '@/types'

// ── Modules ───────────────────────────────────────────────────────────────────

export const modulesApi = {
  list: (category?: string) =>
    api.get<PaginatedResponse<ModuleInfo>>('/modules', { params: { category } }),
  get: (name: string) =>
    api.get<ModuleInfo>(`/modules/${encodeURIComponent(name)}`),
  params: (name: string) =>
    api.get<{ module: string; params: ModuleInfo['params']; example_task: string }>(
      `/modules/${encodeURIComponent(name)}/params`
    ),
  categories: () => api.get<string[]>('/modules/categories'),
}

// ── Playbooks ─────────────────────────────────────────────────────────────────

export const playbooksApi = {
  list: (params?: { category?: string; search?: string; limit?: number; offset?: number }) =>
    api.get<PaginatedResponse<Playbook>>('/playbooks', { params }),
  get: (id: string) => api.get<Playbook>(`/playbooks/${id}`),
  create: (data: { name: string; description?: string; category: string; tags: string[]; tasks: TaskDefinition[]; variables: VariableDefinition[] }) =>
    api.post<Playbook>('/playbooks', data),
  update: (id: string, data: { name: string; description?: string; category: string; tags: string[]; tasks: TaskDefinition[]; variables: VariableDefinition[] }) =>
    api.put<Playbook>(`/playbooks/${id}`, data),
  patch: (id: string, data: Partial<{ name: string; description?: string; category: string; tags: string[]; tasks: TaskDefinition[]; variables: VariableDefinition[] }>) =>
    api.patch<Playbook>(`/playbooks/${id}`, data),
  delete: (id: string) => api.delete(`/playbooks/${id}`),
  yaml: (id: string) => api.get<{ playbook_id: string; yaml: string }>(`/playbooks/${id}/yaml`),
  validate: (id: string) =>
    api.post<{ playbook_id: string; valid: boolean; warnings: string[]; task_count: number }>(
      `/playbooks/${id}/validate`
    ),
}

// ── Templates ─────────────────────────────────────────────────────────────────

export const templatesApi = {
  list: (category?: string) =>
    api.get<PaginatedResponse<Template>>('/templates', { params: { category } }),
  get: (slug: string) => api.get<Template>(`/templates/${slug}`),
  categories: () => api.get<string[]>('/templates/categories'),
  clone: (slug: string, data: { name?: string; description?: string }) =>
    api.post<Playbook>(`/templates/${slug}/clone`, data),
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

export const jobsApi = {
  list: (params?: { playbook_id?: string; status?: string; limit?: number; offset?: number }) =>
    api.get<PaginatedResponse<Job>>('/jobs', { params }),
  get: (id: string) => api.get<Job>(`/jobs/${id}`),
  execute: (data: { playbook_id: string; inventory_selector?: string; extra_vars?: Record<string, unknown> }) =>
    api.post<Job>('/jobs', data),
  cancel: (id: string) => api.post<Job>(`/jobs/${id}/cancel`),
}

// ── Alerts ────────────────────────────────────────────────────────────────────

export const alertsApi = {
  rules: {
    list: (params?: { enabled?: boolean; event_type?: string }) =>
      api.get<PaginatedResponse<AlertRule>>('/alerts/rules', { params }),
    get: (id: string) => api.get<AlertRule>(`/alerts/rules/${id}`),
    create: (data: Omit<AlertRule, 'id' | 'created_by' | 'created_at' | 'updated_at'>) =>
      api.post<AlertRule>('/alerts/rules', data),
    update: (id: string, data: Omit<AlertRule, 'id' | 'created_by' | 'created_at' | 'updated_at'>) =>
      api.put<AlertRule>(`/alerts/rules/${id}`, data),
    patch: (id: string, data: Partial<Omit<AlertRule, 'id' | 'created_by' | 'created_at' | 'updated_at'>>) =>
      api.patch<AlertRule>(`/alerts/rules/${id}`, data),
    delete: (id: string) => api.delete(`/alerts/rules/${id}`),
    test: (id: string) => api.post(`/alerts/rules/${id}/test`),
  },
  history: (params?: { rule_id?: string; delivery_status?: string; limit?: number }) =>
    api.get<{ total: number; items: AlertHistory[] }>('/alerts/history', { params }),
  channels: {
    list: () => api.get<{ total: number; items: NotificationChannel[] }>('/alerts/channels'),
    get: (id: string) => api.get<NotificationChannel>(`/alerts/channels/${id}`),
    create: (data: Omit<NotificationChannel, 'id' | 'created_at'>) =>
      api.post<NotificationChannel>('/alerts/channels', data),
    update: (id: string, data: Omit<NotificationChannel, 'id' | 'created_at'>) =>
      api.put<NotificationChannel>(`/alerts/channels/${id}`, data),
    delete: (id: string) => api.delete(`/alerts/channels/${id}`),
    test: (id: string) => api.post(`/alerts/channels/${id}/test`),
  },
}
