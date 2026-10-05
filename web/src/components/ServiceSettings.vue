<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { api } from '../api';
import WorkflowIcon from './WorkflowIcon.vue';
import { RESOLUTION_OPTIONS, resolutionLabel } from '../videoSettings';
import { LORAS, settingsPatch, workflowPreset, loraPreset, settingsError } from '../serviceSettings';

const props = defineProps({ cfg: { type: Object, required: true } });
const emit = defineEmits(['close', 'saved', 'connection']);
const baseline = ref({ ...props.cfg });
const draft = ref({ ...props.cfg });
const active = ref('video');
const dialog = ref(null);
const panel = ref(null);
const saving = ref(false);
const testing = ref(false);
const error = ref('');
const saved = ref(false);
const discard = ref(false);
const connection = ref(null);
let opener,
  previousOverflow,
  testSerial = 0,
  disposed = false;
const tabs = [
  { id: 'video', label: '视频生成', icon: 'video', note: '连接与工作流' },
  { id: 'text', label: '文本模型', icon: 'note', note: '剧情与提示词' },
  { id: 'image', label: '图片模型', icon: 'image', note: '角色与场景' },
  { id: 'audio', label: '音频服务', icon: 'audio', note: '角色音色样本' },
];
const currentTab = computed(() => tabs.find((t) => t.id === active.value));
const dirty = computed(() => Object.keys(settingsPatch(baseline.value, draft.value)).length > 0);
const connected = computed(() => connection.value?.reachable === true);
const lora = computed(() => LORAS.find((p) => p.value === draft.value.video_lora));
const warning = computed(() =>
  lora.value?.workflow && lora.value.steps !== String(draft.value.video_steps)
    ? `这份加速模型建议 ${lora.value.steps} 步，增加步数未必提高画质。`
    : ''
);
const configured = (id) =>
  id === 'video'
    ? !!(draft.value.video_backend === 'api'
        ? draft.value.video_api_url && draft.value.video_api_key && draft.value.video_api_model
        : draft.value.comfyui_url)
    : id === 'audio'
      ? draft.value.audio_provider === 'edge' || !!draft.value.audio_api_key
      : !!draft.value[`${id}_api_key`];

function close() {
  if (saving.value) return;
  if (dirty.value) {
    discard.value = true;
    return;
  }
  emit('close');
}
function switchTab(id) {
  active.value = id;
  if (panel.value) panel.value.scrollTop = 0;
}
function tabKey(event, index) {
  let next;
  if (['ArrowRight', 'ArrowDown'].includes(event.key)) next = (index + 1) % tabs.length;
  if (['ArrowLeft', 'ArrowUp'].includes(event.key)) next = (index + tabs.length - 1) % tabs.length;
  if (event.key === 'Home') next = 0;
  if (event.key === 'End') next = tabs.length - 1;
  if (next === undefined) return;
  event.preventDefault();
  switchTab(tabs[next].id);
  nextTick(() => dialog.value.querySelector(`#settings-tab-${tabs[next].id}`)?.focus());
}
function keydown(event) {
  if (event.key === 'Escape') {
    event.stopPropagation();
    close();
  }
  if (event.key !== 'Tab') return;
  const controls = [
    ...dialog.value.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]'
    ),
  ].filter((el) => el.getClientRects().length && !el.closest('fieldset:disabled'));
  const first = controls[0],
    last = controls.at(-1);
  if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.value)) {
    event.preventDefault();
    last?.focus();
  } else if (
    !event.shiftKey &&
    (document.activeElement === last || document.activeElement === dialog.value)
  ) {
    event.preventDefault();
    first?.focus();
  }
}
async function save() {
  if (saving.value || !dirty.value) return;
  const validation = settingsError(draft.value);
  if (validation) {
    error.value = validation;
    switchTab('video');
    return;
  }
  saving.value = true;
  error.value = '';
  discard.value = false;
  try {
    const response = await api.setConfig(settingsPatch(baseline.value, draft.value));
    const config = response.config || { ...draft.value };
    baseline.value = { ...config };
    draft.value = { ...config };
    saved.value = true;
    emit('saved', config);
  } catch (err) {
    error.value = err.message;
  } finally {
    saving.value = false;
  }
}
async function testConnection() {
  if (testing.value || !draft.value.comfyui_url.trim()) return;
  const serial = ++testSerial,
    address = draft.value.comfyui_url;
  testing.value = true;
  connection.value = null;
  try {
    const result = await api.testConnection(address);
    if (!disposed && serial === testSerial && address === draft.value.comfyui_url) {
      connection.value = result;
      emit('connection', { ...result, address });
    }
  } catch (err) {
    if (!disposed && serial === testSerial) connection.value = { reachable: false, message: err.message };
  } finally {
    if (serial === testSerial) testing.value = false;
  }
}
watch(
  () => draft.value.comfyui_url,
  () => {
    testSerial++;
    testing.value = false;
    connection.value = null;
  }
);
watch(
  draft,
  () => {
    if (dirty.value) saved.value = false;
    discard.value = false;
  },
  { deep: true }
);
onMounted(() => {
  opener = document.activeElement;
  previousOverflow = document.body.style.overflow;
  document.body.style.overflow = 'hidden';
  dialog.value?.focus();
});
onBeforeUnmount(() => {
  disposed = true;
  document.body.style.overflow = previousOverflow;
  opener?.focus?.();
});
</script>

