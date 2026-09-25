import { defineStore } from 'pinia'

export type CrewMember = {
  id: number
  成员编号: string
  姓名: string
  岗位职务: string
  所属组别: string
  status: string
  [key: string]: string | number | boolean | null
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '未选择经办人',
    shiftLabel: '白班 08:00-20:00',
    scope: '影视剧组拍摄制作管理平台',
    // 当前登录的经办人（剧组成员）。进场/离场/请假按其岗位职务鉴权，
    // 未选择时写操作会被后端拒绝；只读岗位在页面上也不能点动作。
    operatorId: null as number | null,
    operatorRole: '',
    operatorReadOnly: true,
    permissions: [] as string[],
    crewMembers: [] as CrewMember[],
  }),
  getters: {
    canOperate: (state) => state.operatorId !== null && !state.operatorReadOnly,
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setMembers(members: CrewMember[]) {
      this.crewMembers = members
      if (this.operatorId !== null) {
        this.setOperator(this.operatorId)
      }
    },
    setOperator(id: number | null) {
      if (id === null) {
        this.operatorId = null
        this.operator = '未选择经办人'
        this.operatorRole = ''
        this.operatorReadOnly = true
        this.permissions = []
        return
      }
      const member = this.crewMembers.find((item) => item.id === id)
      if (!member) {
        return
      }
      this.operatorId = member.id
      this.operator = member.姓名
      this.operatorRole = this.roleOf(member.岗位职务)
      this.operatorReadOnly = this.operatorRole === '只读成员'
      this.permissions = ROLE_PERMISSIONS[this.operatorRole] ?? []
    },
    canRun(action: string): boolean {
      return this.operatorId !== null && this.permissions.includes(action)
    },
    roleOf(position: string): string {
      return roleOf(position)
    },
  },
})

export const ROLE_LABELS: Record<string, string> = {
  制片: '制片（进场/离场/请假均可办理）',
  场务: '场务（仅可办理进场）',
  演员统筹: '演员统筹（仅可办理请假）',
  只读成员: '只读成员（不能改动）',
}

export const ROLE_PERMISSIONS: Record<string, string[]> = {
  制片: ['办理进场', '办理离场', '登记请假', '归属变更'],
  场务: ['办理进场'],
  演员统筹: ['登记请假'],
  只读成员: [],
}

export function roleOf(position: string): string {
  if (position.includes('演员统筹')) return '演员统筹'
  if (position.includes('场务')) return '场务'
  if (position.includes('制片')) return '制片'
  return '只读成员'
}
