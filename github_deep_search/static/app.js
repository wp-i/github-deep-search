const serviceState = document.querySelector("#service-state");
const githubStatus = document.querySelector("#github-status");
const llmStatus = document.querySelector("#llm-status");
const searchStatus = document.querySelector("#search-status");

function credentialLabel(configured) {
  return configured ? "已配置" : "未配置";
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error(`status request failed: ${response.status}`);
    }
    const status = await response.json();
    githubStatus.textContent = credentialLabel(status.hasGithubToken);
    llmStatus.textContent = credentialLabel(status.hasLlmKey);
    searchStatus.textContent = status.searchAvailable ? "可用" : "重建中";
    serviceState.textContent = "服务已启动";
    serviceState.className = "badge badge-ready";
  } catch (error) {
    githubStatus.textContent = "无法检查";
    llmStatus.textContent = "无法检查";
    searchStatus.textContent = "不可用";
    serviceState.textContent = "服务异常";
    serviceState.className = "badge badge-error";
    console.error(error);
  }
}

loadStatus();
