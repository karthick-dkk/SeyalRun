import { ref } from 'vue'
import api from '@/api/client'

// Shared pending-approval counts (sidebar badge, dashboard card, Approvals page).
// Both lists degrade to 0 for roles that cannot read them.
export const pendingRuns = ref(0)
export const pendingAuthz = ref(0)

export async function refreshApprovals(isAdmin: boolean) {
  try {
    const { data } = await api.get('/job-runs', { params: { status: 'pending_approval' } })
    pendingRuns.value = Array.isArray(data) ? data.length : 0
  } catch { pendingRuns.value = 0 }
  if (!isAdmin) { pendingAuthz.value = 0; return }
  try {
    const { data } = await api.get('/authorizations')
    pendingAuthz.value = Array.isArray(data) ? data.filter((a: any) => a.status === 'pending_approval').length : 0
  } catch { pendingAuthz.value = 0 }
}
