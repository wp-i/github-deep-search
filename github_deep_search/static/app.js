const serviceState = document.querySelector("#service-state");
const githubStatus = document.querySelector("#github-status");
const llmStatus = document.querySelector("#llm-status");
const searchStatus = document.querySelector("#search-status");
const queryInput = document.querySelector("#query-input");
const startRunButton = document.querySelector("#start-run");
const searchNotice = document.querySelector("#search-notice");
const runPanel = document.querySelector("#run-panel");
const runState = document.querySelector("#run-state");
const cancelRunButton = document.querySelector("#cancel-run");
const runActivity = document.querySelector("#run-activity");
const runWarnings = document.querySelector("#run-warnings");
const runError = document.querySelector("#run-error");
const reportPanel = document.querySelector("#report");
const reportHeading = document.querySelector("#report-heading");
const reportProjects = document.querySelector("#report-projects");
const reportUsage = document.querySelector("#report-usage");
const reportActionStatus = document.querySelector("#report-action-status");
const copyReportButton = document.querySelector("#copy-report");
const downloadReportButton = document.querySelector("#download-report");

const stageStatusLabels = {
  not_started: "未开始",
  in_progress: "进行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const runStatusLabels = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const eventTypes = [
  "stage_started",
  "stage_completed",
  "supplemental_discovery",
  "warning",
  "run_completed",
  "run_failed",
  "run_cancelled",
];

let currentRunId = null;
let eventSource = null;
let currentMarkdown = "";
const rememberedRunKey = "github-deep-search:last-run-id";

function credentialLabel(configured) {
  return configured ? "已配置" : "未配置";
}

function closeEventSource() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function rememberRun(runId) {
  try {
    window.localStorage.setItem(rememberedRunKey, runId);
  } catch (error) {
    console.warn("Unable to remember the current run in this browser.");
  }
}

function rememberedRunId() {
  try {
    return window.localStorage.getItem(rememberedRunKey);
  } catch (error) {
    return null;
  }
}

function forgetRememberedRun() {
  try {
    window.localStorage.removeItem(rememberedRunKey);
  } catch (error) {
    console.warn("Unable to clear the remembered run in this browser.");
  }
}

function setSearchControls(status) {
  const configured = status.hasGithubToken && status.hasLlmKey;
  const canStart = configured && !status.hasActiveRun;
  queryInput.disabled = !canStart;
  startRunButton.disabled = !canStart;

  if (!configured) {
    startRunButton.textContent = "请先配置凭据";
    searchNotice.textContent = "GITHUB_TOKEN 和 LLM_API_KEY 都配置后才能开始任务。";
  } else if (status.hasActiveRun) {
    startRunButton.textContent = "已有任务运行中";
    searchNotice.textContent = "每个本地实例同时只运行一个任务。";
  } else {
    startRunButton.textContent = "开始搜索";
    searchNotice.textContent = "运行期间可刷新或关闭页面；任务不会因此取消。";
  }
}

function clearElement(element) {
  while (element.firstChild) {
    element.removeChild(element.firstChild);
  }
}

function appendTextElement(parent, tagName, text, className = "") {
  const element = document.createElement(tagName);
  element.textContent = text;
  if (className) {
    element.className = className;
  }
  parent.appendChild(element);
  return element;
}

function reportLabels(language) {
  if (language === "en") {
    return {
      heading: "The three most relevant projects",
      reason: "Why it is relevant",
      confirmed: "Confirmed requirements",
      gaps: "Partial, conflicting, or unverified requirements",
      facts: "Repository facts",
      pushed: "Last code update",
      archived: "Archive status",
      archivedYes: "Archived",
      archivedNo: "Not archived",
      license: "License",
      release: "Latest release",
      missing: "Not provided",
      risks: "Necessary risks",
      noRisks: "The analysis listed no additional risks.",
      usageInput: "LLM input tokens",
      usageOutput: "LLM output tokens",
      usageTotal: "LLM total tokens",
    };
  }
  return {
    heading: "最相关的三个项目",
    reason: "相关原因",
    confirmed: "已确认满足",
    gaps: "部分满足、明确不符或尚未确认",
    facts: "仓库事实",
    pushed: "最后代码更新时间",
    archived: "归档状态",
    archivedYes: "已归档",
    archivedNo: "未归档",
    license: "许可证",
    release: "最新 Release",
    missing: "未提供",
    risks: "必要风险",
    noRisks: "分析未列出额外风险。",
    usageInput: "LLM 输入 token",
    usageOutput: "LLM 输出 token",
    usageTotal: "LLM 总 token",
  };
}