<template>
  <div class="settings-overlay" @click.self="close">
    <section
      ref="dialog"
      class="settings-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="settings-title"
      tabindex="-1"
      @keydown="keydown"
    >
      <header class="settings-header">
        <div class="settings-heading">
          <span class="settings-symbol"><WorkflowIcon name="settings" /></span>
          <div>
            <h2 id="settings-title">服务设置</h2>
            <p>连接创作所需的服务</p>
          </div>
        </div>
        <button class="icon-button" aria-label="关闭服务配置" @click="close">
          <WorkflowIcon name="close" />
        </button>
      </header>
      <div class="settings-body">
        <nav class="settings-nav" role="tablist" aria-label="服务分类" aria-orientation="vertical">
          <button
            v-for="(tab, index) in tabs"
            :id="`settings-tab-${tab.id}`"
            :key="tab.id"
            role="tab"
            :aria-selected="active === tab.id"
            :aria-controls="`settings-panel-${tab.id}`"
            :tabindex="active === tab.id ? 0 : -1"
            :class="{ active: active === tab.id }"
            @click="switchTab(tab.id)"
            @keydown="tabKey($event, index)"
          >
            <WorkflowIcon :name="tab.icon" /><span
              ><b>{{ tab.label }}</b
              ><small>{{ tab.note }}</small></span
            ><i
              :class="{ configured: configured(tab.id) }"
              :title="configured(tab.id) ? '已填写配置' : '待配置'"
            ></i>
          </button>
          <p class="settings-nav-note">可先配置需要的服务<br />画布编辑无需启动模型</p>
        </nav>
        <div
          ref="panel"
          :id="`settings-panel-${active}`"
          class="settings-panel"
          role="tabpanel"
          :aria-labelledby="`settings-tab-${active}`"
        >
          <div class="panel-heading">
            <div>
              <h3>{{ currentTab.label }}</h3>
              <p>
                {{
                  active === 'video'
                    ? '选择生成方式，连接你的视频服务。'
                    : active === 'text'
                      ? '用于素材规划与分镜提示词编排。'
                      : active === 'image'
                        ? '用于生成角色、场景和道具图片。'
                        : '准备可试听的角色音色样本。'
                }}
              </p>
            </div>
            <span class="configuration-badge" :class="{ ready: configured(active) }">{{
              configured(active) ? '已填写' : '待配置'
            }}</span>
          </div>
          <fieldset :disabled="saving">
            <template v-if="active === 'video'">
              <div class="provider-picker" role="radiogroup" aria-label="视频生成方式">
                <label :class="{ chosen: draft.video_backend === 'comfyui' }"
                  ><input v-model="draft.video_backend" type="radio" value="comfyui" /><span
                    ><b>ComfyUI</b><small>自建或租用 GPU 实例</small></span
                  ></label
                ><label :class="{ chosen: draft.video_backend === 'api' }"
                  ><input v-model="draft.video_backend" type="radio" value="api" /><span
                    ><b>外接视频 API</b><small>使用云端生成服务</small></span
                  ></label
                >
              </div>
              <template v-if="draft.video_backend === 'comfyui'">
                <label class="settings-field"
                  >ComfyUI 地址<input
                    v-model="draft.comfyui_url"
                    placeholder="http://127.0.0.1:8188"
                    autocomplete="url"
                    spellcheck="false"
                /></label>
                <div class="connection-row">
                  <button
                    class="secondary-button"
                    :disabled="testing || !draft.comfyui_url.trim()"
                    @click="testConnection"
                  >
                    <WorkflowIcon :name="testing ? 'spark' : 'link'" />{{
                      testing ? '测试中…' : '测试连接'
                    }}</button
                  ><span v-if="connection" class="connection-result" :class="{ connected }" role="status"
                    ><i></i>{{ connection.message }}</span
                  ><span v-else class="connection-tip">测试地址不会保存或开始生成</span>
                </div>
                <div class="settings-separator"></div>
                <div class="settings-grid">
                  <label class="settings-field"
                    >视频工作流<select
                      :value="draft.video_workflow"
                      @change="Object.assign(draft, workflowPreset($event.target.value))"
                    >
                      <option value="i2v">I2V · 首帧 / 首尾帧</option>
                      <option value="ref2va">Ref2VA · 多图参考</option>
                    </select></label
                  ><label class="settings-field"
                    >默认分辨率（像素预算）<select v-model="draft.video_megapixels">
                      <option v-for="o in RESOLUTION_OPTIONS" :key="o.mp" :value="o.mp">{{ o.label }}</option>
                      <option
                        v-if="!RESOLUTION_OPTIONS.some((o) => o.mp === String(draft.video_megapixels))"
                        :value="draft.video_megapixels"
                      >
                        {{ resolutionLabel(draft.video_megapixels) }}
                      </option>
                    </select></label
                  >
                </div>
                <p class="field-note">
                  {{
                    draft.video_workflow === 'ref2va'
                      ? '最多 9 张图片作为视觉参考，需准备对应模型。'
                      : '使用图片作为视频首帧；支持首尾帧的流程可再提供尾帧。'
                  }}
                </p>
                <p class="field-note">
                  新分镜默认跟随此值；单镜覆盖只影响该镜。MP 表示总像素量，实际尺寸由输入图片与工作流决定。
                </p>
                <details class="advanced-settings">
                  <summary>高级参数<span>加速模型、采样与等待时长</span></summary>
                  <div>
                    <label class="settings-field"
                      >加速模型（LoRA）<select
                        :value="draft.video_lora"
                        @change="Object.assign(draft, loraPreset($event.target.value))"
                      >
                        <option v-for="preset in LORAS" :key="preset.value" :value="preset.value">
                          {{ preset.label }}
                        </option>
                        <option v-if="draft.video_lora && !lora" :value="draft.video_lora">
                          自定义 · {{ draft.video_lora }}
                        </option>
                      </select></label
                    >
                    <div class="settings-grid">
                      <label class="settings-field"
                        >采样步数<input v-model="draft.video_steps" type="number" min="1" max="100" /></label
                      ><label class="settings-field"
                        >等待时长（秒）<input
                          v-model="draft.video_timeout_s"
                          type="number"
                          min="30"
                          max="86400"
                          step="30"
                      /></label>
                    </div>
                    <p v-if="warning" class="field-warning">{{ warning }}</p>
                    <p class="field-note">切换工作流会同时匹配加速模型与步数。</p>
                  </div>
                </details>
              </template>
              <template v-else
                ><label class="settings-field"
                  >API 地址<input
                    v-model="draft.video_api_url"
                    placeholder="https://api.siliconflow.cn/v1"
                    spellcheck="false" /></label
                ><label class="settings-field"
                  >API Key<input
                    v-model="draft.video_api_key"
                    type="password"
                    autocomplete="off"
                    placeholder="输入服务密钥" /></label
                ><label class="settings-field"
                  >模型名称<input
                    v-model="draft.video_api_model"
                    placeholder="MiniMax/Hailuo-02"
                    spellcheck="false"
                /></label>
                <p class="field-note">当前适配硅基流动风格的异步任务接口。其他协议需要单独适配。</p>
                <p class="field-note">
                  当前接口只提交一张首帧图与提示词，分辨率和成片时长由 API
                  模型决定。分镜时长仅用于提示词中的计划节奏。
                </p></template
              >
            </template>
            <template v-else-if="active === 'text' || active === 'image'">
              <label class="settings-field"
                >API 地址<input
                  v-model="draft[`${active}_base_url`]"
                  :placeholder="
                    active === 'text' ? 'https://api.deepseek.com' : 'https://api-inference.modelscope.cn/v1'
                  "
                  spellcheck="false"
              /></label>
              <label class="settings-field"
                >API Key<input
                  v-model="draft[`${active}_api_key`]"
                  type="password"
                  autocomplete="off"
                  placeholder="输入服务密钥"
              /></label>
              <label class="settings-field"
                >模型名称<input
                  v-model="draft[`${active}_model`]"
                  :placeholder="active === 'text' ? 'deepseek-flash' : 'Tongyi-MAI/Z-Image-Turbo'"
                  spellcheck="false"
              /></label>
              <div class="settings-info">
                <WorkflowIcon :name="currentTab.icon" />
                <p>
                  {{
                    active === 'text'
                      ? '手写英文视频提示词时，可以跳过文本模型。'
                      : '更换图片模型后，新生成的素材会使用新配置。'
                  }}
                </p>
              </div>
            </template>
            <template v-else>
              <label class="settings-field"
                >音频服务<select v-model="draft.audio_provider">
                  <option value="edge">Edge TTS · 免费音色</option>
                  <option value="minimax">MiniMax · 云端音色</option>
                </select></label
              >
              <template v-if="draft.audio_provider === 'minimax'"
                ><label class="settings-field"
                  >API 地址<input
                    v-model="draft.audio_base_url"
                    placeholder="https://api.minimaxi.com/v1"
                    spellcheck="false" /></label
                ><label class="settings-field"
                  >API Key<input
                    v-model="draft.audio_api_key"
                    type="password"
                    autocomplete="off"
                    placeholder="输入服务密钥" /></label
                ><label class="settings-field"
                  >模型名称<input
                    v-model="draft.audio_model"
                    placeholder="speech-2.8-hd"
                    spellcheck="false" /></label
              ></template>
              <div class="settings-info">
                <WorkflowIcon name="audio" />
                <p>音色样本可用于试听。当前视频生成链路尚未接入参考音频。</p>
              </div>
            </template>
          </fieldset>
          <p v-if="error" class="settings-error" role="alert">{{ error }}</p>
        </div>
      </div>
      <footer class="settings-footer">
        <template v-if="discard"
          ><span class="unsaved-message">有未保存的修改</span>
          <div>
            <button class="secondary-button" @click="discard = false">继续编辑</button
            ><button class="discard-button" @click="emit('close')">放弃修改</button>
          </div></template
        ><template v-else
          ><span class="footer-status" aria-live="polite"
            ><WorkflowIcon v-if="saved" name="check" />{{
              saving
                ? '正在保存…'
                : saved
                  ? '已保存，立即生效'
                  : dirty
                    ? '有未保存的修改'
                    : '配置仅保存在本机'
            }}</span
          >
          <div>
            <button class="secondary-button" :disabled="saving" @click="close">
              {{ saved ? '完成' : '取消' }}</button
            ><button class="save-button" :disabled="saving || !dirty" @click="save">
              {{ saving ? '保存中…' : '保存设置' }}
            </button>
          </div></template
        >
      </footer>
    </section>
  </div>
