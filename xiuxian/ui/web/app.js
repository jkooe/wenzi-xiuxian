/* ============================================================================
 * 文字修仙 · Web 组件逻辑（Component Logic）
 * ----------------------------------------------------------------------------
 * 组件化思路：渲染函数按组件拆分，统一走 UI 命名空间。
 *   1. UI.renderStatus(data) —— 状态卡（hero/主进度/三槽/属性格/标签/元信息）
 *   2. UI.renderActions(actions, mode) —— 行动按钮（桌面网格 / 底部条）
 *   3. UI.appendFlow(lines, time, cmdLine) —— 叙事文字流（分类着色）
 *   4. UI.toast(text, kind) —— 全局轻提示
 * 移动端复用：直接 import 本文件 + 三个资源文件，接口不变。
 * ========================================================================== */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const logBox = $("log");
  const cmd = $("cmd");
  const pct = (r) => Math.max(0, Math.min(1, r || 0)) * 100 + "%";

  /* ---------- 组件：数值格式化 ---------- */
  const fmt = (n) => (n === undefined || n === null) ? "—" : String(n);

  /* ---------- 组件：StatBar 三槽 ---------- */
  function statRow(lab, v, max, ratio, cls) {
    return '<div class="stat-row"><span class="stat-lab">' + lab + '</span>'
      + '<span class="stat-track ' + cls + '"><i style="width:' + pct(ratio) + '"></i></span>'
      + '<span class="stat-val">' + fmt(v) + '/' + fmt(max) + '</span></div>';
  }

  /* ---------- 组件：StatGrid 属性格 ---------- */
  function statCell(v, k) {
    return '<div class="stat-cell"><div class="v">' + fmt(v) + '</div><div class="k">' + k + '</div></div>';
  }

  /* ---------- 组件：TagList 标签组 ---------- */
  function tagList(d) {
    const tags = [];
    if (d.power) tags.push('<span class="tag gold">神通·' + d.power + '</span>');
    // 功法标签：带品阶，仙阶金色高亮
    for (const a of d.arts || []) {
      const gold = a.rank === "仙阶" ? " gold" : "";
      tags.push('<span class="tag' + gold + '">功法·' + a.name + (a.rank && a.rank !== "凡品" ? '（' + a.rank + '）' : '') + '</span>');
    }
    if (!d.arts || !d.arts.length) {
      if (d.art) tags.push('<span class="tag">功法·' + d.art + '</span>');
    }
    for (const e of d.equip || []) tags.push('<span class="tag">' + e + '</span>');
    if (d.quests) tags.push('<span class="tag">任务 ' + d.quests + '</span>');
    for (const b of d.buffs || []) tags.push('<span class="tag">' + b + '</span>');
    return tags.length ? '<div class="tags">' + tags.join("") + '</div>' : "";
  }

  /* ---------- 组件：StatusCard 状态卡 ---------- */
  function renderStatus(d) {
    if (!d) return;
    const box = $("status");
    // 身份徽章：飞升后显示「仙」金章，凡界显示「凡」灰章
    const badge = d.realm_is_immortal
      ? '<span class="realm-badge immortal">仙</span>'
      : '<span class="realm-badge mortal">凡</span>';
    const hero =
      '<div class="hero">'
      + '<div class="realm">' + badge + d.realm + '</div>'
      + '<div class="name">' + d.name + ' · ' + d.title + '　第 ' + d.day + ' 日 · ' + d.location + '</div>'
      + '<div class="next">下一境界：' + d.next + '</div>'
      + '</div>';
    const mainBar = '<div class="main-bar"><i style="width:' + pct(d.progress) + '"></i></div>';
    const bars =
      '<div class="stat-bars">'
      + statRow("气血", d.hp, d.max_hp, d.hp_ratio, "stat-hp")
      + statRow("灵力", d.mp, d.max_mp, d.mp_ratio, "stat-mp")
      + statRow("精力", d.stamina, "100", d.stamina_ratio, "stat-st")
      + '</div>';
    const grids =
      '<div class="stat-grid">'
      + statCell(d.atk, "攻击") + statCell(d.def, "防御") + statCell(d.speed, "身法") + statCell(d.spirit, "神识")
      + '</div>'
      + '<div class="stat-grid">'
      + statCell(d.comprehension, "悟性") + statCell(d.physique, "根骨") + statCell(d.luck, "气运") + statCell(d.poison, "丹毒")
      + '</div>';
    const meta =
      '<div class="meta">灵石 <b>' + d.stones + '</b>　寿元 <b>' + d.age + '/' + d.lifespan + '</b>'
      + '　灵气 <b>' + d.density + '倍</b>　修为 <b>' + d.exp + '/' + d.need + '</b></div>';
    box.innerHTML = hero + mainBar + bars + grids + meta + tagList(d);
  }

  /* ---------- 组件：ActionBar 行动按钮（mode: grid | bottom） ---------- */
  function renderActions(actions, mode) {
    const box = $(mode === "bottom" ? "actions-bottom" : "actions");
    if (!box) return;
    box.innerHTML = "";
    for (const a of actions || []) {
      const b = document.createElement("button");
      b.className = "btn" + (a.primary ? " primary" : "");
      b.textContent = a.label;
      // 「秘境目录」等目录动作：不直接发命令，改为弹窗
      if (a.open_catalog) {
        b.onclick = () => openCatalog();
        b.classList.add("catalog");
      } else {
        b.onclick = () => sendLine(a.cmd);
      }
      box.appendChild(b);
    }
  }

  /* ---------- 组件：叙事文字流（流式打字机输出） ---------- */
  const FLOW_KEYS = ["吐纳", "论道", "双修", "闭关", "历练", "探查", "心魔", "行走", "调息",
    "静卧", "运转", "采药", "开炉", "炼丹", "参悟", "打坐", "研习", "赶路", "游历"];
  const RESULT_KEYS = ["修为 +", "突破", "拾获", "灵石 +", "成功", "领悟", "顿悟", "升级", "进境"];
  const BAD_KEYS = ["失败", "枯竭", "不足", "无法", "未能", "溃败", "损耗"];
  function classify(line) {
    if (RESULT_KEYS.some(k => line.includes(k))) return "result";
    if (BAD_KEYS.some(k => line.includes(k))) return "bad";
    if (FLOW_KEYS.some(k => line.includes(k))) return "flow";
    return "txt";
  }

  /* 流式输出配置（移动端可调） */
  const STREAM = {
    enabled: true,          // 流式打字机开关
    charMs: 15,             // 每字间隔（毫秒）
    fastThreshold: 6,       // 单批行数超过此值 → 直接全文渲染（防卡顿）
  };
  const _q = [];            // 待流式渲染的日志行队列
  let _busy = false;        // 是否正在打字

  function createLine(cls, time, cmdLine) {
    const d = document.createElement("div");
    d.className = "tline " + cls;
    d.innerHTML = '<span class="ts">' + (time || "") + '</span>'
      + '<span class="cmd">' + (cmdLine || "") + '</span><span class="txt"></span>';
    logBox.appendChild(d);
    return d;
  }
  function scrollToBottom() { logBox.scrollTop = logBox.scrollHeight; }

  /* 逐字打字（可被打断：点击日志区立即完成） */
  function typewrite(el, text, done) {
    const txt = el.querySelector(".txt");
    if (!STREAM.enabled || text.length > 160) {   // 超长行直接呈现
      txt.textContent = text;
      scrollToBottom();
      done();
      return;
    }
    let i = 0;
    const timer = setInterval(() => {
      if (el._skip) {                              // 用户点击跳过
        clearInterval(timer);
        txt.textContent = text;
        scrollToBottom();
        done();
        return;
      }
      i += 1;
      txt.textContent = text.slice(0, i);
      scrollToBottom();
      if (i >= text.length) {
        clearInterval(timer);
        done();
      }
    }, STREAM.charMs);
  }

  function pump() {
    if (_busy) return;
    const task = _q.shift();
    if (!task) return;
    _busy = true;
    const el = createLine(task.cls, task.time, task.cmd);
    typewrite(el, task.text, () => { _busy = false; pump(); });
  }

  function appendFlow(lines, time, cmdLine) {
    if (!lines || !lines.length) return;
    const tasks = [];
    if (cmdLine) tasks.push({ cls: "sys", time: time || "", cmd: cmdLine, text: "" });
    for (const ln of lines) tasks.push({ cls: classify(ln), time: time || "", cmd: "", text: ln });
    // 防卡顿：批量日志（闭关/战斗数十行）或已有排队 → 直接全文渲染
    if (tasks.length > STREAM.fastThreshold || _q.length > 0 || _busy) {
      for (const t of tasks) {
        const el = createLine(t.cls, t.time, t.cmd);
        el.querySelector(".txt").textContent = t.text;
      }
      scrollToBottom();
      return;
    }
    _q.push(...tasks);
    pump();
  }

  /* 点击日志区：跳过当前行打字，立即完成 */
  logBox.addEventListener("click", () => {
    for (const el of logBox.querySelectorAll(".tline")) el._skip = true;
  });

  /* ---------- 组件：Modal 模态框 ---------- */
  const modalOverlay = $("modal-overlay");
  const modalTitle = $("modal-title");
  const modalBody = $("modal-body");
  const modalFooter = $("modal-footer");
  const modalClose = $("modal-close");
  const modalCancel = $("modal-cancel");
  const modalConfirm = $("modal-confirm");

  let onModalConfirm = null;

  function openModal(title, bodyHTML, onConfirmFn) {
    modalTitle.textContent = title;
    modalBody.innerHTML = bodyHTML;
    onModalConfirm = onConfirmFn || (() => closeModal());
    modalOverlay.classList.add("show");
  }

  function closeModal() {
    modalOverlay.classList.remove("show");
    onModalConfirm = null;
  }

  modalClose.onclick = closeModal;
  modalCancel.onclick = closeModal;
  modalConfirm.onclick = () => {
    if (onModalConfirm) onModalConfirm();
  };
  // 点击遮罩关闭
  modalOverlay.onclick = e => {
    if (e.target === modalOverlay) closeModal();
  };

  /* ---------- 组件：秘境目录弹窗 ---------- */
  async function openCatalog() {
    try {
      const r = await fetch("/api/catalog");
      const d = await r.json();
      const items = (d.catalog || []).map(c => {
        const enterable = c.state === "可入";
        let cls = c.state.indexOf("进行中") === 0
          ? "catalog-item in" : (enterable ? "catalog-item" : "catalog-item locked");
        if (enterable) cls += c.is_immortal ? " immortal" : "";
        const badge = c.is_immortal ? '<span class="catalog-badge">仙</span>' : "";
        const depth = c.min_realm + " · " + c.depth + " 层 · 冷却 " + c.cooldown + " 日";
        const reward = c.reward ? '<div class="catalog-reward">通关：' + c.reward + '</div>' : "";
        const stateHtml = !enterable
          ? '<span class="catalog-state locked">' + c.state + '</span>'
          : '<span class="catalog-state">可入 · ' + (c.stamina || 20) + ' 精力</span>';
        return '<div class="' + cls + '" data-id="' + c.id + '" data-enterable="' + (enterable ? 1 : 0) + '">'
          + '<div class="catalog-head">' + badge + '<b>' + c.name + '</b>' + stateHtml + '</div>'
          + '<div class="catalog-meta">' + depth + '</div>'
          + '<div class="catalog-desc">' + c.desc + '</div>'
          + reward
          + '</div>';
      }).join("");
      const body = '<div class="catalog-list">' + items + '</div>'
        + '<p class="catalog-hint">点击「可入」秘境直接进入 · 通关后按冷却日数计闭息</p>';
      openModal("秘境目录", body, () => closeModal());
      // 进入动作：点击可入的秘境卡片即 sendLine 进入
      document.querySelectorAll(".catalog-list .catalog-item").forEach(el => {
        if (el.dataset.enterable === "1") {
          el.classList.add("tap");
          el.onclick = () => { closeModal(); sendLine("dungeon enter " + el.dataset.id); };
        }
      });
    } catch (e) {
      toast("秘境目录加载失败", "error");
    }
  }

  /* ---------- 组件：Toast 全局轻提示 ---------- */
  let toastTimer = null;
  function toast(text, kind) {
    let el = $("toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.className = "toast" + (kind === "error" ? " error" : "");
    el.textContent = text;
    // 触发重排以重启动画
    void el.offsetWidth;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  /* ---------- 数据获取 ---------- */
  async function status() {
    try {
      const r = await fetch("/api/status");
      const d = await r.json();
      renderStatus(d.data);
      renderActions(d.actions || [], "grid");
      renderActions(d.actions || [], "bottom");
    } catch (e) { /* 静默：30s 重试 */ }
  }

  async function sendLine(line) {
    try {
      const r = await fetch("/api/command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line })
      });
      const d = await r.json();
      if (d.error) {
        appendFlow(["【出错】" + d.error], "", "");
        toast(d.error, "error");
      } else {
        appendFlow(d.logs || [], d.time || "", line.trim() ? "> " + line : "");
      }
      await status();
    } catch (e) {
      appendFlow(["（连接中断）"], "", "");
      toast("连接中断，请确认服务仍在运行", "error");
    }
  }

  function send() {
    const line = cmd.value;
    cmd.value = "";
    sendLine(line || "cultivate 4");
  }

  /* ---------- 事件绑定 ---------- */
  $("send").onclick = send;
  cmd.addEventListener("keydown", e => { if (e.key === "Enter") send(); });
  setInterval(status, 30000);
  status();

  /* 导出组件 API（供移动端复用） */
  window.UI = {
    renderStatus,
    renderActions,
    appendFlow,
    toast,
    classify,
    sendLine,
    openCatalog,
    stream: STREAM,          // 流式输出配置（enabled/charMs/fastThreshold 可调）
    openModal,               // 模态框：openModal(title, bodyHTML, onConfirmFn)
    closeModal,
  };
})();
