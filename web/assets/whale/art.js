/* ============================================================================
 * WhaleArt · AI 立绘形象（"好看"模式）
 * ----------------------------------------------------------------------------
 * 与 WhaleRig 的关系：
 *   - 复用 rig.js 导出的 PARAMS / PARAM_IDS / MOTIONS / EXPRESSIONS，
 *     所以 30 个参数、9 个动作、8 个表情「全部仍然可被外部触发」。
 *   - 接口与 WhaleRig 完全一致（motion / expression / setMouth / focus /
 *     update / start / destroy / setParameterValueById / coreModel ...），
 *     stage.js 不需要知道用的是哪个形象。
 *
 * 差别（必须如实说明，别当成全参数骨骼模型卖）：
 *   WhaleRig 是手绘 SVG 分层，每个部件独立变形 —— 能眨眼、能对口型、
 *   能单独转头，但画得不好看。
 *   WhaleArt 是一张 AI 生成的立绘，参数统一驱动「整体」变换
 *   （呼吸 / 点头 / 歪头 / 转身 / 摇摆 / 缩放 / 光晕）。眨眼与口型
 *   只是极轻微的整体 squash / 亮度反馈，不是真正的局部网格变形。
 *   想要既好看又有完整局部骨骼，必须把立绘交给 Cubism 做成 .moc3。
 *
 * 依赖：必须先加载 rig.js（提供 window.WHALE_RIG）。
 * ==========================================================================*/
