/* ============================================================================
 * 深蓝 (Deepy) · 程序化分层骨骼引擎
 * ----------------------------------------------------------------------------
 * 为什么不用 Live2D：
 *   .moc3 是 Cubism Editor 导出的私有二进制，必须由人在编辑器里绑骨导出。
 *   在拿到 moc3 之前，这个文件用一个「参数系统 + 分层 SVG + 二级弹簧」的
 *   自研 rig 顶上来，参数 ID 与 Live2D Cubism 完全同名同义，所以：
 *     - 今天就能开播（有完整表情、动作、口型、物理摆动）
 *     - 以后换成真 moc3 时，stage.js 的调用方零改动
 *
 * 对外接口（与 pixi-live2d-display 的 Live2DModel 保持同构）：
 *   const rig = new WhaleRig(host)
 *   rig.load()                    渲染首帧
 *   rig.motion(name)              播放动作（idle / talk / happy / shake ...）
 *   rig.expression(i)             切换到第 i 个表情（0..N-1）
 *   rig.setParameterValueById(id, v)
 *   rig.getParameterIndex(id)
 *   rig.focus(x, y)               视线跟随（-1..1）
 *   rig.update(dt)                每帧推进
 *   rig.destroy()
 *   rig.internalModel.coreModel   → 交给 stage.js 的口型探测逻辑
 * ==========================================================================*/