function renderAssessmentList(parent, assessments) {
  const list = document.createElement("ul");
  list.className = "assessment-list";
  for (const assessment of assessments) {
    const item = document.createElement("li");
    item.dataset.status = assessment.status;
    const title = appendTextElement(
      item,
      "strong",
      assessment.requirement,
      "assessment-title",
    );
    title.dataset.requirementId = assessment.id;
    appendTextElement(item, "p", assessment.explanation);
    if (assessment.evidence.length) {
      const sources = document.createElement("div");
      sources.className = "evidence-links";
      for (const evidence of assessment.evidence) {
        const link = document.createElement("a");
        link.href = evidence.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = evidence.label;
        sources.appendChild(link);
        if (evidence.quote) {
          appendTextElement(sources, "q", evidence.quote, "evidence-quote");
        }
      }
      item.appendChild(sources);
    }
    list.appendChild(item);
  }
  if (!assessments.length) {
    appendTextElement(list, "li", "—");
  }
  parent.appendChild(list);
}

function appendFact(list, label, value) {
  const wrapper = document.createElement("div");
  appendTextElement(wrapper, "dt", label);
  appendTextElement(wrapper, "dd", value);
  list.appendChild(wrapper);
}

function renderReport(report) {
  clearElement(reportProjects);
  clearElement(reportUsage);
  reportActionStatus.textContent = "";
  currentMarkdown = report.markdown;
  const labels = reportLabels(report.language);
  reportHeading.textContent = labels.heading;

  for (const [index, project] of report.projects.entries()) {
    const article = document.createElement("article");
    article.className = "result-card";
    article.dataset.rank = String(index + 1);

    const header = document.createElement("div");
    header.className = "result-header";
    const title = document.createElement("h3");
    const link = document.createElement("a");
    link.href = project.url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${index + 1}. ${project.repository}`;
    title.appendChild(link);
    header.appendChild(title);
    appendTextElement(header, "strong", `${project.score}/100`, "score");
    article.appendChild(header);

    appendTextElement(article, "h4", labels.reason);
    appendTextElement(article, "p", project.relevanceReason);
    appendTextElement(article, "h4", labels.confirmed);
    renderAssessmentList(article, project.confirmed);
    appendTextElement(article, "h4", labels.gaps);
    renderAssessmentList(article, project.gaps);

    appendTextElement(article, "h4", labels.facts);
    const facts = document.createElement("dl");
    facts.className = "repository-facts";
    appendFact(facts, labels.pushed, project.facts.lastPushedAt || labels.missing);
    appendFact(
      facts,
      labels.archived,
      project.facts.archived ? labels.archivedYes : labels.archivedNo,
    );
    appendFact(facts, labels.license, project.facts.license || labels.missing);
    appendFact(facts, labels.release, project.facts.latestReleaseAt || labels.missing);
    article.appendChild(facts);

    appendTextElement(article, "h4", labels.risks);
    const riskList = document.createElement("ul");
    riskList.className = "risk-list";
    if (project.risks.length) {
      for (const risk of project.risks) {
        appendTextElement(riskList, "li", risk);
      }
    } else {
      appendTextElement(riskList, "li", labels.noRisks);
    }
    article.appendChild(riskList);
    reportProjects.appendChild(article);
  }

  appendFact(reportUsage, labels.usageInput, String(report.usage.llmInputTokens));
  appendFact(reportUsage, labels.usageOutput, String(report.usage.llmOutputTokens));
  appendFact(reportUsage, labels.usageTotal, String(report.usage.llmTotalTokens));
  reportPanel.hidden = false;
}

function hideReport() {
  currentMarkdown = "";
  reportPanel.hidden = true;
  reportActionStatus.textContent = "";
  clearElement(reportProjects);
  clearElement(reportUsage);
}

function renderRun(snapshot) {
  const runPanelWasHidden = runPanel.hidden;
  currentRunId = snapshot.id;
  rememberRun(snapshot.id);
  queryInput.value = snapshot.query;
  runPanel.hidden = false;
  if (runPanelWasHidden) {
    runPanel.scrollIntoView({ block: "start" });
  }
  runState.textContent = runStatusLabels[snapshot.status] || snapshot.status;
  runState.className = `badge ${snapshot.status === "failed" ? "badge-error" : "badge-pending"}`;
  if (snapshot.status === "completed") {
    runState.className = "badge badge-ready";
  }

  for (const stage of snapshot.stages) {
    const row = document.querySelector(`[data-stage="${stage.name}"]`);
    if (!row) {
      continue;
    }
    row.dataset.status = stage.status;
    row.querySelector("strong").textContent = stageStatusLabels[stage.status] || stage.status;
  }

  if (snapshot.supplementalDiscoveryIteration > 0) {
    runActivity.hidden = false;
    runActivity.textContent = `补充发现 · 第 ${snapshot.supplementalDiscoveryIteration} 次`;
  } else {
    runActivity.hidden = true;
    runActivity.textContent = "";
  }

  if (snapshot.warnings.length) {
    runWarnings.hidden = false;
    runWarnings.textContent = snapshot.warnings.join("\n");
  } else {
    runWarnings.hidden = true;
    runWarnings.textContent = "";
  }

  if (snapshot.error) {
    runError.hidden = false;
    runError.textContent = snapshot.error.message;
  } else {
    runError.hidden = true;
    runError.textContent = "";
  }

  if (snapshot.status === "completed" && snapshot.report) {
    renderReport(snapshot.report);
  } else {
    hideReport();
  }

  const running = snapshot.status === "running";
  runPanel.setAttribute("aria-busy", running ? "true" : "false");
  cancelRunButton.hidden = !running;
  queryInput.disabled = running;
  startRunButton.disabled = running;
  if (!running) {
    closeEventSource();
  }
}

async function responseError(response) {
  try {
    const payload = await response.json();
    return payload.error?.message || `请求失败：${response.status}`;
  } catch (error) {
    return `请求失败：${response.status}`;
  }
}

async function refreshRun(runId) {
  const response = await fetch(`/api/runs/${runId}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  const snapshot = await response.json();
  renderRun(snapshot);
  if (snapshot.status !== "running") {
    await loadStatus({ reconnect: false });
  }
  return snapshot;
}

function connectEvents(snapshot) {
  closeEventSource();
  if (snapshot.status !== "running") {
    return;
  }

  eventSource = new EventSource(
    `/api/runs/${snapshot.id}/events?after=${snapshot.lastEventId}`,
  );
  for (const eventType of eventTypes) {
    eventSource.addEventListener(eventType, async () => {
      try {
        await refreshRun(snapshot.id);
      } catch (error) {
        runError.hidden = false;
        runError.textContent = error.message;
      }
    });
  }
}

async function reconnectActiveRun() {
  const response = await fetch("/api/runs/active", { headers: { Accept: "application/json" } });
  if (response.status === 404) {
    return;
  }
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  const snapshot = await response.json();
  renderRun(snapshot);
  connectEvents(snapshot);
}

async function reconnectRememberedRun() {
  const runId = rememberedRunId();
  if (!runId) {
    return;
  }
  const response = await fetch(`/api/runs/${runId}`, { headers: { Accept: "application/json" } });
  if (response.status === 404) {
    forgetRememberedRun();
    return;
  }
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  const snapshot = await response.json();
  renderRun(snapshot);
  connectEvents(snapshot);
}

async function loadStatus({ reconnect = true } = {}) {
  try {
    const response = await fetch("/api/status", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`status request failed: ${response.status}`);
    }
    const status = await response.json();
    githubStatus.textContent = credentialLabel(status.hasGithubToken);
    llmStatus.textContent = credentialLabel(status.hasLlmKey);
    searchStatus.textContent = "可用";
    serviceState.textContent = "服务已启动";
    serviceState.className = "badge badge-ready";
    setSearchControls(status);
    if (reconnect && status.hasActiveRun) {
      await reconnectActiveRun();
    } else if (reconnect) {
      await reconnectRememberedRun();
    }
  } catch (error) {
    githubStatus.textContent = "无法检查";
    llmStatus.textContent = "无法检查";
    searchStatus.textContent = "不可用";
    serviceState.textContent = "服务异常";
    serviceState.className = "badge badge-error";
    queryInput.disabled = true;
    startRunButton.disabled = true;
    searchNotice.textContent = "无法连接本地服务。";
    console.error(error);
  }
}

