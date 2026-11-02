// Agent 发送审批：跟随 Harness 的权限模式。
// - 会话审批策略为 never（完全批准 / headless）→ 不再弹审批，直接发送；
// - ask（逐步审批 / Auto 自动审批）→ 走 ctx.approval.request，仅 allowed-once 才发送；
// - 无法判定时 fail closed 为 ask。
export function createSendGate(ctx) {
  const sessions = ctx.get('sessions')
  const presets = ctx.get('permissionPresets')
  const approval = ctx.get('approval')

  function policyFor(agent) {
    let session = null
    try {
      session = agent && sessions ? sessions.get(agent.id) : null
    } catch {
      session = null
    }
    if (session && approval && typeof approval.overrideOf === 'function') {
      try {
        const override = approval.overrideOf(session)
        if (override === 'never' || override === 'ask') return { policy: override, session }
      } catch { /* 回退到权限预设 */ }
    }
    if (session && presets) {
      try {
        const name = presets.current(session)
        const spec = presets.resolve(name)
        if (spec && spec.approval === 'never') return { policy: 'never', session }
        if (spec && spec.approval === 'ask') return { policy: 'ask', session }
      } catch { /* custom / 缺失投影 → ask */ }
    }
    return { policy: 'ask', session }
  }

  return {
    policyFor,

    async assertAllowedSend(exec, reasonZh, reasonEn) {
      const { policy } = policyFor(exec?.agent)
      if (policy === 'never') return
      if (!approval || typeof approval.request !== 'function') {
        throw new Error('当前 Harness 未提供审批服务，已拒绝发送（fail closed）。')
      }
      if (!exec?.agent) {
        throw new Error('无法确认发起发送的会话，已拒绝发送（fail closed）。')
      }
      let outcome
      try {
        outcome = await approval.request({
          agent: exec.agent,
          toolName: 'deepfeiyu_send',
          callId: exec.callId,
          reason: reasonZh,
          displayReason: { en: reasonEn, zh: reasonZh },
          signal: exec.signal,
        })
      } catch (err) {
        throw new Error(`发送审批未能完成：${err?.message || err}`)
      }
      if (outcome !== 'allowed-once') {
        throw new Error(`发送未获批准（${outcome}）。如需让智能体直接发送，请把会话权限切换到「完全批准」。`)
      }
    },
  }
}