(function (root) {
  'use strict';

  /* ============================== 一、调色板 ==============================
   * DeepSeek 品牌蓝 #4D6BFE 作为主色，鲸身走深海渐层，高光是发光青。
   * 想整体换风格只改这里，美术层不出现硬编码颜色。
   */
  const PALETTE = {
    brand: '#4d6bfe',        // DeepSeek 品牌蓝（发带 / 鲸鳍 / 光点）
    brandDeep: '#2b3fd6',
    skin: '#fdf3ef',
    skinShade: '#f5d9d4',
    blush: '#ff9dbb',
    hair: '#cfe6ff',
    hairShade: '#a9cdf5',
    hairDeep: '#7fa9dd',
    hairGlow: '#eaf6ff',
    iris: '#4d6bfe',
    irisDeep: '#1b2a86',
    irisGlow: '#9fe4ff',
    whale: '#6f9cf7',
    whaleDeep: '#3a5ce0',
    whaleBelly: '#e6efff',
    mouth: '#b32e55',
    mouthIn: '#ff8aae',
    cloth: '#eef4ff',
    clothShade: '#cfdcf7',
    accent: '#8f6bff',
    line: '#3f5a8c',
  };

  /* ============================== 二、参数定义 ==============================
   * id 全部对齐 Cubism 官方命名，方便后面替换真模型时行为一致。
   */
  const PARAMS = {
    // 头部整体
    ParamAngleX: { min: -30, max: 30, def: 0 },
    ParamAngleY: { min: -30, max: 30, def: 0 },
    ParamAngleZ: { min: -30, max: 30, def: 0 },
    // 眼球
    ParamEyeBallX: { min: -1, max: 1, def: 0 },
    ParamEyeBallY: { min: -1, max: 1, def: 0 },
    // 眼睑
    ParamEyeLOpen: { min: 0, max: 1, def: 1 },
    ParamEyeROpen: { min: 0, max: 1, def: 1 },
    ParamEyeLSmile: { min: 0, max: 1, def: 0 },
    ParamEyeRSmile: { min: 0, max: 1, def: 0 },
    ParamBrowLY: { min: -1, max: 1, def: 0 },
    ParamBrowRY: { min: -1, max: 1, def: 0 },
    ParamBrowLForm: { min: -1, max: 1, def: 0 },
    ParamBrowRForm: { min: -1, max: 1, def: 0 },
    // 嘴（口型）
    ParamMouthOpenY: { min: 0, max: 1, def: 0 },
    ParamMouthForm: { min: -1, max: 1, def: 0 },
    // 身体
    ParamBodyAngleX: { min: -10, max: 10, def: 0 },
    ParamBodyAngleY: { min: -10, max: 10, def: 0 },
    ParamBodyAngleZ: { min: -10, max: 10, def: 0 },
    ParamBreath: { min: 0, max: 1, def: 0 },
    ParamArmLA: { min: -1, max: 1, def: 0 },
    ParamArmRA: { min: -1, max: 1, def: 0 },
    ParamShoulderY: { min: -1, max: 1, def: 0 },
    // 头发 / 尾巴 的二级摆动（由弹簧驱动，动作里不直接写死）
    ParamHairFront: { min: -1, max: 1, def: 0 },
    ParamHairSide: { min: -1, max: 1, def: 0 },
    ParamHairBack: { min: -1, max: 1, def: 0 },
    ParamWhaleEar: { min: -1, max: 1, def: 0 },
    ParamTailSway: { min: -1, max: 1, def: 0 },
    ParamTailLift: { min: -1, max: 1, def: 0 },
    // 光效 / 特殊
    ParamWaterGlow: { min: 0, max: 1, def: 0.35 },
    ParamStarTilt: { min: -1, max: 1, def: 0 },
  };

  const PARAM_IDS = Object.keys(PARAMS);

  /* ============================== 三、弹簧 ==============================
   * 二级动画：一级是参数（头、身体），二级是头发尾巴的惯性跟随。
   * 临界阻尼弹簧，参数改了不会抖成筛子。
   */
  class Spring {
    constructor(stiffness = 90, damping = 12, mass = 1) {
      this.k = stiffness;
      this.c = damping;
      this.m = mass;
      this.value = 0;
      this.target = 0;
      this.velocity = 0;
    }
    update(dt) {
      // dt 夹紧，切标签页回来时不要炸
      const step = Math.min(dt, 1 / 30);
      const a = (-this.k * (this.value - this.target) - this.c * this.velocity) / this.m;
      this.velocity += a * step;
      this.value += this.velocity * step;
      if (Math.abs(this.velocity) < 1e-4 && Math.abs(this.value - this.target) < 1e-4) {
        this.value = this.target;
        this.velocity = 0;
      }
      return this.value;
    }
  }

  /* ============================== 四、缓动与插值 ============================== */
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
  const easeInOutSine = (t) => -(Math.cos(Math.PI * t) - 1) / 2;
  const easeOutBack = (t) => {
    const c = 1.70158;
    return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2);
  };
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

  /* ============================== 五、动作库 ==============================
   * 每个动作 = 轨道表：参数 → 关键帧 [时间, 值, 缓动]
   * 时间单位秒；播完自动回 idle。
   * 这是「方便后续触发」的核心：新增动作只要往这里加一条，不用碰渲染代码。
   */
  const ease = { lin: (t) => t, out: easeOutCubic, io: easeInOutSine, back: easeOutBack };

  const MOTIONS = {
    /* 待机：呼吸 + 轻微摇摆，循环播放 */
    idle: {
      loop: true,
      duration: 4.0,
      tracks: {
        ParamBreath: [[0, 0, 'io'], [2, 0.5, 'io'], [4, 0, 'io']],
        ParamBodyAngleZ: [[0, 0, 'io'], [2, 1.6, 'io'], [4, 0, 'io']],
        ParamAngleZ: [[0, 0, 'io'], [2, -1.2, 'io'], [4, 0, 'io']],
        ParamAngleY: [[0, 0, 'io'], [1.2, 1.5, 'io'], [2.6, -1, 'io'], [4, 0, 'io']],
        ParamTailLift: [[0, 0.05, 'io'], [2, 0.22, 'io'], [4, 0.05, 'io']],
        ParamWaterGlow: [[0, 0.3, 'io'], [2, 0.55, 'io'], [4, 0.3, 'io']],
      },
    },

    /* 说话：开口由口型驱动，这里负责「说话时头会动、手会动」 */
    talk: {
      loop: true,
      duration: 3.2,
      tracks: {
        ParamAngleX: [[0, 0, 'io'], [0.9, 3, 'io'], [1.8, -2.5, 'io'], [2.6, 2, 'io'], [3.2, 0, 'io']],
        ParamAngleY: [[0, 0, 'io'], [1.1, -2, 'io'], [2.2, 1.5, 'io'], [3.2, 0, 'io']],
        ParamAngleZ: [[0, 0, 'io'], [1.6, 2.2, 'io'], [3.2, 0, 'io']],
        ParamBodyAngleX: [[0, 0, 'io'], [1.6, 2.5, 'io'], [3.2, 0, 'io']],
        ParamArmLA: [[0, 0, 'io'], [0.8, 0.25, 'io'], [1.9, -0.1, 'io'], [3.2, 0, 'io']],
        ParamArmRA: [[0, 0, 'io'], [1.1, 0.3, 'io'], [2.3, -0.12, 'io'], [3.2, 0, 'io']],
        ParamBrowLY: [[0, 0, 'io'], [1.5, 0.18, 'io'], [3.2, 0, 'io']],
        ParamBrowRY: [[0, 0, 'io'], [1.5, 0.18, 'io'], [3.2, 0, 'io']],
        ParamWaterGlow: [[0, 0.35, 'io'], [1.6, 0.5, 'io'], [3.2, 0.35, 'io']],
      },
    },

    /* 开心：蹦一下 + 眯眼笑 + 耳朵尾巴一起甩（礼物 / 关注） */
    happy: {
      duration: 1.5,
      tracks: {
        ParamBodyAngleY: [[0, 0, 'out'], [0.18, -3.5, 'out'], [0.42, 0, 'io'], [1.5, 0, 'lin']],
        ParamAngleZ: [[0, 0, 'out'], [0.22, -4, 'io'], [0.6, 3.5, 'io'], [1.0, -1.5, 'io'], [1.5, 0, 'io']],
        ParamEyeLSmile: [[0, 0, 'out'], [0.2, 1, 'lin'], [1.1, 1, 'io'], [1.5, 0, 'io']],
        ParamEyeRSmile: [[0, 0, 'out'], [0.2, 1, 'lin'], [1.1, 1, 'io'], [1.5, 0, 'io']],
        ParamEyeLOpen: [[0, 1, 'out'], [0.2, 0.45, 'lin'], [1.1, 0.45, 'io'], [1.5, 1, 'io']],
        ParamEyeROpen: [[0, 1, 'out'], [0.2, 0.45, 'lin'], [1.1, 0.45, 'io'], [1.5, 1, 'io']],
        ParamMouthForm: [[0, 0, 'out'], [0.2, 1, 'lin'], [1.1, 1, 'io'], [1.5, 0, 'io']],
        ParamBrowLY: [[0, 0, 'out'], [0.2, 0.5, 'lin'], [1.1, 0.5, 'io'], [1.5, 0, 'io']],
        ParamBrowRY: [[0, 0, 'out'], [0.2, 0.5, 'lin'], [1.1, 0.5, 'io'], [1.5, 0, 'io']],
        ParamArmLA: [[0, 0, 'back'], [0.3, 0.75, 'io'], [0.9, 0.6, 'io'], [1.5, 0, 'io']],
        ParamArmRA: [[0, 0, 'back'], [0.3, 0.75, 'io'], [0.9, 0.6, 'io'], [1.5, 0, 'io']],
        ParamWhaleEar: [[0, 0, 'out'], [0.15, 0.7, 'io'], [0.45, -0.4, 'io'], [0.8, 0.2, 'io'], [1.5, 0, 'io']],
        ParamWaterGlow: [[0, 0.35, 'out'], [0.25, 0.95, 'io'], [1.0, 0.6, 'io'], [1.5, 0.35, 'io']],
      },
    },

    /* 惊讶：抖一下 + 眼睛睁大 + 眉毛挑起（被点名 / 大礼物） */
    shake: {
      duration: 1.1,
      tracks: {
        ParamBodyAngleX: [[0, 0, 'out'], [0.1, -3, 'io'], [0.24, 3, 'io'], [0.38, -2.2, 'io'], [0.52, 1.4, 'io'], [1.1, 0, 'io']],
        ParamAngleX: [[0, 0, 'out'], [0.12, 2.5, 'io'], [0.3, -2, 'io'], [1.1, 0, 'io']],
        ParamEyeLOpen: [[0, 1, 'out'], [0.12, 1.35, 'io'], [0.6, 1.15, 'io'], [1.1, 1, 'io']],
        ParamEyeROpen: [[0, 1, 'out'], [0.12, 1.35, 'io'], [0.6, 1.15, 'io'], [1.1, 1, 'io']],
        ParamEyeBallY: [[0, 0, 'out'], [0.12, 0.35, 'io'], [0.6, 0.1, 'io'], [1.1, 0, 'io']],
        ParamBrowLY: [[0, 0, 'out'], [0.12, 1, 'io'], [0.7, 0.7, 'io'], [1.1, 0, 'io']],
        ParamBrowRY: [[0, 0, 'out'], [0.12, 1, 'io'], [0.7, 0.7, 'io'], [1.1, 0, 'io']],
        ParamMouthOpenY: [[0, 0, 'out'], [0.12, 0.55, 'io'], [0.55, 0.3, 'io'], [1.1, 0, 'io']],
        ParamWhaleEar: [[0, 0, 'out'], [0.1, -0.8, 'io'], [0.5, 0.3, 'io'], [1.1, 0, 'io']],
        ParamWaterGlow: [[0, 0.35, 'out'], [0.12, 1, 'io'], [0.7, 0.5, 'io'], [1.1, 0.35, 'io']],
      },
    },

    /* 害羞：低头 + 脸红 + 耳朵下垂（被夸） */
    shy: {
      duration: 1.9,
      tracks: {
        ParamAngleY: [[0, 0, 'out'], [0.3, -7, 'io'], [1.4, -6, 'io'], [1.9, 0, 'io']],
        ParamAngleZ: [[0, 0, 'out'], [0.35, 4, 'io'], [1.4, 3, 'io'], [1.9, 0, 'io']],
        ParamEyeBallY: [[0, 0, 'out'], [0.3, -0.5, 'io'], [1.4, -0.45, 'io'], [1.9, 0, 'io']],
        ParamEyeBallX: [[0, 0, 'out'], [0.4, 0.55, 'io'], [1.4, 0.4, 'io'], [1.9, 0, 'io']],
        ParamMouthForm: [[0, 0, 'out'], [0.3, -0.45, 'io'], [1.4, -0.35, 'io'], [1.9, 0, 'io']],
        ParamBrowLY: [[0, 0, 'out'], [0.3, -0.55, 'io'], [1.4, -0.5, 'io'], [1.9, 0, 'io']],
        ParamBrowRY: [[0, 0, 'out'], [0.3, -0.55, 'io'], [1.4, -0.5, 'io'], [1.9, 0, 'io']],
        ParamWhaleEar: [[0, 0, 'out'], [0.3, -0.45, 'io'], [1.4, -0.4, 'io'], [1.9, 0, 'io']],
        ParamArmLA: [[0, 0, 'out'], [0.3, -0.3, 'io'], [1.4, -0.25, 'io'], [1.9, 0, 'io']],
        ParamArmRA: [[0, 0, 'out'], [0.3, -0.3, 'io'], [1.4, -0.25, 'io'], [1.9, 0, 'io']],
        ParamWaterGlow: [[0, 0.35, 'out'], [0.3, 0.15, 'io'], [1.9, 0.35, 'io']],
      },
    },

    /* 点头：表示「嗯嗯对对」，适合接话时用 */
    nod: {
      duration: 1.0,
      tracks: {
        ParamAngleY: [[0, 0, 'out'], [0.16, -9, 'io'], [0.36, 1, 'io'], [0.52, -6, 'io'], [1.0, 0, 'io']],
        ParamBodyAngleY: [[0, 0, 'out'], [0.16, -2, 'io'], [0.52, -1.5, 'io'], [1.0, 0, 'io']],
        ParamEyeLOpen: [[0, 1, 'out'], [0.2, 0.75, 'io'], [0.6, 0.9, 'io'], [1.0, 1, 'io']],
        ParamEyeROpen: [[0, 1, 'out'], [0.2, 0.75, 'io'], [0.6, 0.9, 'io'], [1.0, 1, 'io']],
        ParamMouthForm: [[0, 0, 'out'], [0.2, 0.3, 'io'], [1.0, 0, 'io']],
      },
    },

    /* 摇头：不赞同 / 撒娇否认 */
    sway: {
      duration: 1.2,
      tracks: {
        ParamAngleZ: [[0, 0, 'io'], [0.18, -5, 'io'], [0.44, 5, 'io'], [0.7, -4, 'io'], [0.96, 3, 'io'], [1.2, 0, 'io']],
        ParamAngleX: [[0, 0, 'io'], [0.18, -3, 'io'], [0.44, 3, 'io'], [0.7, -2, 'io'], [1.2, 0, 'io']],
        ParamMouthForm: [[0, 0, 'out'], [0.2, -0.6, 'io'], [0.9, -0.5, 'io'], [1.2, 0, 'io']],
        ParamBrowLY: [[0, 0, 'out'], [0.2, -0.35, 'io'], [1.2, 0, 'io']],
        ParamBrowRY: [[0, 0, 'out'], [0.2, 0.3, 'io'], [1.2, 0, 'io']],
        ParamWhaleEar: [[0, 0, 'out'], [0.2, 0.5, 'io'], [0.6, -0.4, 'io'], [1.2, 0, 'io']],
      },
    },

    /* 喷水 / 表演：DeepSeek 招牌动作，水花特效 + 尾巴上扬 */
    spout: {
      duration: 2.2,
      tracks: {
        ParamAngleY: [[0, 0, 'out'], [0.2, -10, 'io'], [0.9, -9, 'io'], [1.5, 0, 'io'], [2.2, 0, 'lin']],
        ParamAngleZ: [[0, 0, 'io'], [0.5, -3, 'io'], [1.5, 3, 'io'], [2.2, 0, 'io']],
        ParamBodyAngleY: [[0, 0, 'out'], [0.2, -2.5, 'io'], [1.5, 0, 'io'], [2.2, 0, 'lin']],
        ParamEyeLOpen: [[0, 1, 'out'], [0.2, 0.35, 'io'], [1.2, 0.4, 'io'], [2.2, 1, 'io']],
        ParamEyeROpen: [[0, 1, 'out'], [0.2, 0.35, 'io'], [1.2, 0.4, 'io'], [2.2, 1, 'io']],
        ParamEyeLSmile: [[0, 0, 'out'], [0.2, 0.8, 'io'], [1.2, 0.7, 'io'], [2.2, 0, 'io']],
        ParamEyeRSmile: [[0, 0, 'out'], [0.2, 0.8, 'io'], [1.2, 0.7, 'io'], [2.2, 0, 'io']],
        ParamMouthForm: [[0, 0, 'out'], [0.2, 0.7, 'io'], [1.4, 0.6, 'io'], [2.2, 0, 'io']],
        ParamTailLift: [[0, 0, 'back'], [0.35, 0.85, 'io'], [1.3, 0.7, 'io'], [2.2, 0, 'io']],
        ParamArmLA: [[0, 0, 'back'], [0.35, 0.85, 'io'], [1.3, 0.75, 'io'], [2.2, 0, 'io']],
        ParamArmRA: [[0, 0, 'back'], [0.35, 0.85, 'io'], [1.3, 0.75, 'io'], [2.2, 0, 'io']],
        ParamWaterGlow: [[0, 0.35, 'out'], [0.35, 1, 'io'], [1.6, 0.75, 'io'], [2.2, 0.35, 'io']],
      },
    },

    /* 打招呼：抬手挥一挥（进场欢迎） */
    wave: {
      duration: 1.8,
      tracks: {
        ParamArmRA: [[0, 0, 'back'], [0.25, 0.95, 'io'], [0.55, 0.85, 'io'], [1.3, 0.9, 'io'], [1.8, 0, 'io']],
        ParamAngleZ: [[0, 0, 'io'], [0.3, -4, 'io'], [1.2, 2, 'io'], [1.8, 0, 'io']],
        ParamEyeLSmile: [[0, 0, 'out'], [0.3, 0.75, 'io'], [1.4, 0.6, 'io'], [1.8, 0, 'io']],
        ParamEyeRSmile: [[0, 0, 'out'], [0.3, 0.75, 'io'], [1.4, 0.6, 'io'], [1.8, 0, 'io']],
        ParamMouthForm: [[0, 0, 'out'], [0.3, 0.85, 'io'], [1.4, 0.7, 'io'], [1.8, 0, 'io']],
        ParamWhaleEar: [[0, 0, 'out'], [0.25, 0.55, 'io'], [0.7, -0.25, 'io'], [1.1, 0.3, 'io'], [1.8, 0, 'io']],
      },
    },
  };

  /* ============================== 六、表情组 ==============================
   * 表情 = 一组参数的目标值（持续保持），比动作优先级低：动作会临时覆盖，
   * 动作结束回到表情。索引与 stage.js 的 expression(idx) 对齐。
   */
  const EXPRESSIONS = [
    { name: 'neutral', p: {} },
    // 注意：笑眼只用 ParamEyeLSmile 画 ∩ 弧，绝不要同时把 Open 调低。
    // 之前 happy 里写了 EyeLOpen: 0.7 —— 眼睑只盖 30%，
    // 结果虹膜上半截和眼白同时露出一条，看着像翻白眼。
    { name: 'smile', p: { ParamMouthForm: 0.6, ParamEyeLSmile: 0.55, ParamEyeRSmile: 0.55, ParamBrowLY: 0.15, ParamBrowRY: 0.15 } },
    { name: 'happy', p: { ParamMouthForm: 0.95, ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamWaterGlow: 0.7 } },
    { name: 'surprised', p: { ParamEyeLOpen: 1.35, ParamEyeROpen: 1.35, ParamMouthOpenY: 0.35, ParamBrowLY: 0.9, ParamBrowRY: 0.9 } },
    { name: 'shy', p: { ParamAngleY: -5, ParamMouthForm: -0.4, ParamEyeBallY: -0.4, ParamBrowLY: -0.5, ParamBrowRY: -0.5, ParamWhaleEar: -0.4 } },
    { name: 'thinking', p: { ParamEyeBallX: 0.5, ParamEyeBallY: 0.45, ParamMouthForm: -0.2, ParamBrowLY: -0.25, ParamBrowRY: 0.35, ParamAngleZ: 2.5 } },
    { name: 'sleepy', p: { ParamEyeLOpen: 0.25, ParamEyeROpen: 0.25, ParamMouthForm: -0.15, ParamBrowLY: -0.3, ParamBrowRY: -0.3, ParamWaterGlow: 0.15 } },
    { name: 'excited', p: { ParamMouthOpenY: 0.25, ParamMouthForm: 1, ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamBrowLY: 0.7, ParamBrowRY: 0.7, ParamWaterGlow: 0.9 } },
  ];

  /* ============================== 七、渲染：分层 SVG ==============================
   * 结构自上而下 = 后景→前景，group 的 transform 由 rig 每帧驱动。
   * 全部用 viewBox 400x660 的坐标系，导出 PSD 时按这个比例切图即可。
   */
  const VB = { w: 400, h: 660 };

  function buildSvg() {
    const C = PALETTE;
    return `
<svg class="whale-rig" viewBox="0 0 ${VB.w} ${VB.h}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMax meet">
  <defs>
    <linearGradient id="wgHair" x1="0" y1="0" x2="0.1" y2="1">
      <stop offset="0%" stop-color="#f2faff"/><stop offset="38%" stop-color="${C.hair}"/><stop offset="100%" stop-color="${C.hairShade}"/>
    </linearGradient>
    <linearGradient id="wgHairBack" x1="0" y1="0" x2="0.1" y2="1">
      <stop offset="0%" stop-color="${C.hairShade}"/><stop offset="55%" stop-color="#8fb6e4"/><stop offset="100%" stop-color="${C.hairDeep}"/>
    </linearGradient>
    <linearGradient id="wgSkin" x1="0.1" y1="0" x2="0.5" y2="1">
      <stop offset="0%" stop-color="#fffaf8"/><stop offset="52%" stop-color="${C.skin}"/><stop offset="100%" stop-color="#f3d3cd"/>
    </linearGradient>
    <linearGradient id="wgWhale" x1="0.1" y1="0" x2="0.4" y2="1">
      <stop offset="0%" stop-color="#8fb4fb"/><stop offset="55%" stop-color="${C.whale}"/><stop offset="100%" stop-color="${C.whaleDeep}"/>
    </linearGradient>
    <linearGradient id="wgIris" x1="0.2" y1="0" x2="0.5" y2="1">
      <stop offset="0%" stop-color="#12205e"/><stop offset="42%" stop-color="${C.iris}"/><stop offset="82%" stop-color="#7ec8ff"/><stop offset="100%" stop-color="#c9efff"/>
    </linearGradient>
    <linearGradient id="wgCloth" x1="0.15" y1="0" x2="0.4" y2="1">
      <stop offset="0%" stop-color="#ffffff"/><stop offset="55%" stop-color="#f3f8ff"/><stop offset="100%" stop-color="#dbe7fb"/>
    </linearGradient>
    <linearGradient id="wgSkirt" x1="0.15" y1="0" x2="0.35" y2="1">
      <stop offset="0%" stop-color="#c6dcf7"/><stop offset="45%" stop-color="#a3c1ea"/><stop offset="100%" stop-color="#7c9fd6"/>
    </linearGradient>
    <linearGradient id="wgSleeve" x1="0.2" y1="0" x2="0.5" y2="1">
      <stop offset="0%" stop-color="#e6f0fd"/><stop offset="55%" stop-color="#b9d0ee"/><stop offset="100%" stop-color="#8fb0da"/>
    </linearGradient>
    <radialGradient id="wgBlush" cx="50%" cy="50%">
      <stop offset="0%" stop-color="${C.blush}" stop-opacity="0.8"/><stop offset="100%" stop-color="${C.blush}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="wgGlow" cx="50%" cy="50%">
      <stop offset="0%" stop-color="#a8e8ff" stop-opacity="0.75"/><stop offset="55%" stop-color="${C.brand}" stop-opacity="0.22"/><stop offset="100%" stop-color="${C.brand}" stop-opacity="0"/>
    </radialGradient>
    <filter id="wgGlowF" x="-120%" y="-120%" width="340%" height="340%">
      <feGaussianBlur stdDeviation="2.4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>

  <!-- ============ 背后氛围光 ============ -->
  <g id="p-glow">
    <ellipse id="p-glow-blob" cx="200" cy="330" rx="140" ry="170" fill="url(#wgGlow)" opacity="0.42"/>
  </g>

  <!-- ============ 鲸尾（画在身体后面，从裙摆下缘整片探出） ============ -->
  <g id="p-tail">
    <g id="p-tail-tip">
      <!-- 尾柄：从裙下伸出的窄根 -->
      <path d="M186 540 L214 540 L210 600 L190 600 Z" fill="${C.whaleDeep}"/>
      <!-- 尾鳍：横向展开，比裙摆更宽，保证两侧都露在裙子外面 -->
      <path d="M200 588
               C186 588 168 592 156 600
               C128 606 82 618 44 640
               C78 656 132 646 172 620
               L200 602
               L228 620
               C268 646 322 656 356 640
               C318 618 272 606 244 600
               C232 592 214 588 200 588 Z"
            fill="url(#wgWhale)" stroke="${C.line}" stroke-width="2.2" stroke-linejoin="round"/>
      <!-- 鳍面纹路 -->
      <path d="M176 606 C150 618 116 630 82 640" stroke="${C.whaleBelly}" stroke-width="2.4"
            fill="none" opacity="0.45" stroke-linecap="round"/>
      <path d="M224 606 C250 618 284 630 318 640" stroke="${C.whaleBelly}" stroke-width="2.4"
            fill="none" opacity="0.45" stroke-linecap="round"/>
      <!-- 中央缺刻 -->
      <path d="M200 604 L200 632" stroke="${C.line}" stroke-width="1.8" fill="none" opacity="0.35"/>
    </g>
  </g>

  <!-- ============ 后发（长发） ============
       之前是一个完美的半圆罩，读起来像泳帽/头巾。
       现在：顶部贴头，两侧在耳下「收腰」变窄，再向外散开成长发，
       下摆分成左右两束并收成尖角，轮廓不再是圆。 -->
  <g id="p-hair-back">
    <!-- 主发块 -->
    <path d="M200 92
             C142 92 100 138 98 200
             C96 240 104 272 108 306
             C110 330 106 356 98 384
             C112 372 126 368 138 374
             C128 412 130 456 142 496
             C158 464 172 446 190 438
             L210 438
             C228 446 242 464 258 496
             C270 456 272 412 262 374
             C274 368 288 372 302 384
             C294 356 290 330 292 306
             C296 272 304 240 302 200
             C300 138 258 92 200 92 Z"
          fill="url(#wgHairBack)"/>
    <!-- 发缕：几道深浅弧线，把整块头发切开，破掉平面感 -->
    <path d="M200 104 C152 104 114 144 112 198" stroke="${C.hairGlow}" stroke-width="3"
          fill="none" opacity="0.28" stroke-linecap="round"/>
    <path d="M130 190 C120 232 120 272 126 306 C130 330 128 352 122 374"
          stroke="#6d97c8" stroke-width="2.2" fill="none" opacity="0.4" stroke-linecap="round"/>
    <path d="M270 190 C280 232 280 272 274 306 C270 330 272 352 278 374"
          stroke="#6d97c8" stroke-width="2.2" fill="none" opacity="0.4" stroke-linecap="round"/>
    <path d="M158 250 C154 300 156 350 164 396" stroke="#6d97c8" stroke-width="1.8"
          fill="none" opacity="0.3" stroke-linecap="round"/>
    <path d="M242 250 C246 300 244 350 236 396" stroke="#6d97c8" stroke-width="1.8"
          fill="none" opacity="0.3" stroke-linecap="round"/>
  </g>

  <!-- ============ 身体 ============ -->
  <g id="p-body">
    <!-- 脖子：短。可见段只留 40px 左右（下巴 353 → 肩线 372 一带）。
         之前画到 y=394 太长，看着像一根白管子插在胸口。 -->
    <path d="M186 316
             C185 336 181 352 174 366
             C170 372 167 375 165 378
             L235 378
             C233 375 230 372 226 366
             C219 352 215 336 214 316 Z"
          fill="url(#wgSkin)"/>
    <!-- 斜方肌阴影 -->
    <path d="M186 328 C182 344 176 358 168 370" stroke="${C.skinShade}" stroke-width="3"
          fill="none" opacity="0.3" stroke-linecap="round"/>
    <path d="M214 328 C218 344 224 358 232 370" stroke="${C.skinShade}" stroke-width="3"
          fill="none" opacity="0.3" stroke-linecap="round"/>
    <!-- 下巴投影 -->
    <path d="M186 320 C193 336 207 336 214 320 L214 332 C207 346 193 346 186 332 Z"
          fill="${C.skinShade}" opacity="0.5"/>

    <!-- 上衣：关键比例 —— 躯干最宽处必须「窄于头宽」。
         头宽 168（x116~284），这里肩最宽 152（x124~276），腰收到 128（x136~264）。 -->
    <path d="M200 370
             C174 370 152 376 140 388
             C128 400 124 418 124 434
             C124 452 128 470 136 486
             L264 486
             C272 470 276 452 276 434
             C276 418 272 400 260 388
             C248 376 226 370 200 370 Z"
          fill="url(#wgCloth)" stroke="${C.line}" stroke-width="2.2" stroke-linejoin="round"/>
    <!-- 体积光：左侧亮面 -->
    <path d="M200 370 C174 370 152 376 140 388
             C130 399 126 414 125 430
             C138 404 166 392 200 392 Z"
          fill="#ffffff" opacity="0.55"/>
    <!-- 体积影：右侧暗面，做出圆柱转折 -->
    <path d="M276 434 C276 452 272 470 264 486 L242 486
             C254 466 260 448 260 424
             C270 428 274 430 276 434 Z"
          fill="#b9cfee" opacity="0.75"/>
    <!-- 胸部起伏：这两道弧线试过 2.6px 也试过 2px，任何可见的写法都会读成
         「内衣轮廓 / 绷带」。白色水手服上的胸型必须靠整体明暗（上面的
         左侧亮面 + 右侧暗面）来暗示，不要再画局部弧线。 -->

    <!-- 腋下褶：布料被手臂拉起来的走向 -->
    <path d="M134 404 C142 424 144 448 142 470" stroke="#c2d6f0" stroke-width="2"
          fill="none" opacity="0.5" stroke-linecap="round"/>
    <path d="M266 404 C258 424 256 448 258 470" stroke="#c2d6f0" stroke-width="2"
          fill="none" opacity="0.5" stroke-linecap="round"/>
    <!-- 肩部高光 -->
    <path d="M148 400 C160 388 178 382 200 382 C222 382 240 388 252 400"
          stroke="#ffffff" stroke-width="3" fill="none" opacity="0.7" stroke-linecap="round"/>

    <!-- 水手领：两片翻领形成的 V 领 -->
    <path d="M197 366 C184 368 172 374 164 384 L200 412 Z" fill="${C.brand}"/>
    <path d="M203 366 C216 368 228 374 236 384 L200 412 Z" fill="${C.brand}"/>
    <!-- 领结 -->
    <path d="M200 408 L192 420 L200 432 L208 420 Z" fill="${C.brandDeep}"/>
    <circle cx="200" cy="410" r="4.2" fill="${C.brand}"/>

    <!-- 胸前鲸鱼徽章 -->
    <g id="p-badge" transform="translate(200 446)">
      <circle r="16" fill="#ffffff" opacity="0.75"/>
      <circle r="16" fill="none" stroke="${C.brand}" stroke-width="1.4" opacity="0.45"/>
      <path d="M-9 1 C-7 -6 2 -8 6 -2 C8 2 5 6 0 6 C-3 6 -5 5 -6 3
               C-4 4 0 3 1 1 C0 0 -3 0 -5 1 C-6 2 -8 2 -9 1 Z"
            fill="${C.brand}" opacity="0.85"/>
    </g>

    <!-- 腰封 -->
    <path d="M136 482 L264 482 L268 506 L132 506 Z" fill="${C.brandDeep}"/>
    <path d="M136 484 L264 484" stroke="${C.irisGlow}" stroke-width="1.5" opacity="0.5"/>
    <!-- 腰封扣：小结 -->
    <path d="M200 482 C193 490 187 495 184 501 C191 504 197 499 200 494
             C203 499 209 504 216 501 C213 495 207 490 200 482 Z"
          fill="${C.irisGlow}" opacity="0.92"/>

    <!-- 裙摆：A 字百褶。起点抬到 500（塞进腰封下沿 506 之内），
         否则腰封和裙子中间会露出一条白色躯干。 -->
    <path d="M136 500
             C124 530 112 562 96 592
             C116 604 138 598 152 588
             C164 602 184 600 200 590
             C216 600 236 602 248 588
             C262 598 284 604 304 592
             C288 562 276 530 264 500 Z"
          fill="url(#wgSkirt)" stroke="${C.line}" stroke-width="2.2" stroke-linejoin="round"/>
    <!-- 褶裥暗面（每个褶子的背光侧） -->
    <path d="M152 502 C146 532 140 560 134 586 L144 590
             C150 560 156 532 162 502 Z" fill="#6b90c8" opacity="0.45"/>
    <path d="M176 502 C172 532 169 560 167 586 L177 590
             C179 560 182 532 186 502 Z" fill="#6b90c8" opacity="0.45"/>
    <path d="M203 502 C202 532 201 560 200 586 L210 590
             C211 560 212 532 213 502 Z" fill="#6b90c8" opacity="0.4"/>
    <path d="M227 502 C230 532 232 560 235 586 L245 590
             C242 560 239 532 236 502 Z" fill="#6b90c8" opacity="0.45"/>
    <path d="M251 502 C256 532 261 560 265 586 L275 590
             C270 560 264 532 259 502 Z" fill="#6b90c8" opacity="0.45"/>
    <!-- 褶裥亮面（迎光侧，紧贴暗条） -->
    <path d="M144 502 C138 532 132 560 127 586" stroke="#e8f2ff" stroke-width="2.4"
          fill="none" opacity="0.55" stroke-linecap="round"/>
    <path d="M168 502 C164 532 162 560 160 586" stroke="#e8f2ff" stroke-width="2.4"
          fill="none" opacity="0.55" stroke-linecap="round"/>
    <path d="M189 502 C188 532 187 560 186 586" stroke="#e8f2ff" stroke-width="2.2"
          fill="none" opacity="0.5" stroke-linecap="round"/>
    <path d="M216 502 C217 532 218 560 219 586" stroke="#e8f2ff" stroke-width="2.2"
          fill="none" opacity="0.5" stroke-linecap="round"/>
    <path d="M243 502 C247 532 251 560 254 586" stroke="#e8f2ff" stroke-width="2.4"
          fill="none" opacity="0.55" stroke-linecap="round"/>
    <path d="M267 502 C273 532 279 560 284 586" stroke="#e8f2ff" stroke-width="2.4"
          fill="none" opacity="0.5" stroke-linecap="round"/>
    <!-- 腰部往下压的软影：证明布料是从腰上挂下来的 -->
    <path d="M136 502 C176 516 224 516 264 502
             C224 528 176 528 136 502 Z" fill="#6b90c8" opacity="0.32"/>
    <!-- 下沿滚边 -->
    <path d="M96 592 C116 604 138 598 152 588 C164 602 184 600 200 590
             C216 600 236 602 248 588 C262 598 284 604 304 592"
          stroke="${C.brand}" stroke-width="3" fill="none" opacity="0.65" stroke-linejoin="round"/>
  </g>

  <!-- ============ 手臂 ============
       躯干最宽处 x124~276，所以肩点 128 / 272，肘部垂到 x101 / x299。 -->
  <g id="p-arm-l">
    <circle cx="128" cy="388" r="15" fill="url(#wgSleeve)" stroke="${C.line}" stroke-width="2"/>
    <path d="M128 382 C112 402 105 426 104 452"
          stroke="${C.line}" stroke-width="23" fill="none" stroke-linecap="round"/>
    <path d="M128 382 C112 402 105 426 104 452"
          stroke="url(#wgSleeve)" stroke-width="19" fill="none" stroke-linecap="round"/>
    <path d="M92 452 C96 468 111 472 118 460" stroke="${C.brand}" stroke-width="6.5"
          fill="none" opacity="0.85" stroke-linecap="round"/>
    <path d="M105 466 C102 488 101 506 101 520"
          stroke="${C.line}" stroke-width="16" fill="none" stroke-linecap="round"/>
    <path d="M105 466 C102 488 101 506 101 520"
          stroke="url(#wgSkin)" stroke-width="12" fill="none" stroke-linecap="round"/>
    <ellipse cx="101" cy="532" rx="10.5" ry="11.5" fill="url(#wgSkin)" stroke="${C.line}" stroke-width="2"/>
  </g>
  <g id="p-arm-r">
    <circle cx="272" cy="388" r="15" fill="url(#wgSleeve)" stroke="${C.line}" stroke-width="2"/>
    <path d="M272 382 C288 402 295 426 296 452"
          stroke="${C.line}" stroke-width="23" fill="none" stroke-linecap="round"/>
    <path d="M272 382 C288 402 295 426 296 452"
          stroke="url(#wgSleeve)" stroke-width="19" fill="none" stroke-linecap="round"/>
    <path d="M308 452 C304 468 289 472 282 460" stroke="${C.brand}" stroke-width="6.5"
          fill="none" opacity="0.85" stroke-linecap="round"/>
    <path d="M295 466 C298 488 299 506 299 520"
          stroke="${C.line}" stroke-width="16" fill="none" stroke-linecap="round"/>
    <path d="M295 466 C298 488 299 506 299 520"
          stroke="url(#wgSkin)" stroke-width="12" fill="none" stroke-linecap="round"/>
    <ellipse cx="299" cy="532" rx="10.5" ry="11.5" fill="url(#wgSkin)" stroke="${C.line}" stroke-width="2"/>
  </g>

  <!-- ============ 头部 ============ -->
  <g id="p-head">
    <!-- 鲸鳍耳 -->
    <g id="p-ear-l">
      <path d="M112 208 C90 190 74 164 78 140 C96 150 112 170 122 192 Z"
            fill="url(#wgWhale)" stroke="${C.line}" stroke-width="2.1" stroke-linejoin="round"/>
      <path d="M92 156 C98 172 104 186 110 196" stroke="${C.whaleBelly}" stroke-width="1.8"
            fill="none" opacity="0.5" stroke-linecap="round"/>
    </g>
    <g id="p-ear-r">
      <path d="M288 208 C310 190 326 164 322 140 C304 150 288 170 278 192 Z"
            fill="url(#wgWhale)" stroke="${C.line}" stroke-width="2.1" stroke-linejoin="round"/>
      <path d="M308 156 C302 172 296 186 290 196" stroke="${C.whaleBelly}" stroke-width="1.8"
            fill="none" opacity="0.5" stroke-linecap="round"/>
    </g>

    <!-- 脸：宽额 + 收下巴，不是圆球 -->
    <path d="M200 128
             C252 128 284 164 284 222
             C284 272 262 314 228 338
             C217 346 207 352 200 353
             C193 352 183 346 172 338
             C138 314 116 272 116 222
             C116 164 148 128 200 128 Z"
          fill="url(#wgSkin)" stroke="${C.line}" stroke-width="2.2"/>

    <!-- 腮红 -->
    <ellipse id="p-blush-l" cx="136" cy="280" rx="26" ry="15" fill="url(#wgBlush)"/>
    <ellipse id="p-blush-r" cx="264" cy="280" rx="26" ry="15" fill="url(#wgBlush)"/>

    <!-- 鼻 -->
    <path d="M199 288 q3.5 3 0 5" stroke="${C.skinShade}" stroke-width="2.2" fill="none"
          stroke-linecap="round" opacity="0.85"/>

    <!-- 左眼 -->
    <g id="p-eye-l">
      <ellipse cx="160" cy="238" rx="27" ry="31" fill="#ffffff"/>
      <g id="p-iris-l">
        <ellipse cx="160" cy="241" rx="21" ry="26" fill="url(#wgIris)"/>
        <ellipse cx="160" cy="252" rx="14" ry="12" fill="#9bddff" opacity="0.55"/>
        <!-- 瞳孔：放大到 rx11 ry13，才不会被高光切成月牙 -->
        <ellipse cx="160" cy="232" rx="11" ry="13" fill="#0d1745"/>
        <!-- 高光：挪到左上角、缩小，只做反光不遮挡瞳孔 -->
        <circle cx="152" cy="224" r="5" fill="#ffffff"/>
        <circle cx="168" cy="254" r="3.2" fill="#ffffff" opacity="0.85"/>
      </g>
      <!-- 上睫毛（给 ID：闭眼时要跟着眼睑一起收，否则闭眼后睫毛还挂在脸上） -->
      <path id="p-lash-l" d="M131 230 C138 201 182 201 189 230" stroke="#16224f" stroke-width="7"
            fill="none" stroke-linecap="round"/>
      <!-- 下眼线 -->
      <path id="p-under-l" d="M136 259 C146 269 174 269 184 259" stroke="#16224f" stroke-width="2.4"
            fill="none" opacity="0.45" stroke-linecap="round"/>
      <ellipse id="p-lid-l" cx="160" cy="236" rx="28" ry="34" fill="url(#wgSkin)"/>
      <!-- 笑眼弧线必须在眼睑「之后」画，否则会被不透明的眼睑整个盖住、永远看不见 -->
      <path id="p-smile-l" d="M138 244 q22 -20 44 0" stroke="#16224f" stroke-width="4"
            fill="none" stroke-linecap="round" opacity="0"/>
    </g>
    <!-- 右眼 -->
    <g id="p-eye-r">
      <ellipse cx="240" cy="238" rx="27" ry="31" fill="#ffffff"/>
      <g id="p-iris-r">
        <ellipse cx="240" cy="241" rx="21" ry="26" fill="url(#wgIris)"/>
        <ellipse cx="240" cy="252" rx="14" ry="12" fill="#9bddff" opacity="0.55"/>
        <ellipse cx="240" cy="232" rx="11" ry="13" fill="#0d1745"/>
        <circle cx="232" cy="224" r="5" fill="#ffffff"/>
        <circle cx="248" cy="254" r="3.2" fill="#ffffff" opacity="0.85"/>
      </g>
      <path id="p-lash-r" d="M211 230 C218 201 262 201 269 230" stroke="#16224f" stroke-width="7"
            fill="none" stroke-linecap="round"/>
      <path id="p-under-r" d="M216 259 C226 269 254 269 264 259" stroke="#16224f" stroke-width="2.4"
            fill="none" opacity="0.45" stroke-linecap="round"/>
      <ellipse id="p-lid-r" cx="240" cy="236" rx="28" ry="34" fill="url(#wgSkin)"/>
      <path id="p-smile-r" d="M218 244 q22 -20 44 0" stroke="#16224f" stroke-width="4"
            fill="none" stroke-linecap="round" opacity="0"/>
    </g>

    <!-- 眉毛：细、平、尾端下垂一点，才不会显凶 -->
    <path id="p-brow-l" d="M140 206 q20 -7 40 -1" stroke="${C.hairDeep}" stroke-width="2.6"
          fill="none" stroke-linecap="round" opacity="0.6"/>
    <path id="p-brow-r" d="M220 205 q20 -6 40 1" stroke="${C.hairDeep}" stroke-width="2.6"
          fill="none" stroke-linecap="round" opacity="0.6"/>

    <!-- 嘴 -->
    <g id="p-mouth">
      <path id="p-mouth-in" d="M178 306 q22 20 44 0 q-22 9 -44 0 Z" fill="${C.mouthIn}" opacity="0"/>
      <path id="p-mouth-line" d="M178 306 q22 7 44 0" stroke="${C.mouth}" stroke-width="3.2"
            fill="none" stroke-linecap="round"/>
    </g>

    <!-- 刘海：一整块实心厚刘海。
         注意：这条路径必须「单向」描边（从左到右沿下缘走一圈），
         不能来回折返 —— 折返会让路径自相交，nonzero 填充规则把重叠区
         互相抵消，表现为额头上出现两块透明扇形（之前就是这个 bug）。 -->
    <g id="p-hair-front">
      <!-- 外层：贴着头顶的整片头发，左右两侧垂到耳前 -->
      <path d="M200 112
               C154 112 122 140 116 182
               C112 208 116 232 126 254
               C130 224 136 198 148 178
               C160 158 178 148 200 148
               C222 148 240 158 252 178
               C264 198 270 224 274 254
               C284 232 288 208 284 182
               C278 140 246 112 200 112 Z"
            fill="url(#wgHair)"/>
      <!-- 内层：实心刘海块。
           两条硬约束：
           1) 路径必须单向闭合，绝不折返 —— 折返会自相交，nonzero 填充规则
              把重叠区抵消掉，额头中间就漏出底色（踩过两次）。
           2) 下缘锯齿必须停在眉线之上。眉毛 y≈205、眼睛顶部 y=207，
              所以锯齿最低只能到 y=200，否则会把眼睛整个盖住。 -->
      <path d="M122 190
               C122 150 156 122 200 122
               C244 122 278 150 278 190
               L278 192
               L268 198 L262 178 L254 200 L246 180 L238 196 L230 178
               L222 200 L214 180 L206 196 L200 178 L194 196 L186 180
               L178 200 L170 178 L162 196 L154 180 L146 200 L138 178
               L132 198 L122 192 Z"
            fill="url(#wgHair)"/>
      <!-- 发丝分缝：极细两条，只是纹理 -->
      <path d="M196 132 C193 158 192 186 194 210" stroke="${C.hairShade}" stroke-width="1.5"
            fill="none" opacity="0.45"/>
      <path d="M216 134 C220 160 221 186 220 208" stroke="${C.hairShade}" stroke-width="1.5"
            fill="none" opacity="0.35"/>
      <!-- 高光带：头发有光泽才不像塑料片 -->
      <path d="M158 156 C144 176 138 200 140 222" stroke="#ffffff" stroke-width="3.4"
            fill="none" opacity="0.5" stroke-linecap="round"/>
      <path d="M246 158 C254 176 258 198 256 216" stroke="#ffffff" stroke-width="2.2"
            fill="none" opacity="0.32" stroke-linecap="round"/>
    </g>

    <!-- 侧发：从鬓角垂到肩前的发缕，要有弧度、末端收尖，不能是直刀片 -->
    <g id="p-hair-side-l">
      <path d="M130 176
               C118 214 114 258 118 302
               C120 326 126 344 132 356
               C136 336 138 314 140 292
               C142 258 144 222 148 194 Z"
            fill="url(#wgHair)" stroke="${C.line}" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M133 202 C126 240 123 278 126 312" stroke="#ffffff" stroke-width="2.2"
            fill="none" opacity="0.45" stroke-linecap="round"/>
      <path d="M141 200 C138 232 137 264 138 292" stroke="${C.hairShade}" stroke-width="1.6"
            fill="none" opacity="0.5" stroke-linecap="round"/>
    </g>
    <g id="p-hair-side-r">
      <path d="M270 176
               C282 214 286 258 282 302
               C280 326 274 344 268 356
               C264 336 262 314 260 292
               C258 258 256 222 252 194 Z"
            fill="url(#wgHair)" stroke="${C.line}" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M267 202 C274 240 277 278 274 312" stroke="#ffffff" stroke-width="2.2"
            fill="none" opacity="0.45" stroke-linecap="round"/>
      <path d="M259 200 C262 232 263 264 262 292" stroke="${C.hairShade}" stroke-width="1.6"
            fill="none" opacity="0.5" stroke-linecap="round"/>
    </g>

    <!-- 发带 + 悬浮水珠 -->
    <g id="p-band">
      <path d="M120 172 C158 144 242 144 280 172 C242 158 158 158 120 172 Z" fill="${C.brand}"/>
      <circle cx="200" cy="158" r="4.6" fill="${C.irisGlow}"/>
    </g>
    <g id="p-star">
      <circle r="5" fill="${C.brand}" filter="url(#wgGlowF)"/>
      <circle r="2.2" fill="#ffffff"/>
    </g>
    <g id="p-splash" opacity="0">
      <circle cx="150" cy="112" r="5" fill="${C.irisGlow}" opacity="0.9"/>
      <circle cx="252" cy="100" r="4" fill="${C.irisGlow}" opacity="0.8"/>
      <circle cx="200" cy="86" r="6" fill="${C.irisGlow}" opacity="0.85"/>
      <circle cx="118" cy="142" r="3.5" fill="${C.irisGlow}" opacity="0.7"/>
      <circle cx="286" cy="138" r="3.5" fill="${C.irisGlow}" opacity="0.7"/>
    </g>
  </g>
</svg>`;
  }

  /* ============================== 八、Rig 主体 ============================== */
  class WhaleRig {
    constructor(host) {
      this.host = host;
      this.coreModel = new CoreModelStub(this);
      this.internalModel = { coreModel: this.coreModel, width: VB.w, height: VB.h };
      this.width = VB.w;
      this.height = VB.h;

      this.values = {};
      PARAM_IDS.forEach((id) => { this.values[id] = PARAMS[id].def; });

      // 动作 / 表情状态
      this.exprIndex = 0;
      this.exprValues = {};
      this.motionName = '';
      this.motionTime = 0;
      this.autoIdle = true;

      // 视线
      this.focusX = 0;
      this.focusY = 0;
      this.eyeX = 0;
      this.eyeY = 0;

      // 眨眼
      this.blinkTimer = 2.5;
      this.blinkPhase = -1;

      // 外部口型（stage.js 每帧喂进来）。不能被动作轨道覆盖掉，
      // 否则说话时嘴不会动 —— 单独存字段，在 update 里最后合并。
      this.mouth = 0;
      this.mouthAt = 0;

      // 二级弹簧：头发、尾巴、耳鳍。
      // 头发的刚度调高、幅度调小 —— 头发是"长在头上"的，
      // 跟随要快，只留一点点余韵。之前刚度太低，一转头发就明显滞后，
      // 看起来就是"头和头发不同步"。
      this.springs = {
        hairFront: new Spring(240, 22),
        hairSide: new Spring(200, 20),
        hairBack: new Spring(150, 16),
        tail: new Spring(70, 9),
        ear: new Spring(260, 20),
      };

      // 外部覆盖层：setParameterValueById 写进来的值要能"按住"一会儿，
      // 否则会被下一条 _baseline() 立刻抹掉（外部根本没法控制模型）。
      this.overrides = {};
      this.overrideTTL = 700;   // ms

      this.time = 0;
      this.destroyed = false;
      this.nodes = {};
      this._last = 0;
      this._raf = 0;

      this._build();
    }

    /* ---------------- 构建 DOM ---------------- */
    _build() {
      const wrap = document.createElement('div');
      wrap.className = 'whale-rig-wrap';
      wrap.innerHTML = buildSvg();
      this.host.appendChild(wrap);
      this.wrap = wrap;
      this.svg = wrap.querySelector('svg');
      const q = (id) => this.svg.querySelector('#' + id);
      this.nodes = {
        glow: q('p-glow'), glowBlob: q('p-glow-blob'),
        tail: q('p-tail'), tailTip: q('p-tail-tip'),
        hairBack: q('p-hair-back'),
        body: q('p-body'),
        armL: q('p-arm-l'), armR: q('p-arm-r'),
        head: q('p-head'),
        earL: q('p-ear-l'), earR: q('p-ear-r'),
        hairFront: q('p-hair-front'),
        hairSideL: q('p-hair-side-l'), hairSideR: q('p-hair-side-r'),
        star: q('p-star'), band: q('p-band'),
        eyeL: q('p-eye-l'), eyeR: q('p-eye-r'),
        irisL: q('p-iris-l'), irisR: q('p-iris-r'),
        lidL: q('p-lid-l'), lidR: q('p-lid-r'),
        lashL: q('p-lash-l'), lashR: q('p-lash-r'),
        underL: q('p-under-l'), underR: q('p-under-r'),
        smileL: q('p-smile-l'), smileR: q('p-smile-r'),
        browL: q('p-brow-l'), browR: q('p-brow-r'),
        mouthIn: q('p-mouth-in'), mouthLine: q('p-mouth-line'),
        blushL: q('p-blush-l'), blushR: q('p-blush-r'),
        splash: q('p-splash'),
      };
      // 眨眼遮罩：锚点由 render 里的 translate/scale 给出，这里不用 CSS transform
      this.render();
    }

    /* ---------------- 参数读写（Live2D 同构 API） ---------------- */
    /** 写参数。记进 override 层并一直按住（切动作时清空），
     *  这样调试台滑杆、外部脚本才真的"按得住"；
     *  传 ttl 可以只按住一小会儿。 */
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

    /* ---------------- 口型 / 交互 ---------------- */
    /** stage.js 每帧调用一次。带时间戳，超过 0.5 秒没更新就当闭嘴，
     *  这样语音结束、模型切走时嘴不会卡在张开状态。 */
    setMouth(open) {
      this.mouth = clamp(open || 0, 0, 1);
      this.mouthAt = performance.now();
    }

    focus(x, y) {
      this.focusX = clamp(x || 0, -1, 1);
      this.focusY = clamp(y || 0, -1, 1);
    }

    /* ---------------- 动作 ---------------- */
    motion(name) {
      const key = MOTIONS[name] ? name : 'idle';
      this.motionName = key;
      this.motionTime = 0;
      // 换动作时清掉旧的手动覆盖，否则上一次拖的滑杆会压住新动作
      this.overrides = {};
      if (key === 'idle') {
        // idle 由渲染循环自己走基线，不要重复播
        this.values.ParamAngleY = 0;
      }
      return true;
    }

    expression(i) {
      const idx = Number(i);
      if (!Number.isFinite(idx)) return;
      this.exprIndex = clamp(Math.floor(idx), 0, EXPRESSIONS.length - 1);
      this.exprValues = EXPRESSIONS[this.exprIndex].p || {};
      // 表情写进 baseline，动作播完回到这里，不会弹成中性脸
      return true;
    }

    /* ---------------- 每帧推进 ---------------- */
    update(dt) {
      if (this.destroyed) return;
      const step = clamp(dt || 0.016, 0.001, 0.1);
      this.time += step;

      // 1) 动作轨道 → 参数（叠加在表情 baseline 之上）
      this._applyMotion(step);

      // 2) 眨眼
      this._tickBlink(step);

      // 3) 视线平滑跟随
      const eyeTargetX = this.focusX * 0.45 + (this.values.ParamEyeBallX || 0) * 0.6;
      const eyeTargetY = this.focusY * 0.4 + (this.values.ParamEyeBallY || 0) * 0.6;
      this.eyeX += (eyeTargetX - this.eyeX) * Math.min(1, step * 9);
      this.eyeY += (eyeTargetY - this.eyeY) * Math.min(1, step * 9);

      // 4) 二级弹簧：头动了，头发尾巴跟着甩
      const headX = this.values.ParamAngleX || 0;
      const headY = this.values.ParamAngleY || 0;
      const bodyX = this.values.ParamBodyAngleX || 0;
      this.springs.hairFront.target = -(headX * 0.55 + bodyX * 0.3);
      this.springs.hairSide.target = -(headX * 0.4 + headY * 0.15);
      this.springs.hairBack.target = -(headX * 0.3 + bodyX * 0.4);
      this.springs.tail.target = -(bodyX * 0.5 + (this.values.ParamTailSway || 0) * 30);
      this.springs.ear.target = -(headX * 0.3) + (this.values.ParamWhaleEar || 0) * 26;
      Object.values(this.springs).forEach((s) => s.update(step));

      // 5) 外部口型优先：说话时嘴必须跟着音量走，
      //    只有外部不再喂数据（超过 0.5 秒）才用动作/表情里的值
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

      // idle / talk 是循环的；其他动作播完自动回 idle
      if (!m.loop && this.motionTime >= dur) {
        this.motionName = this.autoIdle ? 'idle' : '';
        this.motionTime = 0;
        if (!this.autoIdle) this.motionName = 'idle';
      }

      // ---- 分层混合：base(默认+呼吸+表情) ← override(外部/滑杆) ← 动作轨道 ----
      // 旧写法是「先把 30 个参数全部重置成默认，再叠动作」，后果是：
      //   1) 动作里没写的参数每帧被清零，外部改任何东西都按不住；
      //   2) 表情设置的值也会被反复覆盖，调试台滑杆看上去"没反应"。
      // 现在只有真正被某一层触碰过的参数才会被改写，其余保持原值。
      const base = this._baseline();
      const touched = {};

      Object.keys(base).forEach((id) => {
        if (!(id in this.values)) return;
        this.values[id] = base[id];
        touched[id] = true;
      });

      // 外部 override 盖在 base 上（有效期内的才生效）
      const now = performance.now();
      Object.keys(this.overrides).forEach((id) => {
        const o = this.overrides[id];
        if (!o) return;
        if (now > o.until) { delete this.overrides[id]; return; }
        if (id in this.values) { this.values[id] = o.v; touched[id] = true; }
      });

      // 动作轨道最优先
      const tracks = m.tracks || {};
      Object.keys(tracks).forEach((id) => {
        if (!(id in this.values)) return;
        const v = sampleTrack(tracks[id], t);
        if (v == null) return;
        this.values[id] = clamp(v, PARAMS[id].min, PARAMS[id].max);
        touched[id] = true;
      });
    }

    /** 基线层：只包含「呼吸 / 表情 / 视线」这些常驻状态。
     *  不在这里把 30 个参数全部重置 —— 那会让外部设置按不住，
     *  也会让「动作没写的参数」每帧被清零。 */
    _baseline() {
      const out = {};
      // 呼吸（低频正弦）
      const br = (Math.sin(this.time * 1.15) + 1) / 2;
      out.ParamBreath = br * 0.6;
      out.ParamBodyAngleZ = Math.sin(this.time * 0.85) * 0.9;
      out.ParamAngleZ = Math.sin(this.time * 0.7 + 0.6) * 0.8;
      out.ParamTailLift = 0.08 + br * 0.1;
      out.ParamWaterGlow = 0.3 + br * 0.2;
      // 表情叠加（只覆盖表情里写到的参数）
      Object.keys(this.exprValues).forEach((id) => { out[id] = this.exprValues[id]; });
      return out;
    }

    _tickBlink(step) {
      this.blinkTimer -= step;
      if (this.blinkTimer <= 0 && this.blinkPhase < 0) {
        this.blinkPhase = 0;
        // 随机下次眨眼间隔，偶尔连眨
        this.blinkTimer = 1.8 + Math.random() * 3.2;
      }
      if (this.blinkPhase >= 0) {
        this.blinkPhase += step;
        const t = this.blinkPhase / 0.18;   // 整个眨眼 180ms
        if (t >= 1) {
          this.blinkPhase = -1;
        } else {
          // 0→1 闭，1→0 开
          const close = t < 0.5 ? t * 2 : (1 - t) * 2;
          const open = 1 - close * 0.96;
          this.values.ParamEyeLOpen = Math.min(this.values.ParamEyeLOpen, open);
          this.values.ParamEyeROpen = Math.min(this.values.ParamEyeROpen, open);
        }
      }
    }

    /* ---------------- 渲染到 SVG ---------------- */
    render() {
      const n = this.nodes;
      const v = this.values;
      if (!n.head) return;

      const ax = v.ParamAngleX || 0;      // 左右转头
      const ay = v.ParamAngleY || 0;      // 上下点头
      const az = v.ParamAngleZ || 0;      // 歪头
      const bx = v.ParamBodyAngleX || 0;
      const by = v.ParamBodyAngleY || 0;
      const bz = v.ParamBodyAngleZ || 0;

      // 头部：位移 + 旋转（原点在脖子）。注意 SVG 的 y 向下为正，
      // 而 Cubism 的 ParamAngleY 正值是「抬头」，所以这里要取负号，
      // 否则「点头」动作会变成往后仰。
      const headTf = `translate(${ax * 0.55 + bx * 0.25} ${-ay * 0.5 + by * 0.4}) rotate(${az + bz * 0.2} 200 400)`;
      n.head.setAttribute('transform', headTf);
      // 身体：轻微反向，制造重心感
      n.body.setAttribute('transform',
        `translate(${bx * 0.5} ${by * 0.6 - (v.ParamBreath || 0) * 1.2}) rotate(${bz * 0.35} 200 500)`);

      // 手臂
      const armL = v.ParamArmLA || 0;
      const armR = v.ParamArmRA || 0;
      // 手臂旋转锚点必须是「肩关节」，不是躯干中心。
      // 之前锚点写的是 (140,470)/(260,470)，那是躯干内部；手臂本体在
      // x≈101/299，绕着躯干内的点转，一抬手整条手臂就甩到身体外面，露出黑缝。
      //
      // 转向也很关键：两只手臂都从肩点「向下」垂，所以
      //   左臂在 x=128，要向外抬起必须顺时针（正角度 → 手指往左下撇）；
      //   右臂在 x=272，要向外抬起必须逆时针（负角度）。
      // 之前两条都用了反向符号，参数一拉满两只手会朝身体内侧交叉到胸前。
      n.armL.setAttribute('transform', `rotate(${armL * 45} 128 382) translate(0 ${-armL * 6})`);
      n.armR.setAttribute('transform', `rotate(${-armR * 45} 272 382) translate(0 ${-armR * 6})`);

      // 尾巴：以尾根（200,500）为轴摆动，上扬时整块上提
      const lift = v.ParamTailLift || 0;
      const sway = this.springs.tail.value;
      n.tail.setAttribute('transform', `rotate(${sway * 0.6} 200 500) translate(0 ${-lift * 18})`);
      n.tailTip.setAttribute('transform', `rotate(${Math.sin(this.time * 1.6) * 4 + lift * -7} 200 520)`);

      // 头发摆动。
      // 重点：只有 p-hair-back 在 #p-head 之外，需要自己拼 headTf；
      // p-hair-front / 侧发 / 耳鳍 / 发带 / 水珠本来就在 #p-head 里面，
      // 已经自动继承头部变换 —— 再拼一次 headTf 会把位移和旋转算两遍，
      // 表现为"头和头发各动各的、不同步"。这里只叠各自的弹簧。
      const hf = this.springs.hairFront.value;
      const hs = this.springs.hairSide.value;
      const hb = this.springs.hairBack.value;

      n.hairBack.setAttribute('transform',
        `${headTf} translate(${hb * 0.10} 0) rotate(${hb * 0.05} 200 300)`);

      n.hairFront.setAttribute('transform',
        `rotate(${hf * 0.06} 200 130) translate(${hf * 0.10} 0)`);

      n.hairSideL.setAttribute('transform',
        `translate(${hs * 0.12} 0) rotate(${hs * 0.05} 120 280)`);
      n.hairSideR.setAttribute('transform',
        `translate(${hs * 0.12} 0) rotate(${hs * 0.05} 280 280)`);

      // 耳鳍
      const ear = this.springs.ear.value;
      n.earL.setAttribute('transform', `rotate(${-ear * 0.06 - 4} 108 210)`);
      n.earR.setAttribute('transform', `rotate(${ear * 0.06 + 4} 292 210)`);

      // 发带 / 水珠：同在 #p-head 内，只做自身局部变化
      if (n.band) n.band.setAttribute('transform', '');
      if (n.star) n.star.setAttribute('transform', this._starTf());

      // 眼睛：眼球位移 + 眼睑开合 + 笑眼
      const ix = clamp(this.eyeX, -1, 1) * 5;
      const iy = clamp(this.eyeY, -1, 1) * 5;
      if (n.irisL) n.irisL.setAttribute('transform', `translate(${ix} ${iy})`);
      if (n.irisR) n.irisR.setAttribute('transform', `translate(${ix} ${iy})`);

      const oL = clamp(v.ParamEyeLOpen ?? 1, 0, 1.5);
      const oR = clamp(v.ParamEyeROpen ?? 1, 0, 1.5);
      // open=1 → 眼睑收到眼睛上方（scaleY≈0）；open=0 → 完全盖住眼白（scaleY=1）
      // 锚点必须放在「闭合时的眼睑下缘」，也就是 y=268，这是刚体的正确写法。
      const lidL = clamp(1 - oL, 0, 1);
      const lidR = clamp(1 - oR, 0, 1);
      if (n.lidL) {
        n.lidL.setAttribute('transform', `translate(163 268) scale(1 ${Math.max(lidL, 0.001)}) translate(-163 -268)`);
      }
      if (n.lidR) {
        n.lidR.setAttribute('transform', `translate(237 268) scale(1 ${Math.max(lidR, 0.001)}) translate(-237 -268)`);
      }

      // 闭眼时睫毛和下眼线必须一起收起来。
      // 之前只缩了眼睑，睫毛还挂在脸上 —— 闭眼表情看起来像两条粗眉毛。
      if (n.lashL) n.lashL.setAttribute('opacity', String(1 - lidL));
      if (n.lashR) n.lashR.setAttribute('opacity', String(1 - lidR));
      if (n.underL) n.underL.setAttribute('opacity', String(0.45 * (1 - lidL)));
      if (n.underR) n.underR.setAttribute('opacity', String(0.45 * (1 - lidR)));

      const smileL = clamp(v.ParamEyeLSmile || 0, 0, 1);
      const smileR = clamp(v.ParamEyeRSmile || 0, 0, 1);
      if (n.smileL) n.smileL.setAttribute('opacity', String(smileL));
      if (n.smileR) n.smileR.setAttribute('opacity', String(smileR));

      // 眉毛
      const bly = v.ParamBrowLY || 0;
      const bry = v.ParamBrowRY || 0;
      if (n.browL) n.browL.setAttribute('transform', `translate(0 ${-bly * 7}) rotate(${-bly * 5} 163 196)`);
      if (n.browR) n.browR.setAttribute('transform', `translate(0 ${-bry * 7}) rotate(${bry * 5} 237 196)`);

      // 嘴：开合 + 形状。闭嘴也保留一点弧度，否则中性表情像没画嘴
      const open = clamp(v.ParamMouthOpenY || 0, 0, 1);
      const form = clamp(v.ParamMouthForm || 0, -1, 1);
      const restCurve = 5 + form * 4;        // 闭嘴时的基础弧度（微笑上扬 / 抿嘴下垂）
      const openH = 3 + open * 22;
      if (n.mouthIn) {
        n.mouthIn.setAttribute(
          'd',
          `M182 ${300 - form * 2} q18 ${openH} 36 0 q-18 ${openH * 0.45} -36 0 Z`
        );
        // 抿嘴时只留唇线，不露出内腔
        n.mouthIn.setAttribute('opacity', String(open > 0.04 ? 1 : 0));
      }
      if (n.mouthLine) {
        const curve = open > 0.04 ? openH + form * 5 : restCurve;
        n.mouthLine.setAttribute('d', `M182 ${300 - form * 2} q18 ${curve} 36 0`);
        n.mouthLine.setAttribute('opacity', String(open > 0.04 ? 1 - open * 0.45 : 1));
      }

      // 腮红：害羞 / 开心时更明显
      const blush = 0.28 + Math.max(0, form) * 0.35 + Math.max(0, -form) * 0.4;
      if (n.blushL) n.blushL.setAttribute('opacity', String(clamp(blush, 0, 0.95)));
      if (n.blushR) n.blushR.setAttribute('opacity', String(clamp(blush, 0, 0.95)));

      // 悬浮光点：围绕头部做 8 字轨迹（基准变换已在上面拼进 _starTf）
      const glow = clamp(v.ParamWaterGlow ?? 0.35, 0, 1);
      if (n.star) n.star.setAttribute('opacity', String(0.45 + glow * 0.55));
      if (n.glowBlob) n.glowBlob.setAttribute('opacity', String(0.25 + glow * 0.5));

      // 水花（spout 动作时才会淡入）
      if (n.splash) {
        const s = clamp((v.ParamTailLift || 0) * 1.2 + Math.max(0, (v.ParamWaterGlow || 0) - 0.8) * 3, 0, 1);
        n.splash.setAttribute('opacity', String(s));
        n.splash.setAttribute('transform', `translate(0 ${-s * 10}) scale(${0.8 + s * 0.5}) `);
        n.splash.style.transformOrigin = '200px 130px';
      }
    }

    /** 悬浮光点的局部变换（不含头部基准，基准由 render 拼在前面） */
    _starTf() {
      const glow = clamp(this.values.ParamWaterGlow ?? 0.35, 0, 1);
      const t = this.time;
      const sx = 262 + Math.sin(t * 1.3) * 26;
      const sy = 132 + Math.cos(t * 1.7) * 18;
      return `translate(${sx} ${sy}) scale(${0.7 + glow * 0.7})`;
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
      if (this.wrap && this.wrap.parentNode) this.wrap.parentNode.removeChild(this.wrap);
    }

    /** 供 stage.js 兼容：模型尺寸 */
    get size() { return { width: VB.w, height: VB.h }; }
  }

  /* 采样一条轨道：[[t, v, ease], ...]，返回 t 时刻的值 */
  function sampleTrack(track, t) {
    if (!track || !track.length) return null;
    if (t <= track[0][0]) return track[0][1];
    for (let i = 1; i < track.length; i++) {
      const [t1, v1, e1] = track[i];
      if (t <= t1) {
        const [t0, v0] = track[i - 1];
        const span = t1 - t0 || 1;
        const k = clamp((t - t0) / span, 0, 1);
        const fn = ease[e1] || ease.lin;
        return lerp(v0, v1, fn(k));
      }
    }
    return track[track.length - 1][1];
  }

  /* ============================== 九、coreModel 兼容层 ==============================
   * stage.js 的口型校准会调 coreModel.getParameterIndex / setParameterValueById，
   * 这里转给 rig，保证「换模型不改驱动代码」。
   */
  class CoreModelStub {
    constructor(rig) { this.rig = rig; }
    getParameterIndex(id) { return this.rig.getParameterIndex(id); }
    getParameterCount() { return this.rig.getParameterCount(); }
    setParameterValueById(id, v) { this.rig.setParameterValueById(id, v); }
    getParameterValueById(id) { return this.rig.getParameterValueById(id); }
  }

  /* ============================== 十、导出 ============================== */
  root.WhaleRig = WhaleRig;
  root.WHALE_RIG = {
    PARAMS, PARAM_IDS, MOTIONS, EXPRESSIONS, PALETTE, VB,
  };
})(window);