async function startRun() {
  const query = queryInput.value;
  if (!query.trim()) {
    searchNotice.textContent = "请输入有意义的需求描述。";
    return;
  }

  startRunButton.disabled = true;
  try {
    const response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ query }),
    });
    if (!response.ok) {
      throw new Error(await responseError(response));
    }
    const snapshot = await response.json();
    renderRun(snapshot);
    connectEvents(snapshot);
    await loadStatus({ reconnect: false });
  } catch (error) {
    searchNotice.textContent = error.message;
    await loadStatus();
  }
}

async function cancelRun() {
  if (!currentRunId) {
    return;
  }
  cancelRunButton.disabled = true;
  try {
    const response = await fetch(`/api/runs/${currentRunId}`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseError(response));
    }
    renderRun(await response.json());
    await loadStatus({ reconnect: false });
  } catch (error) {
    runError.hidden = false;
    runError.textContent = error.message;
  } finally {
    cancelRunButton.disabled = false;
  }
}

async function copyReport() {
  if (!currentMarkdown) {
    return;
  }
  try {
    await navigator.clipboard.writeText(currentMarkdown);
    reportActionStatus.textContent = "Markdown 已复制。";
  } catch (error) {
    reportActionStatus.textContent = "无法复制 Markdown，请使用下载功能。";
  }
}

function downloadReport() {
  if (!currentMarkdown) {
    return;
  }
  const blob = new Blob([currentMarkdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "github-deep-search-report.md";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  reportActionStatus.textContent = "Markdown 下载已开始。";
}

startRunButton.addEventListener("click", startRun);
cancelRunButton.addEventListener("click", cancelRun);
copyReportButton.addEventListener("click", copyReport);
downloadReportButton.addEventListener("click", downloadReport);
loadStatus();
