/* 校园生活 AI 助手 —— 前端逻辑（原生 JS，无框架、无外链） */
const $ = (s) => document.querySelector(s);
const state = {
  tab: 'schedule',
  user: null,
  schedule: null,
  meals: null,
  money: null,
  students: [],
  chat: [],
  quick: { questions: [], slots: [] },
  taste: '', canteen: '', budget: '',
  editingCourse: null,
  editingExpense: null,
  editingStudent: null,
};

/* ---------------------------------------------------------------- 工具 */
async function api(url, options) {
  const res = await fetch(url, options);
  if (res.status === 401 && !url.includes('/api/me')) { location.href = '/'; return null; }
  try { return await res.json(); } catch (e) { return null; }
}
function postJSON(url, body) {
  return api(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 1800);
}
function money(n) { return '¥' + Number(n).toFixed(2); }
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function nowHM() {
  const d = new Date();
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}
const CATS = ['早餐', '午餐', '晚餐', '夜宵', '饮品', '购物', '交通', '学习', '娱乐', '其他'];
let CANTEEN_LIST = ['大榕树餐厅（南区）', '北区第一餐厅', '北区第二餐厅', '西区小吃街', '校园超市'];
let PLACES = ['大榕树餐厅（南区）', '北区第一餐厅', '北区第二餐厅', '西区小吃街',
  '校园超市', '其他'];
const DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

/* ================================================================ 课表页 */
async function loadSchedule() {
  state.schedule = await api('/api/schedule');
  renderSchedule();
}

function courseForm(c) {
  // c 为空表示新增；否则为编辑
  const editing = !!c;
  return `
    <div class="form">
      ${editing ? `<div class="edit-head">修改安排<span class="badge-warn">编辑中</span></div>` : ''}
      <input id="cName" value="${esc(c ? c.name : '')}" placeholder="课程名称，如 网络安全实验">
      <div class="grid2">
        <select id="cDay">${DAYS.map((x) => `<option ${c && c.day === x ? 'selected' : ''}>${x}</option>`).join('')}</select>
        <input id="cPlace" value="${esc(c ? c.place : '')}" placeholder="上课地点 / 考场">
      </div>
      <div class="grid2">
        <input id="cStart" type="number" min="1" max="10" value="${c ? c.start_p : 1}" placeholder="开始节次">
        <input id="cEnd" type="number" min="1" max="10" value="${c ? c.end_p : 2}" placeholder="结束节次">
      </div>
      <div class="grid2">
        <input id="cTeacher" value="${esc(c ? c.teacher : '')}" placeholder="教师">
        <input id="cWeeks" value="${esc(c ? c.weeks : '')}" placeholder="周次，如 1-16 周 / 单周 1-16 周">
      </div>
      <div class="form-actions">
        <button class="primary" id="saveCourse">${editing ? '保存修改' : '保存'}</button>
        ${editing ? '<button class="ghost" id="cancelCourse">取消</button>' : ''}
      </div>
    </div>`;
}

function bindCourseForm() {
  $('#saveCourse').onclick = async () => {
    const body = {
      name: $('#cName').value.trim(), day: $('#cDay').value,
      place: $('#cPlace').value.trim(), teacher: $('#cTeacher').value.trim(),
      weeks: $('#cWeeks').value.trim(), kind: '自主添加',
      start_p: $('#cStart').value, end_p: $('#cEnd').value,
    };
    const url = state.editingCourse ? '/api/course/update' : '/api/course';
    if (state.editingCourse) body.id = state.editingCourse;
    const r = await postJSON(url, body);
    if (r && r.ok) {
      toast(r.message); state.editingCourse = null;
      state.schedule = r; renderSchedule();
    } else if (r) toast(r.error || '保存失败');
  };
  const cancel = $('#cancelCourse');
  if (cancel) cancel.onclick = () => { state.editingCourse = null; renderSchedule(); };
}

async function deleteCourse(id, name) {
  if (!confirm(`确定删除「${name}」这条安排吗？`)) return;
  const r = await postJSON('/api/course/delete', { id });
  if (r && r.ok) { toast(r.message); state.schedule = r; renderSchedule(); }
  else if (r) toast(r.error || '删除失败');
}

