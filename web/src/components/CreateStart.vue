<script setup>
import WorkflowIcon from './WorkflowIcon.vue';
import { STORY_TEMPLATES } from '../workflowStudio';
defineProps({
  busy: Boolean,
  title: { type: String, default: '' },
  recent: { type: Array, default: () => [] },
});
defineEmits(['assistant', 'materials', 'canvas', 'configure', 'open', 'update:title']);
</script>

<template>
  <section class="create-start" aria-labelledby="create-start-title">
    <header class="home-heading">
      <div>
        <span class="home-eyebrow">FRAMEFLOW / CREATIVE STUDIO</span>
        <h1 id="create-start-title">你的下一部作品，从这里开始。</h1>
        <p>让灵感有画面，让每一镜都有故事。</p>
      </div>
      <span class="local-badge"><i></i>本地创作空间</span>
    </header>
    <div class="studio-hero">
      <img src="/images/inspiration-mountain.webp" alt="山海晨光中的电影场景" />
      <div class="hero-copy">
        <span class="hero-kicker">想象，自由生长。</span>
        <h2>从一个想法<br />到一整个世界。</h2>
        <p>在无限画布上连接素材、安排镜头，<br />用 ComfyUI 把你的故事变成视频。</p>
        <button :disabled="busy" @click="$emit('canvas', {})">
          {{ busy ? '正在准备…' : '开启创作画布' }}<WorkflowIcon name="arrow" />
        </button>
      </div>
      <div class="hero-stamp"><span>DIRECT YOUR IMAGINATION</span><b>镜序 · FRAMEFLOW</b></div>
    </div>
    <div class="home-section-title">
      <h2>选择你的创作起点</h2>
      <span>素材、剧本、分镜，随时切换</span>
    </div>
    <label class="start-name"
      >作品名称<input
        :value="title"
        maxlength="80"
        :disabled="busy"
        placeholder="给这个故事起个名字（选填）"
        @input="$emit('update:title', $event.target.value)"
    /></label>
    <div class="start-options">
      <button class="start-option" :disabled="busy" @click="$emit('assistant')">
        <span class="option-icon violet"><WorkflowIcon name="spark" /></span>
        <div>
          <h3>从剧本开始</h3>
          <p>上传剧本，让 AI 整理角色与素材</p>
        </div>
        <WorkflowIcon name="arrow" />
      </button>
      <button class="start-option" :disabled="busy" @click="$emit('materials')">
        <span class="option-icon mint"><WorkflowIcon name="image" /></span>
        <div>
          <h3>建立素材库</h3>
          <p>准备角色、场景、道具和音色</p>
        </div>
        <WorkflowIcon name="arrow" />
      </button>
      <button class="start-option" :disabled="busy" @click="$emit('canvas', {})">
        <span class="option-icon amber"><WorkflowIcon name="layout" /></span>
        <div>
          <h3>空白视频画布</h3>
          <p>输入描述、引用素材，直接生成视频</p>
        </div>
        <WorkflowIcon name="arrow" />
      </button>
    </div>
    <div class="home-section-title template-heading">
      <h2>用一个结构，打开灵感</h2>
      <span>可编辑镜头模板 · 不调用模型</span>
    </div>
    <div class="template-gallery">
      <button
        v-for="(template, index) in STORY_TEMPLATES"
        :key="template.id"
        class="template-card"
        :disabled="busy"
        @click="$emit('canvas', { descriptions: template.shots, duration: 5 })"
      >
        <div class="template-image">
          <img :src="`/images/inspiration-${template.image}.webp`" alt="" loading="lazy" /><span
            >0{{ index + 1 }} / STORY TEMPLATE</span
          ><i><WorkflowIcon name="arrow" /></i>
        </div>
        <div class="template-copy">
          <b>{{ template.title }}</b
          ><span>3 镜 · 15s</span>
          <p>{{ template.subtitle }}</p>
        </div>
      </button>
    </div>
    <section v-if="recent.length" class="recent-projects">
      <div class="home-section-title">
        <h2>继续上次的故事</h2>
        <span>最近的 {{ recent.slice(0, 3).length }} 个作品</span>
      </div>
      <div class="recent-list">
        <button v-for="item in recent.slice(0, 3)" :key="item.task_id" @click="$emit('open', item.task_id)">
          <WorkflowIcon name="shot" /><b>{{ item.title || item.idea || '未命名作品' }}</b
          ><span>继续创作</span><WorkflowIcon name="arrow" />
        </button>
      </div>
    </section>
    <footer class="start-footer">
      <span><WorkflowIcon name="shield" />作品自动保存到本地</span
      ><button @click="$emit('configure')">
        <WorkflowIcon name="settings" />配置生成服务<WorkflowIcon name="arrow" />
      </button>
    </footer>
  </section>
