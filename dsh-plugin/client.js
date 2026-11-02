// Deep肥鱼 DSH 插件 Client 半：侧栏「Deep肥鱼」图标 + 中央原生 React 面板。
// Host 半用 ctx.webServer 注册同源前缀路由 /deepfeiyu（自带 Cookie/Host/Origin 鉴权）；
// 面板只经同源 fetch POST JSON，全部请求收敛在 apply 内唯一的 call() 里，便于以后换传输层。
window.__ModuleLoader__.load({
  id: '@local/deepfeiyu-dsh',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    /** Host 侧同源前缀路由，端点名拼在路径后（如 /deepfeiyu/state）。 */
    const ROUTE = '/deepfeiyu';
    /** 状态轮询与选中会话消息轮询共用 4s 节奏。 */
    const POLL_MS = 4000;
    /** 安装日志轮询节奏：比状态更快，安装输出才跟得上。 */
    const DEP_LOG_MS = 800;
    /** 控制台默认端口；state 缺失（Host 刚加载）时作为兜底。 */
    const FALLBACK_PORT = 8765;

    const PHASE_META = {
      running: { text: '运行中', tone: 'ok' },
      attached: { text: '已附着', tone: 'ok' },
      starting: { text: '启动中', tone: 'busy' },
      stopping: { text: '停止中', tone: 'busy' },
      stopped: { text: '已停止', tone: 'idle' },
      error: { text: '异常', tone: 'error' },
    };

    const DRIVER_TEXT = {
      wcferry: 'WCFerry',
      weixin4: '微信 4.x',
      webhook: 'Webhook',
      none: '未连接',
    };

    const MSG_TYPE_TEXT = {
      image: '图片',
      voice: '语音',
      video: '视频',
      emoji: '表情',
      location: '位置',
      link: '链接',
      system: '系统消息',
      pat: '拍一拍',
      file: '文件',
    };

    /** 侧栏图标：接收 sidebar.panellist 的 owner props（size、active），自绘鱼形与 icon.svg 一致。 */
    function DeepFeiyuIcon({ size = 18, active = false }) {
      return h('svg', {
        viewBox: '0 0 24 24',
        width: size,
        height: size,
        'aria-hidden': 'true',
        focusable: 'false',
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth: active ? 2.2 : 1.8,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        style: { display: 'block' },
      },
      h('path', { d: 'M3 12c3-4.5 7-6.5 11-6.5 2.4 0 4.6.8 6.4 2.3L21 6.4v11.2l-1.6-1.4c-1.8 1.5-4 2.3-6.4 2.3-4 0-8-2-11-6.5Z' }),
      h('circle', { cx: 8.2, cy: 11.2, r: 1.15, fill: 'currentColor', stroke: 'none' }));
    }

    // 只使用宿主 --dsw-alias-* 主题 token；fallback 用浅色主题取值，token 改名时不至于渲染成透明。
    const STYLES = `
.dsh-deepfeiyu-root { box-sizing: border-box; display: flex; flex-direction: column; gap: 12px; height: 100%; min-height: 0; padding: 20px clamp(16px, 3vw, 32px) 16px; overflow: hidden; color: var(--dsw-alias-label-primary, #0f1115); background: var(--dsw-alias-bg-base, #fff); font-size: 13px; line-height: 1.6; }
.dsh-deepfeiyu-root *, .dsh-deepfeiyu-root *::before, .dsh-deepfeiyu-root *::after { box-sizing: border-box; }
.dsh-deepfeiyu-header { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 16px; }
.dsh-deepfeiyu-heading { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.dsh-deepfeiyu-title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.dsh-deepfeiyu-title { margin: 0; font-size: 20px; font-weight: 500; line-height: 28px; }
.dsh-deepfeiyu-meta { margin: 0; color: var(--dsw-alias-label-secondary, #61666b); font-size: 12px; line-height: 18px; }
.dsh-deepfeiyu-badge { display: inline-flex; align-items: center; gap: 6px; padding: 1px 9px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 999px; background: var(--dsw-alias-bg-layer-2, #fff); color: var(--dsw-alias-label-secondary, #61666b); font-size: 12px; line-height: 18px; white-space: nowrap; }
.dsh-deepfeiyu-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--dsw-alias-label-caption, #adb2b8); }
.dsh-deepfeiyu-dot.dsh-deepfeiyu-is-ok { background: var(--dsw-alias-state-success-primary, #22c55e); }
.dsh-deepfeiyu-dot.dsh-deepfeiyu-is-busy { background: var(--dsw-alias-state-warn-primary, #f59e0b); }
.dsh-deepfeiyu-dot.dsh-deepfeiyu-is-error { background: var(--dsw-alias-state-error-primary, #ec1313); }
.dsh-deepfeiyu-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.dsh-deepfeiyu-btn { appearance: none; margin: 0; padding: 5px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 8px; background: var(--dsw-alias-button-elevated-fill, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; font-size: 13px; line-height: 20px; white-space: nowrap; cursor: pointer; }
.dsh-deepfeiyu-btn:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-btn:disabled { opacity: .45; cursor: not-allowed; }
.dsh-deepfeiyu-btn:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #4176e6); outline-offset: 1px; }
.dsh-deepfeiyu-btn-primary { border-color: transparent; background: var(--dsw-alias-button-primary-fill, #0f1115); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-btn-primary:hover:not(:disabled) { background: var(--dsw-alias-button-primary-dimmed, #ebeef2); color: var(--dsw-alias-label-primary, #0f1115); }
.dsh-deepfeiyu-errorbar { display: flex; align-items: center; gap: 12px; padding: 8px 12px; border: 1px solid var(--dsw-alias-state-error-primary, #ec1313); border-radius: 10px; background: var(--dsw-alias-interactive-bg-hover-danger, #ec13130d); color: var(--dsw-alias-state-error-primary, #ec1313); font-size: 12px; }
.dsh-deepfeiyu-errorbar-text { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.dsh-deepfeiyu-errorbar-close { flex: none; appearance: none; margin: 0; padding: 0 2px; border: 0; background: transparent; color: inherit; font: inherit; font-size: 12px; cursor: pointer; }
.dsh-deepfeiyu-banner { display: flex; align-items: center; gap: 12px; padding: 10px 14px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-2, #fff); }
.dsh-deepfeiyu-banner.dsh-deepfeiyu-is-busy { border-color: var(--dsw-alias-state-warn-primary, #f59e0b); background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-banner.dsh-deepfeiyu-is-error { border-color: var(--dsw-alias-state-error-primary, #ec1313); background: var(--dsw-alias-interactive-bg-hover-danger, #ec13130d); }
.dsh-deepfeiyu-banner-body { flex: 1; min-width: 0; }
.dsh-deepfeiyu-banner-title { margin: 0; font-size: 13px; font-weight: 500; line-height: 20px; }
.dsh-deepfeiyu-banner.dsh-deepfeiyu-is-error .dsh-deepfeiyu-banner-title { color: var(--dsw-alias-state-error-primary, #ec1313); }
.dsh-deepfeiyu-banner-detail { margin: 2px 0 0; color: var(--dsw-alias-label-secondary, #61666b); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-banner-actions { display: flex; flex: none; gap: 8px; }
.dsh-deepfeiyu-accounts { display: flex; flex-direction: column; gap: 8px; }
.dsh-deepfeiyu-section-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
.dsh-deepfeiyu-section-title { font-size: 13px; font-weight: 500; }
.dsh-deepfeiyu-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.dsh-deepfeiyu-chip { display: inline-flex; align-items: center; gap: 8px; appearance: none; margin: 0; padding: 5px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 10px; background: var(--dsw-alias-bg-layer-2, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; text-align: left; cursor: pointer; }
.dsh-deepfeiyu-chip:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-chip.dsh-deepfeiyu-is-active { border-color: var(--dsw-alias-brand-primary, #0f1115); background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-chip:disabled { cursor: default; opacity: 1; }
.dsh-deepfeiyu-chip:disabled:not(.dsh-deepfeiyu-is-active) { opacity: .6; }
.dsh-deepfeiyu-chip:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #4176e6); outline-offset: 1px; }
.dsh-deepfeiyu-avatar { display: inline-flex; flex: none; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: 50%; background: var(--dsw-alias-button-primary-dimmed, #ebeef2); color: var(--dsw-alias-label-primary, #0f1115); font-size: 12px; font-weight: 600; }
.dsh-deepfeiyu-chip-text { display: flex; flex-direction: column; gap: 0; min-width: 0; }
.dsh-deepfeiyu-chip-name { font-size: 12px; line-height: 18px; }
.dsh-deepfeiyu-chip-sub { color: var(--dsw-alias-label-tertiary, #81858c); font-size: 11px; line-height: 16px; }
.dsh-deepfeiyu-body { display: grid; grid-template-columns: 320px minmax(0, 1fr); gap: 12px; flex: 1; min-height: 0; }
.dsh-deepfeiyu-chats { display: flex; flex-direction: column; gap: 8px; min-height: 0; padding: 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); }
.dsh-deepfeiyu-chats-head { display: flex; gap: 8px; align-items: center; }
.dsh-deepfeiyu-input { flex: 1; min-width: 0; margin: 0; padding: 5px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 8px; background: var(--dsw-alias-bg-base, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; font-size: 13px; line-height: 20px; }
.dsh-deepfeiyu-input:focus, .dsh-deepfeiyu-textarea:focus { outline: none; border-color: var(--dsw-alias-brand-primary, #0f1115); }
.dsh-deepfeiyu-input:disabled { opacity: .6; }
.dsh-deepfeiyu-chat-list { display: flex; flex: 1; flex-direction: column; gap: 2px; min-height: 0; overflow: auto; }
.dsh-deepfeiyu-chat { display: flex; flex-direction: column; gap: 2px; width: 100%; appearance: none; margin: 0; padding: 6px 8px; border: 0; border-radius: 8px; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; }
.dsh-deepfeiyu-chat:hover { background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-chat.dsh-deepfeiyu-is-active { background: var(--dsw-alias-interactive-bg-active, #2631481a); }
.dsh-deepfeiyu-chat:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #4176e6); outline-offset: -1px; }
.dsh-deepfeiyu-chat-line { display: flex; align-items: center; gap: 6px; min-width: 0; }
.dsh-deepfeiyu-chat-name { flex: 0 1 auto; min-width: 0; overflow: hidden; font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.dsh-deepfeiyu-chat-time { flex: none; margin-left: auto; color: var(--dsw-alias-label-tertiary, #81858c); font-size: 11px; line-height: 16px; }
.dsh-deepfeiyu-chat-preview { flex: 1; min-width: 0; overflow: hidden; color: var(--dsw-alias-label-secondary, #61666b); font-size: 12px; line-height: 18px; text-overflow: ellipsis; white-space: nowrap; }
.dsh-deepfeiyu-tags { display: inline-flex; flex: none; gap: 4px; }
.dsh-deepfeiyu-tag { flex: none; padding: 0 5px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 4px; color: var(--dsw-alias-label-secondary, #61666b); font-size: 10px; line-height: 16px; }
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-warn { border-color: var(--dsw-alias-state-warn-primary, #f59e0b); color: var(--dsw-alias-state-warn-primary, #f59e0b); }
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-ok { border-color: var(--dsw-alias-state-success-primary, #22c55e); color: var(--dsw-alias-state-success-primary, #22c55e); }
.dsh-deepfeiyu-unread { display: inline-flex; flex: none; align-items: center; justify-content: center; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: var(--dsw-alias-state-error-primary, #ec1313); color: var(--dsw-alias-label-primary-foreground, #fff); font-size: 10px; line-height: 18px; }
.dsh-deepfeiyu-thread { display: flex; flex-direction: column; min-height: 0; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); overflow: hidden; }
.dsh-deepfeiyu-thread-empty { display: flex; flex: 1; flex-direction: column; align-items: center; justify-content: center; gap: 4px; padding: 24px; text-align: center; }
.dsh-deepfeiyu-empty-title { margin: 0; font-size: 14px; font-weight: 500; }
.dsh-deepfeiyu-thread-head { display: flex; align-items: center; gap: 8px; padding: 9px 14px; border-bottom: 1px solid var(--dsw-alias-border-l1, #0000000a); }
.dsh-deepfeiyu-thread-name { overflow: hidden; font-size: 13px; font-weight: 500; text-overflow: ellipsis; white-space: nowrap; }
.dsh-deepfeiyu-messages { display: flex; flex: 1; flex-direction: column; gap: 10px; min-height: 0; padding: 12px 14px; overflow: auto; }
.dsh-deepfeiyu-msg { display: flex; flex-direction: column; gap: 2px; max-width: 78%; align-self: flex-start; }
.dsh-deepfeiyu-msg.dsh-deepfeiyu-is-mine { align-self: flex-end; }
.dsh-deepfeiyu-msg.dsh-deepfeiyu-is-muted { opacity: .6; }
.dsh-deepfeiyu-msg-meta { display: flex; gap: 6px; color: var(--dsw-alias-label-tertiary, #81858c); font-size: 11px; line-height: 16px; }
.dsh-deepfeiyu-msg.dsh-deepfeiyu-is-mine .dsh-deepfeiyu-msg-meta { justify-content: flex-end; }
.dsh-deepfeiyu-bubble { display: flex; flex-direction: column; gap: 2px; padding: 6px 10px; border: 1px solid var(--dsw-alias-border-l1, #0000000a); border-radius: 10px; background: var(--dsw-alias-bg-layer-2, #fff); overflow-wrap: anywhere; }
.dsh-deepfeiyu-msg.dsh-deepfeiyu-is-mine .dsh-deepfeiyu-bubble { border-color: transparent; background: var(--dsw-alias-button-primary-dimmed, #ebeef2); }
.dsh-deepfeiyu-bubble .dsh-deepfeiyu-tag { align-self: flex-start; }
.dsh-deepfeiyu-msg-text { font-size: 13px; line-height: 1.55; white-space: pre-wrap; }
.dsh-deepfeiyu-composer { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-top: 1px solid var(--dsw-alias-border-l1, #0000000a); }
.dsh-deepfeiyu-composer-row { display: flex; align-items: flex-end; gap: 8px; }
.dsh-deepfeiyu-textarea { flex: 1; min-height: 56px; max-height: 120px; margin: 0; padding: 7px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 8px; background: var(--dsw-alias-bg-base, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; font-size: 13px; line-height: 1.5; resize: none; }
.dsh-deepfeiyu-textarea:disabled { opacity: .6; }
.dsh-deepfeiyu-send-error { margin: 0; color: var(--dsw-alias-state-error-primary, #ec1313); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-stats { display: flex; flex-wrap: wrap; align-items: baseline; gap: 18px; padding: 8px 14px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); }
.dsh-deepfeiyu-stats-title { font-size: 12px; font-weight: 500; }
.dsh-deepfeiyu-stat { display: inline-flex; align-items: baseline; gap: 6px; }
.dsh-deepfeiyu-stat-value { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
.dsh-deepfeiyu-stat-label { color: var(--dsw-alias-label-secondary, #61666b); font-size: 12px; }
.dsh-deepfeiyu-hint { margin: 0; color: var(--dsw-alias-label-tertiary, #81858c); font-size: 12px; line-height: 18px; }
.dsh-deepfeiyu-list-hint { padding: 12px 8px; }
.dsh-deepfeiyu-settings { box-sizing: border-box; display: flex; flex-direction: column; gap: 12px; color: var(--dsw-alias-label-primary, #0f1115); font-size: 13px; line-height: 1.6; }
.dsh-deepfeiyu-settings *, .dsh-deepfeiyu-settings *::before, .dsh-deepfeiyu-settings *::after { box-sizing: border-box; }
.dsh-deepfeiyu-settings-title { margin: 0; font-size: 16px; font-weight: 500; line-height: 24px; }
.dsh-deepfeiyu-dep-list { display: flex; flex-direction: column; gap: 8px; }
.dsh-deepfeiyu-dep-row { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between; gap: 12px 16px; padding: 10px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); }
.dsh-deepfeiyu-dep-main { display: flex; flex: 1 1 260px; flex-direction: column; gap: 2px; min-width: 0; }
.dsh-deepfeiyu-dep-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.dsh-deepfeiyu-dep-name { font-size: 13px; font-weight: 500; line-height: 20px; }
.dsh-deepfeiyu-dep-desc { margin: 0; color: var(--dsw-alias-label-secondary, #61666b); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-dep-note { margin: 0; color: var(--dsw-alias-label-tertiary, #81858c); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-dep-missing { margin: 0; color: var(--dsw-alias-state-warn-primary, #f59e0b); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-dep-failure { margin: 0; color: var(--dsw-alias-state-error-primary, #ec1313); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-dep-error { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 8px 12px; border: 1px solid var(--dsw-alias-state-error-primary, #ec1313); border-radius: 10px; background: var(--dsw-alias-interactive-bg-hover-danger, #ec13130d); color: var(--dsw-alias-state-error-primary, #ec1313); font-size: 12px; }
.dsh-deepfeiyu-dep-actions { display: flex; flex: none; flex-wrap: wrap; align-items: center; gap: 8px; margin-left: auto; }
.dsh-deepfeiyu-dep-terminal-card { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); }
.dsh-deepfeiyu-dep-terminal-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.dsh-deepfeiyu-dep-terminal-title { font-size: 13px; font-weight: 500; line-height: 20px; }
.dsh-deepfeiyu-terminal { margin: 0; padding: 10px 12px; max-height: 260px; overflow: auto; border: 1px solid var(--dsw-alias-border-l1, #0000000a); border-radius: 8px; background: var(--dsw-alias-bg-layer-2, #fff); color: var(--dsw-alias-label-primary, #0f1115); font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace; font-size: 12px; line-height: 18px; white-space: pre-wrap; overflow-wrap: anywhere; }
.dsh-deepfeiyu-dep-log-status { margin: 0; font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-dep-log-status.dsh-deepfeiyu-is-ok { color: var(--dsw-alias-state-success-primary, #22c55e); }
.dsh-deepfeiyu-dep-log-status.dsh-deepfeiyu-is-error { color: var(--dsw-alias-state-error-primary, #ec1313); }
.dsh-deepfeiyu-dep-log-status.dsh-deepfeiyu-is-busy { color: var(--dsw-alias-label-secondary, #61666b); }
.dsh-deepfeiyu-mood { position: fixed; right: 20px; bottom: 72px; z-index: 40; display: flex; flex-direction: column; align-items: flex-end; gap: 8px; pointer-events: auto; }
.dsh-deepfeiyu-mood-button { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 999px; background: var(--dsw-alias-bg-layer-1, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; cursor: pointer; }
.dsh-deepfeiyu-mood-button:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #4176e6); outline-offset: 1px; }
.dsh-deepfeiyu-mood-face { font-size: 16px; }
.dsh-deepfeiyu-mood-value { font-size: 12px; font-variant-numeric: tabular-nums; }
.dsh-deepfeiyu-mood-panel { width: 220px; padding: 10px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); }
@media (max-width: 900px) {
  .dsh-deepfeiyu-body { grid-template-columns: 240px minmax(0, 1fr); }
  .dsh-deepfeiyu-root { padding: 16px 16px 12px; }
}
@media (max-height: 640px) {
  .dsh-deepfeiyu-root { gap: 8px; padding-top: 12px; }
  .dsh-deepfeiyu-title { font-size: 17px; line-height: 24px; }
}
`;

    // ---------------------------------------------------------------- 纯展示辅助

    function phaseMeta(phase) {
      return PHASE_META[phase] || { text: '未知', tone: 'idle' };
    }

    /** 依赖功能行的状态徽标：正在安装优先于已装/不支持/缺项，避免安装中仍显示旧结论。 */
    function depsStatus(feature) {
      const item = feature || {};
      if (item.phase === 'installing') return { text: '安装中…', tone: 'busy' };
      if (item.installed) return { text: '已安装', tone: 'ok' };
      if (item.supported === false) return { text: '不支持当前系统', tone: 'idle' };
      if (item.partial) {
        const count = Array.isArray(item.missing) ? item.missing.filter(Boolean).length : 0;
        return { text: count ? '缺少 ' + count + ' 项' : '缺少部分依赖', tone: 'warn' };
      }
      return { text: '未安装', tone: 'idle' };
    }

    function driverText(driver) {
      const key = String(driver || 'none');
      return DRIVER_TEXT[key] || key;
    }

    function msgTypeText(type) {
      const key = String(type || '');
      return MSG_TYPE_TEXT[key] || key;
    }

    function toDate(ts) {
      const value = Number(ts);
      return Number.isFinite(value) && value > 0 ? new Date(value * 1000) : null;
    }

    function isSameDay(a, b) {
      return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
    }

    function formatClock(ts) {
      return new Date(Number(ts) * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    }

    /** 会话列表用的短时间：今天只显示时分，更早显示月日。 */
    function formatChatTime(ts) {
      const date = toDate(ts);
      if (!date) return '';
      return isSameDay(date, new Date())
        ? formatClock(ts)
        : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
    }

    /** 消息气泡用时间：跨天时补上日期。 */
    function formatMessageTime(ts) {
      const date = toDate(ts);
      if (!date) return '';
      const clock = formatClock(ts);
      return isSameDay(date, new Date())
        ? clock
        : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ' ' + clock;
    }

    function clipText(text, max) {
      const value = String(text === null || text === undefined ? '' : text).replace(/\s+/g, ' ').trim();
      if (value.length <= max) return value;
      return value.slice(0, Math.max(1, max - 1)) + '…';
    }

    function accountName(account) {
      if (!account) return '';
      return String(account.name || account.nickname || account.id || '未命名账号');
    }

    function errorMessage(error) {
      if (!error) return '未知错误';
      if (typeof error === 'string') return error;
      if (typeof error.message === 'string' && error.message) return error.message;
      return String(error);
    }

    function consoleUrl(snapshot) {
      const port = snapshot && snapshot.port ? snapshot.port : FALLBACK_PORT;
      return 'http://127.0.0.1:' + port;
    }

    return {
      // 只依赖 slot 服务；Host 路由自带 Host/Origin/Cookie 鉴权，Client 不注入 connection。
      inject: ['slots'],
      apply(ctx) {
        // ---------------------------------------------------------- 传输层封装
        // 全部同源请求收敛在这一个函数里：换成别的传输实现时只改这里，端点与数据契约不变。
        function timeoutSignal(timeoutMs) {
          try {
            if (typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
              return AbortSignal.timeout(timeoutMs);
            }
          } catch (error) {
            // 老环境没有 AbortSignal.timeout：退化为不设超时，避免面板整体不可用。
          }
          return undefined;
        }

        async function call(endpoint, payload, timeoutMs = 10000) {
          const signal = timeoutSignal(timeoutMs);
          const response = await fetch(ROUTE + '/' + endpoint, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(payload || {}),
            ...(signal === undefined ? {} : { signal }),
          });
          const text = await response.text();
          let body = null;
          try {
            body = text ? JSON.parse(text) : null;
          } catch (error) {
            body = text;
          }
          if (!response.ok) {
            const detail = body && typeof body === 'object' && body.error && typeof body.error.message === 'string'
              ? body.error.message
              : '';
            throw new Error(detail || '控制台请求失败（HTTP ' + response.status + '）');
          }
          // Host 统一返回 {ok:true,value} / {ok:false,error:{message}}；同时兼容未来直接返回业务值。
          if (body && typeof body === 'object' && !Array.isArray(body) && typeof body.ok === 'boolean') {
            if (body.ok === true) return body.value;
            throw new Error(body.error && typeof body.error.message === 'string' && body.error.message
              ? body.error.message
              : '控制台请求失败');
          }
          return body;
        }

        // ---------------------------------------------------------------- 面板
        function DeepFeiyuPanel() {
          // 组件卸载后异步回调不再 setState。
          const mounted = React.useRef(true);
          // 每次请求的序号：慢响应不能覆盖更新的请求。
          const stateSeq = React.useRef(0);
          const chatsSeq = React.useRef(0);
          const messagesSeq = React.useRef(0);
          const wasRunning = React.useRef(false);
          const stickBottom = React.useRef(true);
          const listRef = React.useRef(null);

          const [snapshot, setSnapshot] = React.useState(null);
          const [stateError, setStateError] = React.useState('');
          const [accounts, setAccounts] = React.useState([]);
          const [accountsLoaded, setAccountsLoaded] = React.useState(false);
          const [chats, setChats] = React.useState([]);
          const [chatsLoading, setChatsLoading] = React.useState(false);
          const [keyword, setKeyword] = React.useState('');
          const [selectedUid, setSelectedUid] = React.useState(null);
          const [chat, setChat] = React.useState(null);
          const [messages, setMessages] = React.useState([]);
          const [messagesLoading, setMessagesLoading] = React.useState(false);
          const [stats, setStats] = React.useState(null);
          const [draft, setDraft] = React.useState('');
          const [sending, setSending] = React.useState(false);
          const [sendError, setSendError] = React.useState('');
          // 'start' | 'stop' | 'restart' | 'activate'：按钮与账号切换期间的互斥锁。
          const [action, setAction] = React.useState('');
          // 可关闭的顶部错误条：动作失败、数据加载失败，不包含「控制台离线」——那用常驻横幅。
          const [error, setError] = React.useState('');

          const running = Boolean(snapshot && snapshot.running);
          const phase = snapshot && snapshot.phase ? String(snapshot.phase) : 'unknown';
          const busy = Boolean(action) || phase === 'starting' || phase === 'stopping';
          const activeAccount = accounts.find((item) => item && item.active) || null;
          const selectedChat = chat || chats.find((item) => item && item.uid === selectedUid) || null;
          const selectedName = selectedChat && selectedChat.name ? String(selectedChat.name) : String(selectedUid || '');

          function setErrorOnce(message) {
            if (!mounted.current || !message) return;
            setError((previous) => (previous === message ? previous : message));
          }

          // ------------------------------------------------------------ 数据加载
          async function loadState() {
            const seq = ++stateSeq.current;
            try {
              const value = await call('state', {});
              if (!mounted.current || seq !== stateSeq.current) return;
              if (value && typeof value === 'object') setSnapshot(value);
              setStateError('');
            } catch (error0) {
              if (!mounted.current || seq !== stateSeq.current) return;
              setStateError(errorMessage(error0));
            }
          }

          async function loadAccounts() {
            try {
              const value = await call('accounts.list', {});
              if (!mounted.current) return;
              setAccounts(Array.isArray(value && value.accounts) ? value.accounts : []);
            } catch (error0) {
              if (!mounted.current) return;
              setErrorOnce(errorMessage(error0));
            } finally {
              if (mounted.current) setAccountsLoaded(true);
            }
          }

          async function loadChats(keywordValue) {
            const seq = ++chatsSeq.current;
            if (mounted.current) setChatsLoading(true);
            try {
              const value = await call('chats.list', { keyword: keywordValue || '', limit: 200 });
              if (!mounted.current || seq !== chatsSeq.current) return;
              setChats(Array.isArray(value && value.chats) ? value.chats : []);
            } catch (error0) {
              if (!mounted.current || seq !== chatsSeq.current) return;
              setChats([]);
              setErrorOnce(errorMessage(error0));
            } finally {
              if (mounted.current && seq === chatsSeq.current) setChatsLoading(false);
            }
          }

          async function loadMessages(uid) {
            const seq = ++messagesSeq.current;
            if (mounted.current) setMessagesLoading(true);
            try {
              const value = await call('messages.list', { uid, limit: 60 });
              if (!mounted.current || seq !== messagesSeq.current) return;
              setChat(value && value.chat ? value.chat : null);
              setMessages(Array.isArray(value && value.messages) ? value.messages : []);
              setMessagesLoading(false);
              // 服务端读消息已清未读，列表同步消掉角标，避免下次刷新前仍显示旧数字。
              setChats((previous) => previous.map((item) => (item && item.uid === uid ? { ...item, unread: 0 } : item)));
            } catch (error0) {
              if (!mounted.current || seq !== messagesSeq.current) return;
              setMessagesLoading(false);
              setErrorOnce(errorMessage(error0));
            }
          }

          async function loadStats() {
            try {
              const value = await call('stats', {});
              if (!mounted.current) return;
              setStats(value && value.stats && typeof value.stats === 'object' ? value.stats : null);
            } catch (error0) {
              if (!mounted.current) return;
              setStats(null);
              setErrorOnce(errorMessage(error0));
            }
          }

          // ------------------------------------------------------------ 轮询效果
          // 状态轮询：挂载立即拉一次，之后每 4s；页面在后台时跳过，避免无意义请求。
          React.useEffect(() => {
            mounted.current = true;
            const tick = () => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              void loadState();
            };
            tick();
            const timer = window.setInterval(tick, POLL_MS);
            return () => {
              mounted.current = false;
              window.clearInterval(timer);
            };
          }, []);

          // 控制台开始运行时拉账号/统计；停止时清掉过期数据，避免显示停机前的旧账号与会话。
          React.useEffect(() => {
            if (running && !wasRunning.current) {
              void loadAccounts();
              void loadStats();
            } else if (!running && wasRunning.current) {
              setAccounts([]);
              setAccountsLoaded(false);
              setChats([]);
              setStats(null);
              setSelectedUid(null);
              setChat(null);
              setMessages([]);
              setSendError('');
            }
            wasRunning.current = running;
          }, [running]);

          // 会话搜索：输入停顿 250ms 后拉列表；控制台刚运行时也靠这一条拉首屏。
          React.useEffect(() => {
            if (!running) return undefined;
            const timer = window.setTimeout(() => {
              void loadChats(keyword);
            }, 250);
            return () => window.clearTimeout(timer);
          }, [keyword, running]);

          // 选中会话的消息轮询：与状态轮询同频 4s，切换会话或卸载时清理。
          React.useEffect(() => {
            if (!running || !selectedUid) {
              setMessages([]);
              setChat(null);
              setMessagesLoading(false);
              return undefined;
            }
            void loadMessages(selectedUid);
            const timer = window.setInterval(() => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              void loadMessages(selectedUid);
            }, POLL_MS);
            return () => window.clearInterval(timer);
          }, [selectedUid, running]);

          // 新消息到达时，只有用户本来就停在底部才自动跟随，避免打断向上翻看历史。
          React.useEffect(() => {
            const element = listRef.current;
            if (element && stickBottom.current) element.scrollTop = element.scrollHeight;
          }, [messages, selectedUid, messagesLoading]);

          // ------------------------------------------------------------ 用户操作
          function selectChat(uid) {
            stickBottom.current = true;
            setSelectedUid(uid);
            // 清掉上一个会话的残留，避免新会话数据到达前把旧消息显示在新会话标题下。
            setChat(null);
            setMessages([]);
            setMessagesLoading(true);
            setDraft('');
            setSendError('');
          }

          async function runConsoleAction(name) {
            if (action) return;
            setAction(name);
            setError('');
            try {
              // start/restart 可能要等控制台健康检查就绪（Host 端最多等待约 60s），超时放宽。
              await call('console.' + name, {}, name === 'stop' ? 30000 : 90000);
              if (!mounted.current) return;
              // 动作返回的是进程状态子集，重新拉一次完整快照（含端口/标题/模式）再刷新界面。
              await loadState();
            } catch (error0) {
              if (mounted.current) setError(errorMessage(error0));
            } finally {
              if (mounted.current) setAction('');
            }
          }

          async function activateAccount(accountId) {
            if (!accountId || action || !running) return;
            setAction('activate');
            setError('');
            try {
              await call('accounts.activate', { id: accountId }, 15000);
              if (!mounted.current) return;
              setSelectedUid(null);
              setChat(null);
              setMessages([]);
              await Promise.all([loadAccounts(), loadChats(keyword), loadStats(), loadState()]);
            } catch (error0) {
              if (mounted.current) setError(errorMessage(error0));
            } finally {
              if (mounted.current) setAction('');
            }
          }

          async function sendMessage() {
            const text = draft.trim();
            if (!text || !selectedUid || sending || !running) return;
            setSending(true);
            setSendError('');
            try {
              await call('send.text', { uid: selectedUid, text }, 60000);
              if (!mounted.current) return;
              setDraft('');
              stickBottom.current = true;
              // 发送后立即刷新消息与统计；会话列表的摘要也跟着变。
              await loadMessages(selectedUid);
              void loadChats(keyword);
              void loadStats();
            } catch (error0) {
              if (mounted.current) setSendError(errorMessage(error0));
            } finally {
              if (mounted.current) setSending(false);
            }
          }

          function handleDraftKeyDown(event) {
            if (event.key !== 'Enter' || event.shiftKey) return;
            // 中文输入法组合期间的 Enter 只用于上屏，不能当成发送。
            if (event.nativeEvent && event.nativeEvent.isComposing) return;
            event.preventDefault();
            void sendMessage();
          }

          function openConsole() {
            const url = consoleUrl(snapshot);
            const fallback = () => {
              try {
                window.open(url, '_blank', 'noopener');
              } catch (error0) {
                if (mounted.current) setError('无法打开新窗口：' + errorMessage(error0));
              }
            };
            try {
              // 优先交给宿主右侧栏的 browser 标签页；服务不存在或 kind 未注册时降级窗口打开。
              const sidebarRight = ctx.get('sidebarRight');
              if (sidebarRight && typeof sidebarRight.openTab === 'function') {
                const opened = sidebarRight.openTab('browser', { params: { url } });
                if (opened && typeof opened.catch === 'function') opened.catch(fallback);
                return;
              }
            } catch (error0) {
              // 忽略：走下面的 window.open 降级路径
            }
            fallback();
          }

          // ------------------------------------------------------------ 渲染片段
          function actionButton(label, onClick, options) {
            const settings = options || {};
            return h('button', {
              type: 'button',
              className: 'dsh-deepfeiyu-btn' + (settings.primary ? ' dsh-deepfeiyu-btn-primary' : ''),
              disabled: Boolean(settings.disabled),
              title: settings.title,
              onClick,
            }, label);
          }

          function renderHeader() {
            const meta = phaseMeta(phase);
            const metaParts = [];
            if (snapshot) {
              if (snapshot.version) metaParts.push('v' + snapshot.version);
              metaParts.push('端口 ' + (snapshot.port || FALLBACK_PORT));
              if (snapshot.mode === 'attach') metaParts.push('附着模式');
              if (snapshot.spawnedByPlugin) metaParts.push('由插件拉起');
            } else if (stateError) {
              metaParts.push('Host 连接异常');
            } else {
              metaParts.push('正在连接插件 Host…');
            }
            return h('header', { className: 'dsh-deepfeiyu-header' },
              h('div', { className: 'dsh-deepfeiyu-heading' },
                h('div', { className: 'dsh-deepfeiyu-title-row' },
                  h('h1', { className: 'dsh-deepfeiyu-title' }, snapshot && snapshot.title ? String(snapshot.title) : 'Deep肥鱼'),
                  h('span', { className: 'dsh-deepfeiyu-badge' },
                    h('span', { className: 'dsh-deepfeiyu-dot dsh-deepfeiyu-is-' + (snapshot ? meta.tone : 'idle') }),
                    snapshot ? meta.text : '连接中…')),
                h('p', { className: 'dsh-deepfeiyu-meta' }, metaParts.join(' · '))),
              h('div', { className: 'dsh-deepfeiyu-actions' },
                actionButton('打开完整控制台', openConsole, { title: '在右侧栏标签页打开 Deep肥鱼完整控制台' }),
                actionButton(action === 'start' ? '启动中…' : '启动', () => { void runConsoleAction('start'); }, {
                  disabled: busy || running,
                  title: running ? '控制台已在运行' : '启动控制台进程',
                }),
                actionButton(action === 'stop' ? '停止中…' : '停止', () => { void runConsoleAction('stop'); }, {
                  disabled: busy || !running,
                  title: running ? '停止控制台进程' : '控制台未运行',
                }),
                actionButton(action === 'restart' ? '重启中…' : '重启', () => { void runConsoleAction('restart'); }, {
                  disabled: busy,
                  title: '停止后重新启动控制台',
                })));
          }

          function renderErrorBar() {
            if (!error) return null;
            return h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
              h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, error),
              h('button', {
                type: 'button',
                className: 'dsh-deepfeiyu-errorbar-close',
                'aria-label': '关闭错误提示',
                onClick: () => setError(''),
              }, '关闭'));
          }

          function bannerCard(tone, title, detail, actions) {
            return h('div', { className: 'dsh-deepfeiyu-banner dsh-deepfeiyu-is-' + tone },
              h('span', { className: 'dsh-deepfeiyu-dot dsh-deepfeiyu-is-' + (tone === 'error' ? 'error' : tone === 'busy' ? 'busy' : 'idle') }),
              h('div', { className: 'dsh-deepfeiyu-banner-body' },
                h('p', { className: 'dsh-deepfeiyu-banner-title' }, title),
                detail ? h('p', { className: 'dsh-deepfeiyu-banner-detail' }, detail) : null),
              actions && actions.length ? h('div', { className: 'dsh-deepfeiyu-banner-actions' }, actions) : null);
          }

          function renderBanner() {
            if (stateError) {
              return bannerCard('error', '无法连接插件 Host', stateError, [
                actionButton('重试', () => { void loadState(); }, { primary: true }),
              ]);
            }
            if (!snapshot) return null;
            if (snapshot.running) return null;
            if (phase === 'starting') {
              return bannerCard('busy', '控制台正在启动', '首次启动需要拉起 Python 进程并等待健康检查，通常几秒到几十秒，请稍候。', []);
            }
            if (phase === 'stopping') {
              return bannerCard('busy', '控制台正在停止', '进程退出后即可再次启动。', []);
            }
            const detail = snapshot.lastError
              ? String(snapshot.lastError)
              : '启动后即可查看账号、会话并发送微信消息。';
            return bannerCard(phase === 'error' ? 'error' : 'idle',
              phase === 'error' ? '控制台启动失败' : '控制台未运行',
              detail,
              [actionButton('启动控制台', () => { void runConsoleAction('start'); }, { primary: true, disabled: busy })]);
          }

          function renderAccountChip(account) {
            const id = account && account.id ? String(account.id) : '';
            const active = Boolean(account && account.active);
            const connected = Boolean(account && account.connected);
            return h('button', {
              key: id || accountName(account),
              type: 'button',
              className: 'dsh-deepfeiyu-chip' + (active ? ' dsh-deepfeiyu-is-active' : ''),
              disabled: active || busy || !running,
              title: [account && account.display_name, account && account.detail, account && account.error].filter(Boolean).join(' · ') || id,
              onClick: () => { void activateAccount(id); },
            },
            h('span', { className: 'dsh-deepfeiyu-avatar' }, (accountName(account) || id || '?').slice(0, 1)),
            h('span', { className: 'dsh-deepfeiyu-chip-text' },
              h('span', { className: 'dsh-deepfeiyu-chip-name' }, accountName(account)),
              h('span', { className: 'dsh-deepfeiyu-chip-sub' },
                driverText(account && account.driver) + ' · ' + (connected ? '已连接' : '未连接'))),
            h('span', { className: 'dsh-deepfeiyu-dot ' + (connected ? 'dsh-deepfeiyu-is-ok' : 'dsh-deepfeiyu-is-idle') }));
          }

          function renderAccounts() {
            let body;
            if (!running) {
              body = h('span', { className: 'dsh-deepfeiyu-hint' }, '控制台运行后显示账号');
            } else if (!accounts.length) {
              body = h('span', { className: 'dsh-deepfeiyu-hint' }, accountsLoaded ? '暂无账号' : '正在读取账号…');
            } else {
              body = accounts.map(renderAccountChip);
            }
            return h('section', { className: 'dsh-deepfeiyu-accounts' },
              h('div', { className: 'dsh-deepfeiyu-section-head' },
                h('span', { className: 'dsh-deepfeiyu-section-title' }, '账号'),
                activeAccount ? h('span', { className: 'dsh-deepfeiyu-hint' }, '当前：' + accountName(activeAccount)) : null),
              h('div', { className: 'dsh-deepfeiyu-chips' }, body));
          }

          function renderChatRow(item) {
            const uid = item && item.uid ? String(item.uid) : '';
            if (!uid) return null;
            const unread = Number(item.unread) || 0;
            const tags = [];
            if (item.is_group) tags.push(h('span', { className: 'dsh-deepfeiyu-tag', key: 'group' }, '群'));
            if (item.paused) tags.push(h('span', { className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-warn', key: 'paused' }, '人工'));
            if (item.ai_enabled) tags.push(h('span', { className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-ok', key: 'ai' }, 'AI'));
            return h('button', {
              key: uid,
              type: 'button',
              role: 'listitem',
              className: 'dsh-deepfeiyu-chat' + (uid === selectedUid ? ' dsh-deepfeiyu-is-active' : ''),
              title: item.name ? String(item.name) : uid,
              onClick: () => selectChat(uid),
            },
            h('span', { className: 'dsh-deepfeiyu-chat-line' },
              h('span', { className: 'dsh-deepfeiyu-chat-name' }, item.name ? String(item.name) : uid),
              tags.length ? h('span', { className: 'dsh-deepfeiyu-tags' }, tags) : null,
              h('span', { className: 'dsh-deepfeiyu-chat-time' }, formatChatTime(item.last_ts))),
            h('span', { className: 'dsh-deepfeiyu-chat-line' },
              h('span', { className: 'dsh-deepfeiyu-chat-preview' }, clipText(item.last_message || '（暂无消息）', 36)),
              unread > 0 ? h('span', { className: 'dsh-deepfeiyu-unread' }, unread > 99 ? '99+' : String(unread)) : null));
          }

          function renderChatList() {
            const rows = chats.map(renderChatRow).filter(Boolean);
            let hint;
            if (!running) hint = '控制台运行后显示会话';
            else if (chatsLoading && !rows.length) hint = '正在加载会话…';
            else if (keyword) hint = '没有匹配的会话';
            else hint = '暂无会话';
            return h('aside', { className: 'dsh-deepfeiyu-chats' },
              h('div', { className: 'dsh-deepfeiyu-chats-head' },
                h('input', {
                  className: 'dsh-deepfeiyu-input',
                  type: 'search',
                  value: keyword,
                  placeholder: '搜索会话名称…',
                  'aria-label': '搜索会话',
                  disabled: !running,
                  onChange: (event) => setKeyword(event.target.value),
                }),
                actionButton('刷新', () => { void loadChats(keyword); }, {
                  disabled: !running || chatsLoading,
                  title: '刷新会话列表',
                })),
              h('div', { className: 'dsh-deepfeiyu-chat-list', role: 'list' },
                rows.length ? rows : h('p', { className: 'dsh-deepfeiyu-hint dsh-deepfeiyu-list-hint' }, hint)));
          }

          function renderMessage(message, index) {
            const mine = Boolean(message.is_self);
            const typeText = message.msg_type && message.msg_type !== 'text' ? msgTypeText(message.msg_type) : '';
            const rawContent = String(message.content === null || message.content === undefined ? '' : message.content).trim();
            // 图片/语音等摘要本身就是「[图片]」这类占位，类型标签已经说明，不再重复一遍。
            const content = rawContent && typeText && rawContent === '[' + typeText + ']' ? '' : rawContent;
            return h('div', {
              key: message.id || String(message.ts || 0) + '-' + index,
              className: 'dsh-deepfeiyu-msg'
                + (mine ? ' dsh-deepfeiyu-is-mine' : '')
                + (message.is_revoked ? ' dsh-deepfeiyu-is-muted' : ''),
            },
            h('div', { className: 'dsh-deepfeiyu-msg-meta' },
              h('span', { className: 'dsh-deepfeiyu-msg-sender' }, mine ? '我' : (message.sender_name || '对方')),
              h('span', { className: 'dsh-deepfeiyu-msg-time' }, formatMessageTime(message.ts))),
            h('div', { className: 'dsh-deepfeiyu-bubble' },
              typeText ? h('span', { className: 'dsh-deepfeiyu-tag' }, typeText) : null,
              content || typeText
                ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, content)
                : h('span', { className: 'dsh-deepfeiyu-msg-text' }, '（空消息）')));
          }

          function handleMessagesScroll(event) {
            const element = event.currentTarget;
            stickBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
          }

          function renderComposer() {
            const composerDisabled = !running || !selectedUid;
            const canSend = !composerDisabled && draft.trim().length > 0 && !sending;
            return h('div', { className: 'dsh-deepfeiyu-composer' },
              sendError ? h('p', { className: 'dsh-deepfeiyu-send-error', role: 'alert' }, sendError) : null,
              h('div', { className: 'dsh-deepfeiyu-composer-row' },
                h('textarea', {
                  className: 'dsh-deepfeiyu-textarea',
                  value: draft,
                  rows: 2,
                  maxLength: 2000,
                  placeholder: composerDisabled ? '控制台未运行或未选择会话' : '输入要发送的微信消息…',
                  'aria-label': '要发送的微信消息',
                  disabled: composerDisabled,
                  onChange: (event) => setDraft(event.target.value),
                  onKeyDown: handleDraftKeyDown,
                }),
                actionButton(sending ? '发送中…' : '发送', () => { void sendMessage(); }, {
                  primary: true,
                  disabled: !canSend,
                  title: canSend ? '发送消息（Enter）' : '请输入消息内容',
                })),
              h('p', { className: 'dsh-deepfeiyu-hint' }, 'Enter 发送，Shift+Enter 换行；发送后自动刷新消息。'));
          }

          function renderThread() {
            if (!selectedUid) {
              return h('section', { className: 'dsh-deepfeiyu-thread' },
                h('div', { className: 'dsh-deepfeiyu-thread-empty' },
                  h('p', { className: 'dsh-deepfeiyu-empty-title' }, '选择左侧会话查看消息'),
                  h('p', { className: 'dsh-deepfeiyu-hint' },
                    running ? '选中会话后可以查看最近消息并发送微信消息。' : '控制台运行后可查看与发送微信消息。')));
            }
            const rows = messages.map(renderMessage);
            return h('section', { className: 'dsh-deepfeiyu-thread' },
              h('div', { className: 'dsh-deepfeiyu-thread-head' },
                h('span', { className: 'dsh-deepfeiyu-thread-name' }, selectedName || String(selectedUid)),
                selectedChat && selectedChat.is_group ? h('span', { className: 'dsh-deepfeiyu-tag' }, '群聊') : null,
                h('span', { className: 'dsh-deepfeiyu-hint' }, messages.length ? '最近 ' + messages.length + ' 条' : '')),
              h('div', { className: 'dsh-deepfeiyu-messages', ref: listRef, onScroll: handleMessagesScroll },
                rows.length
                  ? rows
                  : h('p', { className: 'dsh-deepfeiyu-hint dsh-deepfeiyu-list-hint' },
                    messagesLoading ? '正在加载消息…' : '此会话还没有消息')),
              renderComposer());
          }

          function renderStats() {
            const today = stats && stats.today && typeof stats.today === 'object' ? stats.today : null;
            const entries = [
              ['inbound', '收到'],
              ['outbound', '发出'],
              ['ai', 'AI 回复'],
              ['chats', '会话'],
            ];
            return h('footer', { className: 'dsh-deepfeiyu-stats' },
              h('span', { className: 'dsh-deepfeiyu-stats-title' }, '今日统计'),
              entries.map(([key, label]) => h('span', { className: 'dsh-deepfeiyu-stat', key },
                h('span', { className: 'dsh-deepfeiyu-stat-value' }, today ? String(Number(today[key]) || 0) : '—'),
                h('span', { className: 'dsh-deepfeiyu-stat-label' }, label))),
              today ? null : h('span', { className: 'dsh-deepfeiyu-hint' }, running ? '统计数据暂不可用' : '控制台运行后显示今日统计'));
          }

          return h('div', { className: 'dsh-deepfeiyu-root' },
            h('style', null, STYLES),
            renderHeader(),
            renderErrorBar(),
            renderBanner(),
            renderAccounts(),
            h('div', { className: 'dsh-deepfeiyu-body' }, renderChatList(), renderThread()),
            renderStats());
        }

        // ------------------------------------------------------ 设置页：依赖环境
        // 挂在 settings.section：列出控制台六个可选功能依赖的状态，支持一键安装并实时看日志。
        // 控制台未运行时 deps.state 直接失败（错误信息已含“请先启动控制台”），页内给出启动入口，不静默失败。
        function DeepFeiyuSettingsPage() {
          // 组件卸载后异步回调不再 setState；日志定时器也必须清掉，否则会持续请求已关闭的控制台。
          const mounted = React.useRef(true);
          // 依赖状态请求序号：慢响应不能覆盖更新的状态。
          const depsSeq = React.useRef(0);
          const logFeature = React.useRef('');
          const logOffset = React.useRef(0);
          // 日志请求互斥：800ms 轮询比 15s 超时短，避免慢请求堆叠。
          const logBusy = React.useRef(false);
          const logTimer = React.useRef(null);
          const terminalRef = React.useRef(null);

          const [deps, setDeps] = React.useState(null);
          const [depsError, setDepsError] = React.useState('');
          const [startingConsole, setStartingConsole] = React.useState(false);
          // 本地安装请求的互斥标记：POST 返回前就禁用按钮，避免连点启动多个安装任务。
          const [installPending, setInstallPending] = React.useState('');
          const [log, setLog] = React.useState(null);
          // 「关联 DSH 插件」独立于控制台依赖：Host 侧 pluginManager 不可用时只提示，不影响上面。
          const [dshPlugins, setDshPlugins] = React.useState(null);
          const [dshPluginsError, setDshPluginsError] = React.useState('');
          const [dshInstallPending, setDshInstallPending] = React.useState('');
          // 「心情与桌宠（融合）」：绑定账号、开关、双源时间线都由控制台 /api/dsh/* 提供。
          const [fusion, setFusion] = React.useState(null);
          const [fusionError, setFusionError] = React.useState('');
          const [fusionPending, setFusionPending] = React.useState(false);

          function clearLogTimer() {
            if (logTimer.current) {
              window.clearInterval(logTimer.current);
              logTimer.current = null;
            }
          }

          function stopLog() {
            clearLogTimer();
            logFeature.current = '';
            logOffset.current = 0;
            logBusy.current = false;
          }

          function closeLog() {
            stopLog();
            setLog(null);
          }

          // 拉一次增量日志：偏移量只增；服务端环形缓冲被截断时 reset=true，要求整体替换旧内容。
          async function fetchLog() {
            const feature = logFeature.current;
            if (!feature || logBusy.current) return;
            logBusy.current = true;
            try {
              const value = await call('deps.log', { feature, offset: logOffset.current }, 15000);
              if (!mounted.current || logFeature.current !== feature) return;
              const body = value && typeof value === 'object' ? value : {};
              const text = typeof body.text === 'string' ? body.text : '';
              const nextOffset = Number(body.offset);
              const done = Boolean(body.done);
              const phase = body.phase ? String(body.phase) : '';
              logOffset.current = Number.isFinite(nextOffset) && nextOffset >= 0 ? nextOffset : logOffset.current + text.length;
              setLog((previous) => {
                // reset 或换了功能就丢弃旧日志，避免不同安装的片段拼在一起。
                const base = previous && previous.feature === feature && !body.reset ? previous.text : '';
                return {
                  feature,
                  text: base + text,
                  phase: phase || (previous && previous.feature === feature ? previous.phase : ''),
                  step: typeof body.step === 'string' ? body.step : '',
                  error: typeof body.error === 'string' ? body.error : '',
                  done,
                };
              });
              if (done) {
                clearLogTimer();
                void loadDeps();
              }
            } catch (error0) {
              if (!mounted.current) return;
              // 控制台中途退出：停止轮询并保留已输出日志，避免每 800ms 重复报同一个错。
              clearLogTimer();
              setLog((previous) => (previous && previous.feature === feature
                ? { ...previous, error: errorMessage(error0), done: true }
                : previous));
            } finally {
              logBusy.current = false;
            }
          }

          // offset 用安装响应的 log_offset（从当前末尾开始），或 0 表示回看已有日志。
          function watchLog(feature, offset) {
            if (!feature) return;
            clearLogTimer();
            logFeature.current = feature;
            logOffset.current = Math.max(0, Math.trunc(Number(offset) || 0));
            logBusy.current = false;
            setLog({ feature, text: '', phase: '', step: '', error: '', done: false });
            void fetchLog();
            logTimer.current = window.setInterval(() => { void fetchLog(); }, DEP_LOG_MS);
          }

          async function loadDeps() {
            const seq = ++depsSeq.current;
            try {
              const value = await call('deps.state', {}, 15000);
              if (!mounted.current || seq !== depsSeq.current) return;
              const body = value && typeof value === 'object' ? value : {};
              setDeps({
                installing: body.installing ? String(body.installing) : '',
                features: Array.isArray(body.features)
                  ? body.features.filter((item) => item && typeof item === 'object')
                  : [],
              });
              setDepsError('');
            } catch (error0) {
              if (!mounted.current || seq !== depsSeq.current) return;
              // 不保留旧状态：控制台不可达时旧徽标会误导，直接展示错误与启动入口。
              setDeps(null);
              setDepsError(errorMessage(error0));
            }
          }

          async function startConsole() {
            if (startingConsole) return;
            setStartingConsole(true);
            setDepsError('');
            try {
              // 启动要等健康检查就绪（Host 端最多约 60s），超时放宽到 90s。
              await call('console.start', {}, 90000);
              if (!mounted.current) return;
              // 就绪后再拉一次；固定 4s 轮询也会兜底重试。
              window.setTimeout(() => {
                if (mounted.current) void loadDeps();
              }, 1500);
            } catch (error0) {
              if (mounted.current) setDepsError(errorMessage(error0));
            } finally {
              if (mounted.current) setStartingConsole(false);
            }
          }

          // ------------------------------------------------ 关联 DSH 插件（白名单安装）
          async function loadDshPlugins() {
            try {
              const value = await call('dshPlugins.state', {}, 15000);
              if (!mounted.current) return;
              const body = value && typeof value === 'object' ? value : {};
              setDshPlugins({
                available: body.available !== false,
                error: typeof body.error === 'string' ? body.error : '',
                installing: body.installing ? String(body.installing) : '',
                plugins: Array.isArray(body.plugins)
                  ? body.plugins.filter((item) => item && typeof item === 'object')
                  : [],
                last: body.last && typeof body.last === 'object' ? body.last : null,
              });
              setDshPluginsError('');
            } catch (error0) {
              if (!mounted.current) return;
              // 面板主体仍可用：这里只影响 DSH 插件区块，不清空上面的依赖列表。
              setDshPlugins(null);
              setDshPluginsError(errorMessage(error0));
            }
          }

          async function installDshPlugin(plugin) {
            const id = plugin && plugin.id ? String(plugin.id) : '';
            if (!id || dshInstallPending || (dshPlugins && dshPlugins.installing)) return;
            const name = plugin && plugin.name ? String(plugin.name) : id;
            const size = plugin && plugin.sizeHint ? String(plugin.sizeHint) : '较大体积';
            // 安装 bundle = 下载第三方代码在 Host 进程全权限运行：必须让用户明确点过确认。
            const confirmed = typeof window === 'undefined' || typeof window.confirm !== 'function'
              ? true
              : window.confirm('将安装第三方 DSH 插件 ' + name + '（npm 最新版，' + size + '）。\n\n'
                + '它会在 Harness 内执行第三方代码，且由 Harness 会话驱动，与控制台里的功能是两套。\n\n确定继续？');
            if (!confirmed) return;
            setDshInstallPending(id);
            setDshPluginsError('');
            try {
              const value = await call('dshPlugins.install', { id }, 30000);
              if (!mounted.current) return;
              if (value && typeof value === 'object' && value.ok === false) {
                setDshPluginsError(typeof value.error === 'string' && value.error ? value.error : '安装未能启动');
                return;
              }
              // 后台安装可能几分钟：先刷一次拿到 installing，之后由 1.5s 轮询跟进。
              await loadDshPlugins();
            } catch (error0) {
              if (mounted.current) setDshPluginsError(errorMessage(error0));
            } finally {
              if (mounted.current) setDshInstallPending('');
            }
          }

          async function loadFusion() {
            try {
              const value = await call('fusion.state', {}, 15000);
              if (!mounted.current) return;
              setFusion(value && typeof value === 'object' ? value : null);
              setFusionError('');
            } catch (error0) {
              if (!mounted.current) return;
              // 融合区块失败不影响依赖列表：只清融合状态并就地显示错误。
              setFusion(null);
              setFusionError(errorMessage(error0));
            }
          }

          async function saveFusion(patch) {
            if (fusionPending) return;
            setFusionPending(true);
            setFusionError('');
            try {
              const value = await call('fusion.config', patch, 15000);
              if (!mounted.current) return;
              setFusion(value && typeof value === 'object' ? value : null);
              await loadFusion();
            } catch (error0) {
              if (mounted.current) setFusionError(errorMessage(error0));
            } finally {
              if (mounted.current) setFusionPending(false);
            }
          }

          async function installFeature(feature) {
            const id = feature && feature.id ? String(feature.id) : '';
            if (!id || installPending || (deps && deps.installing)) return;
            setInstallPending(id);
            setDepsError('');
            try {
              const value = await call('deps.install', { feature: id }, 15000);
              if (!mounted.current) return;
              // 控制台业务失败仍可能返回 HTTP 200 的 {ok:false}，不能当成安装已启动。
              if (value && typeof value === 'object' && value.ok === false) {
                setDepsError(typeof value.error === 'string' && value.error ? value.error : '安装未能启动');
                return;
              }
              const offset = value && typeof value === 'object' ? Number(value.log_offset) : 0;
              watchLog(id, Number.isFinite(offset) ? offset : 0);
              // 等状态刷新回来再解除本地互斥，避免 installing 尚未上报前按钮短暂可点、重复启动安装。
              await loadDeps();
            } catch (error0) {
              if (mounted.current) setDepsError(errorMessage(error0));
            } finally {
              if (mounted.current) setInstallPending('');
            }
          }

          // 依赖状态轮询：挂载立即拉一次，之后每 4s；页面在后台时跳过。
          React.useEffect(() => {
            mounted.current = true;
            const tick = () => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              void loadDeps();
              void loadDshPlugins();
              void loadFusion();
            };
            tick();
            const timer = window.setInterval(tick, POLL_MS);
            return () => {
              mounted.current = false;
              window.clearInterval(timer);
              clearLogTimer();
            };
          }, []);

          // 安装 DSH 插件期间用更快的轮询拿结果（大包可能下载几分钟）；其余时间跟随 4s 主轮询。
          const dshInstallingId = dshPlugins && dshPlugins.installing ? String(dshPlugins.installing) : '';
          React.useEffect(() => {
            if (!dshInstallingId) return undefined;
            const timer = window.setInterval(() => { void loadDshPlugins(); }, 1500);
            return () => window.clearInterval(timer);
          }, [dshInstallingId]);

          // 日志增长时自动贴底，和终端一致；安装输出通常只需要看最新几行。
          React.useEffect(() => {
            const element = terminalRef.current;
            if (element) element.scrollTop = element.scrollHeight;
          }, [log && log.text]);

          // ------------------------------------------------------------ 渲染片段
          function settingsButton(label, onClick, options) {
            const settings = options || {};
            return h('button', {
              type: 'button',
              className: 'dsh-deepfeiyu-btn' + (settings.primary ? ' dsh-deepfeiyu-btn-primary' : ''),
              disabled: Boolean(settings.disabled),
              title: settings.title,
              onClick,
            }, label);
          }

          function renderDepsError() {
            if (!depsError) return null;
            return h('div', { className: 'dsh-deepfeiyu-dep-error', role: 'alert' },
              h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, depsError),
              settingsButton(startingConsole ? '启动中…' : '启动控制台', () => { void startConsole(); }, {
                primary: true,
                disabled: startingConsole,
                title: '启动 Deep肥鱼控制台进程',
              }));
          }

          function renderDepRow(feature) {
            const id = feature && feature.id ? String(feature.id) : '';
            if (!id) return null;
            const status = depsStatus(feature);
            const statusTone = status.tone === 'warn' ? 'busy' : status.tone;
            const supported = feature.supported !== false;
            const installed = Boolean(feature.installed);
            const installing = Boolean((deps && deps.installing === id) || feature.phase === 'installing');
            const failed = Boolean(feature.error) || feature.phase === 'error';
            const missing = Array.isArray(feature.missing) ? feature.missing.filter(Boolean).map(String) : [];
            const desc = typeof feature.desc === 'string' ? feature.desc.trim() : '';
            const note = typeof feature.note === 'string' ? feature.note.trim() : '';
            const installLabel = installing ? '安装中…' : installed ? '重新安装' : '一键安装';
            return h('div', { key: id, className: 'dsh-deepfeiyu-dep-row' },
              h('div', { className: 'dsh-deepfeiyu-dep-main' },
                h('div', { className: 'dsh-deepfeiyu-dep-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-name' }, feature.label ? String(feature.label) : id),
                  h('span', { className: 'dsh-deepfeiyu-badge' },
                    h('span', { className: 'dsh-deepfeiyu-dot dsh-deepfeiyu-is-' + statusTone }),
                    status.text)),
                desc ? h('p', { className: 'dsh-deepfeiyu-dep-desc' }, desc) : null,
                note ? h('p', { className: 'dsh-deepfeiyu-dep-note' }, note) : null,
                missing.length ? h('p', { className: 'dsh-deepfeiyu-dep-missing' }, '缺少：' + missing.join('、')) : null,
                feature.error ? h('p', { className: 'dsh-deepfeiyu-dep-failure' }, String(feature.error)) : null),
              h('div', { className: 'dsh-deepfeiyu-dep-actions' },
                settingsButton(installLabel, () => { void installFeature(feature); }, {
                  primary: !installed,
                  disabled: globalInstalling || !supported,
                  title: !supported
                    ? '当前系统不支持该功能'
                    : globalInstalling ? '已有安装任务进行中' : '安装该功能所需的 Python 依赖',
                }),
                installed || failed || installing
                  ? settingsButton('查看日志', () => watchLog(id, 0), { title: '查看该功能的安装日志' })
                  : null));
          }

          function renderLog() {
            if (!log) return null;
            const feature = currentFeature;
            const note = feature && typeof feature.note === 'string' ? feature.note.trim() : '';
            const phase = log.phase || '';
            let statusLine;
            if (phase === 'error' || log.error) {
              statusLine = h('p', { className: 'dsh-deepfeiyu-dep-log-status dsh-deepfeiyu-is-error', role: 'alert' },
                '安装失败：' + (log.error || '请查看上方日志定位原因。'));
            } else if (phase === 'ok') {
              // note 里若写了“重启控制台”之类的收尾提示，这里原样提醒一次，避免装完不生效的困惑。
              statusLine = h('p', { className: 'dsh-deepfeiyu-dep-log-status dsh-deepfeiyu-is-ok' },
                '✓ 安装完成' + (/重启/.test(note) ? ' · 请重启控制台使新依赖生效。' : ''));
            } else if (log.done) {
              statusLine = h('p', { className: 'dsh-deepfeiyu-dep-log-status dsh-deepfeiyu-is-busy' }, '安装已结束。');
            } else {
              statusLine = h('p', { className: 'dsh-deepfeiyu-dep-log-status dsh-deepfeiyu-is-busy' },
                '正在安装…' + (log.step ? ' ' + log.step : ''));
            }
            return h('section', { className: 'dsh-deepfeiyu-dep-terminal-card' },
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' },
                  '安装日志 · ' + (feature && feature.label ? String(feature.label) : log.feature)),
                settingsButton('收起', closeLog, { title: '收起日志面板' })),
              h('pre', { className: 'dsh-deepfeiyu-terminal', ref: terminalRef }, log.text || '等待安装日志…'),
              statusLine);
          }

          function dshPluginStatus(plugin) {
            const id = plugin && plugin.id ? String(plugin.id) : '';
            const installing = Boolean(
              (dshPlugins && dshPlugins.installing === id)
              || dshInstallPending === id);
            if (installing) return { text: '安装中…', tone: 'busy' };
            if (plugin && plugin.installed) {
              const version = plugin.version ? ' v' + String(plugin.version) : '';
              return { text: '已安装' + version, tone: 'ok' };
            }
            return { text: '未安装', tone: 'idle' };
          }

          function renderDshPluginRow(plugin) {
            const id = plugin && plugin.id ? String(plugin.id) : '';
            if (!id) return null;
            const status = dshPluginStatus(plugin);
            const installing = status.tone === 'busy';
            const installed = Boolean(plugin.installed);
            const desc = typeof plugin.description === 'string' ? plugin.description.trim() : '';
            const note = typeof plugin.note === 'string' ? plugin.note.trim() : '';
            const size = typeof plugin.sizeHint === 'string' ? plugin.sizeHint : '';
            // 一次只跑一个安装：已有任务时所有按钮都禁用，避免并发写 profile。
            const globalBusy = Boolean((dshPlugins && dshPlugins.installing) || dshInstallPending);
            const label = installing ? '安装中…' : installed ? '已安装' : '一键安装最新版';
            return h('div', { key: id, className: 'dsh-deepfeiyu-dep-row' },
              h('div', { className: 'dsh-deepfeiyu-dep-main' },
                h('div', { className: 'dsh-deepfeiyu-dep-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-name' }, plugin.label ? String(plugin.label) : id),
                  h('span', { className: 'dsh-deepfeiyu-badge' },
                    h('span', { className: 'dsh-deepfeiyu-dot dsh-deepfeiyu-is-' + status.tone }),
                    status.text)),
                desc ? h('p', { className: 'dsh-deepfeiyu-dep-desc' }, desc) : null,
                note ? h('p', { className: 'dsh-deepfeiyu-dep-note' }, note) : null,
                plugin.error ? h('p', { className: 'dsh-deepfeiyu-dep-failure' }, '插件状态异常：' + String(plugin.error)) : null),
              h('div', { className: 'dsh-deepfeiyu-dep-actions' },
                settingsButton(label, () => { void installDshPlugin(plugin); }, {
                  primary: !installed,
                  disabled: globalBusy || installed,
                  title: installed
                    ? '已安装；更新请到 DSH 插件页操作'
                    : '从 npm 安装最新版' + (size ? '（' + size + '）' : ''),
                })));
          }

          function renderDshPluginsSection() {
            const plugins = dshPlugins && Array.isArray(dshPlugins.plugins) ? dshPlugins.plugins : [];
            const last = dshPlugins && dshPlugins.last ? dshPlugins.last : null;
            const labelOf = (id) => {
              const found = plugins.find((item) => item && String(item.id) === String(id));
              return found && found.label ? String(found.label) : String(id || '');
            };
            let lastLine = null;
            if (last) {
              const text = last.ok
                ? '✓ ' + labelOf(last.id) + '：' + (last.version ? 'v' + String(last.version) + ' · ' : '') + String(last.message || '已安装')
                : '✗ ' + labelOf(last.id) + '安装失败：' + String(last.message || '未知错误');
              lastLine = h('p', {
                className: 'dsh-deepfeiyu-dep-log-status ' + (last.ok ? 'dsh-deepfeiyu-is-ok' : 'dsh-deepfeiyu-is-error'),
                ...(last.ok ? {} : { role: 'alert' }),
              }, text);
            }
            return h('section', { className: 'dsh-deepfeiyu-dep-terminal-card' },
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '关联 DSH 插件'),
                settingsButton('刷新', () => { void loadDshPlugins(); }, { title: '刷新 DSH 插件安装状态' })),
              h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '这两个上游插件由 Harness 会话驱动，与控制台里的桌宠/心情是两套；安装会在 Harness 内执行第三方代码，装的是 npm 最新版。'),
              dshPluginsError ? h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' }, dshPluginsError) : null,
              dshPlugins && dshPlugins.available === false
                ? h('p', { className: 'dsh-deepfeiyu-dep-note' }, dshPlugins.error || '当前 Harness 没有插件管理服务')
                : null,
              plugins.length
                ? h('div', { className: 'dsh-deepfeiyu-dep-list' }, plugins.map(renderDshPluginRow))
                : (dshPluginsError || (dshPlugins && dshPlugins.available === false)
                  ? null
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取 DSH 插件状态…')),
              lastLine);
          }

          function renderFusionSection() {
            const config = fusion && fusion.config ? fusion.config : {};
            const accounts = fusion && Array.isArray(fusion.accounts) ? fusion.accounts : [];
            const mood = fusion && fusion.mood ? fusion.mood : null;
            const timeline = fusion && Array.isArray(fusion.timeline) ? fusion.timeline.slice(0, 8) : [];
            const selected = fusion && fusion.accountId ? String(fusion.accountId) : '';
            const toggles = [
              ['judge_enabled', 'DSH 判断心情', 'Harness 回合结束后用默认模型判断，写回共享库'],
              ['pet_events_enabled', '宠物反映 Harness', '工具调用/回合结束时驱动同一只桌宠'],
              ['cross_reason_enabled', '允许 Harness 来历进微信', '只带 ≤60 字理由；群聊永远只说心情'],
            ];
            return h('section', { className: 'dsh-deepfeiyu-dep-terminal-card' },
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '心情与桌宠（融合）'),
                settingsButton('刷新', () => { void loadFusion(); }, { title: '刷新融合状态' })),
              h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '绑定账号后，Harness 与微信共享同一份心情；桌宠由控制台进程渲染，这里只控制与查看。'),
              fusionError ? h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' }, fusionError) : null,
              accounts.length
                ? h('div', { className: 'dsh-deepfeiyu-dep-row' },
                  h('div', { className: 'dsh-deepfeiyu-dep-main' },
                    h('div', { className: 'dsh-deepfeiyu-dep-head' },
                      h('span', { className: 'dsh-deepfeiyu-dep-name' }, '绑定账号'),
                      fusion && fusion.boundFallback
                        ? h('span', { className: 'dsh-deepfeiyu-badge' }, '已回落到当前账号')
                        : null),
                    h('p', { className: 'dsh-deepfeiyu-dep-desc' }, 'DSH 侧心情读写的微信账号')),
                  h('div', { className: 'dsh-deepfeiyu-dep-actions' },
                    h('select', {
                      className: 'dsh-deepfeiyu-input',
                      value: selected,
                      disabled: fusionPending,
                      onChange: (event) => { void saveFusion({ account_id: event.target.value }); },
                    }, accounts.map((account) => h('option', {
                      key: String(account.id), value: String(account.id),
                    }, String(account.name) + (account.active ? '（当前）' : ''))))))
                : null,
              h('div', { className: 'dsh-deepfeiyu-dep-list' }, toggles.map(([key, label, desc]) =>
                h('div', { key, className: 'dsh-deepfeiyu-dep-row' },
                  h('div', { className: 'dsh-deepfeiyu-dep-main' },
                    h('div', { className: 'dsh-deepfeiyu-dep-head' },
                      h('span', { className: 'dsh-deepfeiyu-dep-name' }, label)),
                    h('p', { className: 'dsh-deepfeiyu-dep-desc' }, desc)),
                  h('div', { className: 'dsh-deepfeiyu-dep-actions' },
                    settingsButton(config[key] ? '开' : '关', () => {
                      void saveFusion({ [key]: !config[key] });
                    }, { disabled: fusionPending, title: '切换' + label }))))),
              mood ? h('p', { className: 'dsh-deepfeiyu-dep-desc' },
                '当前心情：' + Math.round(Number(mood.mood) || 0) + '/100'
                + (fusion && fusion.boundFallback ? '（绑定账号失效，使用当前账号）' : '')) : null,
              timeline.length
                ? h('div', { className: 'dsh-deepfeiyu-dep-list' }, timeline.map((row, index) =>
                  h('p', { key: String(row.id || index), className: 'dsh-deepfeiyu-dep-note' },
                    (row.origin === 'harness' ? 'Harness' : '微信') + ' · '
                    + (row.reason ? String(row.reason).slice(0, 60) : '自然平复'))))
                : null);
          }

          const features = deps && Array.isArray(deps.features) ? deps.features : [];
          // 顶层 installing 是契约字段；再兜底看各功能 phase，避免字段缺失时重复点击启动第二个安装。
          const globalInstalling = Boolean(
            (deps && deps.installing)
            || installPending
            || features.some((item) => item && item.phase === 'installing'));
          const currentFeature = log
            ? features.find((item) => item && String(item.id) === log.feature) || null
            : null;

          return h('div', { className: 'dsh-deepfeiyu-settings' },
            h('style', null, STYLES),
            h('div', { className: 'dsh-deepfeiyu-heading' },
              h('h2', { className: 'dsh-deepfeiyu-settings-title' }, 'Deep肥鱼 · 依赖环境'),
              h('p', { className: 'dsh-deepfeiyu-meta' }, '控制台各可选功能的 Python 依赖；装完按提示重启控制台。')),
            renderDepsError(),
            deps
              ? h('div', { className: 'dsh-deepfeiyu-dep-list' },
                features.length
                  ? features.map(renderDepRow)
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '控制台未报告任何依赖功能。'))
              : (depsError ? null : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取依赖状态…')),
            renderLog(),
            renderDshPluginsSection(),
            renderFusionSection());
        }

        // 右下角心情挂饰：只读共享状态，20s 轮询；控制台不可达时显示“—”。
        function DeepFeiyuMoodWidget() {
          const mounted = React.useRef(true);
          const [state, setState] = React.useState(null);
          const [open, setOpen] = React.useState(false);

          async function load() {
            try {
              const value = await call('fusion.state', {}, 15000);
              if (!mounted.current) return;
              setState(value && typeof value === 'object' ? value : null);
            } catch {
              if (mounted.current) setState(null);
            }
          }

          React.useEffect(() => {
            mounted.current = true;
            void load();
            const timer = window.setInterval(() => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              void load();
            }, 20_000);
            return () => {
              mounted.current = false;
              window.clearInterval(timer);
            };
          }, []);

          const mood = state && state.mood ? state.mood : null;
          const value = mood ? Math.round(Number(mood.mood) || 0) : null;
          const face = value === null ? '·' : value >= 75 ? '☺' : value >= 50 ? '◡' : '·';
          const timeline = state && Array.isArray(state.timeline) ? state.timeline.slice(0, 5) : [];
          return h('div', { className: 'dsh-deepfeiyu-mood' },
            open ? h('div', { className: 'dsh-deepfeiyu-mood-panel' },
              h('p', { className: 'dsh-deepfeiyu-dep-name' },
                '心情 ' + (value === null ? '—' : value + '/100')),
              timeline.map((row, index) => h('p', {
                key: String(row.id || index), className: 'dsh-deepfeiyu-dep-note',
              }, (row.origin === 'harness' ? 'Harness' : '微信') + ' · '
                + (row.reason ? String(row.reason).slice(0, 40) : '自然平复')))) : null,
            h('button', {
              type: 'button',
              className: 'dsh-deepfeiyu-mood-button',
              'aria-label': '心情挂饰',
              onClick: () => setOpen((previous) => !previous),
            },
            h('span', { className: 'dsh-deepfeiyu-mood-face' }, face),
            h('span', { className: 'dsh-deepfeiyu-mood-value' }, value === null ? '—' : String(value))));
        }

        // 用 slots.inject 等父声明就绪后再注册：冷启动时 UI 树尚未声明这两个 slot，
        // 直接 register 会抛 "slot ... is not declared"，让 Client 插件激活失败并触发 boot 审计 fatal。
        ctx.effect(() => ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
          name: 'sidebar.panellist',
          id: 'deepfeiyu',
          order: 20,
          label: () => 'Deep肥鱼',
        }, DeepFeiyuIcon)), 'deepfeiyu.sidebar-icon');

        ctx.effect(() => ctx.slots.inject('main', () => ctx.slots.register({
          name: 'main',
          key: 'deepfeiyu',
        }, DeepFeiyuPanel)), 'deepfeiyu.main-panel');

        // 设置页分区：与上面同样走 inject 等待 slot 声明，冷启动时 settings 页可能还没挂载。
        // id 不与内置的 account/general/models/plugins/agent-presets 冲突。
        ctx.effect(() => ctx.slots.inject('settings.section', () => ctx.slots.register({
          name: 'settings.section',
          id: 'deepfeiyu',
          order: 30,
          label: () => 'Deep肥鱼',
        }, DeepFeiyuSettingsPage)), 'deepfeiyu.settings-section');

        // 右下角挂饰：frame-wide overlay 列表 slot，固定右下角，不抢任何面板布局。
        ctx.effect(() => ctx.slots.inject('shell.overlay', () => ctx.slots.register({
          name: 'shell.overlay',
          id: 'deepfeiyu-mood',
          order: 60,
          label: () => '心情',
        }, DeepFeiyuMoodWidget)), 'deepfeiyu.mood-widget');
      },
    };
  },
});