function renderSchedule() {
  const d = state.schedule;
  if (!d) return;
  const next = d.next;
  const today = d.today;
  const courses = d.today_courses || [];
  const week = d.week_no;

  const hero = next ? `
    <div class="hero">
      <div class="hero-label">${d.status}</div>
      <div class="hero-title">${esc(next.name)}</div>
      <div class="hero-meta">
        <span>⏰ ${next.begin}–${next.end}（${next.periods}）</span>
        <span>🚩 ${esc(next.place)}</span>
        <span>👤 ${esc(next.teacher)}</span>
      </div>
      <div class="hero-kind">${esc(next.kind || '考核')}${next.weeks ? ' · ' + esc(next.weeks) : ''}</div>
    </div>` : `
    <div class="hero calm">
      <div class="hero-label">第 ${week} 教学周 · ${today}</div>
      <div class="hero-title">今天没有考核安排</div>
      <div class="hero-meta"><span>可以安排复习、竞赛或运动 💪</span></div>
    </div>`;

  const list = courses.length ? courses.map((c) => `
    <div class="row-card">
      <div class="time-col"><b>${c.start_p}-${c.end_p}</b><span>节</span></div>
      <div class="row-main">
        <div class="row-title">${esc(c.name)}</div>
        <div class="row-sub">🚩 ${esc(c.place)} · 👤 ${esc(c.teacher)}${c.weeks ? ' · ' + esc(c.weeks) : ''}</div>
      </div>
      <div class="row-acts">
        <button class="mini" data-edit-course="${c.id}">修改</button>
        <button class="mini del" data-del-course="${c.id}" data-name="${esc(c.name)}">删除</button>
      </div>
    </div>`).join('') : '<div class="empty">今天没有安排，好好休息～</div>';

  const dedupe = (items) => {
    const seen = new Set();
    return items.filter((c) => {
      const k = c.name + c.place;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  };
  const slotBox = (c) => `
    <div class="wk-item" title="${esc(c.name)}｜${c.begin}-${c.end}｜${esc(c.place)}｜${esc(c.weeks || '每周')}">
      <b>${esc(c.name)}</b>
      <span>${c.start_p}-${c.end_p} 节</span>
      <span class="wk-place">${c.weeks ? esc(c.weeks.replace(' 周', '周')) : '每周'}</span>
    </div>`;

  const grid = ['周一', '周二', '周三', '周四', '周五'].map((day) => {
    const items = dedupe(d.week_map[day] || []);
    return `<div class="wk-col">
      <div class="wk-day ${day === today ? 'today' : ''}">${day}</div>
      ${items.length ? items.map(slotBox).join('') : '<div class="wk-free">—</div>'}
    </div>`;
  }).join('');

  const weekend = dedupe((d.week_map['周六'] || []).concat(d.week_map['周日'] || []));

  const up = d.upcoming;
  const upText = up ? `下一场：${up.day} ${up.begin}–${up.end} ${up.name}（🚩 ${up.place}，${up.in_days === 0 ? '就在今天' : up.in_days + ' 天后'}）` : '本周暂无后续安排';

  const allRows = (d.all_courses || []).map((c) => `
    <div class="row-card">
      <div class="time-col"><b>${esc(c.day)}</b><span>${c.start_p}-${c.end_p} 节</span></div>
      <div class="row-main">
        <div class="row-title">${esc(c.name)}</div>
        <div class="row-sub">🚩 ${esc(c.place)} · 👤 ${esc(c.teacher)}</div>
      </div>
      <div class="row-acts">
        <span class="pill">${esc(c.weeks || '每周')}</span>
        <button class="mini" data-edit-course="${c.id}">修改</button>
        <button class="mini del" data-del-course="${c.id}" data-name="${esc(c.name)}">删除</button>
      </div>
    </div>`).join('');

  $('#view').innerHTML = `
    <div class="info-strip">
      <span>🏫 ${esc(d.student.college)} · ${esc(d.student.major)}</span>
      <span>👥 ${esc(d.student.class_name)}（${d.student.class_size} 人）</span>
      <span>📖 第 ${week} 教学周 · 共 ${d.course_count} 门 / ${d.total_credit} 学分</span>
    </div>
    ${hero}
    <div class="section-title">今日考核 <span class="count">${courses.length} 场</span></div>
    <div class="list">${list}</div>
    <div class="upcoming-note">⏭ ${esc(upText)}</div>
    <div class="section-title">本周安排 <span class="count">第 ${week} 教学周 · 已过滤单双周</span></div>
    <div class="week">${grid}</div>
    <p class="hint">格子内显示节次与生效周次；考场与教师见下方「全部考核安排」。</p>
    ${weekend.length ? `<div class="weekend">周末：${weekend.map((c) =>
      esc(c.name) + '（' + c.day + ' ' + c.start_p + '-' + c.end_p + ' 节）').join('、')}</div>` : ''}
    <div class="section-title">全部考核安排 <span class="count">${(d.all_courses || []).length} 条 · 可修改/删除</span></div>
    <div class="list">${allRows}</div>
    <div class="row-actions">
      <button class="ghost" id="clearCourses">清空全部安排</button>
    </div>
    <details class="add" ${state.editingCourse ? 'open' : ''}>
      <summary>${state.editingCourse ? '✎ 正在修改一条安排' : '＋ 添加一门课 / 一场考核'}</summary>
      ${courseForm(state.editingCourse ? (d.all_courses || []).find((x) => x.id === state.editingCourse) : null)}
    </details>`;

  document.querySelectorAll('[data-del-course]').forEach((b) =>
    b.onclick = () => deleteCourse(b.dataset.delCourse, b.dataset.name));
  document.querySelectorAll('[data-edit-course]').forEach((b) =>
    b.onclick = () => {
      state.editingCourse = Number(b.dataset.editCourse);
      renderSchedule();
      const box = document.querySelector('details.add');
      if (box) { box.open = true; box.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
    });
  $('#clearCourses').onclick = async () => {
    if (!confirm('确定清空全部考核安排吗？（同学名单与记账不受影响）')) return;
    const r = await postJSON('/api/course/clear', {});
    if (r && r.ok) { toast(r.message); state.schedule = r; renderSchedule(); }
  };
  bindCourseForm();
}

/* ================================================================ 干饭页 */
async function loadMeals() {
  const qs = new URLSearchParams();
  if (state.taste) qs.set('taste', state.taste);
  if (state.canteen) qs.set('canteen', state.canteen);
  if (state.budget) qs.set('budget', state.budget);
  state.meals = await api('/api/meals?' + qs.toString());
  renderMeals();
}

function renderMeals() {
  const d = state.meals;
  if (!d) return;
  const tastes = ['', '咸香', '辣', '清淡', '低脂', '汤面'];
  const cards = d.list.map((m) => `
    <div class="dish rank-${m.rank}">
      <div class="dish-head">
        <div class="badge">TOP ${m.rank}</div>
        <div class="dish-name">${esc(m.name)}</div>
        <div class="dish-score">${m.score} 分</div>
      </div>
      <div class="dish-meta">
        <span>🍽 ${esc(m.canteen)} · ${esc(m.window)}</span>
        <span>⏱ 约 ${m.wait} 分钟</span>
        <span>⭐ ${m.rating}</span>
      </div>
      <div class="dish-price">${money(m.price)}
        <span class="tags">${m.tags.map((t) => `<i>${esc(t)}</i>`).join('')}</span>
      </div>
      <div class="dish-reason">推荐理由：${esc(m.reason)}</div>
    </div>`).join('');

  $('#view').innerHTML = `
    <div class="hero meal">
      <div class="hero-label">当前时段 · ${esc(d.slot)}</div>
      <div class="hero-title">${esc(d.slot)}时间 · 共 ${d.all_count} 道菜参与推荐</div>
      ${d.note ? `<div class="hero-meta"><span>💡 ${esc(d.note)}</span></div>` : ''}
      <div class="hero-meta"><span>按「时段匹配 + 口味 + 预算 + 排队时间」实时打分排序</span></div>
    </div>
    <div class="filters">
      <div class="filter-row">
        <span class="fl">口味</span>
        ${tastes.map((t) => `<button class="chip ${state.taste === t ? 'on' : ''}"
           data-taste="${t}">${t || '不限'}</button>`).join('')}
      </div>
      <div class="filter-row">
        <span class="fl">食堂</span>
        ${[''].concat(d.canteens).map((c) => `<button class="chip ${state.canteen === c ? 'on' : ''}"
           data-canteen="${esc(c)}">${c || '不限'}</button>`).join('')}
      </div>
      <div class="filter-row">
        <span class="fl">单餐预算</span>
        ${['', '10', '15', '20'].map((b) => `<button class="chip ${String(state.budget) === b ? 'on' : ''}"
           data-budget="${b}">${b ? '¥' + b : '不限'}</button>`).join('')}
      </div>
    </div>
    <div class="cards">${cards}</div>
    <p class="hint">推荐结果由本地算法实时计算，不依赖网络；口味偏好会保存到你的账号。</p>`;

  document.querySelectorAll('[data-taste]').forEach((b) => b.onclick = async () => {
    state.taste = b.dataset.taste; await savePrefs(); loadMeals();
  });
  document.querySelectorAll('[data-canteen]').forEach((b) => b.onclick = () => {
    state.canteen = b.dataset.canteen; loadMeals();
  });
  document.querySelectorAll('[data-budget]').forEach((b) => b.onclick = () => {
    state.budget = b.dataset.budget; loadMeals();
  });
}

async function savePrefs() {
  await postJSON('/api/prefs', {
    taste: state.taste, budget_meal: state.budget || 15,
    canteen: state.canteen, avoid_wait: 12,
  });
}

/* ================================================================ 记账页 */
async function loadMoney() {
  state.money = await api('/api/expenses');
  renderMoney();
}

function bars(items, key) {
  if (!items.length) return '<div class="empty">暂无数据</div>';
  const max = Math.max(...items.map((i) => i.value));
  return items.map((i) => `
    <div class="bar-row">
      <span class="bar-label">${esc(i[key])}</span>
      <span class="bar-track"><i style="width:${Math.max(3, i.value / max * 100)}%"></i></span>
      <span class="bar-value">${money(i.value)}</span>
    </div>`).join('');
}

function renderMoney() {
  const d = state.money;
  if (!d) return;
  const days = d.by_day.slice(-14);
  const maxDay = Math.max(...days.map((x) => x.value), 1);
  const cols = days.map((x) => `
    <div class="col" title="${x.date} ${money(x.value)}">
      <i style="height:${Math.max(4, x.value / maxDay * 100)}%"
         class="${x.value > d.budget_day ? 'over' : ''}"></i>
      <span>${x.date.slice(8)}</span>
    </div>`).join('');

  const editing = state.editingExpense;
  const target = editing ? d.items.find((i) => i.id === editing) : null;

  const recent = d.items.slice(0, 10).map((i) => `
    <div class="row-card">
      <div class="time-col"><b>${esc(i.category)}</b><span>${i.ts.slice(5, 10)}</span></div>
      <div class="row-main">
        <div class="row-title">${esc(i.note || i.category)}</div>
        <div class="row-sub">${esc(i.place || '—')} · ${i.ts.slice(11, 16)}</div>
      </div>
      <div class="amount-col">${money(i.amount)}</div>
      <div class="row-acts">
        <button class="mini" data-edit-exp="${i.id}">改</button>
        <button class="mini del" data-del-exp="${i.id}" data-amount="${i.amount}">删</button>
      </div>
    </div>`).join('');

  const form = target ? `
    <div class="form card-form">
      <div class="edit-head">修改这笔记录<span class="badge-warn">编辑中</span></div>
      <div class="grid2">
        <input id="eAmount" type="number" step="0.01" value="${target.amount}" placeholder="金额">
        <select id="eCat">${CATS.map((c) => `<option ${c === target.category ? 'selected' : ''}>${c}</option>`).join('')}</select>
      </div>
      <div class="grid2">
        <select id="ePlace">${PLACES.map((c) => `<option ${c === target.place ? 'selected' : ''}>${c}</option>`).join('')}</select>
        <input id="eNote" value="${esc(target.note || '')}" placeholder="备注">
      </div>
      <div class="form-actions">
        <button class="primary" id="saveExp">保存修改</button>
        <button class="ghost" id="cancelExp">取消</button>
      </div>
    </div>` : `
    <div class="form card-form">
      <div class="grid2">
        <input id="eAmount" type="number" step="0.01" placeholder="金额，如 13.5">
        <select id="eCat">${CATS.map((c) => `<option>${c}</option>`).join('')}</select>
      </div>
      <div class="grid2">
        <select id="ePlace">${PLACES.map((c) => `<option>${c}</option>`).join('')}</select>
        <input id="eNote" placeholder="备注，如 黄焖鸡米饭">
      </div>
      <button class="primary" id="addExp">记一笔</button>
    </div>`;

  $('#view').innerHTML = `
    <div class="hero money">
      <div class="hero-label">今日支出</div>
      <div class="hero-title">${money(d.today_total)}
        <span class="sub-inline">/ 建议上限 ${money(d.budget_day)}</span></div>
      <div class="hero-meta">
        <span>本月已花 ${money(d.month_total)}</span>
        <span>剩余 ${money(Math.max(0, d.left_month))}</span>
        <span>日均建议 ${money(d.daily_allow)}</span>
      </div>
    </div>
    <div class="section-title">${target ? '修改记录' : '快速记账'}</div>
    ${form}
    <div class="section-title">近 14 天走势 <span class="count">橙色为超过 ${money(d.budget_day)}/天</span></div>
    <div class="chart">${cols}</div>
    <div class="section-title">消费构成</div>
    <div class="panel">${bars(d.by_cat, 'name')}</div>
    <div class="section-title">常去地点 TOP5</div>
    <div class="panel">${bars(d.by_place, 'name')}</div>
    <div class="section-title">省钱建议</div>
    <div class="panel advice">
      ${d.advice.map((a) => `<p>· ${esc(a)}</p>`).join('') || '<p>记录满几天后会出现个性化建议。</p>'}
    </div>
    <div class="section-title">最近记录 <span class="count">每条都可修改/删除</span></div>
    <div class="list">${recent || '<div class="empty">还没有记录</div>'}</div>
    <div class="row-actions">
      <a class="ghost" href="/api/export">导出 CSV</a>
      <button class="ghost" id="resetDemo">重置演示数据</button>
    </div>`;

  const save = async () => {
    const body = {
      amount: $('#eAmount').value, category: $('#eCat').value,
      place: $('#ePlace').value, note: $('#eNote').value.trim(),
    };
    if (target) {
      body.id = target.id;
      const r = await postJSON('/api/expense/update', body);
      if (r && r.ok) { toast(r.message); state.editingExpense = null; state.money = r; renderMoney(); }
      else if (r) toast(r.error || '保存失败');
    } else {
      const r = await postJSON('/api/expense', body);
      if (r && r.ok) { toast(r.message); state.money = r; renderMoney(); }
      else if (r) toast(r.error || '记账失败');
    }
  };
  if (target) {
    $('#saveExp').onclick = save;
    $('#cancelExp').onclick = () => { state.editingExpense = null; renderMoney(); };
  } else {
    $('#addExp').onclick = save;
  }
  document.querySelectorAll('[data-edit-exp]').forEach((b) =>
    b.onclick = () => {
      state.editingExpense = Number(b.dataset.editExp);
      renderMoney();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  document.querySelectorAll('[data-del-exp]').forEach((b) =>
    b.onclick = async () => {
      if (!confirm(`确定删除这笔 ${money(b.dataset.amount)} 的记录吗？`)) return;
      const r = await postJSON('/api/expense/delete', { id: b.dataset.delExp });
      if (r && r.ok) { toast(r.message); state.editingExpense = null; state.money = r; renderMoney(); }
      else if (r) toast(r.error || '删除失败');
    });
  $('#resetDemo').onclick = async () => {
    if (!confirm('重置后会恢复 14 天示例账本，你手动加的记录会被清掉，继续吗？')) return;
    const r = await postJSON('/api/reset', {});
    if (r && r.ok) { toast(r.message); state.money = r; renderMoney(); }
  };
}

/* ================================================================ 同学页 */
async function loadStudents() {
  const d = await api('/api/students');
  state.students = (d && d.students) || [];
  renderStudents();
}

function renderStudents() {
  const list = state.students;
  const t = state.editingStudent;
  const editing = t ? list.find((s) => s.id === t) : null;

  const rows = list.length ? list.map((s) => `
    <div class="row-card">
      <div class="time-col"><b>${esc(s.role || '同学')}</b><span>${esc(s.dept || '—')}</span></div>
      <div class="row-main">
        <div class="row-title">${esc(s.name)}${s.sid ? ` <span class="pill">${esc(s.sid)}</span>` : ''}</div>
        <div class="row-sub">${esc(s.contact || '未填写联系方式')}${s.note ? ' · ' + esc(s.note) : ''}</div>
      </div>
      <div class="row-acts">
        <button class="mini" data-edit-stu="${s.id}">改</button>
        <button class="mini del" data-del-stu="${s.id}" data-name="${esc(s.name)}">删</button>
      </div>
    </div>`).join('') : '<div class="empty">还没有同学，下面添加第一位吧。</div>';

  const form = editing ? `
    <div class="form card-form">
      <div class="edit-head">修改同学信息<span class="badge-warn">编辑中</span></div>
      <div class="grid2">
        <input id="sName" value="${esc(editing.name)}" placeholder="姓名">
        <input id="sSid" value="${esc(editing.sid)}" placeholder="学号">
      </div>
      <div class="grid2">
        <input id="sContact" value="${esc(editing.contact)}" placeholder="联系方式">
        <select id="sRole">${['同学', '班长', '学习委员', '团支书', '舍友'].map((r) => `<option ${r === editing.role ? 'selected' : ''}>${r}</option>`).join('')}</select>
      </div>
      <div class="grid2">
        <input id="sDept" value="${esc(editing.dept)}" placeholder="学院 / 班级">
        <input id="sNote" value="${esc(editing.note)}" placeholder="备注">
      </div>
      <div class="form-actions">
        <button class="primary" id="saveStu">保存修改</button>
        <button class="ghost" id="cancelStu">取消</button>
      </div>
    </div>` : `
    <div class="form card-form">
      <div class="grid2">
        <input id="sName" placeholder="姓名">
        <input id="sSid" placeholder="学号">
      </div>
      <div class="grid2">
        <input id="sContact" placeholder="联系方式">
        <select id="sRole">${['同学', '班长', '学习委员', '团支书', '舍友'].map((r) => `<option>${r}</option>`).join('')}</select>
      </div>
      <div class="grid2">
        <input id="sDept" placeholder="学院 / 班级">
        <input id="sNote" placeholder="备注">
      </div>
      <button class="primary" id="addStu">添加同学</button>
    </div>`;

  $('#view').innerHTML = `
    <div class="info-strip">
      <span>👥 同学名单 · 共 ${list.length} 人</span>
      <span>支持新增、修改、删除，仅自己可见</span>
    </div>
    <div class="section-title">${editing ? '修改信息' : '添加同学'}</div>
    ${form}
    <div class="section-title">名单 <span class="count">每条都可修改/删除</span></div>
    <div class="list">${rows}</div>`;

  const payload = () => ({
    name: $('#sName').value.trim(), sid: $('#sSid').value.trim(),
    contact: $('#sContact').value.trim(), role: $('#sRole').value,
    dept: $('#sDept').value.trim(), note: $('#sNote').value.trim(),
  });
  if (editing) {
    $('#saveStu').onclick = async () => {
      const body = payload(); body.id = editing.id;
      const r = await postJSON('/api/student/update', body);
      if (r && r.ok) { toast(r.message); state.editingStudent = null; state.students = r.students; renderStudents(); }
      else if (r) toast(r.error || '保存失败');
    };
    $('#cancelStu').onclick = () => { state.editingStudent = null; renderStudents(); };
  } else {
    $('#addStu').onclick = async () => {
      const r = await postJSON('/api/student', payload());
      if (r && r.ok) { toast(r.message); state.students = r.students; renderStudents(); }
      else if (r) toast(r.error || '添加失败');
    };
  }
  document.querySelectorAll('[data-edit-stu]').forEach((b) =>
    b.onclick = () => {
      state.editingStudent = Number(b.dataset.editStu);
      renderStudents();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  document.querySelectorAll('[data-del-stu]').forEach((b) =>
    b.onclick = async () => {
      if (!confirm(`确定把「${b.dataset.name}」从名单里删除吗？`)) return;
      const r = await postJSON('/api/student/delete', { id: b.dataset.delStu });
      if (r && r.ok) { toast(r.message); state.students = r.students; renderStudents(); }
      else if (r) toast(r.error || '删除失败');
    });
}

/* ================================================================ 校园信息页 */
async function loadCampus() {
  const d = await api('/api/campus');
  state.campus = d;
  if (d && d.canteens) { CANTEEN_LIST = d.canteens; PLACES = d.canteens.concat(['其他']); }
  renderCampus();
}

function renderCampus() {
  const d = state.campus;
  if (!d) return;
  const s = d.school;
  const groups = {};
  d.links.forEach((l) => { (groups[l.tag] = groups[l.tag] || []).push(l); });
  const linkBlocks = Object.keys(groups).map((tag) => `
    <div class="link-group">
      <div class="link-tag">${esc(tag)}</div>
      ${groups[tag].map((l) => `
        <a class="link-row" href="${esc(l.url)}" target="_blank" rel="noopener">
          <div class="link-main">
            <div class="link-title">${esc(l.name)}</div>
            <div class="link-desc">${esc(l.desc)}</div>
          </div>
          <span class="link-go">打开 ›</span>
        </a>`).join('')}
    </div>`).join('');

  const phones = Object.keys(s.phone).map((k) => `
    <div class="phone-row"><span>${esc(k)}</span><b>${esc(s.phone[k])}</b></div>`).join('');

  $('#view').innerHTML = `
    <div class="hero campus">
      <div class="hero-label">${esc(s.address)} · 邮编 ${esc(s.zip)}</div>
      <div class="hero-title">${esc(s.name)}</div>
      <div class="hero-meta">
        <span>校训：${esc(s.motto)}</span>
        <span>校园精神：${esc(s.spirit)}</span>
      </div>
    </div>
    <div class="info-strip">
      <span>🏫 ${s.colleges} 个二级学院 · ${s.majors} 个在招本科专业</span>
      <span>📐 占地 ${esc(s.area)} · ${esc(s.campuses.join(' / '))}</span>
    </div>
    <p class="hint">${esc(s.intro)}</p>

    <div class="section-title">官方入口 <span class="count">来自学校官网公开链接</span></div>
    ${linkBlocks}

    <div class="section-title">联系电话</div>
    <div class="panel">${phones}</div>

    <div class="section-title">本地生活</div>
    <div class="panel">
      <p class="note">🍚 食堂：${esc(CANTEEN_LIST.join(' · '))}</p>
      <p class="note">🛰 校园网认证：连上网络后打开任意网页，用无线校园网账号登录；账号问题拨 3728107 / 3728108。</p>
      <p class="note">🗓 校历、教务、图书馆资源见上方“官方入口”，校外访问校内系统一般需先连学校 VPN。</p>
    </div>
    <p class="hint">以上信息整理自黄淮学院官网公开页面，仅供学习与演示；如与学校最新通知不一致，以学校官方发布为准。</p>`;
}

/* ================================================================ 问答页 */
async function loadChat() {
  const d = await api('/api/chat');
  state.chat = (d && d.history) || [];
  renderChat();
}

function bubble(role, text) {
  return `<div class="msg ${role}">
    <div class="bubble">${esc(text).replace(/\n/g, '<br>')}</div></div>`;
}

function renderChat() {
  const list = state.chat.length ? state.chat.map((m) =>
    bubble(m.role === 'user' ? 'me' : 'bot', m.text)).join('')
    : bubble('bot', '你好，我是校园生活助手 🤖\n可以问我：校园网断网怎么办、图书馆几点开门、请假流程、快递在哪取……');

  const slots = ['早餐', '午餐', '下午茶', '晚餐', '夜宵'];
  $('#view').innerHTML = `
    ${state.chat.length ? '<div class="row-actions"><button class="ghost" id="clearChat">清空问答记录</button></div>' : ''}
    <div class="chat-wrap" id="chatWrap">${list}</div>
    <div class="quick">
      ${state.quick.questions.map((q) => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join('')}
    </div>
    <div class="chat-input">
      <input id="chatText" placeholder="输入你的问题…">
      <button class="primary" id="send">发送</button>
    </div>
    <p class="hint">时段参考：${slots.map((s) => {
      const slot = state.quick.slots.find((x) => x.name === s);
      return slot ? `${s} ${slot.begin}-${slot.end}` : s;
    }).join(' · ')}</p>`;

  const wrap = $('#chatWrap');
  if (wrap) wrap.scrollTop = wrap.scrollHeight;

  const send = async (text) => {
    const q = (text || $('#chatText').value).trim();
    if (!q) return;
    state.chat.push({ role: 'user', text: q });
    renderChat();
    const r = await postJSON('/api/chat', { text: q });
    if (r && r.ok) { state.chat.push({ role: 'bot', text: r.answer }); renderChat(); }
  };
  $('#send').onclick = () => send();
  $('#chatText').onkeydown = (e) => { if (e.key === 'Enter') send(); };
  document.querySelectorAll('[data-q]').forEach((b) => b.onclick = () => send(b.dataset.q));
  const clear = $('#clearChat');
  if (clear) clear.onclick = async () => {
    if (!confirm('清空全部问答记录？')) return;
    const r = await postJSON('/api/chat/clear', {});
    if (r && r.ok) { toast(r.message); state.chat = []; renderChat(); }
  };
}

/* ================================================================ 路由 */
const LOADERS = {
  schedule: loadSchedule, meals: loadMeals, money: loadMoney,
  students: loadStudents, campus: loadCampus, chat: loadChat,
};

async function switchTab(tab) {
  state.tab = tab;
  state.editingCourse = null;
  state.editingExpense = null;
  state.editingStudent = null;
  document.querySelectorAll('.tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === tab));
  $('#view').innerHTML = '<div class="loading">加载中…</div>';
  await LOADERS[tab]();
  const titles = {
    schedule: '今天有几场考核、下一场在哪，一眼看清',
    meals: '到点就推荐，不用再纠结吃什么',
    money: '花得明白，才省得下来',
    students: '名单随用随改，找人不求人',
    campus: '黄淮学院的官方入口与本地生活，一处找齐',
    chat: '校园事务问一句就有答案',
  };
  $('#subline').textContent = titles[tab];
}

async function boot() {
  const me = await api('/api/me');
  if (!me || !me.ok) { location.href = '/'; return; }
  state.user = me.user;
  // 注册时如果姓名填成了纯数字（学号），就别把学号当名字显示
  const rawName = (me.user.name || '').trim();
  const displayName = /^\d+$/.test(rawName) ? '同学' : rawName;
  state.user.displayName = displayName;
  $('#hello').textContent = `你好，${displayName}`;
  $('#avatar').textContent = displayName === '同学' ? '同' : displayName.slice(0, 1);
  const quick = await api('/api/quick');
  if (quick) state.quick = quick;
  document.querySelectorAll('.tab').forEach((b) =>
    b.onclick = () => switchTab(b.dataset.tab));
  $('#logout').onclick = async () => {
    await postJSON('/api/logout', {});
    location.href = '/';
  };
  await switchTab('schedule');
}

boot();
