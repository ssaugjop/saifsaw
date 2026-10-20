// Saw Host — Cloudflare Worker: يربط sawhost.de5.net (و *.sawhost.de5.net) بالـ Space على HuggingFace.
// المتغيرات (Settings → Variables): ORIGIN = https://sawhost-sawhost.hf.space ، EDGE_SECRET = نفس سرّ المنصة (Secret)
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, env.ORIGIN);
    const h = new Headers(request.headers);
    h.set("X-Saw-Edge", env.EDGE_SECRET);          // يُثبت للمنصة أن الطلب جاء من هذا الـ Worker
    h.set("X-Saw-Host", url.hostname);             // المضيف الأصلي الذي كتبه الزائر
    h.set("X-Saw-Client-IP", request.headers.get("CF-Connecting-IP") || "");
    h.delete("X-Forwarded-Host");
    const init = { method: request.method, headers: h, redirect: "manual" };
    if (!["GET", "HEAD"].includes(request.method)) init.body = request.body;
    return fetch(new Request(target, init));
  },
};
