const ADMIN_AUTH_KEY = "coze_admin_api_key";
const ADMIN_SKIP_LOGIN_KEY = "coze_admin_skip_login";

export function getAdminApiKey(): string {
  return localStorage.getItem(ADMIN_AUTH_KEY)?.trim() || "";
}

export function setAdminApiKey(apiKey: string): void {
  localStorage.setItem(ADMIN_AUTH_KEY, apiKey.trim());
  localStorage.removeItem(ADMIN_SKIP_LOGIN_KEY);
}

export function clearAdminApiKey(): void {
  localStorage.removeItem(ADMIN_AUTH_KEY);
}

export function enableSkipLogin(): void {
  localStorage.setItem(ADMIN_SKIP_LOGIN_KEY, "1");
}

export function disableSkipLogin(): void {
  localStorage.removeItem(ADMIN_SKIP_LOGIN_KEY);
}

export function isLoggedIn(): boolean {
  return Boolean(getAdminApiKey()) || localStorage.getItem(ADMIN_SKIP_LOGIN_KEY) === "1";
}
