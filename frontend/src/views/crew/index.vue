<template>
  <section class="page" data-module="crew">
    <header class="page-head">
      <div>
        <h2>剧组人员权限归属台账</h2>
        <p class="page-desc">
          按岗位职务限定制片、场务、演员统筹可办理的进场、离场及请假；越权请求一律拒绝，只读成员不能改动。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" :disabled="busy" @click="exportRows">导出清单与台账</button>
      </div>
    </header>

    <!-- 经办人：必须是名册内剧组成员，岗位职务决定可办动作 -->
    <div class="operator-bar">
      <label class="filter-item">
        <span>当前经办人</span>
        <select v-model.number="operatorId" @change="applyOperator">
          <option v-for="member in roster" :key="String(member.id)" :value="member.id">
            {{ member.姓名 }}（{{ member.岗位职务 }}）
          </option>
        </select>
      </label>
      <article v-if="profile" class="role-chip" :class="{ readonly: profile.只读 }">
        <strong>{{ profile.角色 }}</strong>
        <span v-if="profile.只读">只读成员 · 不可办理任何动作</span>
        <span v-else>可办：{{ profile.可办动作.join('、') }}</span>
      </article>
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
        <input v-model="filters.keyword" placeholder="按成员编号或姓名检索" />
      </label>
      <label class="filter-item">
        <span>在组状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作（按经办人岗位）</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] || '—' }}</td>
          <td class="row-actions">
            <template v-if="canOperate && actionList(row).length">
              <button
                v-for="item in actionList(row)"
                :key="item.action"
                class="link"
                type="button"
                @click="runAction(item.action, row)"
              >
                {{ item.action }}
              </button>
            </template>
            <span v-else class="muted-text">{{ readonlyHint(row) }}</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无符合条件的剧组成员</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条剧组人员记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 成员归属登记：仅办理岗可用，只读成员不显示入口 -->
    <section v-if="canOperate" class="ledger-panel">
      <h3>成员归属登记</h3>
      <form class="filter-bar" @submit.prevent="createMember">
        <label class="filter-item">
          <span>成员编号</span>
          <input v-model="createForm.成员编号" placeholder="如 CREW-0101" />
        </label>
        <label class="filter-item">
          <span>姓名</span>
          <input v-model="createForm.姓名" placeholder="成员姓名" />
        </label>
        <label class="filter-item">
          <span>岗位职务</span>
          <input v-model="createForm.岗位职务" placeholder="如 场务 / 演员 / 导演" />
        </label>
        <label class="filter-item">
          <span>所属组别</span>
          <input v-model="createForm.所属组别" placeholder="如 场务组" />
        </label>
        <button class="btn primary" type="submit" :disabled="busy">登记入册</button>
      </form>
    </section>

    <!-- 权限归属台账：只追加、不可改；人员列表、在组统计与它共享同一份成员数据 -->
    <section class="ledger-panel">
      <div class="ledger-head">
        <h3>权限归属台账（历史进出记录保持原样，只追加）</h3>
        <form class="ledger-filter" @submit.prevent="reloadLedger">
          <input v-model="ledgerFilters.keyword" placeholder="按成员或经办人检索" />
          <select v-model="ledgerFilters.result">
            <option value="">全部结果</option>
            <option value="已通过">已通过</option>
            <option value="已拒绝">已拒绝</option>
          </select>
          <button class="btn" type="submit">刷新台账</button>
        </form>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in ledgerColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="log in ledgerRows" :key="String(log.id)">
            <td v-for="column in ledgerColumns" :key="column">
              <span v-if="column === '结果'" :class="log.结果 === '已拒绝' ? 'error-text' : 'ok-text'">
                {{ log[column] }}
              </span>
              <span v-else>{{ log[column] || '—' }}</span>
            </td>
          </tr>
          <tr v-if="!ledgerRows.length">
            <td :colspan="ledgerColumns.length" class="empty-state">暂无台账记录</td>
          </tr>
        </tbody>
      </table>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | null>
interface OperatorProfile {
  id: number
  成员编号: string
  姓名: string
  岗位职务: string
  角色: string
  可办动作: string[]
  只读: boolean
}

const ENDPOINT = '/api/crew'
const columns = ['成员编号', '姓名', '岗位职务', '所属组别', '联系电话', '进场日期', '离场日期', '在组状态']
const ledgerColumns = ['时间', '动作', '成员编号', '成员姓名', '经办人岗位', '经办人姓名', '变更前状态', '变更后状态', '结果', '说明']
const statuses = ['待进场', '在组', '已离场', '已请假']
// 状态前置条件（后端仍会强制校验，这里只用于收起必被拒绝的按钮）
const ACTION_FROM: Record<string, string> = {
  办理进场: '待进场',
  办理离场: '在组',
  登记请假: '在组',
}

