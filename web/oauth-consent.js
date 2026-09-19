import { createClient } from "@supabase/supabase-js";

const config = JSON.parse(document.getElementById("oauth-config").textContent);
const authorizationId = new URLSearchParams(window.location.search).get("authorization_id");
const supabase = createClient(config.supabaseUrl, config.publishableKey);

const loading = document.getElementById("loading");
const login = document.getElementById("login");
const consent = document.getElementById("consent");
const errorBox = document.getElementById("error");

function showError(message) {
  loading.classList.add("hidden");
  errorBox.textContent = message;
  errorBox.classList.remove("hidden");
}

function showLogin() {
  loading.classList.add("hidden");
  consent.classList.add("hidden");
  login.classList.remove("hidden");
}

async function loadAuthorization() {
  if (!authorizationId) {
    showError("缺少 authorization_id");
    return;
  }

  const { data: userData, error: userError } = await supabase.auth.getUser();
  if (userError || !userData.user) {
    showLogin();
    return;
  }

  const { data, error } = await supabase.auth.oauth.getAuthorizationDetails(authorizationId);
  if (error || !data) {
    showError(error?.message || "无法读取授权请求");
    return;
  }

  if (!("authorization_id" in data)) {
    window.location.assign(data.redirect_url);
    return;
  }

  document.getElementById("clientName").textContent = data.client?.name || "OAuth 客户端";
  document.getElementById("redirectUri").textContent = data.redirect_uri || "";
  const scopes = (data.scope || "").trim().split(/\s+/).filter(Boolean);
  const scopeBlock = document.getElementById("scopeBlock");
  const scopeList = document.getElementById("scopes");
  scopeList.replaceChildren(...scopes.map((scope) => {
    const item = document.createElement("li");
    item.textContent = scope;
    return item;
  }));
  scopeBlock.classList.toggle("hidden", scopes.length === 0);

  loading.classList.add("hidden");
  login.classList.add("hidden");
  consent.classList.remove("hidden");
}

document.getElementById("loginButton").addEventListener("click", async () => {
  errorBox.classList.add("hidden");
  const email = document.getElementById("email").value;
  const password = document.getElementById("password").value;
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    showError(error.message);
    login.classList.remove("hidden");
    return;
  }
  loading.classList.remove("hidden");
  await loadAuthorization();
});

document.getElementById("approveButton").addEventListener("click", async () => {
  const { data, error } = await supabase.auth.oauth.approveAuthorization(authorizationId);
  if (error || !data?.redirect_url) {
    showError(error?.message || "授权失败");
    return;
  }
  window.location.assign(data.redirect_url);
});

document.getElementById("denyButton").addEventListener("click", async () => {
  const { data, error } = await supabase.auth.oauth.denyAuthorization(authorizationId);
  if (error || !data?.redirect_url) {
    showError(error?.message || "拒绝授权失败");
    return;
  }
  window.location.assign(data.redirect_url);
});

await loadAuthorization();
