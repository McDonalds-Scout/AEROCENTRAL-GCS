const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { spawn } = require("child_process");

const root = __dirname;
const port = Number(process.env.PORT || 8080);
const url = `http://127.0.0.1:${port}`;
const telemetryClients = new Set();
let latestTelemetry = null;
const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".png": "image/png"
};
const bootTime = new Date().toISOString();
const versionFiles = ["index.html", "app.js", "styles.css"];

function openBrowser() {
  if (process.env.AUTO_OPEN !== "1") return;
  const child = spawn("explorer.exe", [url], {
    detached: true,
    stdio: "ignore"
  });
  child.unref();
}

function fileHash(file) {
  try {
    return crypto.createHash("sha256").update(fs.readFileSync(path.join(root, file))).digest("hex").slice(0, 12);
  } catch (_) {
    return "missing";
  }
}

function frontendHash() {
  const hash = crypto.createHash("sha256");
  versionFiles.forEach((file) => {
    const fullPath = path.join(root, file);
    if (!fs.existsSync(fullPath)) return;
    hash.update(file);
    hash.update("\0");
    hash.update(fs.readFileSync(fullPath));
    hash.update("\0");
  });
  return hash.digest("hex").slice(0, 12);
}

function versionPayload() {
  const headPath = path.join(root, ".git", "HEAD");
  let gitCommit = process.env.GCS_GIT_COMMIT || "unknown";
  try {
    const head = fs.readFileSync(headPath, "utf8").trim();
    if (head.startsWith("ref:")) {
      gitCommit = fs.readFileSync(path.join(root, ".git", head.split(" ")[1]), "utf8").trim().slice(0, 8);
    } else {
      gitCommit = head.slice(0, 8);
    }
  } catch (_) {}
  const version = process.env.GCS_VERSION || frontendHash();
  return {
    app: "Tianxun UAV Ground Station",
    version,
    buildTime: bootTime,
    gitCommit,
    frontendHash: frontendHash(),
    backend: "node-static-telemetry",
    files: Object.fromEntries(versionFiles.map((file) => [file, fileHash(file)]))
  };
}

function sendJson(response, status, data) {
  const payload = JSON.stringify(data);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-App-Version": versionPayload().version
  });
  response.end(payload);
}

function staticCacheHeaders(filePath, requestUrl) {
  const parsed = new URL(requestUrl, "http://127.0.0.1");
  const rel = path.relative(root, filePath).replace(/\\/g, "/");
  const ext = path.extname(filePath).toLowerCase();
  if (path.basename(filePath) === "index.html" || ext === ".html") {
    return {
      "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
      "Pragma": "no-cache",
      "Expires": "0"
    };
  }
  if (parsed.searchParams.has("v") || rel.startsWith("assets/") || rel.startsWith("vendor/") || rel.startsWith("reports/")) {
    return { "Cache-Control": "public, max-age=31536000, immutable" };
  }
  return {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0"
  };
}

function versionIndex(filePath, data) {
  if (path.basename(filePath) !== "index.html") return data;
  const version = frontendHash();
  return Buffer.from(data.toString("utf8")
    .replace(/(href="styles\.css)(\?v=[^"]*)?(")/g, `$1?v=${version}$3`)
    .replace(/(src="app\.js)(\?v=[^"]*)?(")/g, `$1?v=${version}$3`));
}

const server = http.createServer((request, response) => {
  const urlPath = decodeURIComponent(request.url.split("?")[0]);

  if ((urlPath === "/api/version" || urlPath === "/version.json") && request.method === "GET") {
    sendJson(response, 200, versionPayload());
    return;
  }

  if (urlPath === "/api/telemetry/stream" && request.method === "GET") {
    response.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive"
    });
    response.write("retry: 1000\n\n");
    telemetryClients.add(response);
    if (latestTelemetry) {
      response.write(`data: ${JSON.stringify(latestTelemetry)}\n\n`);
    }
    request.on("close", () => telemetryClients.delete(response));
    return;
  }

  if (urlPath === "/api/telemetry" && request.method === "POST") {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 100000) request.destroy();
    });
    request.on("end", () => {
      try {
        const data = JSON.parse(body);
        if (!data || typeof data !== "object" || Array.isArray(data)) {
          throw new Error("telemetry object is required");
        }
        latestTelemetry = { ...data, receivedAt: Date.now() };
        const packet = `data: ${JSON.stringify(latestTelemetry)}\n\n`;
        telemetryClients.forEach((client) => client.write(packet));
        sendJson(response, 202, { ok: true });
      } catch (error) {
        sendJson(response, 400, { ok: false, error: error.message });
      }
    });
    return;
  }

  if (urlPath === "/api/telemetry" && request.method === "GET") {
    sendJson(response, 200, latestTelemetry || {});
    return;
  }

  const requestedPath = urlPath === "/" ? "index.html" : urlPath.replace(/^\/+/, "");
  const filePath = path.resolve(root, requestedPath);

  if (!filePath.startsWith(root)) {
    response.writeHead(403);
    response.end("Forbidden");
    return;
  }

  fs.readFile(filePath, (error, data) => {
    if (error) {
      response.writeHead(404);
      response.end("Not found");
      return;
    }
    const payload = versionIndex(filePath, data);
    response.writeHead(200, {
      "Content-Type": types[path.extname(filePath)] || "application/octet-stream",
      "X-Content-Type-Options": "nosniff",
      "X-App-Version": versionPayload().version,
      "X-Frontend-Hash": frontendHash(),
      ...staticCacheHeaders(filePath, request.url)
    });
    response.end(payload);
  });
});

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    console.log(`端口 ${port} 已有服务运行，正在打开现有页面：${url}`);
    openBrowser();
    return;
  }
  throw error;
});

server.listen(port, "127.0.0.1", () => {
  console.log(`UAV dashboard: ${url}`);
  openBrowser();
});