</template>

<style scoped>
.settings-overlay {
  position: fixed;
  inset: 0;
  z-index: 150;
  background: var(--day-backdrop, #10171880);
  backdrop-filter: blur(3px);
  display: grid;
  place-items: center;
  padding: 28px;
}
.settings-dialog {
  color-scheme: var(--app-color-scheme, dark);
  width: min(900px, 100%);
  height: min(670px, calc(100dvh - 56px));
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: 0 24px 90px var(--day-shadow, #0e141a40);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  outline: none;
  color: var(--fg);
}
.settings-header {
  height: 90px;
  flex-shrink: 0;
  padding: 22px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--line);
}
.settings-heading {
  display: flex;
  align-items: center;
  gap: 13px;
}
.settings-symbol {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: 10px;
  color: var(--fg-3);
}
.settings-heading h2 {
  font-size: 19px;
  margin: 0;
  font-weight: 600;
}
.settings-heading p {
  font-size: 12px;
  color: var(--fg-2);
  margin: 2px 0 0;
}
.icon-button {
  background: transparent;
  border: 0;
  padding: 8px;
  color: var(--fg-3);
  display: grid;
  place-items: center;
}
.settings-body {
  display: flex;
  flex: 1;
  min-height: 0;
}
.settings-nav {
  width: 195px;
  flex-shrink: 0;
  padding: 22px 12px;
  background: var(--bg);
  border-right: 1px solid var(--line);
}
.settings-nav button {
  width: 100%;
  padding: 13px 12px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
  color: var(--fg-3);
  margin-bottom: 6px;
}
.settings-nav button.active {
  background: var(--accent-dim);
  border-color: var(--line);
  box-shadow: 0 2px 6px var(--day-shadow, #23302004);
  color: var(--fg);
}
.settings-nav button.active > svg {
  color: var(--accent);
}
.settings-nav b {
  display: block;
  font-size: 12px;
  font-weight: 500;
}
.settings-nav small {
  display: block;
  font-size: 10px;
  margin-top: 2px;
  color: var(--fg-2);
}
.settings-nav i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--surface-2);
  margin-left: auto;
  flex-shrink: 0;
}
.settings-nav i.configured {
  background: var(--mint);
}
.settings-nav-note {
  font-size: 10px;
  line-height: 1.9;
  color: var(--fg-2);
  margin: 30px 12px 0;
}
.settings-panel {
  flex: 1;
  min-width: 0;
  padding: 27px 32px 30px;
  overflow: auto;
  overscroll-behavior: contain;
}
.panel-heading {
  display: flex;
  align-items: flex-start;
  gap: 15px;
  justify-content: space-between;
  margin-bottom: 24px;
}
.panel-heading h3 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}
.panel-heading p {
  margin: 5px 0 0;
  color: var(--fg-2);
  font-size: 11px;
}
.configuration-badge {
  font-size: 10px;
  color: var(--fg-2);
  background: var(--surface);
  padding: 3px 9px;
  border-radius: 20px;
  white-space: nowrap;
}
.configuration-badge.ready {
  color: var(--mint);
  background: var(--surface);
}
fieldset {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.provider-picker {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 23px;
}
.provider-picker label {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 15px 13px;
  border: 1px solid var(--line);
  border-radius: 8px;
  cursor: pointer;
}
.provider-picker label.chosen {
  background: var(--accent-dim);
  border-color: var(--accent);
}
.provider-picker input {
  width: 14px;
  height: 14px;
  margin: 3px 0 0;
  flex-shrink: 0;
}
.provider-picker b {
  display: block;
  font-size: 12px;
  font-weight: 500;
}
.provider-picker small {
  display: block;
  color: var(--fg-2);
  font-size: 10px;
  margin-top: 4px;
}
.settings-field {
  display: block;
  font-size: 11px;
  font-weight: 500;
  color: var(--fg-3);
  margin-bottom: 20px;
}
.settings-field input,
.settings-field select {
  display: block;
  margin-top: 8px;
  height: 40px;
  border: 1px solid var(--line);
  background-color: var(--surface);
  color: var(--fg-3);
  font-size: 12px;
  border-radius: 7px;
  font-weight: 400;
}
.settings-field input[type='password'] {
  letter-spacing: 1px;
}
.settings-field input:focus,
.settings-field select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--day-shadow, #cf9a7820);
}
.settings-field input::placeholder {
  color: var(--fg-2);
}
.connection-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: -8px;
}
.connection-tip,
.connection-result {
  font-size: 10px;
  color: var(--fg-2);
}
.connection-result {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--danger);
}
.connection-result.connected {
  color: var(--mint);
}
.connection-result i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}
.settings-separator {
  height: 1px;
  background: var(--surface);
  margin: 25px 0;
}
.field-note {
  font-size: 11px;
  color: var(--fg-2);
  line-height: 1.8;
  margin: -10px 0 23px;
}
.advanced-settings {
  border-top: 1px solid var(--line);
  margin-top: 25px;
}
.advanced-settings summary {
  padding: 16px 0 4px;
  cursor: pointer;
  font-size: 11px;
  color: var(--fg-3);
}
.advanced-settings summary span {
  margin-left: 12px;
  color: var(--fg-2);
  font-size: 10px;
}
.advanced-settings > div {
  padding-top: 22px;
}
.settings-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 15px;
}
.field-warning {
  color: var(--warn);
  background: var(--surface);
  padding: 10px;
  font-size: 11px;
  border-radius: 6px;
}
.settings-info {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 16px;
  background: var(--surface);
  border-radius: 8px;
  margin-top: 28px;
  color: var(--fg-2);
}
.settings-info svg {
  width: 16px;
}
.settings-info p {
  margin: 0;
  font-size: 11px;
  line-height: 1.8;
}
.settings-error {
  padding: 12px;
  color: var(--danger);
  font-size: 11px;
  background: var(--surface);
  border: 1px solid var(--warn);
  border-radius: 6px;
}
.settings-footer {
  min-height: 75px;
  flex-shrink: 0;
  border-top: 1px solid var(--line);
  padding: 16px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.settings-footer > div {
  display: flex;
  gap: 8px;
}
.footer-status,
.unsaved-message {
  font-size: 11px;
  color: var(--fg-2);
  display: flex;
  align-items: center;
  gap: 6px;
}
.footer-status svg {
  width: 14px;
  color: var(--mint);
}
.unsaved-message {
  color: var(--warn);
}
.secondary-button,
.save-button,
.discard-button {
  padding: 8px 14px;
  font-size: 11px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  white-space: nowrap;
}
.secondary-button {
  background: var(--surface);
  border: 1px solid var(--line);
  color: var(--fg-3);
}
.secondary-button svg {
  width: 14px;
}
.save-button {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--accent-ink);
}
.save-button:hover:not(:disabled) {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}
.discard-button {
  color: var(--warn);
  background: var(--surface);
  border-color: var(--warn);
}
@media (max-width: 680px) {
  .settings-overlay {
    padding: 12px;
  }
  .settings-dialog {
    color-scheme: var(--app-color-scheme, dark);
    height: calc(100dvh - 24px);
  }
  .settings-header {
    height: 75px;
    padding: 18px;
  }
  .settings-body {
    flex-direction: column;
  }
  .settings-nav {
    width: 100%;
    padding: 10px;
    display: flex;
    border-right: 0;
    border-bottom: 1px solid var(--line);
  }
  .settings-nav button {
    padding: 9px 8px;
    justify-content: center;
    margin-bottom: 0;
  }
  .settings-nav button > svg,
  .settings-nav small,
  .settings-nav i,
  .settings-nav-note {
    display: none;
  }
  .settings-panel {
    padding: 22px 20px;
  }
  .settings-footer {
    padding: 14px 18px;
  }
  .provider-picker {
    gap: 7px;
  }
  .provider-picker label {
    padding: 12px 9px;
  }
  .settings-footer .footer-status {
    font-size: 10px;
  }
  .settings-nav b {
    white-space: nowrap;
  }
}
.settings-header {
  height: 80px;
}
.settings-footer {
  min-height: 67px;
  padding-top: 12px;
  padding-bottom: 12px;
}
.settings-panel {
  padding-top: 24px;
  padding-bottom: 24px;
}
.panel-heading {
  margin-bottom: 19px;
}
.provider-picker {
  margin-bottom: 18px;
}
.provider-picker label {
  padding-top: 12px;
  padding-bottom: 12px;
}
.settings-field {
  margin-bottom: 17px;
}
.settings-separator {
  margin: 20px 0;
}
.advanced-settings {
  margin-top: 15px;
}
.advanced-settings summary {
  padding-top: 13px;
}
.settings-grid {
  gap: 12px;
}
@media (max-width: 440px) {
  .settings-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
  .connection-row {
    align-items: flex-start;
    flex-direction: column;
    gap: 7px;
  }
  .advanced-settings summary span {
    display: none;
  }
  .settings-panel {
    padding: 20px 16px;
  }
  .settings-heading h2 {
    font-size: 18px;
  }
}
</style>
