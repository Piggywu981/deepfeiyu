// Deep肥鱼 DSH 插件 Client 半：侧栏「Deep肥鱼」图标 + 中央原生 React 面板。
// Host 半用 ctx.webServer 注册同源前缀路由 /deepfeiyu（自带 Cookie/Host/Origin 鉴权）；
// 面板只经同源 fetch POST JSON，全部请求收敛在 apply 内唯一的 call() 里，便于以后换传输层。
window.__ModuleLoader__.load({
  id: 'deepfeiyu-dsh',
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
.dsh-deepfeiyu-chats-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dsh-deepfeiyu-chat.dsh-deepfeiyu-is-checked { background: var(--dsw-alias-interactive-bg-hover, #2631480f); box-shadow: inset 0 0 0 1px var(--dsw-alias-state-business-primary, #4176e6); }
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
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-clickable { cursor: pointer; }
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-clickable:hover { border-color: var(--dsw-alias-state-business-primary, #4176e6); color: var(--dsw-alias-state-business-primary, #4176e6); }
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-warn { border-color: var(--dsw-alias-state-warn-primary, #f59e0b); color: var(--dsw-alias-state-warn-primary, #f59e0b); }
.dsh-deepfeiyu-tag.dsh-deepfeiyu-is-ok { border-color: var(--dsw-alias-state-success-primary, #22c55e); color: var(--dsw-alias-state-success-primary, #22c55e); }
.dsh-deepfeiyu-unread { display: inline-flex; flex: none; align-items: center; justify-content: center; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; background: var(--dsw-alias-state-error-primary, #ec1313); color: var(--dsw-alias-label-primary-foreground, #fff); font-size: 10px; line-height: 18px; }
.dsh-deepfeiyu-thread { display: flex; flex-direction: column; min-height: 0; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 12px; background: var(--dsw-alias-bg-layer-1, #fff); overflow: hidden; }
.dsh-deepfeiyu-thread-empty { display: flex; flex: 1; flex-direction: column; align-items: center; justify-content: center; gap: 4px; padding: 24px; text-align: center; }
.dsh-deepfeiyu-empty-title { margin: 0; font-size: 14px; font-weight: 500; }
.dsh-deepfeiyu-thread-head { display: flex; align-items: center; gap: 8px; padding: 9px 14px; border-bottom: 1px solid var(--dsw-alias-border-l1, #0000000a); }
.dsh-deepfeiyu-thread-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.dsh-deepfeiyu-thread-title { display: flex; align-items: center; gap: 8px; min-width: 0; flex-wrap: wrap; }
.dsh-deepfeiyu-thread-sub { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dsh-deepfeiyu-thread-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.dsh-deepfeiyu-feedback { font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
.dsh-deepfeiyu-feedback.dsh-deepfeiyu-is-error { color: var(--dsw-alias-state-error-primary, #ec1313); }
.dsh-deepfeiyu-feedback.dsh-deepfeiyu-is-ok { color: var(--dsw-alias-state-success-primary, #22c55e); }
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
.dsh-deepfeiyu-msg-image-link { display: inline-block; max-width: 100%; }
.dsh-deepfeiyu-msg-image { display: block; max-width: 260px; max-height: 260px; border-radius: 8px; object-fit: cover; background: var(--dsw-alias-bg-layer-2, #f5f6f7); }
.dsh-deepfeiyu-msg-emoji-wrap { display: inline-flex; flex-direction: column; gap: 4px; align-items: flex-start; }
.dsh-deepfeiyu-msg-emoji { max-width: 160px; max-height: 120px; object-fit: contain; }
.dsh-deepfeiyu-emoji-actions { display: inline-flex; gap: 6px; opacity: 0; transition: opacity .15s; }
.dsh-deepfeiyu-msg-emoji-wrap:hover .dsh-deepfeiyu-emoji-actions, .dsh-deepfeiyu-emoji-actions:focus-within { opacity: 1; }
.dsh-deepfeiyu-emoji-download { font-size: 12px; line-height: 20px; color: var(--dsw-alias-label-secondary, #61666b); text-decoration: none; }
.dsh-deepfeiyu-emoji-error { color: var(--dsw-alias-state-error-primary, #ec1313); font-size: 12px; line-height: 18px; overflow-wrap: anywhere; }
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
.dsh-deepfeiyu-risklist strong { color: var(--dsw-alias-label-primary, #0f1115); }
.dsh-deepfeiyu-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0 10px; }
.dsh-deepfeiyu-tab { appearance: none; margin: 0; padding: 5px 12px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 8px; background: var(--dsw-alias-button-elevated-fill, #fff); color: var(--dsw-alias-label-secondary, #61666b); font: inherit; font-size: 13px; line-height: 20px; cursor: pointer; }
.dsh-deepfeiyu-tab:hover:not([aria-selected="true"]) { background: var(--dsw-alias-interactive-bg-hover, #2631480f); }
.dsh-deepfeiyu-tab[aria-selected="true"] { border-color: transparent; background: var(--dsw-alias-button-primary-fill, #0f1115); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-tab:focus-visible { outline: 2px solid var(--dsw-alias-state-business-primary, #4176e6); outline-offset: 1px; }
.dsh-deepfeiyu-tabpanel { display: flex; flex-direction: column; gap: 12px; }
.dsh-deepfeiyu-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px 14px; }
.dsh-deepfeiyu-field { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.dsh-deepfeiyu-field > span { font-size: 12.5px; color: var(--dsw-alias-label-secondary, #61666b); }
.dsh-deepfeiyu-field input, .dsh-deepfeiyu-field textarea, .dsh-deepfeiyu-field select { box-sizing: border-box; width: 100%; padding: 5px 9px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 6px; background: var(--dsw-alias-bg-base, #fff); color: var(--dsw-alias-label-primary, #0f1115); font: inherit; font-size: 13px; line-height: 20px; }
.dsh-deepfeiyu-field > em { font-size: 11.5px; line-height: 16px; color: var(--dsw-alias-label-tertiary, #81858c); font-style: normal; }
.dsh-deepfeiyu-toggle-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--dsw-alias-border-l1, #0000000a); }
.dsh-deepfeiyu-toggle-row:last-child { border-bottom: 0; }
.dsh-deepfeiyu-toggle-text { min-width: 0; }
.dsh-deepfeiyu-toggle-text strong { display: block; font-size: 13px; font-weight: 500; color: var(--dsw-alias-label-primary, #0f1115); }
.dsh-deepfeiyu-toggle-text em { display: block; font-size: 11.5px; font-style: normal; color: var(--dsw-alias-label-tertiary, #81858c); overflow-wrap: anywhere; }
.dsh-deepfeiyu-form-actions { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
.dsh-deepfeiyu-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.dsh-deepfeiyu-chip { appearance: none; margin: 0; padding: 3px 10px; border: 1px solid var(--dsw-alias-border-l2, #0000001a); border-radius: 999px; background: var(--dsw-alias-button-elevated-fill, #fff); color: var(--dsw-alias-label-secondary, #61666b); font: inherit; font-size: 12px; line-height: 18px; cursor: pointer; }
.dsh-deepfeiyu-chip.on { border-color: transparent; background: var(--dsw-alias-button-primary-fill, #0f1115); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-chip.warn { border-color: var(--dsw-alias-state-warn-primary, #f59e0b); }
.dsh-deepfeiyu-btn-on { border-color: transparent; background: var(--dsw-alias-state-business-primary, #4176e6); color: var(--dsw-alias-label-primary-foreground, #fff); }
.dsh-deepfeiyu-risklist { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; line-height: 19px; color: var(--dsw-alias-label-secondary, #61666b); }
@media (max-width: 900px) {
  .dsh-deepfeiyu-body { grid-template-columns: 240px minmax(0, 1fr); }
  .dsh-deepfeiyu-root { padding: 16px 16px 12px; }
}
@media (max-height: 640px) {
  .dsh-deepfeiyu-root { gap: 8px; padding-top: 12px; }
  .dsh-deepfeiyu-title { font-size: 17px; line-height: 24px; }
}
`;

    // 设置页标签：P1 四个，P2 会插入「账号与通道」（规格 §5）。
    const SETTINGS_TABS = [
      ['model', '模型与人格'],
      ['auto', '自动回复'],
      ['deps', '依赖与融合'],
      ['update', '更新与边界'],
    ];

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

    /** 图片字节代理地址：同源 GET，令牌只在 Host 内使用。 */
    function imageUrl(uid, msgId) {
      return '/deepfeiyu/image?uid=' + encodeURIComponent(uid) + '&msg=' + encodeURIComponent(msgId);
    }

    /** 表情字节代理地址；download=1 时由控制台加 Content-Disposition。 */
    function emojiSrc(info) {
      return '/deepfeiyu/emoji?url=' + encodeURIComponent(info.url)
        + (info.md5 ? '&md5=' + encodeURIComponent(info.md5) : '');
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
          // 会话控制：AI 开关 / 恢复接管 / 批量操作共用一把互斥锁与一条行内反馈。
          const [chatBusy, setChatBusy] = React.useState(false);
          const [chatFeedback, setChatFeedback] = React.useState(null);
          const feedbackTimer = React.useRef(null);
          // 多选批量：selectMode 下点行只切换选中，不打开会话。
          const [selectMode, setSelectMode] = React.useState(false);
          const [selectedChats, setSelectedChats] = React.useState(() => new Set());
          // 富媒体回退：加载失败的图片/表情按 key 记账，轮询重渲染不再重复打请求。
          const [mediaFailed, setMediaFailed] = React.useState(() => new Set());
          // 表情收藏态：控制台运行时拉一次表情墙，收藏成功后本地追加。
          const [emojiFavs, setEmojiFavs] = React.useState(() => new Set());
          const [emojiFavError, setEmojiFavError] = React.useState('');

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

          // 行内反馈：错误留到下一次操作才清，成功 4s 后自动消失。
          function setChatFeedbackOnce(next) {
            setChatFeedback(next);
            if (feedbackTimer.current) window.clearTimeout(feedbackTimer.current);
            feedbackTimer.current = null;
            if (next && next.tone === 'ok') {
              feedbackTimer.current = window.setTimeout(() => {
                feedbackTimer.current = null;
                setChatFeedback((previous) => (previous && previous.text === next.text ? null : previous));
              }, 4000);
            }
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
              const list = Array.isArray(value && value.chats) ? value.chats : [];
              setChats(list);
              // 只有不带关键词的全量拉取才修剪：搜索结果是子集，
              // 拿子集修剪会把「当前搜索看不见但仍然有效」的选中项误删。
              if (!keywordValue) {
                setSelectedChats((previous) => {
                  if (!previous.size) return previous;
                  const alive = new Set(list.map((item) => item && item.uid).filter(Boolean));
                  const next = new Set([...previous].filter((uid) => alive.has(uid)));
                  return next.size === previous.size ? previous : next;
                });
              }
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
              if (feedbackTimer.current) {
                window.clearTimeout(feedbackTimer.current);
                feedbackTimer.current = null;
              }
            };
          }, []);

          // 控制台开始运行时拉账号/统计；停止时清掉过期数据，避免显示停机前的旧账号与会话。
          React.useEffect(() => {
            if (running && !wasRunning.current) {
              void loadAccounts();
              void loadStats();
              void loadEmojiFavs();
            } else if (!running && wasRunning.current) {
              setAccounts([]);
              setAccountsLoaded(false);
              setChats([]);
              setStats(null);
              setSelectedUid(null);
              setChat(null);
              setMessages([]);
              setSendError('');
              setChatFeedback(null);
              setSelectMode(false);
              setSelectedChats(new Set());
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
            setChatFeedback(null);
            setSelectMode(false);
            setSelectedChats(new Set());
            setMediaFailed(new Set());
            setEmojiFavError('');
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
              setChatFeedback(null);
              setSelectMode(false);
              setSelectedChats(new Set());
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

          // ------------------------------------------------------------ 会话控制
          async function toggleChatAi(enabled) {
            if (!selectedUid || chatBusy || !running) return;
            setChatBusy(true);
            setChatFeedback(null);
            try {
              await call('chat.ai', { uid: selectedUid, enabled });
              if (!mounted.current) return;
              // 线程头读的是 messages.list 返回的 chat，与列表是两份数据，必须同步合并。
              setChat((previous) => (previous && previous.uid === selectedUid ? { ...previous, ai_enabled: enabled } : previous));
              setChatFeedbackOnce({ scope: 'thread', tone: 'ok', text: enabled ? '已开启 AI 自动回复' : '已关闭 AI 自动回复' });
              await loadChats(keyword);
            } catch (error0) {
              // 规格 §5.5：行内反馈 + 顶部错误条双通道，不能只在一处显示。
              if (!mounted.current) return;
              setError(errorMessage(error0));
              setChatFeedbackOnce({ scope: 'thread', tone: 'error', text: errorMessage(error0) });
            } finally {
              if (mounted.current) setChatBusy(false);
            }
          }

          async function resumeChat(uid) {
            const target = typeof uid === 'string' && uid ? uid : selectedUid;
            if (!target || chatBusy || !running) return;
            const scope = target === selectedUid ? 'thread' : 'list';
            setChatBusy(true);
            setChatFeedback(null);
            try {
              await call('chat.resume', { uid: target });
              if (!mounted.current) return;
              if (target === selectedUid) {
                setChat((previous) => (previous && previous.uid === target ? { ...previous, paused: false } : previous));
              }
              setChatFeedbackOnce({ scope, tone: 'ok', text: '已恢复 AI 自动回复' });
              await loadChats(keyword);
            } catch (error0) {
              if (!mounted.current) return;
              setError(errorMessage(error0));
              setChatFeedbackOnce({ scope, tone: 'error', text: errorMessage(error0) });
            } finally {
              if (mounted.current) setChatBusy(false);
            }
          }

          function toggleSelectMode() {
            if (!running) return;
            if (selectMode) setSelectedChats(new Set());
            setSelectMode(!selectMode);
            setChatFeedback(null);
          }

          function handleChatRowClick(uid) {
            if (selectMode) {
              setSelectedChats((previous) => {
                const next = new Set(previous);
                if (next.has(uid)) next.delete(uid);
                else next.add(uid);
                return next;
              });
              return;
            }
            selectChat(uid);
          }

          function toggleSelectAll() {
            setSelectedChats((previous) => {
              const alive = chats.filter((item) => item && item.uid).map((item) => item.uid);
              if (!alive.length) return previous;
              const all = alive.every((uid) => previous.has(uid));
              const next = new Set(previous);
              for (const uid of alive) {
                if (all) next.delete(uid);
                else next.add(uid);
              }
              return next;
            });
          }

          async function bulkSetAi(enabled) {
            const uids = [...selectedChats];
            if (!uids.length || chatBusy || !running) return;
            setChatBusy(true);
            setChatFeedback(null);
            try {
              // 200 个会话逐个改库可能慢于默认 10s，放宽到 30s。
              const value = await call('chats.batchAi', { uids, enabled }, 30000);
              if (!mounted.current) return;
              const changed = value && typeof value.changed === 'number' ? value.changed : 0;
              setSelectMode(false);
              setSelectedChats(new Set());
              setChatFeedbackOnce({
                scope: 'list',
                tone: 'ok',
                text: '已把 ' + changed + ' 个会话设为' + (enabled ? ' AI 自动回复' : '人工接管'),
              });
              await loadChats(keyword);
            } catch (error0) {
              if (!mounted.current) return;
              setError(errorMessage(error0));
              setChatFeedbackOnce({ scope: 'list', tone: 'error', text: errorMessage(error0) });
            } finally {
              if (mounted.current) setChatBusy(false);
            }
          }

          function markMediaFailed(key) {
            if (!key) return;
            setMediaFailed((previous) => (previous.has(key) ? previous : new Set(previous).add(key)));
          }

          async function loadEmojiFavs() {
            try {
              const value = await call('emojis.list', { limit: 500 });
              if (!mounted.current || !Array.isArray(value)) return;
              setEmojiFavs(new Set(value.map((item) => item && item.url).filter(Boolean)));
            } catch {
              // 表情墙拉不到只影响「已收藏」标记：静默降级，不打扰消息流。
            }
          }

          async function favEmoji(info) {
            setEmojiFavError('');
            try {
              await call('emoji.fav', {
                url: info.url,
                md5: info.md5 || '',
                width: Number(info.width) || 0,
                height: Number(info.height) || 0,
              }, 15000);
              if (!mounted.current) return;
              setEmojiFavs((previous) => new Set(previous).add(info.url));
            } catch (error0) {
              if (mounted.current) setEmojiFavError(errorMessage(error0));
            }
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
            if (item.paused) tags.push(h('span', {
              key: 'paused',
              className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-warn dsh-deepfeiyu-is-clickable',
              role: 'button',
              tabIndex: 0,
              title: '人工接管中，点击恢复 AI 自动回复',
              onClick: (event) => {
                event.stopPropagation();
                void resumeChat(uid);
              },
              onKeyDown: (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                event.stopPropagation();
                void resumeChat(uid);
              },
            }, '人工'));
            if (item.ai_enabled) tags.push(h('span', { className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-ok', key: 'ai' }, 'AI'));
            return h('button', {
              key: uid,
              type: 'button',
              role: 'listitem',
              className: 'dsh-deepfeiyu-chat'
                + (uid === selectedUid ? ' dsh-deepfeiyu-is-active' : '')
                + (selectMode && selectedChats.has(uid) ? ' dsh-deepfeiyu-is-checked' : ''),
              title: item.name ? String(item.name) : uid,
              onClick: () => handleChatRowClick(uid),
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
              h('div', { className: 'dsh-deepfeiyu-chats-tools' },
                actionButton(selectMode ? '退出多选' : '多选', toggleSelectMode, {
                  disabled: !running,
                  title: '批量设置会话的 AI 开关',
                }),
                selectMode
                  ? h(React.Fragment, null,
                      actionButton(
                        chats.length > 0 && chats.every((item) => item && item.uid && selectedChats.has(item.uid))
                          ? '取消全选'
                          : '全选',
                        toggleSelectAll,
                        { disabled: !chats.length, title: '全选当前列表里的会话' },
                      ),
                      h('span', { className: 'dsh-deepfeiyu-hint' }, '已选 ' + selectedChats.size),
                      actionButton('设为人工', () => { void bulkSetAi(false); }, {
                        disabled: !running || chatBusy || !selectedChats.size,
                        title: '把选中会话的 AI 关闭',
                      }),
                      actionButton('设为 AI', () => { void bulkSetAi(true); }, {
                        disabled: !running || chatBusy || !selectedChats.size,
                        title: '把选中会话交给 AI 自动回复',
                      }))
                  : null,
                chatFeedback && chatFeedback.scope === 'list'
                  ? h('span', {
                      className: 'dsh-deepfeiyu-feedback dsh-deepfeiyu-is-' + (chatFeedback.tone === 'ok' ? 'ok' : 'error'),
                      role: chatFeedback.tone === 'ok' ? 'status' : 'alert',
                    }, chatFeedback.text)
                  : null),
              h('div', { className: 'dsh-deepfeiyu-chat-list', role: 'list' },
                rows.length ? rows : h('p', { className: 'dsh-deepfeiyu-hint dsh-deepfeiyu-list-hint' }, hint)));
          }

          function renderMessage(message, index) {
            const mine = Boolean(message.is_self);
            const typeText = message.msg_type && message.msg_type !== 'text' ? msgTypeText(message.msg_type) : '';
            const rawContent = String(message.content === null || message.content === undefined ? '' : message.content).trim();
            // 图片/语音等摘要本身就是「[图片]」这类占位，类型标签已经说明，不再重复一遍。
            const content = rawContent && typeText && rawContent === '[' + typeText + ']' ? '' : rawContent;
            const imageId = message.msg_type === 'image' && message.id ? String(message.id) : '';
            const emojiInfo = message.meta && message.meta.emoji && message.meta.emoji.url ? message.meta.emoji : null;
            const imageKey = imageId ? 'image:' + imageId : '';
            const emojiKey = emojiInfo ? 'emoji:' + emojiInfo.url : '';
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
              imageId
                ? (mediaFailed.has(imageKey)
                    ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, '[图片]')
                    : h('a', {
                        className: 'dsh-deepfeiyu-msg-image-link',
                        href: imageUrl(selectedUid, imageId),
                        target: '_blank',
                        rel: 'noopener',
                        title: '点开查看原图',
                      },
                      h('img', {
                        className: 'dsh-deepfeiyu-msg-image',
                        src: imageUrl(selectedUid, imageId),
                        alt: '图片',
                        loading: 'lazy',
                        onError: () => markMediaFailed(imageKey),
                      })))
                : emojiInfo
                  ? (mediaFailed.has(emojiKey)
                      ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, '[表情]')
                      : h('span', { className: 'dsh-deepfeiyu-msg-emoji-wrap' },
                          h('img', {
                            className: 'dsh-deepfeiyu-msg-emoji',
                            src: emojiSrc(emojiInfo),
                            alt: '表情',
                            loading: 'lazy',
                            onError: () => markMediaFailed(emojiKey),
                          }),
                          h('span', { className: 'dsh-deepfeiyu-emoji-actions' },
                            h('button', {
                              type: 'button',
                              className: 'dsh-deepfeiyu-btn' + (emojiFavs.has(emojiInfo.url) ? ' dsh-deepfeiyu-btn-on' : ''),
                              title: emojiFavs.has(emojiInfo.url) ? '已收藏' : '收藏到表情面板',
                              onClick: () => { void favEmoji(emojiInfo); },
                            }, emojiFavs.has(emojiInfo.url) ? '★已收藏' : '★收藏'),
                            h('a', {
                              className: 'dsh-deepfeiyu-emoji-download',
                              href: emojiSrc(emojiInfo) + '&download=1',
                              download: 'emoji.gif',
                              title: '下载',
                            }, '⬇')),
                          emojiFavError
                            ? h('span', { className: 'dsh-deepfeiyu-emoji-error', role: 'alert' }, emojiFavError)
                            : null))
                  : (content || typeText
                      ? h('span', { className: 'dsh-deepfeiyu-msg-text' }, content)
                      : h('span', { className: 'dsh-deepfeiyu-msg-text' }, '（空消息）'))));
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
            const aiState = !selectedChat
              ? ''
              : !selectedChat.ai_enabled
                ? 'AI 已关闭'
                : selectedChat.paused
                  ? '人工接管中'
                  : 'AI 自动回复';
            const kindLabel = selectedChat ? (selectedChat.is_group ? '群聊' : '私聊') : '';
            return h('section', { className: 'dsh-deepfeiyu-thread' },
              h('div', { className: 'dsh-deepfeiyu-thread-head' },
                h('div', { className: 'dsh-deepfeiyu-thread-main' },
                  h('div', { className: 'dsh-deepfeiyu-thread-title' },
                    h('span', { className: 'dsh-deepfeiyu-thread-name' }, selectedName || String(selectedUid)),
                    kindLabel ? h('span', { className: 'dsh-deepfeiyu-tag' }, kindLabel) : null,
                    selectedChat && selectedChat.paused
                      ? h('span', { className: 'dsh-deepfeiyu-tag dsh-deepfeiyu-is-warn' }, '人工接管中')
                      : null),
                  h('div', { className: 'dsh-deepfeiyu-thread-sub' },
                    aiState ? h('span', { className: 'dsh-deepfeiyu-hint' }, aiState) : null,
                    h('span', { className: 'dsh-deepfeiyu-hint' }, messages.length ? '最近 ' + messages.length + ' 条' : ''),
                    chatFeedback && chatFeedback.scope === 'thread'
                      ? h('span', {
                          className: 'dsh-deepfeiyu-feedback dsh-deepfeiyu-is-' + (chatFeedback.tone === 'ok' ? 'ok' : 'error'),
                          role: chatFeedback.tone === 'ok' ? 'status' : 'alert',
                        }, chatFeedback.text)
                      : null)),
                h('div', { className: 'dsh-deepfeiyu-thread-actions' },
                  selectedChat && selectedChat.paused
                    ? actionButton('恢复 AI', () => { void resumeChat(selectedUid); }, {
                        disabled: !running || chatBusy,
                        title: '恢复该会话的 AI 自动回复',
                      })
                    : null,
                  h('button', {
                    type: 'button',
                    role: 'switch',
                    'aria-checked': Boolean(selectedChat && selectedChat.ai_enabled),
                    className: 'dsh-deepfeiyu-btn' + (selectedChat && selectedChat.ai_enabled ? ' dsh-deepfeiyu-btn-on' : ''),
                    disabled: !running || chatBusy || !selectedChat,
                    title: '该会话是否由 AI 自动回复',
                    onClick: () => { void toggleChatAi(!(selectedChat && selectedChat.ai_enabled)); },
                  }, selectedChat && selectedChat.ai_enabled ? 'AI 开' : 'AI 关'))),
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
        // 设置表单控件：只做展示，状态一律留在各标签的 draft 里。
        function Field({ label, hint, children }) {
          return h('label', { className: 'dsh-deepfeiyu-field' },
            h('span', null, label),
            children,
            hint ? h('em', null, hint) : null);
        }

        function TextInput({ value, onChange, placeholder, type, min, max }) {
          return h('input', {
            type: type || 'text',
            value,
            placeholder,
            min,
            max,
            onChange: (event) => onChange(event.target.value),
          });
        }

        function TextArea({ value, onChange, rows, placeholder }) {
          return h('textarea', {
            rows: rows || 3,
            value,
            placeholder,
            onChange: (event) => onChange(event.target.value),
          });
        }

        function ToggleRow({ label, hint, checked, onChange }) {
          return h('div', { className: 'dsh-deepfeiyu-toggle-row' },
            h('div', { className: 'dsh-deepfeiyu-toggle-text' },
              h('strong', null, label),
              hint ? h('em', null, hint) : null),
            h('button', {
              type: 'button',
              role: 'switch',
              'aria-checked': Boolean(checked),
              className: 'dsh-deepfeiyu-btn' + (checked ? ' dsh-deepfeiyu-btn-on' : ''),
              onClick: () => onChange(!checked),
            }, checked ? '开' : '关'));
        }

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
          // 页内标签：P1 四个，随各任务加入；默认依赖与融合保持原习惯（规格 §7）。
          const [tab, setTab] = React.useState('deps');
          // 模型与人格标签：settings 是只读快照，draft 是表单草稿；
          // 草稿只在首次加载或保存回执时重建，4s 轮询不会覆盖正在编辑的内容。
          const [settings, setSettings] = React.useState(null);
          const [settingsError, setSettingsError] = React.useState('');
          const [settingsPending, setSettingsPending] = React.useState(false);
          const [settingsFlash, setSettingsFlash] = React.useState('');
          const [draft, setDraft] = React.useState(null);
          const [testResult, setTestResult] = React.useState('');
          const [testPending, setTestPending] = React.useState(false);
          const [presets, setPresets] = React.useState([]);
          const [presetName, setPresetName] = React.useState('');
          const [presetPending, setPresetPending] = React.useState(false);
          // 自动回复标签：独立草稿（与模型草稿互不覆盖），错误/保存中状态与模型标签共用
          // ——两者读写同一个 GET/PUT /api/settings，同一个错误态在两个标签里各显各的。
          const [autoDraft, setAutoDraft] = React.useState(null);
          const [autoPending, setAutoPending] = React.useState(false);
          // 更新标签：首次打开自动查一次，之后只手动（避免每 4s 打 Gitee）。
          const [update, setUpdate] = React.useState(null);
          const [updateError, setUpdateError] = React.useState('');
          const [updatePending, setUpdatePending] = React.useState(false);
          const [consoleBase, setConsoleBase] = React.useState('http://127.0.0.1:8765');
          const updateFetched = React.useRef(false);

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

          // ------------------------------------------------ 模型与人格
          function draftFromSettings(s) {
            const ai = s.ai || {};
            const mem = s.memory || {};
            const emb = mem.embedding || {};
            return {
              base_url: ai.base_url || '',
              api_key: '',   // 只写：掩码不回填到输入框，占位符展示 ai.api_key
              model: ai.model || '',
              temperature: String(ai.temperature ?? 0.7),
              vision_enabled: !!ai.vision_enabled,
              web_enabled: ai.web_enabled !== false,
              memory_enabled: ai.memory_enabled !== false,
              system_prompt: ai.system_prompt || '',
              persona_name: ai.persona_name || '',
              memory_profile_enabled: mem.profile_enabled !== false,
              memory_context_chars: String(mem.context_chars ?? 1200),
              memory_max_items: String(mem.max_items ?? 24),
              memory_compress_after: String(mem.compress_after_messages ?? 24),
              embedding_enabled: !!emb.enabled,
              embedding_base_url: emb.base_url || '',
              embedding_model: emb.model || '',
              embedding_key_env: emb.key_env || 'EMBEDDING_API_KEY',
            };
          }

          function settingsPatchFromDraft(d) {
            return {
              ai: {
                base_url: d.base_url.trim(),
                api_key: d.api_key.trim(),   // 空串 = 控制台不覆盖真值
                model: d.model.trim(),
                temperature: parseFloat(d.temperature) || 0.7,
                vision_enabled: d.vision_enabled,
                web_enabled: d.web_enabled,
                memory_enabled: d.memory_enabled,
                system_prompt: d.system_prompt.trim(),
                persona_name: d.persona_name.trim(),
              },
              memory: {
                profile_enabled: d.memory_profile_enabled,
                context_chars: parseInt(d.memory_context_chars, 10) || 1200,
                max_items: parseInt(d.memory_max_items, 10) || 24,
                compress_after_messages: parseInt(d.memory_compress_after, 10) || 24,
                embedding: {
                  enabled: d.embedding_enabled,
                  base_url: d.embedding_base_url.trim(),
                  model: d.embedding_model.trim(),
                  key_env: d.embedding_key_env.trim() || 'EMBEDDING_API_KEY',
                },
              },
            };
          }

          async function loadSettingsTab() {
            try {
              const value = await call('settings.get', {}, 15000);
              if (!mounted.current) return;
              setSettings(value);
              // 只在草稿为空（首次进入/保存回执后已由保存函数重建）时初始化，轮询不覆盖编辑
              setDraft((prev) => prev || draftFromSettings(value));
              setAutoDraft((prev) => prev || autoFromSettings(value));
              setSettingsError('');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            }
          }

          async function saveSettingsTab() {
            if (!draft || settingsPending) return;
            setSettingsPending(true);
            setSettingsError('');
            try {
              const value = await call('settings.put', { patch: settingsPatchFromDraft(draft) }, 15000);
              if (!mounted.current) return;
              const saved = value && typeof value === 'object' ? value.settings : null;
              if (saved) {
                setSettings(saved);
                setDraft(draftFromSettings(saved));
              }
              setSettingsFlash('已保存 ' + new Date().toLocaleTimeString());
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setSettingsPending(false);
            }
          }

          async function switchProfile(id) {
            if (!draft || !settings || settingsPending) return;
            const ai = settings.ai || {};
            if (!id || id === (ai.active_profile || '')) return;
            const base = draft.base_url.trim();
            const model = draft.model.trim();
            setSettingsPending(true);
            setSettingsError('');
            try {
              // 与控制台一致：未保存的地址/模型先落回当前档，再切档，避免手改内容被吞
              if ((base && base !== (ai.base_url || '')) || (model && model !== (ai.model || ''))) {
                const flush = {};
                if (base) flush.base_url = base;
                if (model) flush.model = model;
                await call('settings.put', { patch: { ai: flush } }, 15000);
              }
              const value = await call('settings.put', { patch: { ai: { active_profile: id } } }, 15000);
              if (!mounted.current) return;
              const saved = value && typeof value === 'object' ? value.settings : null;
              if (saved) {
                setSettings(saved);
                setDraft(draftFromSettings(saved));
              }
              setSettingsFlash('已切到「' + id + '」');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setSettingsPending(false);
            }
          }

          async function testAiConnection() {
            if (!draft || testPending) return;
            setTestPending(true);
            setTestResult('正在测试…');
            try {
              const value = await call('settings.testAi', {
                base_url: draft.base_url.trim(),
                api_key: draft.api_key.trim() || undefined,
                model: draft.model.trim(),
              }, 30000);
              if (!mounted.current) return;
              const latency = value && Number.isFinite(Number(value.latency_ms))
                ? Number(value.latency_ms) : 0;
              const reply = value && value.reply ? value.reply : 'ok';
              setTestResult('连接正常 · ' + latency + 'ms · ' + reply);
              // 控制台 test-ai 会先落盘再测试：刷新快照以更新「已配置」等标记
              await loadSettingsTab();
            } catch (error0) {
              if (mounted.current) setTestResult(errorMessage(error0));
            } finally {
              if (mounted.current) setTestPending(false);
            }
          }

          async function loadPresets() {
            try {
              const value = await call('personas.list', {}, 15000);
              if (!mounted.current) return;
              setPresets(value && Array.isArray(value.personas) ? value.personas : []);
            } catch {
              // 预设读不到只置空：该区显示 0 份，不影响其它字段编辑
              if (mounted.current) setPresets([]);
            }
          }

          function applyPreset(name) {
            const found = presets.find((item) => item.name === name);
            if (!found || !draft) return;
            setPresetName(name);
            setDraft((prev) => (prev
              ? { ...prev, system_prompt: found.system_prompt || '', persona_name: found.persona_name || '' }
              : prev));
            setSettingsFlash('已应用预设「' + name + '」，点「保存设置」生效');
          }

          async function savePresetAs() {
            const name = presetName.trim();
            if (!draft || presetPending) return;
            if (!name) {
              setSettingsError('先在预设名称里写一个名字');
              return;
            }
            const exists = presets.some((item) => item.name === name);
            if (exists && !window.confirm('覆盖已有预设「' + name + '」？')) return;
            setPresetPending(true);
            setSettingsError('');
            try {
              await call('personas.add', {
                name,
                persona_name: draft.persona_name.trim(),
                system_prompt: draft.system_prompt.trim(),
              }, 15000);
              await loadPresets();
              setSettingsFlash('已保存预设「' + name + '」');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setPresetPending(false);
            }
          }

          async function deletePreset(name) {
            if (!name || presetPending) return;
            if (!window.confirm('删除人格预设「' + name + '」？')) return;
            setPresetPending(true);
            setSettingsError('');
            try {
              await call('personas.delete', { name }, 15000);
              if (presetName === name) setPresetName('');
              await loadPresets();
              setSettingsFlash('已删除预设「' + name + '」');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setPresetPending(false);
            }
          }

          async function syncWikiPresets() {
            if (presetPending) return;
            setPresetPending(true);
            setSettingsError('');
            try {
              const value = await call('personas.syncWiki', {}, 60000);
              await loadPresets();
              const created = value && Number.isFinite(Number(value.created)) ? Number(value.created) : 0;
              const updated = value && Number.isFinite(Number(value.updated)) ? Number(value.updated) : 0;
              setSettingsFlash('图集预设同步：新建 ' + created + ' 份、更新 ' + updated + ' 份');
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setPresetPending(false);
            }
          }

          // ------------------------------------------------ 自动回复
          function splitLines(value) {
            return String(value || '')
              .split('\n')
              .map((item) => item.trim())
              .filter(Boolean);
          }

          function autoFromSettings(s) {
            const a = s.auto || {};
            const ai = s.ai || {};
            const c = s.cross || {};
            return {
              enabled: !!a.enabled,
              default_ai_on: !!a.default_ai_on,
              group_need_at: !!a.group_need_at,
              emoji_reply: a.emoji_reply !== false,
              pat_reply: a.pat_reply !== false,
              instant_reply: !!a.instant_reply,
              only_whitelist: !!a.only_whitelist,
              whitelist: (a.whitelist || []).join('\n'),
              blacklist: (a.blacklist || []).join('\n'),
              min_interval_sec: String(a.min_interval_sec ?? 3),
              daily_limit: String(a.daily_limit ?? 300),
              takeover_pause_min: String(a.takeover_pause_min ?? 10),
              prefix: a.prefix || '',
              reply_timeout_sec: String(a.reply_timeout_sec ?? 60),
              timeout_fallback: a.timeout_fallback || '',
              split_reply: !!a.split_reply,
              emoji_with_reply: ai.emoji_with_reply !== false,
              emoji_auto_collect: ai.emoji_auto_collect !== false,
              wiki_emoji_only: !!ai.wiki_emoji_only,
              chuanzi_mode: !!ai.chuanzi_mode,
              chuanzi_site_emoji: !!ai.chuanzi_site_emoji,
              cross_enabled: c.enabled !== false,
              cross_relay: c.relay !== false,
              cross_visit: c.visit !== false,
              cross_daily_limit: String(c.daily_limit ?? 30),
              cross_visit_gap_min: String(Math.round((c.visit_gap_sec ?? 600) / 60)),
            };
          }

          function autoPatchFromDraft(d) {
            return {
              auto: {
                enabled: d.enabled,
                default_ai_on: d.default_ai_on,
                group_need_at: d.group_need_at,
                emoji_reply: d.emoji_reply,
                pat_reply: d.pat_reply,
                instant_reply: d.instant_reply,
                only_whitelist: d.only_whitelist,
                whitelist: splitLines(d.whitelist),
                blacklist: splitLines(d.blacklist),
                min_interval_sec: parseInt(d.min_interval_sec, 10) || 0,
                daily_limit: parseInt(d.daily_limit, 10) || 0,
                takeover_pause_min: parseInt(d.takeover_pause_min, 10) || 0,
                prefix: d.prefix,
                reply_timeout_sec: parseFloat(d.reply_timeout_sec) || 0,
                timeout_fallback: d.timeout_fallback.trim(),
                split_reply: d.split_reply,
              },
              ai: {
                emoji_with_reply: d.emoji_with_reply,
                emoji_auto_collect: d.emoji_auto_collect,
                wiki_emoji_only: d.wiki_emoji_only,
                chuanzi_mode: d.chuanzi_mode,
                chuanzi_site_emoji: d.chuanzi_site_emoji,
              },
              cross: {
                enabled: d.cross_enabled,
                relay: d.cross_relay,
                visit: d.cross_visit,
                daily_limit: parseInt(d.cross_daily_limit, 10) || 0,
                visit_gap_sec: (parseInt(d.cross_visit_gap_min, 10) || 0) * 60,
              },
            };
          }

          async function saveAutoTab() {
            if (!autoDraft || autoPending) return;
            setAutoPending(true);
            setSettingsError('');
            try {
              const value = await call('settings.put', { patch: autoPatchFromDraft(autoDraft) }, 15000);
              if (!mounted.current) return;
              const saved = value && typeof value === 'object' ? value.settings : null;
              if (saved) {
                setSettings(saved);
                setAutoDraft(autoFromSettings(saved));
              }
              setSettingsFlash('已保存 ' + new Date().toLocaleTimeString());
            } catch (error0) {
              if (mounted.current) setSettingsError(errorMessage(error0));
            } finally {
              if (mounted.current) setAutoPending(false);
            }
          }

          // ------------------------------------------------ 更新与边界
          async function checkUpdateTab() {
            if (updatePending) return;
            setUpdatePending(true);
            setUpdateError('');
            try {
              const value = await call('update.check', {}, 30000);
              if (!mounted.current) return;
              setUpdate(value && typeof value === 'object' ? value : null);
            } catch (error0) {
              if (mounted.current) setUpdateError(errorMessage(error0));
            } finally {
              if (mounted.current) setUpdatePending(false);
            }
          }

          async function readConsoleBase() {
            try {
              const value = await call('state', {}, 5000);
              if (mounted.current && value && value.host) {
                setConsoleBase('http://' + value.host + ':' + (value.port || 8765));
              }
            } catch {
              // 面板快照读不到时保留默认 8765，协议入口仍可用
            }
          }

          function openExternal(url) {
            const sidebarRight = ctx.get('sidebarRight');
            if (sidebarRight && typeof sidebarRight.openTab === 'function') {
              sidebarRight.openTab('browser', { params: { url } });
              return;
            }
            window.open(url, '_blank', 'noopener');
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

          // 组件生命周期与日志定时器只在挂载/卸载时处理：
          // 拆出来是为了让标签切换的轮询 effect 可以重跑，而安装日志轮询不被打断。
          React.useEffect(() => {
            mounted.current = true;
            return () => {
              mounted.current = false;
              clearLogTimer();
            };
          }, []);

          // 数据轮询跟随当前标签：打开标签立即拉一次，之后每 4s；页面在后台时跳过。
          React.useEffect(() => {
            const tick = () => {
              if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return;
              if (tab === 'deps') {
                void loadDeps();
                void loadDshPlugins();
                void loadFusion();
              }
              if (tab === 'model') {
                void loadSettingsTab();
                void loadPresets();
              }
              if (tab === 'auto') void loadSettingsTab();
              if (tab === 'update' && !updateFetched.current) {
                updateFetched.current = true;
                void checkUpdateTab();
                void readConsoleBase();
              }
            };
            tick();
            const timer = window.setInterval(tick, POLL_MS);
            return () => window.clearInterval(timer);
          }, [tab]);

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

          function TabBar() {
            return h('div', {
              className: 'dsh-deepfeiyu-tabs',
              role: 'tablist',
              'aria-label': 'Deep肥鱼设置分组',
            },
            SETTINGS_TABS.map(([id, label]) => h('button', {
              key: id,
              type: 'button',
              role: 'tab',
              id: 'dsh-tab-' + id,
              'aria-controls': 'dsh-tabpanel-' + id,
              'aria-selected': tab === id,
              className: 'dsh-deepfeiyu-tab',
              onClick: () => setTab(id),
            }, label)));
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

          function renderUpdateTab() {
            const panel = { role: 'tabpanel', id: 'dsh-tabpanel-update', 'aria-labelledby': 'dsh-tab-update', className: 'dsh-deepfeiyu-tabpanel' };
            const r = update;
            let versionLine;
            if (updateError) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' }, updateError);
            } else if (!r) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-hint' }, '正在检查更新…');
            } else if (!r.git_repo) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-hint' },
                '当前 v' + (r.version || '—') + '（解压安装的副本不做在线比对）');
            } else if (r.error) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-failure', role: 'alert' },
                '检查更新失败：' + r.error);
            } else if (r.has_update) {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '发现新版本 ' + (r.remote_short || '')
                + '（当前 v' + (r.version || '未知') + ' · ' + (r.local_short || '')
                + '）：在项目目录运行 git pull 后重启服务。');
            } else {
              versionLine = h('p', { className: 'dsh-deepfeiyu-dep-note' },
                '当前 v' + (r.version || '—') + (r.local_short ? ' · ' + r.local_short : '')
                + '，已是最新版本');
            }
            return h('div', panel,
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '软件更新')),
                versionLine,
                h('div', { className: 'dsh-deepfeiyu-form-actions' },
                  r && r.repo_url
                    ? settingsButton('打开仓库', () => openExternal(String(r.repo_url)), {
                      title: '在 DSH 右侧栏打开仓库页面',
                    })
                    : null,
                  settingsButton(updatePending ? '检查中…' : '检查更新', () => { void checkUpdateTab(); }, {
                    disabled: updatePending,
                    title: '与 Gitee 仓库的 master 分支比对',
                  }))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '使用边界')),
                h('ul', { className: 'dsh-deepfeiyu-risklist' },
                  h('li', null, h('strong', null, '账号风险：'),
                    '个人微信自动化不符合《微信个人账号使用规范》，存在被限制或封禁的可能，建议用小号先验证。'),
                  h('li', null, h('strong', null, '版本限制：'),
                    '本地 Hook 仅支持微信 PC 3.9.x；4.x 收消息走数据库解密，发送走前台窗口自动化（会短暂占用前台）。'),
                  h('li', null, h('strong', null, '能力边界：'),
                    '朋友圈、支付、小程序、语音视频通话不在 Hook 能力范围内。'),
                  h('li', null, h('strong', null, '数据：'),
                    '所有消息与配置只存在本机 data/ 目录，不同账号的数据按账号隔离。'),
                  h('li', null, h('strong', null, '协议：'),
                    '使用本软件即表示同意 ',
                    settingsButton('《最终用户许可协议》', () => openExternal(consoleBase + '/eula'), {
                      title: '在 DSH 右侧栏打开控制台协议页',
                    }),
                    '；英文译本仅供参考，以中文版为准。'))));
          }

          function renderAutoTab() {
            const panel = { role: 'tabpanel', id: 'dsh-tabpanel-auto', 'aria-labelledby': 'dsh-tab-auto', className: 'dsh-deepfeiyu-tabpanel' };
            if (!autoDraft) {
              return h('div', panel,
                settingsError
                  ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                    h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                    settingsButton('重试', () => { void loadSettingsTab(); }, {}))
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取设置…'));
            }
            const set = (key, value) => setAutoDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
            return h('div', panel,
              settingsError
                ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                  h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                  settingsButton('关闭', () => setSettingsError(''), { title: '收起错误提示' }))
                : null,
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '自动回复策略')),
                h(ToggleRow, { label: '自动回复总开关', hint: '关着不调模型、不注入、不判断心情', checked: autoDraft.enabled, onChange: (v) => set('enabled', v) }),
                h(ToggleRow, { label: '新会话默认开 AI', checked: autoDraft.default_ai_on, onChange: (v) => set('default_ai_on', v) }),
                h(ToggleRow, { label: '群聊需 @ 才回', hint: '按账号配置的昵称检测', checked: autoDraft.group_need_at, onChange: (v) => set('group_need_at', v) }),
                h(ToggleRow, { label: '表情回复', checked: autoDraft.emoji_reply, onChange: (v) => set('emoji_reply', v) }),
                h(ToggleRow, { label: '拍一拍也回', checked: autoDraft.pat_reply, onChange: (v) => set('pat_reply', v) }),
                h(ToggleRow, { label: '仅白名单回复', checked: autoDraft.only_whitelist, onChange: (v) => set('only_whitelist', v) }),
                h(Field, { label: '白名单（每行一个）' },
                  h(TextArea, { value: autoDraft.whitelist, onChange: (v) => set('whitelist', v), rows: 3, placeholder: '张三\n工作群' })),
                h(Field, { label: '黑名单（每行一个）' },
                  h(TextArea, { value: autoDraft.blacklist, onChange: (v) => set('blacklist', v), rows: 3, placeholder: '永远不回的会话' })),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '最短回复间隔（秒）' },
                    h(TextInput, { type: 'number', value: autoDraft.min_interval_sec, onChange: (v) => set('min_interval_sec', v), min: 0 })),
                  h(Field, { label: '每日回复上限' },
                    h(TextInput, { type: 'number', value: autoDraft.daily_limit, onChange: (v) => set('daily_limit', v), min: 0 })),
                  h(Field, { label: '人工接管暂停（分钟）', hint: '你亲自回一句后 AI 退让的时长' },
                    h(TextInput, { type: 'number', value: autoDraft.takeover_pause_min, onChange: (v) => set('takeover_pause_min', v), min: 0 })),
                  h(Field, { label: '回复前缀（可空）' },
                    h(TextInput, { value: autoDraft.prefix, onChange: (v) => set('prefix', v), placeholder: '例如：[AI]' })),
                  h(Field, { label: '回复超时（秒，0 = 不限制）' },
                    h(TextInput, { type: 'number', value: autoDraft.reply_timeout_sec, onChange: (v) => set('reply_timeout_sec', v), min: 0 })),
                  h(Field, { label: '超时兜底话（可空）' },
                    h(TextInput, { value: autoDraft.timeout_fallback, onChange: (v) => set('timeout_fallback', v), placeholder: '稍等，我想想怎么回你' }))),
                h(ToggleRow, { label: '一条回复拆成多条发送', hint: 'AI 可用 --- 分隔拆成最多 3 条短消息', checked: autoDraft.split_reply, onChange: (v) => set('split_reply', v) })),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '表情包与串子')),
                h(ToggleRow, { label: '回复必带表情包', hint: '按场景从表情库自选', checked: autoDraft.emoji_with_reply, onChange: (v) => set('emoji_with_reply', v) }),
                h(ToggleRow, { label: '自动收集聊天表情', checked: autoDraft.emoji_auto_collect, onChange: (v) => set('emoji_auto_collect', v) }),
                h(ToggleRow, { label: '仅 AI 娘表情', hint: '只允许 tags 带图鉴角色名的表情', checked: autoDraft.wiki_emoji_only, onChange: (v) => set('wiki_emoji_only', v) }),
                h(ToggleRow, { label: '串子模式', hint: '回复像串子；只对准观点与梗', checked: autoDraft.chuanzi_mode, onChange: (v) => set('chuanzi_mode', v) }),
                h(ToggleRow, { label: '串子站表情', hint: '联网查台词库并入库截图，默认关', checked: autoDraft.chuanzi_site_emoji, onChange: (v) => set('chuanzi_site_emoji', v) })),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '跨对话沟通')),
                h(ToggleRow, { label: '跨对话总开关', hint: '转告/代发与主动串门', checked: autoDraft.cross_enabled, onChange: (v) => set('cross_enabled', v) }),
                h(ToggleRow, { label: '允许转告 / 代发', checked: autoDraft.cross_relay, onChange: (v) => set('cross_relay', v) }),
                h(ToggleRow, { label: '允许主动串门', checked: autoDraft.cross_visit, onChange: (v) => set('cross_visit', v) }),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '每日跨对话上限（0 = 不限）' },
                    h(TextInput, { type: 'number', value: autoDraft.cross_daily_limit, onChange: (v) => set('cross_daily_limit', v), min: 0 })),
                  h(Field, { label: '串门冷却（分钟）' },
                    h(TextInput, { type: 'number', value: autoDraft.cross_visit_gap_min, onChange: (v) => set('cross_visit_gap_min', v), min: 0 })))),
              h('div', { className: 'dsh-deepfeiyu-form-actions' },
                h('span', { className: 'dsh-deepfeiyu-hint' }, settingsFlash),
                settingsButton(autoPending ? '保存中…' : '保存设置', () => { void saveAutoTab(); }, {
                  primary: true,
                  disabled: autoPending || !autoDraft,
                  title: '只提交本页字段的窄补丁，模型与账号设置不受影响',
                })));
          }

          function renderModelTab() {
            const panel = { role: 'tabpanel', id: 'dsh-tabpanel-model', 'aria-labelledby': 'dsh-tab-model', className: 'dsh-deepfeiyu-tabpanel' };
            const ai = settings ? settings.ai || {} : null;
            const profiles = ai && Array.isArray(ai.profiles) ? ai.profiles : [];
            const activeId = ai ? ai.active_profile || '' : '';
            if (!draft) {
              return h('div', panel,
                settingsError
                  ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                    h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                    settingsButton('重试', () => { void loadSettingsTab(); }, {}))
                  : h('p', { className: 'dsh-deepfeiyu-hint' }, '正在读取设置…'));
            }
            const set = (key, value) => setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
            const active = profiles.find((item) => item.id === activeId) || null;
            const keyEnv = (active && active.key_env) || 'DEEPSEEK_API_KEY';
            return h('div', panel,
              settingsError
                ? h('div', { className: 'dsh-deepfeiyu-errorbar', role: 'alert' },
                  h('span', { className: 'dsh-deepfeiyu-errorbar-text' }, settingsError),
                  settingsButton('关闭', () => setSettingsError(''), { title: '收起错误提示' }))
                : null,
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '模型档'),
                  h('span', { className: 'dsh-deepfeiyu-badge' }, ai && ai.configured ? '已配置' : '未配置')),
                h('div', { className: 'dsh-deepfeiyu-chips' },
                  profiles.map((item) => h('button', {
                    key: item.id,
                    type: 'button',
                    className: 'dsh-deepfeiyu-chip'
                      + (item.id === activeId ? ' on' : '')
                      + (item.configured ? '' : ' warn'),
                    'aria-pressed': item.id === activeId,
                    disabled: settingsPending,
                    title: [item.description, item.base_url, item.model,
                      '密钥：.env 的 ' + (item.key_env || 'DEEPSEEK_API_KEY'),
                    ].filter(Boolean).join(' · '),
                    onClick: () => { void switchProfile(item.id); },
                  }, (item.name || item.id) + (item.configured ? '' : ' · 未填 Key')))),
                active
                  ? h('p', { className: 'dsh-deepfeiyu-hint' },
                    (active.name || active.id) + '：' + (active.description || '')
                    + '（密钥读 .env 的 ' + keyEnv + '）')
                  : null,
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: 'Base URL' },
                    h(TextInput, { value: draft.base_url, onChange: (v) => set('base_url', v), placeholder: 'https://api.deepseek.com' })),
                  h(Field, { label: 'API Key', hint: '只写入项目根 .env 的 ' + keyEnv + '，不进 data/config.json；留空不改' },
                    h(TextInput, { value: draft.api_key, onChange: (v) => set('api_key', v), placeholder: ai && ai.api_key ? ai.api_key : 'sk-…' })),
                  h(Field, { label: '模型名', hint: '这一档实际发给服务商的模型名，改了只覆盖这一档' },
                    h(TextInput, { value: draft.model, onChange: (v) => set('model', v), placeholder: 'deepseek-chat' })),
                  h(Field, { label: '温度' },
                    h(TextInput, { type: 'number', value: draft.temperature, onChange: (v) => set('temperature', v), min: 0, max: 2 }))),
                h('div', { className: 'dsh-deepfeiyu-form-actions' },
                  h('span', { className: 'dsh-deepfeiyu-hint' }, testResult),
                  settingsButton(testPending ? '测试中…' : '测试连接', () => { void testAiConnection(); }, {
                    disabled: testPending,
                    title: '用当前表单值先落盘，再发一次两字小请求测连通与延迟',
                  }))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '能力与记忆')),
                h(ToggleRow, { label: '启用视觉理解', hint: '多模态识图', checked: draft.vision_enabled, onChange: (v) => set('vision_enabled', v) }),
                h(ToggleRow, { label: '启用联网搜索', hint: 'AI 可搜索网页并抓取正文', checked: draft.web_enabled, onChange: (v) => set('web_enabled', v) }),
                h(ToggleRow, { label: '启用长期记忆', checked: draft.memory_enabled, onChange: (v) => set('memory_enabled', v) }),
                h(ToggleRow, { label: '注入人物档案', hint: '按重要度/相关度把档案带进提示词', checked: draft.memory_profile_enabled, onChange: (v) => set('memory_profile_enabled', v) }),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '记忆上下文长度（字符）' },
                    h(TextInput, { type: 'number', value: draft.memory_context_chars, onChange: (v) => set('memory_context_chars', v), min: 200 })),
                  h(Field, { label: '每层最大条数' },
                    h(TextInput, { type: 'number', value: draft.memory_max_items, onChange: (v) => set('memory_max_items', v), min: 1 })),
                  h(Field, { label: '压缩阈值（条）' },
                    h(TextInput, { type: 'number', value: draft.memory_compress_after, onChange: (v) => set('memory_compress_after', v), min: 1 })))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '语义检索（Embedding）')),
                h(ToggleRow, { label: '启用语义检索', hint: '需要一个 OpenAI 兼容的 /embeddings 接口', checked: draft.embedding_enabled, onChange: (v) => set('embedding_enabled', v) }),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: 'Embedding 接口地址' },
                    h(TextInput, { value: draft.embedding_base_url, onChange: (v) => set('embedding_base_url', v), placeholder: 'https://api.deepseek.com' })),
                  h(Field, { label: 'Embedding 模型' },
                    h(TextInput, { value: draft.embedding_model, onChange: (v) => set('embedding_model', v), placeholder: 'text-embedding-v3' })),
                  h(Field, { label: '密钥环境变量', hint: '只读环境变量，不写进 config.json' },
                    h(TextInput, { value: draft.embedding_key_env, onChange: (v) => set('embedding_key_env', v), placeholder: 'EMBEDDING_API_KEY' })))),
              h('div', { className: 'dsh-deepfeiyu-dep-terminal-card' },
                h('div', { className: 'dsh-deepfeiyu-dep-terminal-head' },
                  h('span', { className: 'dsh-deepfeiyu-dep-terminal-title' }, '人设与人格预设')),
                h(Field, { label: '系统提示词（全局默认）', hint: '各账号可在账号表单里单独覆盖（P2 标签）' },
                  h(TextArea, { value: draft.system_prompt, onChange: (v) => set('system_prompt', v), rows: 4, placeholder: '你是一个…' })),
                h(Field, { label: '人格名', hint: 'AI 的人格称呼，随人设一起注入提示词' },
                  h(TextInput, { value: draft.persona_name, onChange: (v) => set('persona_name', v), placeholder: 'Deep肥鱼' })),
                h('div', { className: 'dsh-deepfeiyu-grid' },
                  h(Field, { label: '选择已保存的人格' },
                    h('select', {
                      value: presetName,
                      onChange: (event) => setPresetName(event.target.value),
                    },
                    h('option', { value: '' }, '— 选择已保存的人格 —'),
                    presets.map((item) => h('option', { key: item.name, value: item.name },
                      (item.persona_name && item.persona_name !== item.name
                        ? item.name + '（' + item.persona_name + '）' : item.name)
                      + (item.source === 'ai-moe-wiki' ? '·图集' : ''))))),
                  h(Field, { label: '预设名称', hint: '选择或新建时用这个名字' },
                    h(TextInput, { value: presetName, onChange: setPresetName, placeholder: '例如：工作号人格' }))),
                h('div', { className: 'dsh-deepfeiyu-form-actions' },
                  h('span', { className: 'dsh-deepfeiyu-badge' }, presets.length + ' 份'),
                  settingsButton('应用所选', () => applyPreset(presetName.trim()), {
                    disabled: !presetName.trim() || presetPending,
                    title: '把预设填入上方提示词与人格名（需再点保存设置）',
                  }),
                  settingsButton(presetPending ? '处理中…' : '保存为预设', () => { void savePresetAs(); }, {
                    disabled: presetPending,
                    title: '把当前提示词与人格名存成预设；同名先确认覆盖',
                  }),
                  settingsButton('删除', () => { void deletePreset(presetName.trim()); }, {
                    disabled: presetPending || !presetName.trim(),
                  }),
                  settingsButton('从图集同步', () => { void syncWikiPresets(); }, {
                    disabled: presetPending,
                    title: '从 ai-moe-wiki 图集生成/更新预设，跳过用户自建同名',
                  }))),
              h('div', { className: 'dsh-deepfeiyu-form-actions' },
                h('span', { className: 'dsh-deepfeiyu-hint' }, settingsFlash),
                settingsButton(settingsPending ? '保存中…' : '保存设置', () => { void saveSettingsTab(); }, {
                  primary: true,
                  disabled: settingsPending || !draft,
                  title: '只提交本页字段的窄补丁，其它配置不受影响',
                })));
          }

          function renderDepsTab() {
            return h('div', {
              role: 'tabpanel',
              id: 'dsh-tabpanel-deps',
              'aria-labelledby': 'dsh-tab-deps',
              className: 'dsh-deepfeiyu-tabpanel',
            },
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

          return h('div', { className: 'dsh-deepfeiyu-settings' },
            h('style', null, STYLES),
            h('div', { className: 'dsh-deepfeiyu-heading' },
              h('h2', { className: 'dsh-deepfeiyu-settings-title' }, 'Deep肥鱼 · 设置'),
              h('p', { className: 'dsh-deepfeiyu-meta' }, '控制台的设置、依赖与融合；状态以控制台为准，保存后就地刷新。')),
            h(TabBar),
            tab === 'model' ? renderModelTab() : null,
            tab === 'auto' ? renderAutoTab() : null,
            tab === 'deps' ? renderDepsTab() : null,
            tab === 'update' ? renderUpdateTab() : null);
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
