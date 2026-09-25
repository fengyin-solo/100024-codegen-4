<template>
  <section class="page" data-module="crew">
    <header class="page-head">
      <div>
        <h2>剧组人员管理</h2>
        <p class="page-desc">按岗位职务限定制片、场务、演员统筹可办理的进场、离场与请假；只读成员不能改动。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="openLedger">查看权限归属台账</button>
        <button class="btn" type="button" @click="exportRows">导出剧组人员清单</button>
      </div>
    </header>

    <div class="operator-bar">
      <label class="filter-item">
        <span>当前经办人（决定可办动作）</span>
        <select :value="session.operatorId ?? ''" @change="onOperatorChange">
          <option value="">未选择经办人（写操作将被拒绝）</option>
          <option v-for="member in session.crewMembers" :key="member.id" :value="member.id">
            {{ member.姓名 }} · {{ member.岗位职务 }} · {{ member.所属组别 }}
          </option>
        </select>
      </label>
      <span v-if="session.operatorId !== null" class="role-tag" :class="session.operatorReadOnly ? 'role-readonly' : 'role-writable'">
        {{ session.operatorRole === '只读成员' ? '只读成员' : ROLE_LABELS[session.operatorRole] }}
      </span>
    </div>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>成员编号 / 姓名</span>
        <input v-model="keyword" placeholder="按成员编号或姓名检索" />
      </label>
      <label class="filter-item">
        <span>在组状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="item in statuses" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>所属组别</span>
        <input v-model="groupFilter" placeholder="按所属组别检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="!session.canRun(action)"
              :title="actionTitle(action)"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无剧组人员数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条剧组人员记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { ROLE_LABELS, useSessionStore, type CrewMember } from '@/stores/session'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/crew'
const columns = ['成员编号', '姓名', '岗位职务', '所属组别', '联系电话', '进场日期', '离场日期', '在组状态']
const actions = ['办理进场', '办理离场', '登记请假']
const statuses = ['待进场', '在组', '已离场', '已请假']

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const groupFilter = ref('')
const stats = ref<Record<string, number>>({ 在组人数: 0, 待进场人员: 0, 今日请假: 0, 已离场人数: 0 })

const statCards = computed(() => [
  { label: '在组人数', value: stats.value['在组人数'] ?? 0 },
  { label: '待进场人员', value: stats.value['待进场人员'] ?? 0 },
  { label: '今日请假', value: stats.value['今日请假'] ?? 0 },
  { label: '已离场人数', value: stats.value['已离场人数'] ?? 0 },
])

function actionTitle(action: string): string {
  if (session.operatorId === null) {
    return '请先在上方选择经办人'
  }
  return session.canRun(action) ? action : `当前岗位无权办理「${action}」，请求会被拒绝`
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  groupFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export/all`, '_blank')
}

function openLedger() {
  window.open('/crew-ledger', '_blank')
}

function onOperatorChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  session.setOperator(value ? Number(value) : null)
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  if (session.operatorId === null) {
    errorMessage.value = '请先选择经办人，再办理进场/离场/请假'
    return
  }
  if (!session.canRun(action)) {
    errorMessage.value = `越权请求已拦截：${ROLE_LABELS[session.operatorRole]}，不能办理「${action}」`
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, operatorId: session.operatorId }),
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '剧组人员动作未生效，请稍后重试')
    }
    await Promise.all([reload(), reloadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '剧组人员操作失败'
  }
}

async function reloadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (response.ok) {
      stats.value = await response.json()
    }
  } catch {
    // 统计读失败时保留上次结果，不打断列表操作
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  if (groupFilter.value) query.set('group', groupFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('剧组成员列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    // 人员列表同时用于顶部经办人选项，保证归属调整后经办人岗位即时刷新
    const allMembers = rows.value.length === total.value ? rows.value : await fetchAllMembers()
    session.setMembers(allMembers as unknown as CrewMember[])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '剧组人员列表读取失败'
  }
}

async function fetchAllMembers(): Promise<Row[]> {
  const response = await request(`${ENDPOINT}?size=200`)
  const payload = await response.json()
  return payload.items ?? []
}

onMounted(async () => {
  const members = await fetchAllMembers()
  session.setMembers(members as unknown as CrewMember[])
  rows.value = members
  total.value = members.length
  await reloadStats()
})
</script>

<style scoped>
.operator-bar {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.role-tag {
  font-size: 12px;
  border-radius: 999px;
  padding: 4px 10px;
}
.role-writable {
  background: #e7f1ff;
  color: #1f6feb;
}
.role-readonly {
  background: #f2f4f7;
  color: #64748b;
}
.link:disabled {
  color: #b0b8c4;
  cursor: not-allowed;
}
</style>