</template>
<style scoped>
.create-start {
  width: min(1180px, 100%);
  margin: auto;
  padding: 23px 28px 20px;
}
.home-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 26px;
  gap: 15px;
}
.home-eyebrow {
  font-size: 9px;
  letter-spacing: 2.3px;
  color: var(--day-accent, #a28cc5);
}
.home-heading h1 {
  font-size: 26px;
  font-weight: 500;
  letter-spacing: -0.5px;
  margin: 8px 0 6px;
  line-height: 1.5;
}
.home-heading p {
  margin: 0;
  font-size: 12px;
  color: var(--day-muted, #9ca0b2);
}
.local-badge {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 10px;
  border: 1px solid var(--day-line, #363a43);
  border-radius: 20px;
  padding: 6px 11px;
  color: var(--day-muted, #a7aea9);
  white-space: nowrap;
}
.local-badge i {
  width: 5px;
  height: 5px;
  background: var(--day-mint, #97c6b0);
  border-radius: 50%;
}
.studio-hero {
  position: relative;
  isolation: isolate;
  min-height: 285px;
  border: 1px solid var(--day-line, #40404c);
  border-radius: 14px;
  overflow: hidden;
  background: var(--day-panel, #272936);
}
.studio-hero > img {
  position: absolute;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 50% 56%;
  z-index: -2;
}
.studio-hero::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  background:
    linear-gradient(
      90deg,
      var(--day-hero, #171925ed) 0%,
      var(--day-hero, #171925b5) 35%,
      var(--day-hero-fade, #17192520) 75%
    ),
    linear-gradient(0deg, var(--day-hero-fade, #13182465), transparent);
}
.hero-copy {
  padding: 29px 34px;
}
.hero-kicker {
  font-size: 10px;
  color: var(--day-accent, #d5c3ef);
  letter-spacing: 2px;
}
.hero-copy h2 {
  font-size: 35px;
  font-weight: 500;
  letter-spacing: 1px;
  line-height: 1.35;
  margin: 11px 0 12px;
  color: var(--day-text, #f5f2fa);
}
.hero-copy p {
  font-size: 11px;
  line-height: 1.9;
  color: var(--day-text, #c4c2d1);
  margin: 0 0 19px;
}
.hero-copy button {
  display: inline-flex;
  gap: 20px;
  align-items: center;
  padding: 10px 17px;
  background: var(--day-accent, #c3abf9);
  color: var(--day-on-accent, #211a30);
  border-color: var(--day-accent-line, #c3abf9);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
}
.hero-copy button svg {
  width: 15px;
}
.hero-stamp {
  position: absolute;
  bottom: 25px;
  right: 28px;
  text-align: right;
}
.hero-stamp span {
  display: block;
  font-size: 8px;
  letter-spacing: 2px;
  color: var(--day-text, #dfdeedb3);
}
.hero-stamp b {
  display: block;
  font-size: 11px;
  font-weight: 400;
  letter-spacing: 1.3px;
  margin-top: 7px;
  color: var(--day-text, #eeedf8);
}
.home-section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin: 25px 0 14px;
}
.home-section-title h2 {
  font-size: 14px;
  font-weight: 500;
  margin: 0;
}
.home-section-title > span {
  font-size: 10px;
  color: var(--day-muted, #858a9d);
}
.start-name {
  display: flex;
  align-items: center;
  gap: 15px;
  font-size: 11px;
  color: var(--day-muted, #9ca0b2);
  margin: 0 0 15px;
}
.start-name input {
  width: min(340px, calc(100% - 75px));
  padding: 8px 11px;
  font-size: 11px;
  background: var(--day-inset, #1d1e26);
  border-color: var(--day-line, #383a47);
  border-radius: 7px;
}
.start-options {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 13px;
}
.start-option {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 17px 15px;
  background: var(--day-panel, #21222a);
  border: 1px solid var(--day-line, #353642);
  text-align: left;
  border-radius: 10px;
}
.start-option:hover:not(:disabled) {
  border-color: var(--day-accent-line, #76648f);
  background: var(--day-accent-soft, #282530);
}
.start-option > div {
  flex: 1;
  min-width: 0;
}
.start-option h3 {
  font-size: 12px;
  font-weight: 500;
  margin: 0 0 5px;
}
.start-option p {
  font-size: 10px;
  color: var(--day-muted, #9399ac);
  margin: 0;
  line-height: 1.7;
}
.start-option > svg {
  width: 13px;
  color: var(--day-muted, #85899e);
}
.option-icon {
  display: grid;
  place-items: center;
  width: 35px;
  height: 35px;
  border-radius: 10px;
  flex-shrink: 0;
}
.option-icon svg {
  width: 18px;
  height: 18px;
}
.violet {
  color: var(--day-accent, #bc9ff4);
  background: var(--day-accent-soft, #b498f418);
}
.mint {
  color: var(--day-mint, #98c9b8);
  background: var(--day-mint-soft, #98c9b816);
}
.amber {
  color: var(--day-warn, #d3b38d);
  background: var(--day-warn-soft, #d3b38d16);
}
.template-gallery {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.template-card {
  display: block;
  text-align: left;
  border: 1px solid var(--day-line, #363743);
  padding: 0;
  border-radius: 10px;
  background: var(--day-panel, #21222a);
  overflow: hidden;
}
.template-card:hover:not(:disabled) {
  border-color: var(--day-accent-line, #77608f);
  background: var(--day-accent-soft, #292631);
}
.template-image {
  height: 136px;
  position: relative;
  overflow: hidden;
}
.template-image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.25s;
}
.template-card:hover img {
  transform: scale(1.04);
}
.template-image::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(transparent 30%, var(--day-inset, #0b0d1670));
}
.template-image > span {
  position: absolute;
  bottom: 10px;
  left: 13px;
  font-size: 8px;
  letter-spacing: 1px;
  color: var(--day-text, #f3f2ff);
  z-index: 1;
}
.template-image i {
  position: absolute;
  bottom: 10px;
  right: 12px;
  display: grid;
  place-items: center;
  width: 25px;
  height: 25px;
  border-radius: 50%;
  background: var(--day-hover, #ffffff20);
  color: var(--day-text, #fff);
  z-index: 1;
}
.template-image svg {
  width: 13px;
}
.template-copy {
  padding: 12px 14px;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px;
}
.template-copy b {
  font-size: 12px;
  font-weight: 500;
}
.template-copy span {
  font-size: 9px;
  color: var(--day-accent, #aa96c8);
}
.template-copy p {
  grid-column: 1 / -1;
  color: var(--day-muted, #949aae);
  margin: 0;
  font-size: 10px;
}
.recent-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}
.recent-list button {
  display: flex;
  align-items: center;
  gap: 9px;
  min-width: 0;
  text-align: left;
  font-size: 10px;
  background: var(--day-inset, #1d1e25);
  border-color: var(--day-line, #353643);
  padding: 12px;
}
.recent-list b {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 400;
}
.recent-list span {
  color: var(--day-accent, #9d8eba);
  font-size: 9px;
}
.recent-list svg {
  width: 14px;
  color: var(--day-accent, #9d8eba);
}
.start-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 15px;
  margin: 25px 0 0;
  border-top: 1px solid var(--day-line, #31333e);
  padding-top: 15px;
  color: var(--day-muted, #898fa3);
  font-size: 10px;
}
.start-footer > span,
.start-footer button {
  display: flex;
  align-items: center;
  gap: 7px;
}
.start-footer button {
  background: none;
  border: 0;
  color: var(--day-muted, #a09ab2);
  padding: 0;
  font-size: 10px;
}
.start-footer svg {
  width: 13px;
  height: 13px;
}
@media (max-width: 1000px) {
  .create-start {
    padding: 18px 14px;
  }
  .start-option {
    padding: 14px 10px;
    gap: 9px;
  }
  .start-option > svg {
    display: none;
  }
  .home-heading h1 {
    font-size: 23px;
  }
}
@media (max-width: 680px) {
  .home-heading {
    margin-bottom: 18px;
  }
  .home-heading h1 {
    font-size: 21px;
  }
  .local-badge {
    display: none;
  }
  .home-section-title > span {
    display: none;
  }
  .studio-hero {
    min-height: 270px;
  }
  .hero-copy {
    padding: 25px;
  }
  .hero-copy h2 {
    font-size: 31px;
  }
  .hero-stamp {
    display: none;
  }
  .start-options {
    grid-template-columns: 1fr;
    gap: 9px;
  }
  .start-option {
    padding: 13px;
  }
  .start-option > svg {
    display: block;
  }
  .template-gallery {
    gap: 8px;
  }
  .template-image {
    height: 100px;
  }
  .template-image > span {
    display: none;
  }
  .template-copy {
    padding: 9px;
    display: block;
  }
  .template-copy b {
    font-size: 11px;
  }
  .template-copy span {
    display: block;
    margin: 4px 0;
  }
  .template-copy p {
    font-size: 9px;
  }
  .recent-list {
    grid-template-columns: 1fr;
  }
  .start-footer {
    font-size: 9px;
    gap: 5px;
  }
  .start-footer button {
    font-size: 9px;
  }
}
</style>