const session = useSessionStore()
const rows = ref<Row[]>([])
const total = ref(0)
const roster = ref<Row[]>([])
const ledgerRows = ref<Row[]>([])
const stats = ref<Record<string, number>>({})
const profile = ref<OperatorProfile | null>(null)
const operatorId = ref<number>(Number(session.operatorId) || 1)
const errorMessage = ref('')
const busy = ref(false)

const filters = reactive<{ keyword: string; status: string }>({ keyword: '', status: '' })
const ledgerFilters = reactive<{ keyword: string; result: string }>({ keyword: '', result: '' })
const createForm = reactive<Record<string, string>>({ 成员编号: '', 姓名: '', 岗位职务: '', 所属组别: '' })

const canOperate = computed(() => Boolean(profile.value && !profile.value.只读))
const statCards = computed(() => [
  { label: '成员总数', value: stats.value['成员总数'] ?? 0 },
  { label: '在组人数', value: stats.value['在组人数'] ?? 0 },
  { label: '待进场人员', value: stats.value['待进场人员'] ?? 0 },
  { label: '今日请假', value: stats.value['请假中人数'] ?? 0 },
  { label: '已离场人员', value: stats.value['已离场人员'] ?? 0 },
  { label: '台账记录数', value: stats.value['台账记录数'] ?? 0 },
])

function operatorPayload(): Record<string, string | number> {
  return { 经办人编号: operatorId.value }
}

function actionList(row: Row): { action: string }[] {
  if (!profile.value) return []
  return profile.value.可办动作
    .filter((action) => ACTION_FROM[action] === row.status)
    .map((action) => ({ action }))
}

function readonlyHint(row: Row): string {
  if (!profile.value) return ''
  if (profile.value.只读) return '只读成员，不能办理'
  const allowable = profile.value.可办动作.filter((action) => ACTION_FROM[action] === row.status)
  return allowable.length ? '' : '当前岗位对此状态无可办动作'
}

async function applyOperator() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/permissions?operator_id=${operatorId.value}`)
    if (!response.ok) throw new Error('经办人权限读取失败')
    const payload = await response.json()
    profile.value = payload.operator as OperatorProfile
    session.setCrewOperator(profile.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '经办人权限读取失败'
  }
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.status) query.set('status', filters.status)
  try {
    const [listResp, statsResp] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/stats`),
    ])
    if (!listResp.ok) throw new Error('剧组成员列表读取失败')
    if (!statsResp.ok) throw new Error('在组统计读取失败')
    const payload = await listResp.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = await statsResp.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '剧组人员数据读取失败'
  }
}

async function reloadLedger() {
  const query = new URLSearchParams({ size: '50' })
  if (ledgerFilters.keyword) query.set('keyword', ledgerFilters.keyword)
  if (ledgerFilters.result) query.set('result', ledgerFilters.result)
  const response = await request(`${ENDPOINT}/ledger?${query.toString()}`)
  if (response.ok) {
    const payload = await response.json()
    ledgerRows.value = payload.items ?? []
  }
}

async function refreshAll() {
  await Promise.all([reload(), reloadLedger()])
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  busy.value = true
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action, ...operatorPayload() } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '该动作被拒绝'
    }
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口请求失败'
  } finally {
    busy.value = false
  }
}

async function createMember() {
  errorMessage.value = ''
  busy.value = true
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm, ...operatorPayload() } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '登记失败'
    } else {
      createForm.成员编号 = ''
      createForm.姓名 = ''
      createForm.岗位职务 = ''
      createForm.所属组别 = ''
    }
    await refreshAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接口请求失败'
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  // 经办人下拉用同一份名册；默认选中制片，便于演示完整办理权限
  const rosterResp = await request(`${ENDPOINT}?size=200`)
  if (rosterResp.ok) {
    roster.value = (await rosterResp.json()).items ?? []
  }
  if (!roster.value.some((member) => Number(member.id) === Number(operatorId.value))) {
    operatorId.value = Number(roster.value[0]?.id ?? 1)
  }
  await applyOperator()
  await refreshAll()
})
</script>

<style scoped>
.operator-bar {
  display: flex;
  gap: 16px;
  align-items: flex-end;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.role-chip {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 13px;
  color: var(--muted);
}
.role-chip.readonly strong {
  color: #b42318;
}
.muted-text {
  color: var(--muted);
  font-size: 12px;
}
.ok-text {
  color: #067647;
}
.ledger-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}
.ledger-panel h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.ledger-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.ledger-filter {
  display: flex;
  gap: 8px;
}
.ledger-filter input,
.ledger-filter select {
  padding: 4px 8px;
}
select {
  padding: 4px 8px;
}
</style>