(function (root) {
  'use strict';

  const D = root.WHALE_RIG;
  if (!D) {
    console.error('[WhaleArt] rig.js 必须先加载（需要 window.WHALE_RIG）');
    return;
  }
  const { PARAMS, PARAM_IDS, MOTIONS, EXPRESSIONS, VB } = D;

  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);
  const lerp = (a, b, t) => a + (b - a) * t;
  const ease = {
    lin: (t) => t,
    out: (t) => 1 - Math.pow(1 - t, 3),
    io: (t) => -(Math.cos(Math.PI * t) - 1) / 2,
    back: (t) => {
      const c = 1.70158;
      return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
    },
  };

  function sampleTrack(track, t) {
    if (!track || !track.length) return null;
    if (t <= track[0][0]) return track[0][1];
    for (let i = 1; i < track.length; i++) {
      const [t1, v1, e1] = track[i];
      if (t <= t1) {
        const [t0, v0] = track[i - 1];
        const k = clamp((t - t0) / (t1 - t0 || 1), 0, 1);
        return lerp(v0, v1, (ease[e1] || ease.lin)(k));
      }
    }
    return track[track.length - 1][1];
  }

  // 抠好绿幕的透明立绘。替换成任何同规格 PNG 即可换形象，无需改代码。
  const DEFAULT_SRC = '/static/assets/whale/art/deepy.png';

  /* 表情贴片：由 .tools/build_patches.py 从 AI 图生图变体切出。
   * rect = [left, top, width, height]，全部是相对于整张立绘的比例。
   * 有了它，眨眼和闭嘴是**真的换了一帧画**，不再是整体压扁的近似。
   * 贴片缺失时自动退回近似做法，不会白屏。 */
  const PATCHES = {
    eyes: { file: 'deepy.patch-eyes.png', rect: [0.28383, 0.258, 0.42828, 0.11514] },
    mouth: { file: 'deepy.patch-mouth.png', rect: [0.44659, 0.371, 0.10783, 0.05473] },
  };

  class CoreModelStub {
    constructor(art) { this.art = art; }
    getParameterIndex(id) { return this.art.getParameterIndex(id); }
    getParameterCount() { return this.art.getParameterCount(); }
    setParameterValueById(id, v) { this.art.setParameterValueById(id, v); }
    getParameterValueById(id) { return this.art.getParameterValueById(id); }
  }

  class WhaleArt {
    constructor(host, src) {
      this.host = host;
      this.src = src || DEFAULT_SRC;
      this.coreModel = new CoreModelStub(this);
      this.internalModel = { coreModel: this.coreModel, width: VB.w, height: VB.h };
      this.width = VB.w;
      this.height = VB.h;

      this.values = {};
      PARAM_IDS.forEach((id) => { this.values[id] = PARAMS[id].def; });

      this.exprIndex = 0;
      this.exprValues = {};
      this.motionName = '';
      this.motionTime = 0;
      this.autoIdle = true;

      this.focusX = 0;
      this.focusY = 0;
      this.blinkTimer = 2.5;
      this.blinkPhase = -1;

      this.mouth = 0;
      this.mouthAt = 0;

      this.overrides = {};
      this.time = 0;
      this.destroyed = false;
      this._last = 0;
      this._raf = 0;
      // 贴片是否可用；不可用时 render() 退回近似眨眼/口型
      this.patchOk = { eyes: false, mouth: false };

      this._build();
    }

    /* ---------------- DOM ----------------
     * applyRigLook() 会直接改写 .whale-rig-wrap 的 transform 来做布局，
     * 所以动画 transform 必须放在「内层」.wa-stage 上，不能放在 wrap 上，
     * 否则会被布局代码整条覆盖掉。 */
    _build() {
      const wrap = document.createElement('div');
      wrap.className = 'whale-rig-wrap whale-art-wrap';

      const stage = document.createElement('div');
      stage.className = 'wa-stage';

      const glow = document.createElement('div');
      glow.className = 'wa-glow';

      const img = document.createElement('img');
      img.className = 'wa-img';
      img.src = this.src;
      img.alt = 'Deepy';
      img.draggable = false;
      img.addEventListener('error', () => {
        console.error('[WhaleArt] 立绘素材加载失败:', this.src);
        wrap.classList.add('wa-missing');
      });

      stage.appendChild(glow);
      stage.appendChild(img);
      // 贴片必须排在 img 之后，才能盖在脸上
      this.patches = {};
      const dir = this.src.replace(/[^/]*$/, '');
      Object.keys(PATCHES).forEach((key) => {
        const meta = PATCHES[key];
        const p = document.createElement('img');
        p.className = 'wa-patch wa-patch-' + key;
        p.alt = '';
        p.draggable = false;
        p.style.left = (meta.rect[0] * 100).toFixed(3) + '%';
        p.style.top = (meta.rect[1] * 100).toFixed(3) + '%';
        p.style.width = (meta.rect[2] * 100).toFixed(3) + '%';
        p.style.height = (meta.rect[3] * 100).toFixed(3) + '%';
        p.style.opacity = '0';
        p.addEventListener('load', () => { this.patchOk[key] = true; });
        p.addEventListener('error', () => {
          // 换了立绘却没配贴片时，静默退回近似方案即可，不要刷屏报错
          console.warn('[WhaleArt] 表情贴片缺失，改用近似方案:', dir + meta.file);
          this.patchOk[key] = false;
          p.remove();
        });
        p.src = dir + meta.file;
        stage.appendChild(p);
        this.patches[key] = p;
      });
      wrap.appendChild(stage);
      this.host.appendChild(wrap);

      this.wrap = wrap;
      this.stage = stage;
      this.glow = glow;
      this.img = img;
      this.render();
    }

    /* ---------------- 参数读写（Live2D 同构 API） ---------------- */
    setParameterValueById(id, v, ttl) {
      if (!(id in this.values)) return;
      const p = PARAMS[id];
      const val = clamp(v, p.min, p.max);
      this.values[id] = val;
      this.overrides[id] = {
        v: val,
        until: performance.now() + (Number(ttl) > 0 ? Number(ttl) : 1e9),
      };
    }
    getParameterValueById(id) { return this.values[id] ?? 0; }
    getParameterIndex(id) { return PARAM_IDS.indexOf(id); }
    getParameterCount() { return PARAM_IDS.length; }

    setMouth(open) {
      this.mouth = clamp(open || 0, 0, 1);
      this.mouthAt = performance.now();
    }
    focus(x, y) {
      this.focusX = clamp(x || 0, -1, 1);
      this.focusY = clamp(y || 0, -1, 1);
    }

    motion(name) {
      const key = MOTIONS[name] ? name : 'idle';
      this.motionName = key;
      this.motionTime = 0;
      this.overrides = {};
      if (key === 'idle') this.values.ParamAngleY = 0;
      return true;
    }

    expression(i) {
      const idx = Number(i);
      if (!Number.isFinite(idx)) return;
      this.exprIndex = clamp(Math.floor(idx), 0, EXPRESSIONS.length - 1);
      this.exprValues = EXPRESSIONS[this.exprIndex].p || {};
      return true;
    }

    /* ---------------- 每帧 ---------------- */
    update(dt) {
      if (this.destroyed) return;
      const step = clamp(dt || 0.016, 0.001, 0.1);
      this.time += step;
      this._applyMotion(step);
      this._tickBlink(step);

      // 外部口型优先（stage.js 每帧喂音量），超过 0.5s 没数据就闭嘴
      if (this.mouth > 0.001 && performance.now() - this.mouthAt < 500) {
        this.values.ParamMouthOpenY = this.mouth;
      } else if (performance.now() - this.mouthAt >= 500) {
        this.mouth = 0;
      }
      this.render();
    }

    _applyMotion(step) {
      const m = MOTIONS[this.motionName] || MOTIONS.idle;
      this.motionTime += step;
      const dur = m.duration || 1;
      const t = m.loop ? this.motionTime % dur : Math.min(this.motionTime, dur);
      if (!m.loop && this.motionTime >= dur) {
        this.motionName = 'idle';
        this.motionTime = 0;
      }

      // 分层混合：base(呼吸/表情) ← override(外部) ← 动作轨道
      const base = this._baseline();
      Object.keys(base).forEach((id) => {
        if (id in this.values) this.values[id] = base[id];
      });
      const now = performance.now();
      Object.keys(this.overrides).forEach((id) => {
        const o = this.overrides[id];
        if (!o) return;
        if (now > o.until) { delete this.overrides[id]; return; }
        if (id in this.values) this.values[id] = o.v;
      });
      const tracks = m.tracks || {};
      Object.keys(tracks).forEach((id) => {
        if (!(id in this.values)) return;
        const v = sampleTrack(tracks[id], t);
        if (v == null) return;
        this.values[id] = clamp(v, PARAMS[id].min, PARAMS[id].max);
      });
    }

    _baseline() {
      const out = {};
      const br = (Math.sin(this.time * 1.15) + 1) / 2;
      out.ParamBreath = br * 0.6;
      out.ParamBodyAngleZ = Math.sin(this.time * 0.85) * 0.9;
      out.ParamAngleZ = Math.sin(this.time * 0.7 + 0.6) * 0.8;
      out.ParamTailLift = 0.08 + br * 0.1;
      out.ParamWaterGlow = 0.3 + br * 0.2;
      Object.keys(this.exprValues).forEach((id) => { out[id] = this.exprValues[id]; });
      return out;
    }

    _tickBlink(step) {
      this.blinkTimer -= step;
      if (this.blinkTimer <= 0 && this.blinkPhase < 0) {
        this.blinkPhase = 0;
        this.blinkTimer = 1.8 + Math.random() * 3.2;
      }
      if (this.blinkPhase >= 0) {
        this.blinkPhase += step;
        const t = this.blinkPhase / 0.18;
        if (t >= 1) this.blinkPhase = -1;
        else {
          const close = t < 0.5 ? t * 2 : (1 - t) * 2;
          const open = 1 - close * 0.96;
          this.values.ParamEyeLOpen = Math.min(this.values.ParamEyeLOpen, open);
          this.values.ParamEyeROpen = Math.min(this.values.ParamEyeROpen, open);
        }
      }
    }

    /* ---------------- 渲染：参数 → 整体 CSS transform ----------------
     * 幅度刻意压小：立绘是一整张图，转太多会像贴纸在飘。
     * transform-origin 设在底部（脚/尾），符合站立角色的物理直觉。 */
    render() {
      if (!this.stage) return;
      const v = this.values;
      const br = v.ParamBreath || 0;
      const ax = v.ParamAngleX || 0;
      const ay = v.ParamAngleY || 0;
      const az = v.ParamAngleZ || 0;
      const bx = v.ParamBodyAngleX || 0;
      const by = v.ParamBodyAngleY || 0;
      const bz = v.ParamBodyAngleZ || 0;
      const tailSway = v.ParamTailSway || 0;
      const tailLift = v.ParamTailLift || 0;
      const glow = v.ParamWaterGlow || 0;
      const mouthOpen = v.ParamMouthOpenY || 0;
      const smile = Math.max(v.ParamEyeLSmile || 0, v.ParamEyeRSmile || 0);
      const eyeOpen = Math.min(v.ParamEyeLOpen ?? 1, v.ParamEyeROpen ?? 1);

      /* 有贴片就换真帧；没有才退回整体压扁。
       * 两者不能同时生效，否则闭眼会又压扁又贴片，看着像缩了一下。 */
      const useEyePatch = this.patchOk.eyes;
      const useMouthPatch = this.patchOk.mouth;
      if (useEyePatch) {
        this.patches.eyes.style.opacity = String(clamp(1 - eyeOpen, 0, 1));
      }
      if (useMouthPatch) {
        this.patches.mouth.style.opacity = String(clamp(1 - mouthOpen, 0, 1));
      }
      const eyeClose = useEyePatch ? 0 : (1 - eyeOpen);
      const mouthSquash = useMouthPatch ? 0 : mouthOpen;

      const rotZ = clamp(az * 0.55 + bz * 0.8 + tailSway * 0.5, -14, 14);
      const rotY = clamp(ax * 0.32 + bx * 0.25 + this.focusX * 3, -12, 12);
      const rotX = clamp(-ay * 0.30 + by * 0.18 + this.focusY * 2, -10, 10);
      const tx = clamp(ax * 0.10 + bx * 0.14, -6, 6);
      const ty = clamp(-br * 0.9 + ay * 0.05 + mouthSquash * 0.35 - tailLift * 0.3, -4, 4);
      const skew = clamp(tailSway * 0.35 + bx * 0.1, -3, 3);

      const sc = 1 + br * 0.010 + smile * 0.012;
      // eyeClose / mouthSquash 在贴片可用时恒为 0，只剩呼吸与微笑的缩放
      const sx = sc * (1 + eyeClose * 0.006);
      const sy = sc * (1 - eyeClose * 0.016 - mouthSquash * 0.004);

      this.stage.style.transform =
        `perspective(1100px) translate(${tx.toFixed(3)}%, ${ty.toFixed(3)}%) ` +
        `rotateZ(${rotZ.toFixed(2)}deg) rotateY(${rotY.toFixed(2)}deg) ` +
        `rotateX(${rotX.toFixed(2)}deg) skewX(${skew.toFixed(2)}deg) ` +
        `scale(${sx.toFixed(4)}, ${sy.toFixed(4)})`;
      this.stage.style.filter = `brightness(${(1 + mouthSquash * 0.05).toFixed(3)})`;
      this.glow.style.opacity = String(clamp(0.18 + glow * 0.75, 0, 1));
    }

    /* ---------------- 生命周期 ---------------- */
    start() {
      const loop = () => {
        if (this.destroyed) return;
        const now = performance.now();
        const dt = this._last ? (now - this._last) / 1000 : 0.016;
        this._last = now;
        this.update(dt);
        this._raf = requestAnimationFrame(loop);
      };
      this._raf = requestAnimationFrame(loop);
    }

    destroy() {
      this.destroyed = true;
      if (this._raf) cancelAnimationFrame(this._raf);
      try { this.wrap?.remove(); } catch (e) {}
      this.stage = this.glow = this.img = this.wrap = null;
    }
  }

  root.WhaleArt = WhaleArt;
  root.WHALE_ART = { DEFAULT_SRC };
})(window);
