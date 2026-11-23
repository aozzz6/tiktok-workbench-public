/* 每日更新设置。轮询运行状态时保留正在编辑的表单。 */
(function () {
  "use strict";
  const view = document.getElementById("view-schedule");
  if (!view) return;
  const field = name => document.getElementById("schedule-" + name);
  const form = field("form");
  const states = { running: "运行中", done: "已完成", failed: "失败", stopped: "已停止",
    interrupted: "已中断" };
  let dirty = false;
  let saving = false;
  let revision = 0;

  function message(text, error = false) {
    field("message").textContent = text;
    field("message").dataset.error = String(error);
  }

  function errorMessage(err) {
    const raw = String(err.message || err);
    try { return JSON.parse(raw.slice(raw.indexOf("{"))).detail || raw; }
    catch { return raw; }
  }

  function updateForm(data) {
    field("enabled").checked = data.enabled;
    field("time").value = data.time;
    const selected = new Set(data.accounts);
    field("scope").value = selected.size ? "selected" : "all";
    field("accounts").hidden = !selected.size;
    const choices = field("choices");
    choices.replaceChildren();
    const available = new Set(data.eligible_accounts);
    for (const account of new Set([...available, ...selected])) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = account;
      input.checked = selected.has(account);
      const name = available.has(account) ? account : account + "（已停用或移除）";
      label.append(input, document.createTextNode(" " + name));
      choices.append(label);
    }
    if (!choices.childElementCount) choices.textContent = "请先在账号管理中添加并启用账号。";
    field("fields").disabled = false;
  }

  function render(data) {
    if (!dirty) updateForm(data);
    field("next").textContent = data.enabled ? data.next_at : "定时已关闭";
    const scope = data.accounts.length ? "指定账号" : "全部已启用账号";
    field("targets").textContent = scope + " · " + data.target_count + " 个可更新";
    const result = data.last_result;
    let outcome = states[result?.status] || "已触发，暂无结果";
    if (result?.status === "done" && result.level === "WARN") outcome = "已完成，有待处理项";
    if (result?.counts) {
      const counts = result.counts;
      outcome += "（正常 " + (counts.ok || 0) + "，待处理 " + (counts.warn || 0) + "，失败 " + (counts.failed || 0) + "）";
    }
    field("last").textContent = data.last_run
      ? data.last_run + " · " + outcome
      : "暂无定时运行记录";
    field("waiting").textContent = data.waiting || "";
    const issues = field("issues");
    const markup = accountIssueMarkup(result?.account_issues);
    if (issues.dataset.markup !== markup) {
      issues.innerHTML = markup ? "<h3>账号失败原因</h3>" + markup : "";
      issues.hidden = !markup;
      issues.dataset.markup = markup;
    }
  }

  async function refresh() {
    if (saving) return;
    const request = ++revision;
    try {
      const data = await api("/api/schedule");
      if (request !== revision) return;
      render(data);
      if (!dirty && field("message").dataset.error === "true") message("");
    } catch (err) {
      if (request === revision) message("读取定时设置失败。" + errorMessage(err), true);
    }
  }

  form.addEventListener("input", () => { dirty = true; message("有未保存的修改。"); });
  field("scope").addEventListener("change", () => {
    field("accounts").hidden = field("scope").value === "all";
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (saving || field("fields").disabled) return;
    const subset = field("scope").value === "selected";
    const accounts = subset
      ? Array.from(field("choices").querySelectorAll("input:checked"), input => input.value) : [];
    if (subset && !accounts.length) { message("请至少选择一个账号。", true); return; }
    const payload = {enabled: field("enabled").checked, time: field("time").value, accounts};
    saving = true;
    ++revision;
    field("fields").disabled = true;
    try {
      const data = await api("/api/schedule", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)});
      dirty = false;
      render(data);
      message("设置已保存。");
    } catch (err) {
      message("保存失败。" + errorMessage(err), true);
    } finally {
      saving = false;
      field("fields").disabled = false;
    }
  });
  field("refresh").addEventListener("click", refresh);
  window.dailySchedule = {refresh};
  setInterval(() => {
    if (!document.hidden && view.classList.contains("active")) refresh();
  }, 30000);
})();
