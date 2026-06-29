const searchProfile = {
  label: "完整调研",
  mode: "detailed",
  budget: "continue",
  action: "开始真实搜索"
};

const steps = ["理解目标", "规划搜索", "收集证据", "分析项目", "生成报告"];
let timer = null;
let lastReport = null;

const queryInput = document.getElementById("query");
const runButton = document.getElementById("run");
const runLabel = document.getElementById("runLabel");
const statusArea = document.getElementById("statusArea");
const statusText = document.getElementById("status");
const progressValue = document.getElementById("progressValue");
const profileText = document.getElementById("profile");
const progressFill = document.getElementById("progressFill");
const stepsElement = document.getElementById("steps");
const errorElement = document.getElementById("error");
const resultsElement = document.getElementById("results");
const reportElement = document.getElementById("report");
const keyStatus = document.getElementById("keyStatus");
const copyMarkdownButton = document.getElementById("copyMarkdown");
const downloadJsonButton = document.getElementById("downloadJson");

function setBusy(busy) {
  runButton.disabled = busy;
  runLabel.textContent = busy ? "正在调研" : searchProfile.action;
}

function setProgress(percent) {
  progressFill.style.width = `${percent}%`;
  progressValue.textContent = `${percent}%`;
}

function startProgress() {
  let current = 0;
  statusArea.classList.add("active");
  statusText.textContent = steps[0];
  setProgress(10);
  stepsElement.innerHTML = steps.map((step, index) => `<span class="${index === 0 ? "active" : ""}">${step}</span>`).join("");
  timer = window.setInterval(() => {
    current = Math.min(current + 1, steps.length - 1);
    statusText.textContent = steps[current];
    setProgress(Math.min(18 + current * 18, 88));
    Array.from(stepsElement.children).forEach((node, index) => node.classList.toggle("active", index <= current));
  }, 5500);
}

function finishProgress(success) {
  if (timer) window.clearInterval(timer);
  timer = null;
  if (!success) {
    statusText.textContent = "调研未完成";
    setProgress(0);
    return;
  }
  statusText.textContent = "调研完成";
  setProgress(100);
  Array.from(stepsElement.children).forEach((node) => node.classList.add("active"));
}

function explainError(response, data) {
  if (data && data.detail) return String(data.detail);
  if (response.status === 422) return "请输入至少 2 个字符的需求描述。";
  if (response.status === 429) return "GitHub 或上游服务限流了，请稍后重试，或配置 GITHUB_TOKEN。";
  if (response.status >= 500) return "服务端调研失败。请检查 GITHUB_TOKEN、LLM_API_KEY 和网络连接。";
  return "暂时无法完成调研，请稍后重试。";
}

async function runSearch() {
  const query = queryInput.value.trim();
  if (!query) {
    queryInput.focus();
    return;
  }

  errorElement.classList.remove("active");
  profileText.textContent = searchProfile.label;
  setBusy(true);
  startProgress();
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({query, mode: searchProfile.mode, budget: searchProfile.budget})
    });
    let data = null;
    try {
      data = await response.json();
    } catch (_error) {
      data = null;
    }
    if (!response.ok) throw new Error(explainError(response, data));
    lastReport = data;
    reportElement.innerHTML = data.reportHtml || "";
    resultsElement.classList.add("active");
    finishProgress(true);
    resultsElement.scrollIntoView({behavior: "smooth", block: "start"});
  } catch (error) {
    errorElement.textContent = error.message || "暂时无法完成调研，请稍后重试。";
    errorElement.classList.add("active");
    finishProgress(false);
  } finally {
    setBusy(false);
  }
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error("status failed");
    const status = await response.json();
    const ready = status.hasGithubToken && status.hasLlmKey;
    keyStatus.innerHTML = `<span class="status-dot ${ready ? "ready" : "warning"}" aria-hidden="true"></span><span>${ready ? "已配置 API keys" : "需要 API keys 才能执行真实调研"}</span>`;
  } catch (_error) {
    keyStatus.innerHTML = '<span class="status-dot warning" aria-hidden="true"></span><span>无法读取配置状态</span>';
  }
}

function copyMarkdown() {
  if (!lastReport || !lastReport.reportMarkdown) return;
  navigator.clipboard.writeText(lastReport.reportMarkdown).then(() => {
    copyMarkdownButton.textContent = "已复制";
    window.setTimeout(() => { copyMarkdownButton.textContent = "复制 Markdown"; }, 1400);
  });
}

function downloadJson() {
  if (!lastReport) return;
  const blob = new Blob([JSON.stringify(lastReport, null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "github-deep-search-report.json";
  link.click();
  URL.revokeObjectURL(url);
}

runButton.addEventListener("click", runSearch);
copyMarkdownButton.addEventListener("click", copyMarkdown);
downloadJsonButton.addEventListener("click", downloadJson);
queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) runSearch();
});

loadStatus();
