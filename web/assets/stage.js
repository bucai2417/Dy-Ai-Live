/* ============================================================================
 * 大肥鱼 AI 直播 · 舞台脚本
 * 职责：WebSocket 实时总线 / Live2D 加载与口型驱动 / 弹幕渲染 / 音频播放与音量包络
 * ==========================================================================*/
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const stage = $('stage');

  /* ------------------------------ 全局状态 ------------------------------ */
  const State = {
    ws: null,
    connected: false,
    live2d: null,
    rig: null,           // 程序化形象实例（无 moc3 时使用）
    app: null,
    audioCtx: null,
    analyser: null,
    gain: null,
    voiceQueue: [],
    voiceGeneration: 0,
    voiceSource: null,
    cancelVoice: null,
    playing: false,
    soundOn: true,
    speaking: false,
    lipsyncOn: false,
    mouthOpen: 0,
    motionTimer: 0,
    lastSpeechAt: 0,
    stats: { danmu: 0, online: 0 },
    modelReady: false,
    mouse: { x: 0, y: 0 },
    stageConf: null,     // 服务端下发的舞台配置（模型/大小/位置）
    modelUrl: '',
    ingestSource: '',    // 当前接入方式，用于提示文案
    roomId: '',
    ballPit: [],         // 漂浮气泡的物理状态
    ballRaf: 0,
    rankCache: '',       // 上次排行榜签名，用于避免无谓重绘
  };

  /* OBS 浏览器源：URL 带 ?obs=1 时隐藏控件、放大内容 */
  const params = new URLSearchParams(location.search);
  if (params.get('obs') === '1') document.body.classList.add('obs');

  /* ============================== 气泡背景 ============================== */
  (function bubbles() {
    const host = $('bubbles');
    const N = window.innerWidth < 700 ? 14 : 26;
    for (let i = 0; i < N; i++) {
      const b = document.createElement('i');
      const size = 6 + Math.random() * 26;
      b.style.width = size + 'px';
      b.style.height = size + 'px';
      b.style.left = Math.random() * 100 + '%';
      b.style.animationDuration = 9 + Math.random() * 14 + 's';
      b.style.animationDelay = -Math.random() * 18 + 's';
      b.style.setProperty('--dx', (Math.random() * 80 - 40) + 'px');
      host.appendChild(b);
    }
  })();

  /* ============================== 音效与口型 ============================== */
  function ensureAudio() {
    if (State.audioCtx) return State.audioCtx;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    const ctx = new Ctx();
    State.audioCtx = ctx;
    State.analyser = ctx.createAnalyser();
    State.analyser.fftSize = 1024;
    State.analyser.smoothingTimeConstant = 0.55;
    State.gain = ctx.createGain();
    State.gain.gain.value = State.soundOn ? 1 : 0;
    State.analyser.connect(State.gain);
    State.gain.connect(ctx.destination);
    // 便于排查“有没有真声音”：OBS/自动化脚本可以读它
    window.__stageAnalyser = State.analyser;
    return ctx;
  }

  /** 播放一段 base64 mp3，并在播放期间用音量包络驱动口型。
   *  返回一个 Promise，播完才 resolve —— 保证多句不叠音、口型与语音对齐。 */
  async function playVoice(b64, generation) {
    if (!State.soundOn) {
      // 静音时也让口型按估算时长动，保持"看得到在说话"
      const d = estimateDuration(b64);
      simulateMouth(d);
      await wait(d * 1000);
      return;
    }
    const ctx = ensureAudio();
    if (!ctx) return;
    if (ctx.state === 'suspended') { try { await ctx.resume(); } catch (e) {} }
    if (generation !== State.voiceGeneration) return;

    try {
      const buf = base64ToArrayBuffer(b64);
      const audioBuf = await ctx.decodeAudioData(buf);
      if (generation !== State.voiceGeneration) return;
      const src = ctx.createBufferSource();
      State.voiceSource = src;
      src.buffer = audioBuf;
      src.connect(State.analyser);
      State.lipsyncOn = true;
      await new Promise((resolve) => {
        src.onended = resolve;
        src.start(ctx.currentTime);
      });
      src.disconnect();
      if (State.voiceSource === src) State.voiceSource = null;
      if (generation === State.voiceGeneration) State.lipsyncOn = false;
    } catch (err) {
      if (generation !== State.voiceGeneration) return;
      console.warn('音频解码失败，退化为口型模拟', err);
      const d = estimateDuration(b64);
      simulateMouth(d);
      await wait(d * 1000);
    }
  }

  function wait(ms) { return new Promise((r) => setTimeout(r, ms)); }

  function base64ToArrayBuffer(b64) {
    const bin = atob(b64);
    const len = bin.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return bytes.buffer;
  }

  function estimateDuration(b64) {
    // mp3 约 16KB/s（64kbps）粗略估计，够用于静音模式
    return Math.min(Math.max(b64.length * 0.75 / 8000, 0.8), 20);
  }

  function stopVoice() {
    State.voiceGeneration += 1;
    State.voiceQueue.length = 0;
    if (State.voiceSource) {
      try { State.voiceSource.stop(); } catch (error) {}
      State.voiceSource = null;
    }
    if (State.cancelVoice) State.cancelVoice();
    State.speaking = false;
    State.lipsyncOn = false;
    State.mouthOpen = 0;
    simUntil = 0;
    duckMusic(false);
  }

  /* 静音兜底：用正弦噪声模拟口型开合 */
  let simUntil = 0;
  function simulateMouth(sec) { simUntil = performance.now() + sec * 1000; }

  /* 口型参数驱动：把音量映射成 ParamMouthOpenY */
  const dataArr = () => {
    if (!State.analyser) return null;
    if (!State._buf) State._buf = new Uint8Array(State.analyser.fftSize);
    State.analyser.getByteTimeDomainData(State._buf);
    return State._buf;
  };

  function tick() {
    requestAnimationFrame(tick);
    stepMouth();
  }

  /** 口型/表情的单步推进，抽出来供后台定时器复用。 */
  function stepMouth() {
    const now = performance.now();
    let target = 0;

    if (State.lipsyncOn && State.analyser) {
      const buf = dataArr();
      if (buf) {
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = (buf[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / buf.length);
        target = Math.min(1, rms * 4.2);
      }
    } else if (now < simUntil) {
      // 静音/无音频时用近似节奏
      target = 0.25 + 0.55 * Math.abs(Math.sin(now / 90));
    }

    // 平滑 + 加一点随机抖动，看起来不像机器
    State.mouthOpen += (target - State.mouthOpen) * (target > State.mouthOpen ? 0.55 : 0.22);
    if (target > 0.05 && Math.random() < 0.12) State.mouthOpen *= 0.8;

    applyMouth(State.mouthOpen);
    if (State.rig) {
      // 程序化形象：视线跟随鼠标/触摸
      try { State.rig.focus(State.mouse.x, State.mouse.y); } catch (e) {}
    } else if (State.live2d && State.modelReady) {
      // 轻微视线跟随鼠标/触摸
      try {
        State.live2d.focus(State.mouse.x * 0.6, State.mouse.y * 0.6);
      } catch (e) { /* ignore */ }
    }
  }

  /* 浏览器把标签页切到后台时会完全暂停 requestAnimationFrame，
     但直播画面要一直放着（OBS 抓的就是这个页面），所以隐藏时改用
     低频定时器继续推进嘴型，切回来再交还给 rAF。 */
  let bgTimer = 0;
  function startBgFallback() {
    const set = (v) => { bgTimer = v; window.__bgTimer = v; };  // 挂全局便于排查
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (bgTimer) return;
        set(setInterval(stepMouth, 120));     // ~8fps，够用且几乎不耗电
      } else if (bgTimer) {
        clearInterval(bgTimer);
        set(0);
      }
    });
    window.__bgState = () => ({ hidden: document.hidden, timer: !!bgTimer });
  }

  function applyMouth(v) {
    if (!State.modelReady || !State.live2d) {
      // Live2D 未就绪时，让 SVG 占位鱼的嘴动起来
      document.body.classList.toggle('speaking', v > 0.08);
      return;
    }
    // 程序化形象自带嘴形映射，直接喂开口度，避免每帧覆盖表情
    if (State.rig) {
      try { State.rig.setMouth(Math.min(1, v * 1.35)); } catch (e) {}
      document.body.classList.toggle('speaking', v > 0.08);
      return;
    }
    const core = State.live2d.internalModel?.coreModel;
    if (!core) return;
    const val = Math.min(1, v * 1.35);
    try {
      core.setParameterValueById(State.mouthParam || 'ParamMouthOpenY', val);
      // 嘴型随开口度变化，比只开合更自然
      core.setParameterValueById('ParamMouthForm', 0.2 + val * 0.55);
    } catch (e) { /* 参数名不同则忽略 */ }
  }

  /* ============================== Live2D 加载 ============================== */
  const DEFAULT_MODEL = '/static/models/haru/haru_greeter_t03.model3.json';

  /* ---------------------- 程序化形象（rig）适配 ----------------------
   * 没拿到 .moc3 之前，用 rig.js 里的自研骨骼顶替 Live2D。
   * 判定方式：stage.model 以 rig: 开头，例如 rig:whale-deepy。
   * 之所以做成「同样的 motion/expression/focus 接口」，是为了将来换成真
   * moc3 时，这里一行都不用改。
   */
  const RIG_PREFIX = 'rig:';
  const RIG_DEFAULT = 'whale-deepy';

  function isRigModel(url) { return String(url || '').includes(RIG_PREFIX); }

  function rigName(url) {
    const s = String(url || '');
    const i = s.indexOf(RIG_PREFIX);
    return i >= 0 ? (s.slice(i + RIG_PREFIX.length) || RIG_DEFAULT) : RIG_DEFAULT;
  }

  /** 舞台配置变化时重新摆放；模型换了则重新加载。 */
  function applyStage(conf, forceReload) {
    if (!conf) return;
    const prev = State.stageConf;
    State.stageConf = conf;
    // 排行显示条数/特效开关可能变了，作废缓存让下一次统计推送必然重绘
    State.rankCache = '';

    // 画面开关
    const dl = $('danmuList');
    if (dl) dl.style.display = conf.show_danmu === false ? 'none' : '';
    const sub = $('subtitle');
    if (sub) sub.dataset.hide = conf.show_subtitle === false ? '1' : '';

    // 信息块开关：主播可能只想在自己后台看，不想全推到画面上
    const toggle = (id, on) => {
      const el = $(id);
      if (el) el.style.display = on === false ? 'none' : '';
    };
    toggle('datapods', conf.show_datapods);
    toggle('rankboard', conf.show_ranking);
    toggle('ballpit', conf.show_ballpit);
    const sb = document.querySelector('.statebar');
    if (sb) sb.style.display = conf.show_badges === false ? 'none' : '';

    // 漂浮气泡手感：速度/数量/大小/是否回弹，全由后台控制
    const pit = $('ballpit');
    if (pit) {
      const sz = Math.max(0.6, Math.min(1.8, Number(conf.ball_size ?? 1)));
      pit.style.setProperty('--ball-scale', sz);
      pit.querySelectorAll('.ball').forEach((b) => {
        b.style.transformOrigin = 'left top';
      });
    }
    // 关掉气泡或数量改成 0 时，把已有气泡清掉
    if (conf.show_ballpit === false || Number(conf.ball_max ?? 8) === 0) {
      clearBalls();
    } else {
      // 数量上限变小了，立刻裁掉多出来的
      trimBalls(Number(conf.ball_max ?? 8));
    }

    // 程序化形象用 rig: 前缀，其余按 Live2D 模型文件路径处理
    const raw = conf.model || '';
    const want = raw.startsWith(RIG_PREFIX) ? raw : (raw ? `/static/models/${raw}` : DEFAULT_MODEL);
    const changed = want !== State.modelUrl;
    State.modelUrl = want;

    if (!State.live2d) return;                   // 还没加载完，等 initLive2D 自己读
    if (changed && forceReload !== false) {
      loadModel(want);
      return;
    }
    if (State.rig) applyRigLook();
    else layoutModel();
    applyModelLook();
  }

  /** 透明度 / 水平翻转，独立于位置缩放，改这两个不必重排。 */
  function applyModelLook() {
    const m = State.live2d;
    const c = State.stageConf;
    if (!m || !c) return;
    try {
      m.alpha = Math.max(0.1, Math.min(1, Number(c.opacity ?? 1)));
      const s = Math.abs(m.scale.x) || 1;
      m.scale.x = c.flip ? -s : s;
    } catch (e) { /* ignore */ }
  }

  /** 换模型：卸载旧的再加载新的，失败则回到占位形象。 */
  async function loadModel(url) {
    State.modelReady = false;
    // 先分流：程序化形象不依赖 PIXI，不能因为 app 还没建好就被拦在门外
    if (isRigModel(url)) {
      try {
        if (State.live2d && State.app) {
          try { State.app.stage.removeChild(State.live2d); } catch (e) {}
        }
        if (State.live2d && typeof State.live2d.destroy === 'function') {
          try { State.live2d.destroy(); } catch (e) {}
        }
        State.live2d = null;
        if (State.rig) { try { State.rig.destroy(); } catch (e) {} State.rig = null; }
      } catch (e) { /* ignore */ }
      await loadRig(url);
      return;
    }

    const app = State.app;
    if (!app) return;
    try {
      if (State.live2d) {
        try { app.stage.removeChild(State.live2d); State.live2d.destroy(); } catch (e) {}
        State.live2d = null;
      }
      if (State.rig) { try { State.rig.destroy(); } catch (e) {} State.rig = null; }

      const model = await PIXI.live2d.Live2DModel.from(url, { autoInteract: true, autoUpdate: true });
      State.live2d = model;
      app.stage.addChild(model);
      layoutModel();
      applyModelLook();
      calibrateMouthParam(model);
      State.modelReady = true;
      $('fishFallback').classList.remove('show');
      $('canvasHost').style.display = '';
      try { model.motion('Idle'); } catch (e) {}
      console.log('[fish] 模型已加载', url);
    } catch (err) {
      console.error('Live2D 加载失败', err);
      showFallback(String(err && err.message ? err.message : err));
    }
  }

  /** 加载程序化形象。加载失败自动退回 Live2D 示例模型，直播不中断。
   *
   *  rig:whale-deepy → 手绘 SVG 分层（局部骨骼完整，但画面较简陋）
   *  rig:whale-art   → AI 立绘（画面好，参数驱动整体变换）
   *  两者接口完全一致，下面这套驱动代码一行都不用改。 */
  async function loadRig(url) {
    const host = $('canvasHost');
    try {
      const name = rigName(url);
      const useArt = name === 'whale-art';
      if (useArt && !window.WhaleArt) throw new Error('art.js 未加载');
      if (!useArt && !window.WhaleRig) throw new Error('rig.js 未加载');
      const w = Number(State.stageConf?.scale) || 1;
      const rig = useArt ? new window.WhaleArt(host) : new window.WhaleRig(host);
      rig.motion('idle');
      rig.expression(1);
      rig.start();
      State.rig = rig;
      // 复用同一套「模型」状态：口型探测拿得到 coreModel
      State.live2d = rig;
      State.modelReady = true;
      State.mouthParam = 'ParamMouthOpenY';
      $('fishFallback').classList.remove('show');
      host.style.display = '';
      host.dataset.rig = name;
      applyRigLook(w);
      console.log('[fish] 程序化形象已加载', name);
    } catch (err) {
      console.error('程序化形象加载失败，回退示例模型', err);
      try { State.rig?.destroy(); } catch (e) {}
      State.rig = null;
      State.live2d = null;
      await loadModel(DEFAULT_MODEL);
    }
  }

  /** rig 的大小/位置：复用后台的 scale / offset / flip 三个配置。
   *
   *  注意两处换算，都是踩过的坑：
   *  1) 后台的 scale 是按示例模型 haru（半身）调的，rig 是全身立绘，
   *     直接沿用会小一圈，所以乘一个 RIG_SCALE_GAIN 让它撑满画面。
   *  2) haru 用 PIXI 坐标（y 向下为正），offset_y 为负是「往上」；
   *     CSS 的 bottom 为正才是往上，所以这里要取负号，否则人物会被推出屏幕底部。
   */
  const RIG_SCALE_GAIN = 1.5;

  function applyRigLook(scale) {
    const wrap = document.querySelector('.whale-rig-wrap');
    const c = State.stageConf || {};
    if (!wrap) return;
    const s = Number(c.scale ?? scale ?? 1) || 1;
    const portrait = window.innerHeight > window.innerWidth;
    const base = portrait ? 90 : 84;          // 竖屏/横屏的基准占比
    const pct = Math.max(35, Math.min(118, base * s * RIG_SCALE_GAIN));
    wrap.style.height = pct + '%';
    wrap.style.bottom = (-(Number(c.offset_y) || 0) * 100) + '%';
    wrap.style.left = (50 + (Number(c.offset_x) || 0) * 100) + '%';
    wrap.style.transform = `translateX(-50%) ${c.flip ? 'scaleX(-1)' : ''}`;
    wrap.style.opacity = String(Math.max(0.1, Math.min(1, Number(c.opacity ?? 1))));
  }

  async function initLive2D() {
    const host = $('canvasHost');
    // 程序化形象不需要 PIXI，先分流，避免没装 pixi 时直接判死
    const url0 = State.modelUrl || DEFAULT_MODEL;
    if (isRigModel(url0)) {
      window.addEventListener('resize', () => applyRigLook());
      window.addEventListener('orientationchange', () => setTimeout(() => applyRigLook(), 260));
      await loadModel(url0);
      return;
    }
    if (!window.PIXI || !window.PIXI.live2d) {
      showFallback('Live2D 库未加载');
      return;
    }
    try {
      const app = new PIXI.Application({
        backgroundAlpha: 0,
        antialias: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
        autoDensity: true,
        resizeTo: stage,
      });
      host.appendChild(app.view);
      State.app = app;

      window.addEventListener('resize', layoutModel);
      window.addEventListener('orientationchange', () => setTimeout(layoutModel, 260));

      const url = State.modelUrl || DEFAULT_MODEL;
      await loadModel(url);
      State.modelReady = true;
      // 加载后若已有配置，按配置再摆一次（防止配置比模型先到）
      if (State.rig) applyRigLook();
      else layoutModel();
      applyModelLook();
      try {
        State.live2d?.internalModel.motionManager.expressionManager?.setRandomExpression?.();
      } catch (e) {}
    } catch (err) {
      console.error('Live2D 初始化失败', err);
      showFallback(String(err && err.message ? err.message : err));
    }
  }

  /** 检测模型真正的口型参数名（Cubism3/4/5 命名可能不同），并把嘴"合上"。 */
  function calibrateMouthParam(model) {
    const core = model.internalModel?.coreModel;
    const candidates = ['ParamMouthOpenY', 'ParamMouthOpen', 'ParamMouth'];
    State.mouthParam = null;
    if (!core) return;
    try {
      if (typeof core.getParameterIndex === 'function') {
        for (const p of candidates) {
          if (core.getParameterIndex(p) >= 0) { State.mouthParam = p; break; }
        }
      } else {
        const ids = (core._parameterIds || core.parameters?.ids || []);
        State.mouthParam = candidates.find((p) => ids.includes(p)) || null;
      }
    } catch (e) { /* ignore */ }
    if (!State.mouthParam) State.mouthParam = 'ParamMouthOpenY';
    // 合上嘴，避免加载后一直张着
    try { core.setParameterValueById(State.mouthParam, 0); } catch (e) {}
    console.log('[fish] mouth param =', State.mouthParam);
  }

  /** 按屏幕比例摆放模型，再叠加用户在后台调的大小和位置。
   *
   *  auto_layout=true：竖屏贴近底部放大、横屏居中偏右（默认审美摆法）
   *  auto_layout=false：完全按 scale/offset 来，适合手动精调
   *  offset_x/offset_y 是「屏幕宽高的比例」，所以换分辨率不会跑位。
   */
  function layoutModel() {
    const m = State.live2d;
    if (!m || !State.app) return;
    if (State.rig) { applyRigLook(); return; }   // 程序化形象走 CSS 布局
    const c = State.stageConf || {};
    const W = State.app.renderer.width / (State.app.renderer.resolution || 1);
    const H = State.app.renderer.height / (State.app.renderer.resolution || 1);
    const portrait = H > W;
    const base = Math.max(m.internalModel.width, m.internalModel.height) || 1;

    let scale, x, y;
    if (c.auto_layout === false) {
      // 手动模式：以「撑满短边」为 1.0 基准，便于理解
      scale = (Math.min(W, H) * 1.5) / base * (Number(c.scale) || 1);
      x = W * 0.5;
      y = H * 1.0;
    } else {
      const auto = portrait ? (W * 1.42) / base : (H * 1.16) / base;
      scale = auto * (Number(c.scale) || 1);
      x = W * (portrait ? 0.5 : 0.52);
      y = H * (portrait ? 0.995 : 1.005);
    }

    // 偏移量按屏幕尺寸换算成像素
    x += (Number(c.offset_x) || 0) * W;
    y += (Number(c.offset_y) || 0) * H;

    m.scale.set(scale);
    m.anchor.set(0.5, 1);
    m.position.set(x, y);
    applyModelLook();
  }

  function showFallback(reason) {
    console.warn('使用占位形象：', reason);
    $('fishFallback').classList.add('show');
    const host = $('canvasHost');
    if (host) host.style.display = 'none';
  }

  /* 触发表情/动作 */
  /** 后端传来的动作名 → rig 动作名。深浅两套模型各用各的，互不影响。 */
  const RIG_MOTION_ALIAS = {
    talk: 'talk', happy: 'happy', shake: 'shake',
    idle: 'idle', wave: 'wave', nod: 'nod', shy: 'shy',
    sway: 'sway', spout: 'spout',
  };
  /** 动作名 → 默认表情（rig 用；Live2D 仍走原来的随机逻辑） */
  const RIG_MOTION_EXPR = {
    happy: 2, shake: 3, shy: 4, wave: 2, nod: 1, spout: 7, talk: 1,
  };

  function playMotion(name) {
    const m = State.live2d;
    if (!m || !State.modelReady) {
      bumpBubble(name);
      return;
    }
    const now = Date.now();
    if (now - State.motionTimer < 900) return;
    State.motionTimer = now;

    // 程序化形象：动作库里有的直接播，没有的退化成 talk
    if (State.rig) {
      const key = RIG_MOTION_ALIAS[name] || (window.WHALE_RIG?.MOTIONS[name] ? name : 'talk');
      try {
        State.rig.motion(key);
        const e = RIG_MOTION_EXPR[key];
        if (e != null) State.rig.expression(e);
      } catch (e) {}
      bumpBubble(name);
      return;
    }

    try {
      if (name === 'happy') {
        m.motion('Tap');
        m.expression(1);
      } else if (name === 'shake') {
        // 模型中不一定有专门的"抖"动作，用轻推 + 随机表情模拟
        m.motion('Idle');
        m.expression(Math.floor(Math.random() * 3));
        nudgeModel();
      } else {
        m.expression(Math.floor(Math.random() * 3));
      }
    } catch (e) {}
    bumpBubble(name);
  }

  /** 触发指定表情（后台可配 0-7 或 auto，由服务端决定具体值）。 */
  function playExpression(v) {
    const m = State.rig || State.live2d;
    if (!m || !State.modelReady || v === '' || v == null) return;
    const idx = Number(v);
    if (!Number.isFinite(idx)) return;
    try { m.expression(idx); } catch (e) { /* 模型表情数不同则忽略 */ }
  }

  /** 模型左右轻晃一下，给"惊讶/被夸"这类反应一个可见的动作。 */
  let nudge = 0;
  function nudgeModel() {
    const m = State.live2d;
    if (!m || State.rig) return;      // rig 有自己的 shake 动作，不需要外挂位移
    nudge = 1;
    const startX = m.position.x;
    const t0 = performance.now();
    const tickN = () => {
      if (!State.live2d) return;
      const t = (performance.now() - t0) / 420;
      if (t >= 1) { m.position.x = startX; nudge = 0; return; }
      m.position.x = startX + Math.sin(t * Math.PI * 4) * 14 * (1 - t);
      requestAnimationFrame(tickN);
    };
    requestAnimationFrame(tickN);
  }

  /* ============================== 视觉特效 ============================== */
  /** effect 由后台规则指定：confetti 彩带 / hearts 爱心 / sparkle 闪光 / flash 白闪 */
  function playEffect(kind) {
    if (!kind) return;
    const layer = $('fxLayer');
    if (!layer) return;
    if (kind === 'confetti') return confetti(layer);
    if (kind === 'hearts') return hearts(layer);
    if (kind === 'sparkle') return sparkle(layer);
    if (kind === 'flash') return flashScreen();
  }

  const pick = (a) => a[Math.floor(Math.random() * a.length)];

  function confetti(layer) {
    const colors = ['#39d0ff', '#7a6bff', '#ffd23d', '#3ddc84', '#ff6b9d', '#ffbe3d'];
    for (let i = 0; i < 36; i++) {
      const p = document.createElement('i');
      p.className = 'fx-confetti';
      p.style.left = Math.random() * 100 + '%';
      p.style.background = pick(colors);
      p.style.setProperty('--dx', (Math.random() * 160 - 80) + 'px');
      p.style.setProperty('--rot', (Math.random() * 720 - 360) + 'deg');
      p.style.animationDelay = Math.random() * 0.25 + 's';
      p.style.animationDuration = 1.5 + Math.random() * 1.1 + 's';
      layer.appendChild(p);
      setTimeout(() => p.remove(), 3000);
    }
  }

  function hearts(layer) {
    const chars = ['❤️', '💖', '💗', '💕', '🫶'];
    for (let i = 0; i < 14; i++) {
      const p = document.createElement('i');
      p.className = 'fx-heart';
      p.textContent = pick(chars);
      p.style.left = 30 + Math.random() * 40 + '%';
      p.style.fontSize = 16 + Math.random() * 22 + 'px';
      p.style.setProperty('--dx', (Math.random() * 120 - 60) + 'px');
      p.style.animationDelay = Math.random() * 0.4 + 's';
      p.style.animationDuration = 1.8 + Math.random() * 1.2 + 's';
      layer.appendChild(p);
      setTimeout(() => p.remove(), 3400);
    }
  }

  function sparkle(layer) {
    for (let i = 0; i < 16; i++) {
      const p = document.createElement('i');
      p.className = 'fx-sparkle';
      p.style.left = 25 + Math.random() * 50 + '%';
      p.style.top = 20 + Math.random() * 55 + '%';
      p.style.animationDelay = Math.random() * 0.5 + 's';
      layer.appendChild(p);
      setTimeout(() => p.remove(), 1600);
    }
  }

  function flashScreen() {
    const el = document.createElement('div');
    el.className = 'fx-flash';
    document.getElementById('stage').appendChild(el);
    requestAnimationFrame(() => el.classList.add('on'));
    setTimeout(() => { el.classList.remove('on'); setTimeout(() => el.remove(), 400); }, 120);
  }

  function bumpBubble(name) {
    if (name !== 'happy') return;
    const el = document.createElement('div');
    el.className = 'bubble';
    el.textContent = ['谢谢老板！', '嘿嘿', '开心！', '爱了爱了'][Math.floor(Math.random() * 4)];
    const rect = $('avatarWrap').getBoundingClientRect();
    el.style.left = rect.width * 0.5 + (Math.random() * 60 - 30) + 'px';
    el.style.top = rect.height * 0.28 + 'px';
    $('avatarWrap').appendChild(el);
    requestAnimationFrame(() => el.classList.add('on'));
    setTimeout(() => { el.classList.remove('on'); setTimeout(() => el.remove(), 300); }, 1400);
  }

  /* ============================== 弹幕渲染 ============================== */
  // 弹幕条数随可用空间调整：竖屏 / 矮屏都要少放，避免被裁切
  const MAX_DANMU = () => {
    if (window.innerHeight < 520) return 4;          // 手机横屏、小窗
    if (window.innerHeight > window.innerWidth) return 5; // 手机竖屏
    return 10;
  };

  /** 没有收到真实弹幕时给一个明确提示，避免观众以为画面卡死了。
   *
   *  这里绝不伪造弹幕：显示的每一条都来自真实直播间。
   */
  function updateEmptyHint() {
    const list = $('danmuList');
    if (!list) return;
    const old = list.querySelector('.empty-hint');
    if (State.stats.danmu > 0) {
      if (old) old.remove();
      return;
    }
    if (old) return;

    const el = document.createElement('div');
    el.className = 'empty-hint';
    const mode = State.ingestSource || '';
    if (mode !== 'douyin') {
      el.textContent = '当前不是抖音真实接入，请在后台把「接入方式」改为抖音直播间';
    } else if (!State.roomId) {
      el.textContent = '还没填直播间房间号，请在后台「弹幕接入」里填写后保存';
    } else {
      el.textContent = `正在等待直播间 ${State.roomId} 的真实弹幕…（开播后弹幕才会出现）`;
    }
    list.appendChild(el);
  }

  function pushDanmu(ev, historical = false) {
    if (!historical && ev.kind === 'chat') State.stats.danmu++;
    $('danmuNum').textContent = State.stats.danmu;

    const list = $('danmuList');
    const hint = list.querySelector('.empty-hint');
    if (hint) hint.remove();

    const el = document.createElement('div');
    el.className = 'item ' + (ev.kind || 'chat');
    el.dataset.id = ev.id;

    let body = '';
    if (ev.kind === 'gift') {
      body = `<span class="u">${esc(ev.userName)}</span><span class="t">🎁 ${esc(ev.giftName)} ×${ev.giftCount}</span>`;
      if (!historical) floatGift(ev);
    } else if (ev.kind === 'enter') {
      body = `<span class="u">${esc(ev.userName)}</span><span class="t">来了</span>`;
    } else if (ev.kind === 'like') {
      body = `<span class="u">${esc(ev.userName)}</span><span class="t">点了 ${ev.giftCount} 个赞</span>`;
    } else if (ev.kind === 'follow') {
      body = `<span class="u">${esc(ev.userName)}</span><span class="t">关注了主播</span>`;
    } else {
      body = `<span class="u">${esc(ev.userName)}</span><span class="t">${esc(ev.text)}</span>`;
    }
    el.innerHTML = body;
    list.appendChild(el);

    // 超上限的旧弹幕先淡出再移除，避免整列突然上跳（看着像在循环重播）。
    // 正在淡出的不再计入数量，否则淡出那 240ms 里会连着标好几条。
    const alive = () => [...list.children].filter((n) => !n.classList.contains('out'));
    let overflow = alive().length - MAX_DANMU();
    while (overflow-- > 0) {
      const old = alive()[0];
      if (!old) break;
      old.classList.add('out');
      setTimeout(() => old.remove(), 240);
    }
  }

  function markAnswered(id) {
    const el = document.querySelector(`.danmu .item[data-id="${id}"]`);
    if (!el) return;
    el.classList.add('answered');
    const t = document.createElement('span');
    t.className = 'tagx';
    t.textContent = '已回';
    el.appendChild(t);
  }

  function floatGift(ev) {
    const layer = $('floatLayer');
    const el = document.createElement('div');
    el.className = 'floatGift';
    el.textContent = `${ev.userName} 送出 ${ev.giftName} ×${ev.giftCount}`;
    el.style.top = 15 + Math.random() * 45 + '%';
    layer.appendChild(el);
    setTimeout(() => el.remove(), 4400);
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /* ============================== 今日数据气泡 ============================== */
  /** 数字滚动动画：数值变大时滚上去，观感比直接跳变好。 */
  function animateNum(el, from, to, fmt) {
    if (!el) return;
    const dur = 520;
    const t0 = performance.now();
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur);
      // easeOutCubic
      const e = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(from + (to - from) * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  const fmtInt = (v) => {
    const n = Math.round(v);
    // 上万用「万」，避免长数字撑破气泡
    if (n >= 10000) return (n / 10000).toFixed(n >= 100000 ? 0 : 1) + '万';
    return String(n);
  };
  const fmtMoney = (v) => (v < 0.01 ? v.toFixed(3) : v.toFixed(2));

  const lastPod = { tokens: 0, cost: 0, chat: 0 };

  function renderStats(st) {
    if (!st) return;

    const tokEl = $('podTokensVal');
    const costEl = $('podCostVal');
    const chatEl = $('podChatVal');

    if (tokEl) {
      animateNum(tokEl, lastPod.tokens, st.tokens || 0, fmtInt);
      lastPod.tokens = st.tokens || 0;
      // 估算值加个 ~ 前缀，让运营知道不是账单级精确
      tokEl.dataset.est = st.estimated ? '1' : '';
    }
    if (costEl) {
      animateNum(costEl, lastPod.cost, st.cost || 0, fmtMoney);
      lastPod.cost = st.cost || 0;
    }
    if (chatEl) {
      animateNum(chatEl, lastPod.chat, st.replies || 0, fmtInt);
      lastPod.chat = st.replies || 0;
    }

    // 数值变化时轻微弹一下
    ['podTokensVal', 'podCostVal', 'podChatVal'].forEach((id) => {
      const el = $(id);
      const pod = el && el.closest('.pod');
      if (!pod) return;
      pod.classList.add('bump');
      setTimeout(() => pod.classList.remove('bump'), 220);
    });

    renderRanking(st.ranking || []);
    syncBalls(st.ranking || []);

    // 服务端是权威数据：用它校正本地即时累加，避免长期偏差
    localRank = (st.ranking || []).map((r, i) => ({ ...r, rank: i + 1 }));
  }

  /* ============================== 礼物排行榜 ============================== */
  const RANK_ICON = ['🥇', '🥈', '🥉'];

  function renderRanking(rows) {
    const box = $('rankList');
    if (!box) return;

    const c = State.stageConf || {};
    const cap = Math.max(1, Math.min(10, Number(c.rank_max ?? 3)));
    const withFx = c.rank_effect !== false;
    rows = (rows || []).slice(0, cap);

    // 用签名判断有没有变化，避免每 10 秒无谓重绘导致闪烁
    const sig = rows.map((r) => `${r.name}:${r.value}:${r.rank}`).join('|') + `#${cap}${withFx}`;
    if (sig === State.rankCache) return;
    State.rankCache = sig;

    if (!rows.length) {
      box.innerHTML = '<div class="rank-empty">还没有人送礼</div>';
      return;
    }

    box.innerHTML = '';
    rows.forEach((r) => {
      const row = document.createElement('div');
      // rank_effect 关掉就只保留普通行样式，不上金光/流光
      row.className = 'rank-row' + (withFx ? ' r' + r.rank : '');
      const initial = esc((r.name || '?').slice(0, 1));

      // 头像优先用真实图片，加载失败回退到首字
      const av = r.avatar
        ? `<img class="av" src="${esc(r.avatar)}" alt="" referrerpolicy="no-referrer"
             onerror="this.replaceWith(Object.assign(document.createElement('span'),
               {className:'av',textContent:'${initial}'}))" />`
        : `<span class="av">${initial}</span>`;

      row.innerHTML = `
        <span class="no">${withFx && r.rank <= 3 ? RANK_ICON[r.rank - 1] : r.rank}</span>
        ${av}
        <span class="who">${esc(r.name)}</span>
        <span class="val">${fmtInt(r.value)}</span>`;
      row.classList.add('flash');
      box.appendChild(row);
    });
  }

  /* ============================== 送礼观众漂浮气泡 ============================== */
  /** 每位送过礼的观众变成一个气泡，在画面里自由漂、碰边回弹。
   *
   *  为什么用 rAF 手写物理而不是 CSS 动画：
   *  需要真实的边界反弹与气泡互相碰撞，CSS 关键帧做不到"碰到墙改方向"。
   *  数量控制在 8 个以内，逐帧只改 transform，性能没问题。
   */
  const MAX_BALLS = 10;

  /** 清空所有漂浮气泡（后台关闭该功能时用）。 */
  function clearBalls() {
    const pit = $('ballpit');
    if (pit) pit.innerHTML = '';
    State.ballPit = [];
    if (State.ballRaf) {
      cancelAnimationFrame(State.ballRaf);
      State.ballRaf = 0;
    }
  }

  /** 把气泡数量裁到上限以内；超出的从末尾（排名最低）移除。 */
  function trimBalls(max) {
    const cap = Math.max(0, Math.min(MAX_BALLS, Number(max) || 0));
    while (State.ballPit.length > cap) {
      const b = State.ballPit.pop();
      if (b && b.el && b.el.parentNode) b.el.parentNode.removeChild(b.el);
    }
  }

  /** 当前生效的漂浮参数，全部来自后台配置。 */
  function ballConf() {
    const c = State.stageConf || {};
    return {
      speed: Math.max(0.1, Math.min(3, Number(c.ball_speed ?? 1))),
      max: Math.max(0, Math.min(MAX_BALLS, Number(c.ball_max ?? 8))),
      size: Math.max(0.6, Math.min(1.8, Number(c.ball_size ?? 1))),
      bounce: c.ball_bounce !== false,
      on: c.show_ballpit !== false,
    };
  }

  function syncBalls(rows) {
    const pit = $('ballpit');
    if (!pit) return;
    const bc = ballConf();
    if (!bc.on || bc.max === 0) {
      clearBalls();
      return;
    }

    // 只给真正送过礼、且榜上有名的人建气泡
    const want = rows.slice(0, bc.max);
    const wantNames = new Set(want.map((r) => r.name));

    // 移除已掉出榜单的
    for (let i = State.ballPit.length - 1; i >= 0; i--) {
      const b = State.ballPit[i];
      if (!wantNames.has(b.name)) {
        if (b.el && b.el.parentNode) b.el.parentNode.removeChild(b.el);
        State.ballPit.splice(i, 1);
      }
    }

    // 新增一个（同一用户只建一次，重复送礼只更新名次样式）
    want.forEach((r) => {
      let b = State.ballPit.find((x) => x.name === r.name);
      if (!b) {
        if (State.ballPit.length >= bc.max) return;
        b = createBall(r, pit);
        State.ballPit.push(b);
      }
      b.rank = r.rank;
      b.el.className = 'ball g' + r.rank;
      // 前三名光晕受「排行榜特效」开关控制
      if (State.stageConf && State.stageConf.rank_effect === false && r.rank <= 3) {
        b.el.classList.remove('g1', 'g2', 'g3');
      }
    });

    trimBalls(bc.max);
    if (!State.ballRaf && State.ballPit.length) startBallLoop();
  }

  function createBall(r, pit) {
    const el = document.createElement('div');
    el.className = 'ball g' + r.rank;
    const initial = esc((r.name || '?').slice(0, 1));
    const avatar = r.avatar
      ? `<img class="bav" src="${esc(r.avatar)}" alt="" referrerpolicy="no-referrer"
           onerror="this.replaceWith(Object.assign(document.createElement('span'),
             {className:'bav',textContent:'${initial}'}))" />`
      : `<span class="bav">${initial}</span>`;
    el.innerHTML = `${avatar}<span class="bnm">${esc(r.name)}</span>`;
    pit.appendChild(el);

    // 量一下未缩放的原始尺寸，物理碰撞按缩放后的实际占位算
    const bw = el.offsetWidth || 90;
    const bh = el.offsetHeight || 30;
    const bc0 = ballConf();
    el.style.transformOrigin = 'left top';
    const W = pit.clientWidth || window.innerWidth;
    const H = pit.clientHeight || window.innerHeight;

    // 从右侧入场，避免全堆在同一个点
    const angle = Math.random() * Math.PI * 2;
    const speed = 0.35 + Math.random() * 0.45;   // px/ms，慢速漂浮

    return {
      name: r.name,
      rank: r.rank,
      el,
      bw,
      bh,
      // 实际占位 = 原始尺寸 × 缩放，供碰撞与边界判断使用
      get w() { return this.bw * ballConf().size; },
      get h() { return this.bh * ballConf().size; },
      x: W * 0.5 + (Math.random() - 0.5) * W * 0.5,
      y: H * 0.45 + (Math.random() - 0.5) * H * 0.3,
      vx: Math.cos(angle) * speed,
      vy: Math.sin(angle) * speed,
    };
  }

  function startBallLoop() {
    const pit = $('ballpit');
    if (!pit) return;
    let last = performance.now();

    const frame = (now) => {
      // 限制单帧步长，切换标签页回来时不会瞬移
      const dt = Math.min(48, now - last);
      last = now;

      const bc = ballConf();
      const W = pit.clientWidth || window.innerWidth;
      const H = pit.clientHeight || window.innerHeight;
      // 顶部让开标题栏与数据气泡，底部让开字幕/控件
      const padTop = window.innerHeight < 520 ? 46 : 90;
      const padBottom = window.innerHeight < 520 ? 60 : 96;

      for (const b of State.ballPit) {
        b.x += b.vx * bc.speed * dt;
        b.y += b.vy * bc.speed * dt;

        if (bc.bounce) {
          // 碰边反弹：把位置夹回边界内并翻转速度
          if (b.x < 0) { b.x = 0; b.vx = Math.abs(b.vx); }
          if (b.x + b.w > W) { b.x = W - b.w; b.vx = -Math.abs(b.vx); }
          if (b.y < padTop) { b.y = padTop; b.vy = Math.abs(b.vy); }
          if (b.y + b.h > H - padBottom) { b.y = H - padBottom - b.h; b.vy = -Math.abs(b.vy); }
        } else {
          // 关掉回弹就从对面穿出来，形成循环漂移
          if (b.x + b.w < 0) b.x = W;
          else if (b.x > W) b.x = -b.w;
          if (b.y + b.h < padTop) b.y = H - padBottom - b.h;
          else if (b.y > H - padBottom) b.y = padTop;
        }

        // 气泡之间互相轻推，避免叠在一起看不清
        for (const o of State.ballPit) {
          if (o === b) continue;
          const dx = (b.x + b.w / 2) - (o.x + o.w / 2);
          const dy = (b.y + b.h / 2) - (o.y + o.h / 2);
          const dist = Math.hypot(dx, dy) || 1;
          const minDist = (b.w + o.w) / 2 * 0.9;
          if (dist < minDist) {
            const push = (minDist - dist) / dist * 0.5;
            b.vx += dx * push * 0.02;
            b.vy += dy * push * 0.02;
          }
        }

        // 限速，避免碰撞把速度越推越快
        const sp = Math.hypot(b.vx, b.vy);
        const maxSp = 0.7;
        if (sp > maxSp) { b.vx = b.vx / sp * maxSp; b.vy = b.vy / sp * maxSp; }

        const sz = ballConf().size;
        b.el.style.transform =
          `translate3d(${b.x.toFixed(1)}px,${b.y.toFixed(1)}px,0) scale(${sz})`;
      }

      if (State.ballPit.length) {
        State.ballRaf = requestAnimationFrame(frame);
      } else {
        State.ballRaf = 0;
        last = 0;
      }
    };

    State.ballRaf = requestAnimationFrame(frame);
  }

  /* 礼物即时反馈：在本地维护一份榜单，让画面不必等轮询 */
  let localRank = [];

  function bumpLocalRank(ev) {
    const name = ev.userName || '观众';
    const value = Number(ev.giftValue) || 0;
    let rec = localRank.find((r) => r.name === name);
    if (!rec) {
      rec = { name, avatar: ev.avatar || '', value: 0, count: 0 };
      localRank.push(rec);
    }
    rec.value += value;
    rec.count += Math.max(1, Number(ev.giftCount) || 1);
    if (ev.avatar) rec.avatar = ev.avatar;

    localRank.sort((a, b) => b.value - a.value);
    localRank = localRank.slice(0, 10).map((r, i) => ({ ...r, rank: i + 1 }));
    renderRanking(localRank);
    syncBalls(localRank);
  }

  /* ============================== 音乐播放器 ============================== */
  /** 播放器状态。
   *
   *  播放由浏览器 <audio> 完成，服务端只维护队列与“该放哪首”。
   *  不把音频推到服务端混流：直播画面本身就是浏览器渲染的，
   *  页面内直接播最简单，OBS 抓窗口/浏览器源都能收到声音。
   */
  const Player = {
    audio: null,
    curId: '',
    curUrl: '',
    playing: false,
    conf: { volume: 0.6, show_player: true },
    timer: 0,
    needPlay: false,      // 想播但被浏览器拦了
    warned: false,
    resumeArmed: false,
    lastPct: 0,
  };

  function ensurePlayerAudio() {
    if (Player.audio) return Player.audio;
    const a = new Audio();
    a.preload = 'auto';
    a.volume = Player.conf.volume ?? 0.6;

    a.addEventListener('ended', () => {
      Player.playing = false;
      renderPlayer();
      fetch('/api/music/played', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: Player.curId }),
      }).catch(() => {});
    });
    a.addEventListener('error', () => {
      // 直链过期/防盗链失败：自动跳下一首，别让画面卡在这首
      console.warn('音频加载失败，跳过');
      fetch('/api/music/played', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({id: Player.curId}),
      }).catch(() => {});
    });
    a.addEventListener('play', () => { Player.playing = true; renderPlayer(); });
    a.addEventListener('pause', () => { Player.playing = false; renderPlayer(); });
    Player.audio = a;
    // 挂到 window 便于排查（也能让 OBS/自动化脚本查到播放状态）
    window.__plAudio = a;
    return a;
  }

  function skipNext() {
    fetch('/api/music/next', { method: 'POST' }).catch(() => {});
  }

  /* 浏览器自动播放策略：页面没被用户点过时 play() 会被拒。
     OBS 浏览器源、刷新后无人值守的场景都会撞上，所以不能只 catch 了事——
     记下"想播但被拦"，等下一次真实交互或定时器恢复时再补播。 */
  function tryPlay(a) {
    if (Player.conf.enabled === false || Player.last?.playing === false) {
      Player.needPlay = false;
      return;
    }
    if (!a || !a.src) return;
    const p = a.play();
    if (p && typeof p.catch === 'function') {
      p.then(() => { Player.needPlay = false; })
       .catch(() => {
         Player.needPlay = true;
         if (!Player.warned) {
           Player.warned = true;
           console.warn('自动播放被浏览器拦截，将在用户交互后自动继续');
         }
       });
    }
  }

  /** 被拦过一次后，等用户点任意地方就补播（OBS 源没有点击，靠下面的定时器）。 */
  function armAutoplayResume() {
    if (Player.resumeArmed) return;
    Player.resumeArmed = true;
    const resume = () => {
      if (Player.needPlay) tryPlay(Player.audio);
    };
    ['pointerdown', 'keydown', 'touchstart'].forEach((ev) =>
      window.addEventListener(ev, resume, { passive: true }));
  }

  function applyMusic(m) {
    if (!m) return;
    Player.last = m;
    const cur = m.current;

    if (cur && cur.id !== Player.curId) {
      Player.curId = cur.id;
      Player.curUrl = cur.url || '';
      const a = ensurePlayerAudio();
      a.src = Player.curUrl;
      a.volume = Player.conf.volume ?? 0.6;
      // 切歌时 lastPct 归零，进度条别停在上首的末尾
      Player.lastPct = 0;
      if (m.playing) tryPlay(a);
    }
    if (!cur && Player.curId) {
      Player.curId = '';
      Player.curUrl = '';
      const a = ensurePlayerAudio();
      a.pause();
      a.removeAttribute('src');
    }
    if (m.playing === false || Player.conf.enabled === false) {
      Player.needPlay = false;
      if (Player.audio) Player.audio.pause();
    }
    if (cur && m.playing === true && !Player.playing) {
      tryPlay(ensurePlayerAudio());
    }

    renderPlayer(m);
  }

  /** 人数上万后折成「1.2万」，否则大数字会把顶栏撑爆。 */
  function fmtNum(n) {
    const v = Number(n) || 0;
    if (v >= 10000) return (v / 10000).toFixed(1).replace(/\.0$/, '') + '万';
    return String(v);
  }

  /** 歌名来自观众输入，必须转义后再塞进 innerHTML。 */
  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function renderPlayer(m) {
    const box = $('player');
    if (!box) return;
    box.style.display = Player.conf.show_player === false ? 'none' : '';
    // 不传 m 时用上次的数据，避免被无参调用清空标题
    if (m) Player.last = m;
    m = Player.last || {};
    const cur = m.current;
    box.classList.toggle('on', !!cur && Player.playing);
    box.classList.toggle('dim', !cur);

    $('plTitle').textContent = cur ? cur.title : '还没有放歌';
    $('plBy').textContent = cur
      ? (cur.by || '') + (cur.requestedBy ? ` · ${cur.requestedBy}点` : '')
      : '点歌请说「点歌 歌名」';

    const srcEl = $('plSrc');
    const label = { local: '本地', netease: '网易云', qq: 'QQ' }[cur?.provider] || '';
    srcEl.textContent = label;
    srcEl.style.display = label ? '' : 'none';

    const list = m?.queue || [];
    const n = list.length;
    const qEl = $('plQueue');
    if (n) {
      // 队列直接铺开歌名，观众一眼能看到"我点的排第几"
      const rows = list.slice(0, 5).map((t, i) => {
        const who = t.requestedBy ? ` · ${t.requestedBy}` : '';
        const gift = t.giftValue ? ' 🎁' : '';
        return `<div class="pl-qi"><span class="pl-qn">${i + 1}</span>` +
               `<span class="pl-qt">${escapeHtml(t.title || '')}</span>` +
               `<span class="pl-qb">${escapeHtml(who)}${gift}</span></div>`;
      }).join('');
      const more = n > 5 ? `<div class="pl-qmore">还有 ${n - 5} 首…</div>` : '';
      qEl.innerHTML = rows + more;
      qEl.style.display = '';
    } else {
      qEl.textContent = '';
      qEl.style.display = 'none';
    }

    $('plToggle').textContent = Player.playing ? '⏸' : '▶';

    let pct = 0;
    const a = Player.audio;
    if (a && a.duration && Number.isFinite(a.duration)) {
      pct = (a.currentTime / a.duration) * 100;
    } else if (cur && m?.elapsed && cur.duration) {
      pct = (m.elapsed / cur.duration) * 100;
    }
    $('plBar').style.width = Math.max(0, Math.min(100, pct)) + '%';
  }

  function startPlayerTicker() {
    if (Player.timer) return;
    armAutoplayResume();
    // 用 setInterval 而不是 rAF：浏览器切到后台会把 rAF 完全暂停，
    // 但 setInterval 仍会跑（只是被节流到 ~1Hz），足以驱动补播和进度条。
    Player.timer = setInterval(() => {
      const a = Player.audio;
      // 被自动播放策略拦下且页面此刻已获得交互权限时，这里自动补播
      if (Player.needPlay && a && a.paused && a.src) tryPlay(a);

      const bar = $('plBar');
      if (!bar || !a || !a.duration || !Number.isFinite(a.duration)) return;
      const pct = Math.min(100, (a.currentTime / a.duration) * 100);
      // 进度没变就不碰 DOM，减少后台无谓重排
      if (Math.abs(pct - Player.lastPct) < 0.4) return;
      Player.lastPct = pct;
      bar.style.width = pct + '%';
    }, 500);
  }

  function bindPlayer() {
    const t = $('plToggle');
    const n = $('plNext');
    if (t) {
      t.onclick = async () => {
        try {
          const action = Player.last?.playing ? 'stop' : 'resume';
          const response = await fetch('/api/music/' + action, {method: 'POST'});
          const result = await response.json();
          if (!response.ok || !result.ok) throw new Error(result.error || '音乐操作失败');
          applyMusic(result.music);
        } catch (error) { setHint(error.message); }
      };
    }
    if (n) n.onclick = () => skipNext();
    startPlayerTicker();
  }

  /** 说话时把音乐压低，说完平滑恢复。
   *
   *  直接改 volume 会“咔”一下，听感很硬，所以用 180ms 的小渐变。
   *  duck=1 表示不压（保持原音量），保留给「就想让 BGM 一直响」的人。
   *
   *  注意：不只挂在 speech_start/end 上。TTS 是逐句合成的，
   *  每句之间会短暂 speech_end，若只跟这两个事件音乐就会一直忽大忽小。
   *  所以同时看「还有没有声音在放」——voice 音频元素没播完就继续保持压低。
   */
  let duckTimer = 0;
  let ducked = false;

  function duckMusic(on) {
    const a = Player.audio;
    if (!a) return;
    ducked = on;
    const base = Player.conf.volume ?? 0.6;
    const duck = Player.conf.duck ?? 0.35;
    const target = on ? base * duck : base;

    // 渐变驱动：可见时用 rAF 更顺滑，但标签页切到后台 rAF 会彻底停住，
    // 音量就会永远卡在压低状态——所以后台改用定时器。
    stopDuckDrive();
    const step = () => {
      const diff = target - a.volume;
      if (Math.abs(diff) < 0.01) {
        a.volume = target;
        return false;
      }
      a.volume = Math.max(0, Math.min(1, a.volume + diff * 0.28));
      return true;
    };
    if (document.hidden) {
      step();
      // 后台只需几帧就到位，10 次定时足够，不用长期挂着
      let n = 0;
      duckTimer = setInterval(() => { if (!step() || ++n > 10) stopDuckDrive(); }, 60);
    } else {
      const tick = () => {
        if (step()) duckTimer = requestAnimationFrame(tick);
      };
      tick();
    }
  }

  function stopDuckDrive() {
    if (!duckTimer) return;
    cancelAnimationFrame(duckTimer);
    clearInterval(duckTimer);
    duckTimer = 0;
  }

  /** 语音真正播完才恢复音乐音量。 */
  function duckReleaseSoon() {
    if (State.playing || State.voiceQueue.length || State.speaking) return;
    duckMusic(false);
  }

  /* ============================== 字幕 ============================== */
  let subTimer = 0;
  function showSubtitle(text) {
    const box = $('subtitle');
    $('subText').textContent = text;
    box.classList.add('on');
    clearTimeout(subTimer);
    subTimer = setTimeout(() => box.classList.remove('on'), 6000);
  }

  function setHint(text) { const h = $('hintText'); if (h) h.textContent = text; }

  function setBadge(id, text, hot) {
    const el = $(id);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('hot', !!hot);
  }

  /* ============================== WebSocket ============================== */
  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws`);
    State.ws = ws;
    $('conn').classList.add('show');

    ws.onopen = () => {
      State.connected = true;
      $('conn').classList.remove('show');
      setHint('已连接直播服务，AI 正在看弹幕…');
    };

    ws.onclose = () => {
      State.connected = false;
      $('conn').classList.add('show');
      $('conn').textContent = '连接已断开，3 秒后重连…';
      setTimeout(connect, 3000);
    };

    ws.onerror = () => { /* onclose 会接管重连 */ };

    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      handle(msg);
    };
  }

  function handle(msg) {
    switch (msg.type) {
      case 'hello':
        if ($('danmuList')) $('danmuList').innerHTML = '';
        State.stats.danmu = msg.eventStats?.danmu_total || 0;
        $('danmuNum').textContent = State.stats.danmu;
        if (msg.config?.name) {
          $('brandName').textContent = msg.config.name;
          document.title = `${msg.config.name} · AI 直播间`;
        }
        if (msg.config) {
          State.ingestSource = msg.config.ingest || '';
          State.roomId = msg.config.room || '';
          setBadge('badgeTts', msg.config.tts === 'edge' ? '语音 · 在线' : '语音 · 关闭', msg.config.tts === 'edge');
          setBadge('badgeBrain', msg.config.llm ? '大脑 · 在线' : '大脑 · 离线', !!msg.config.llm);
          if (msg.config.stage) applyStage(msg.config.stage);
          if (msg.config.music) {
            Player.conf = msg.config.music;
            renderPlayer();
          }
          updateEmptyHint();
        }
        if (msg.stats) renderStats(msg.stats);
        if (msg.music) applyMusic(msg.music);
        (msg.recent || []).forEach((message) => {
          if (message.type === 'event' && message.event) pushDanmu(message.event, true);
        });
        if (msg.paused) setHint('⏸ 直播休息中，语音与音乐已暂停');
        updateEmptyHint();
        break;

      case 'music':
        applyMusic(msg.music);
        break;

      case 'reaction':
        // 互动规则命中：动作 + 表情 + 特效，与语音并行
        if (msg.motion) playMotion(msg.motion);
        if (msg.expression !== undefined) playExpression(msg.expression);
        if (msg.effect) playEffect(msg.effect);
        break;

      case 'config_changed':
        if (msg.config?.stage) applyStage(msg.config.stage);
        if (msg.config?.music) {
          Player.conf = msg.config.music;
          if (Player.audio) Player.audio.volume = Player.conf.volume ?? 0.6;
          if (Player.conf.enabled === false) {
            Player.needPlay = false;
            if (Player.audio) Player.audio.pause();
          } else if (Player.last) applyMusic(Player.last);
          renderPlayer();
        }
        // 后台改了人设/名字/舞台，直播画面立即跟上，不用刷新
        State.rankCache = '';   // 排行样式可能变了，强制重绘
        if (msg.config?.name) {
          $('brandName').textContent = msg.config.name;
          document.title = `${msg.config.name} · AI 直播间`;
        }
        if (msg.config?.tts) {
          setBadge('badgeTts', msg.config.tts === 'edge' ? '语音 · 在线' : '语音 · 关闭', msg.config.tts === 'edge');
        }
        if (msg.config?.llm !== undefined) {
          setBadge('badgeBrain', msg.config.llm ? '大脑 · 在线' : '大脑 · 离线', !!msg.config.llm);
        }
        if (msg.config?.ingest !== undefined) State.ingestSource = msg.config.ingest;
        if (msg.config?.room !== undefined) State.roomId = msg.config.room;
        updateEmptyHint();
        if (msg.config?.stage) applyStage(msg.config.stage);
        // 榜单条数/特效/气泡数改了，立刻按当前榜单重画，不必等下一次统计推送
        if (localRank && localRank.length) {
          renderRanking(localRank);
          syncBalls(localRank);
        }
        break;

      case 'event':
        if (msg.event) {
          pushDanmu(msg.event);
          // 礼物到了先本地累加一次，榜单立刻有反应，不必等 10 秒的统计推送
          if (msg.event.kind === 'gift') bumpLocalRank(msg.event);
        }
        break;

      case 'stats':
        // 今日 token/花费/对话数 + 礼物榜，服务端每 10 秒推一次
        renderStats(msg.stats);
        // 抖音真实人数：在线 = 当前观众，累计 = 整场观看人次
        if (msg.room) {
          State.stats.online = msg.room.online || 0;
          State.stats.totalWatch = msg.room.total_watch || 0;
          const el = $('onlineNum');
          if (el) el.textContent = fmtNum(State.stats.online);
          const tw = $('totalWatchNum');
          if (tw) tw.textContent = fmtNum(State.stats.totalWatch);
        }
        break;

      case 'answered':
        markAnswered(msg.id);
        break;

      case 'subtitle':
        showSubtitle(msg.text);
        State.speaking = true;
        break;

      case 'voice':
        State.voiceQueue.push(msg.audio);
        if (!State.playing) drainVoice();
        break;

      case 'reset':
        stopVoice();
        // 服务端清场：把画面上的历史也一起清掉
        Player.curId = '';
        Player.curUrl = '';
        Player.last = null;
        if (Player.audio) {
          Player.audio.pause();
          Player.audio.removeAttribute('src');
        }
        clearBalls();
        State.rankCache = '';
        if ($('danmuList')) $('danmuList').innerHTML = '';
        renderPlayer({ queue: [] });
        break;

      case 'speech_start':
        State.speaking = true;
        setHint('正在说话…');
        duckMusic(true);          // AI 开口时把音乐压下去，"让一让"
        break;

      case 'speech_end':
        State.speaking = false;
        setHint('AI 正在看弹幕，随时准备接话…');
        // 真等语音播完再恢复；语音队列还在就继续保持压低
        duckReleaseSoon();
        break;

      case 'speech_interrupt':
        stopVoice();
        break;

      case 'motion':
        playMotion(msg.name);
        break;

      case 'online':
        // 这条是「有几个网页开着直播画面」，不是抖音观众数。
        // 界面上不拿它冒充在线人数，只留作内部参考。
        State.stats.viewers = msg.count || 0;
        break;

      case 'state':
        if (msg.paused) stopVoice();
        setHint(msg.paused ? '⏸ 直播休息中，语音与音乐已暂停' : 'AI 正在看弹幕…');
        break;

      default:
        break;
    }
  }

  async function drainVoice() {
    State.playing = true;
    // 整轮语音期间音乐都压低：逐句合成会有短暂空隙，
    // 跟着 speech_start/end 走会忽大忽小，这里以「队列清空」为准
    duckMusic(true);
    while (State.voiceQueue.length) {
      const b64 = State.voiceQueue.shift();
      const cancelled = new Promise((resolve) => { State.cancelVoice = resolve; });
      await Promise.race([playVoice(b64, State.voiceGeneration), cancelled]);
      State.cancelVoice = null;
    }
    State.playing = false;
    duckReleaseSoon();
  }

  /* ============================== 交互 ============================== */
  $('btnSound').addEventListener('click', async () => {
    State.soundOn = !State.soundOn;
    ensureAudio();
    if (State.gain) State.gain.gain.value = State.soundOn ? 1 : 0;
    $('btnSound').style.opacity = State.soundOn ? 1 : 0.45;
    if (State.soundOn && State.audioCtx?.state === 'suspended') await State.audioCtx.resume();
  });

  $('btnFull').addEventListener('click', () => {
    if (!document.fullscreenElement) stage.requestFullscreen?.();
    else document.exitFullscreen?.();
  });

  // 首次点击解锁音频（浏览器自动播放策略）
  const unlock = async () => {
    ensureAudio();
    if (State.audioCtx?.state === 'suspended') await State.audioCtx.resume();
    window.removeEventListener('pointerdown', unlock);
  };
  window.addEventListener('pointerdown', unlock);

  // 视线跟随
  window.addEventListener('pointermove', (e) => {
    State.mouse.x = (e.clientX / window.innerWidth) * 2 - 1;
    State.mouse.y = (e.clientY / window.innerHeight) * 2 - 1;
  }, { passive: true });

  /* ============================== 启动 ============================== */
  bindPlayer();
  startBgFallback();   // 切到后台时接替 rAF，别让口型停住

  window.addEventListener('load', () => {
    connect();
    // 稍等库脚本就绪
    setTimeout(initLive2D, 60);
    requestAnimationFrame(tick);
  });

  // 暴露给控制台调试
  window.__fish = State;
})();
