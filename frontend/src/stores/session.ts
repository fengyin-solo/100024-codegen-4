import { defineStore } from 'pinia'

interface CrewOperator {
  id: number
  成员编号: string
  姓名: string
  岗位职务: string
  角色: string
  可办动作: string[]
  只读: boolean
}

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    operatorId: '' as string | number,
    operatorProfile: null as CrewOperator | null,
    shiftLabel: '白班 08:00-20:00',
    scope: '影视剧组拍摄制作管理平台',
  }),
  getters: {
    canOperate: (state) => state.operator.length > 0,
    // 只读成员（导演、演员等非办理岗位）在剧组台账页不能改动任何成员
    isCrewReadonly: (state) => state.operatorProfile?.只读 ?? false,
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setCrewOperator(profile: CrewOperator | null) {
      this.operatorProfile = profile
      this.operator = profile ? profile.姓名 : '值班管理员'
      this.operatorId = profile ? String(profile.id) : ''
    },
  },
})
