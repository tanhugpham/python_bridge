// Phase 9G: drive the real host-controller page in headless Chrome over CDP.
// Usage: node phase9g-cdp.js <plan.json>
// Chrome must already listen on 127.0.0.1:9222 (see phase9g-run.ps1).
const fs = require('node:fs');
const http = require('node:http');

const PORT = Number(process.env.CDP_PORT || 9222);

function request(method, path) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port: PORT, path, method }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => { body += chunk; });
      res.on('end', () => resolve(body ? JSON.parse(body) : null));
    });
    req.on('error', reject);
    req.end();
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && this.pending.has(msg.id)) {
        const done = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        done(msg);
      }
    };
  }

  send(method, params) {
    const id = ++this.id;
    return new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }

  async eval(expression) {
    const reply = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
      userGesture: true,
    });
    if (reply.result && reply.result.exceptionDetails) {
      return { error: reply.result.exceptionDetails.text || 'exception' };
    }
    const value = reply.result && reply.result.result ? reply.result.result.value : null;
    return { value };
  }
}

async function main() {
  const plan = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const targets = await request('GET', '/json/list');
  let target = targets.find((row) => row.type === 'page');
  if (!target) {
    target = await request('PUT', '/json/new?about:blank');
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onclose = reject;
  });
  const cdp = new Cdp(ws);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');
  const out = [];
  const log = (row) => {
    out.push(row);
    console.log(JSON.stringify(row));
  };

  for (const step of plan.steps) {
    if (step.op === 'navigate') {
      await cdp.send('Page.navigate', { url: step.url });
      await sleep(step.waitMs || 1500);
      log({ op: 'navigate', url: step.url });
    } else if (step.op === 'eval') {
      const result = await cdp.eval(step.expr);
      log({ op: 'eval', label: step.label || '', value: result.value, error: result.error });
    } else if (step.op === 'click') {
      const expr = "(function(){var el=document.getElementById('" + step.id + "');"
        + "if(!el){return 'missing';}el.click();return 'clicked';})()";
      const result = await cdp.eval(expr);
      log({ op: 'click', id: step.id, value: result.value });
      await sleep(step.waitMs || 400);
    } else if (step.op === 'clickText') {
      const expr = "(function(){var want=" + JSON.stringify(step.text) + ";"
        + "var rows=Array.prototype.slice.call(document.querySelectorAll('" + step.scope + " button'));"
        + "for(var i=0;i<rows.length;i++){if(rows[i].textContent.indexOf(want)>=0){rows[i].click();return rows[i].textContent;}}"
        + "return 'missing';})()";
      const result = await cdp.eval(expr);
      log({ op: 'clickText', text: step.text, value: result.value });
      await sleep(step.waitMs || 400);
    } else if (step.op === 'sleep') {
      await sleep(step.ms);
      log({ op: 'sleep', ms: step.ms });
    } else if (step.op === 'shot') {
      const reply = await cdp.send('Page.captureScreenshot', { format: 'png' });
      const data = reply.result && reply.result.data;
      if (data) {
        fs.writeFileSync(step.path, Buffer.from(data, 'base64'));
      }
      log({ op: 'shot', path: step.path, bytes: data ? data.length : 0 });
    } else if (step.op === 'waitFor') {
      const deadline = Date.now() + (step.timeoutMs || 15000);
      let value = null;
      while (Date.now() < deadline) {
        const result = await cdp.eval(step.expr);
        value = result.value;
        if (typeof value === 'string' && value.indexOf(step.contains) >= 0) {
          break;
        }
        await sleep(step.pollMs || 500);
      }
      log({ op: 'waitFor', contains: step.contains, value });
    }
  }
  fs.writeFileSync(plan.out, JSON.stringify(out, null, 2));
  ws.close();
}

main().catch((error) => {
  console.error('phase9g-cdp failed: ' + error.message);
  process.exit(1);
});
