// ── Module types ──────────────────────────────────────────────────────────────

export interface ParamSchema {
  name: string
  type: string
  required: boolean
  default?: string
  choices?: string[]
  description: string
  example?: string
}

export interface ModuleInfo {
  name: string
  short_name: string
  category: string
  description: string
  docs_url: string
  params: ParamSchema[]
  example_task: string
}

// ── Playbook types ────────────────────────────────────────────────────────────

export interface TaskDefinition {
  task_id: string
  name: string
  module: string
  params: Record<string, unknown>
  when?: string
  register?: string
  become?: boolean
  ignore_errors?: boolean
  notify?: string[]
  tags?: string[]
}

export interface VariableDefinition {
  name: string
  default_value?: string
  description?: string
  required?: boolean
}

export interface Playbook {
  id: string
  name: string
  description?: string
  category: string
  tags: string[]
  tasks: TaskDefinition[]
  variables: VariableDefinition[]
  is_template: boolean
  source_template_id?: string
  created_by: string
  created_at: string
  updated_at: string
}

// ── Template types ────────────────────────────────────────────────────────────

export interface TemplateVarDef {
  name: string
  description?: string
  default_value?: string
  required: boolean
}

export interface Template {
  slug: string
  name: string
  description: string
  category: string
  tags: string[]
  required_vars: TemplateVarDef[]
  tasks: TaskDefinition[]
  variables: VariableDefinition[]
  estimated_duration_seconds: number
  risk_level: 'low' | 'medium' | 'high'
}

// ── Job types ─────────────────────────────────────────────────────────────────

export type JobStatus = 'pending' | 'running' | 'success' | 'failed' | 'cancelled'

export interface Job {
  id: string
  playbook_id?: string
  status: JobStatus
  triggered_by: string
  ab_job_id?: string
  inventory_selector: string
  extra_vars: Record<string, unknown>
  started_at?: string
  finished_at?: string
  duration_seconds?: number
  exit_code?: number
  output_lines: string[]
  created_at: string
}

// ── Alert types ───────────────────────────────────────────────────────────────

export interface AlertConditions {
  playbook_pattern?: string
  triggered_by_pattern?: string
  duration_threshold_seconds?: number
  host_group_pattern?: string
  severity_min?: number
}

export interface AlertChannel {
  type: 'webhook' | 'email'
  url?: string
  secret?: string
  to?: string[]
  subject?: string
}

export interface AlertRule {
  id: string
  name: string
  description?: string
  enabled: boolean
  event_type: string
  conditions: AlertConditions
  channels: AlertChannel[]
  created_by: string
  created_at: string
  updated_at: string
}

export interface AlertHistory {
  id: string
  rule_id?: string
  rule_name: string
  event_type: string
  event_payload: Record<string, unknown>
  channels_tried: Array<{type: string; success: boolean; error?: string}>
  delivery_status: string
  delivered_at: string
  error_detail?: string
}

export interface NotificationChannel {
  id: string
  name: string
  channel_type: string
  config: Record<string, unknown>
  is_active: boolean
  created_at: string
}

// ── API response wrapper ──────────────────────────────────────────────────────

export interface PaginatedResponse<T> {
  total: number
  items: T[]
}
