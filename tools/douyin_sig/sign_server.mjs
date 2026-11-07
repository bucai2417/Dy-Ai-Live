/**
 * 可选的外部抖音签名服务接口，通过 JSON 行请求和响应。
 * webmssdk.js 由使用者自行合法配置，不随公开源码分发。
 * 来源与许可边界见本目录 README.md 和根目录 THIRD_PARTY_NOTICES.md。
 * 请求 {"id":1,"op":"sign","params":{"live_id":"1"}}
 * 响应 {"id":1,"ok":true,"signature":"...","userUniqueId":"..."}
 * 请求 {"id":2,"op":"ping"}
 * 响应 {"id":2,"ok":true,"pong":true}
 */
import crypto from 'node:crypto';
import { createInterface } from 'node:readline';
import { get_sign } from './webmssdk.js';

/** 抖音要求每次连接带一个随机的 user_unique_id */
function getUserUniqueId() {
  return (
    Math.floor(Math.random() * (7999999999999999999 - 7300000000000000000)) +
    7300000000000000000
  ).toString();
}

/** 把参数字典拼成待签字符串再 md5，这是签名函数的输入 */
function getXMsStub(params) {
  const joined = Object.entries(params)
    .map(([k, v]) => `${k}=${v}`)
    .join(',');
  return crypto.createHash('md5').update(joined).digest('hex');
}

function sign(params) {
  const stub = getXMsStub(params);
  try {
    return get_sign(stub);
  } catch {
    // 签名函数内部出错时给个占位，服务端会收到 401 从而知道要退避
    return '00000000';
  }
}

const rl = createInterface({ input: process.stdin, terminal: false });

rl.on('line', (line) => {
  const text = line.trim();
  if (!text) return;

  let req;
  try {
    req = JSON.parse(text);
  } catch {
    return; // 半行/脏数据直接丢弃，别让子进程崩
  }

  const id = req.id ?? 0;
  try {
    if (req.op === 'ping') {
      process.stdout.write(JSON.stringify({ id, ok: true, pong: true }) + '\n');
      return;
    }
    if (req.op === 'sign') {
      const params = req.params || {};
      const userUniqueId = params.user_unique_id || getUserUniqueId();
      params.user_unique_id = userUniqueId;
      const signature = sign(params);
      process.stdout.write(
        JSON.stringify({ id, ok: true, signature, userUniqueId }) + '\n'
      );
      return;
    }
    process.stdout.write(
      JSON.stringify({ id, ok: false, error: `unknown op: ${req.op}` }) + '\n'
    );
  } catch (err) {
    process.stdout.write(
      JSON.stringify({ id, ok: false, error: String(err?.message || err) }) + '\n'
    );
  }
});

// 父进程退出后 stdin 会关闭，这里跟着退出，避免留孤儿进程
rl.on('close', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));
