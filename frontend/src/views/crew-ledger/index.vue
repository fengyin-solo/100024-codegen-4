<template>
  <section class="page" data-module="crew-ledger">
    <header class="page-head">
      <div>
        <h2>剧组成员权限归属台账</h2>
        <p class="page-desc">
          按岗位职务限定可办动作：制片可办进场/离场/请假，场务仅可办进场，演员统筹仅可办请假，其余成员只读。
          人员列表、在组统计与本台账共享同一份成员数据。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新台账</button>
      </div>
    </header>

    <h3 class="block-title">岗位权限矩阵</h3>
    <table class="data-table matrix-table">
      <thead>
        <tr>
          <th>岗位角色</th>
          <th v-for="action in actionList" :key="action">{{ action }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="role in roleList" :key="role">
          <td>{{ ROLE_LABELS[role] }}</td>
          <td v-for="action in actionList" :key="action">
            <span :class="matrix[role]?.includes(action) ? 'perm-yes' : 'perm-no'">
              {{ matrix[role]?.includes(action) ? '可办理' : '拒绝' }}
            </span>
          </td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">在组统计</h3>
    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>
    <p class="group-line">
      各组在组：
      <span v-if="!groupEntries.length" class="empty-inline">暂无在组人员</span>
      <span v-for="item in groupEntries" :key="item.name" class="group-chip">{{ item.name }} {{ item.value }} 人</span>
    </p>

    <h3 class="block-title">成员权限归属（{{ entries.length }} 人）</h3>
    <form class="filter-bar" @submit.prevent="applyFilter">
      <label class="filter-item">
        <span>成员编号 / 姓名</span>
        <input v-model="keyword" placeholder="按成员编号或姓名检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="keyword = ''">重置</button>
    </form>
    <table class="data-table">
      <thead>
        <tr>
          <th>成员编号</th>
          <th>姓名</th>
          <th>岗位职务</th>
          <th>所属组别</th>
          <th>在组状态</th>
          <th>权限角色</th>
          <th>可办理动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="entry in filteredEntries" :key="String(entry.id)">
          <td>{{ entry.成员编号 }}</td>
          <td>{{ entry.姓名 }}</td>
          <td>{{ entry.岗位职务 }}</td>
          <td>{{ entry.所属组别 }}</td>
          <td>{{ entry.在组状态 }}</td>
          <td>
            <span class="role-tag" :class="entry.只读 ? 'role-readonly' : 'role-writable'">{{ entry.权限角色 }}</span>
          </td>
          <td>
            <span v-if="entry.只读" class="perm-no">只读，不能改动</span>
            <span v-else>{{ entry.可办理动作.join('、') }}</span>
          </td>
        </tr>
        <tr v-if="!filteredEntries.length">
          <td colspan="7" class="empty-state">没有符合条件的成员</td>
        </tr>
      </tbody>
    </table>

    <h3 class="block-title">历史进出记录（只追加不改写，共 {{ historyTotal }} 条）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>发生时间</th>
          <th>成员编号</th>
          <th>姓名</th>
          <th>岗位职务</th>
          <th>动作类型</th>
          <th>变更后状态</th>
          <th>办理人</th>
          <th>办理人岗位</th>
          <th>备注</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in history" :key="String(item.id)">
          <td>{{ item.发生时间 }}</td>
          <td>{{ item.成员编号 }}</td>
          <td>{{ item.姓名 }}</td>
          <td>{{ item.岗位职务 }}</td>
          <td>{{ item.动作类型 }}</td>
          <td>{{ item.变更后状态 }}</td>
          <td>{{ item.办理人 }}</td>
          <td>{{ item.办理人岗位 }}</td>
          <td>{{ item.备注 || '—' }}</td>
        </tr>
        <tr v-if="!history.length">
          <td colspan="9" class="empty-state">暂无进出记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson } from '@/api/client'
import { ROLE_LABELS } from '@/stores/session'

type LedgerEntry = {
  id: number
  成员编号: string
  姓名: string
  岗位职务: string
  所属组别: string
  在组状态: string
  权限角色: string
  可办理动作: string[]
  只读: boolean
}

type HistoryRow = Record<string, string | number>

type Ledger = {
  权限矩阵: Record<string, { 角色: string; 可办理动作: string[] }>
  在组统计: {
    在组人数: number
    待进场人员: number
    今日请假: number
    已离场人数: number
    成员总数: number
    各组在组: Record<string, number>
  }
  成员台账: LedgerEntry[]
  进出记录: HistoryRow[]
}

const actionList = ['办理进场', '办理离场', '登记请假']
const roleList = ['制片', '场务', '演员统筹', '只读成员']

const ledger = ref<Ledger | null>(null)
const history = ref<HistoryRow[]>([])
const historyTotal = ref(0)
const errorMessage = ref('')
const keyword = ref('')

const entries = computed<LedgerEntry[]>(() => ledger.value?.成员台账 ?? [])
const matrix = computed<Record<string, string[]>>(() => {
  const result: Record<string, string[]> = {}
  const source = ledger.value?.权限矩阵 ?? {}
  for (const role of roleList) {
    result[role] = source[role]?.可办理动作 ?? []
  }
  return result
})
const statCards = computed(() => {
  const stats = ledger.value?.在组统计
  return [
    { label: '在组人数', value: stats?.在组人数 ?? 0 },
    { label: '待进场人员', value: stats?.待进场人员 ?? 0 },
    { label: '今日请假', value: stats?.今日请假 ?? 0 },
    { label: '已离场人数', value: stats?.已离场人数 ?? 0 },
    { label: '成员总数', value: stats?.成员总数 ?? 0 },
  ]
})
const groupEntries = computed(() =>
  Object.entries(ledger.value?.在组统计.各组在组 ?? {}).map(([name, value]) => ({ name, value })),
)
const filteredEntries = computed(() => {
  const key = keyword.value.trim()
  if (!key) return entries.value
  return entries.value.filter(
    (item) => item.成员编号.includes(key) || item.姓名.includes(key),
  )
})

function applyFilter() {
  // 纯前端过滤当前台账，不改变共享数据
}

async function reload() {
  errorMessage.value = ''
  try {
    ledger.value = await fetchJson<Ledger>('/api/crew/ledger')
    const historyPayload = await fetchJson<{ items: HistoryRow[]; total: number }>(
      '/api/crew/history?size=100',
    )
    history.value = historyPayload.items
    historyTotal.value = historyPayload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '台账读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.block-title {
  font-size: 14px;
  margin: 18px 0 8px;
}
.matrix-table th,
.matrix-table td {
  text-align: center;
}
.perm-yes {
  color: #067647;
  font-weight: 600;
}
.perm-no {
  color: #b42318;
}
.role-tag {
  font-size: 12px;
  border-radius: 999px;
  padding: 2px 10px;
}
.role-writable {
  background: #e7f1ff;
  color: #1f6feb;
}
.role-readonly {
  background: #f2f4f7;
  color: #64748b;
}
.group-line {
  font-size: 13px;
  color: var(--muted);
}
.group-chip {
  display: inline-block;
  background: #eef2f7;
  border-radius: 6px;
  padding: 2px 8px;
  margin: 0 6px 4px 0;
  color: #1f2937;
}
.empty-inline {
  color: var(--muted);
}
</style>
