export const IMAGE_MODES = [
  {
    value: 'native',
    label: '原生',
    placeholder: '描述你想要的画面，也可以直接填写自己的完整提示词…',
    hint: '直接使用你的描述生成图片，保留自己的写法。',
  },
  {
    value: 'character',
    label: '人物三视图',
    sheet: true,
    placeholder: '描述人物的年龄、长相、服装和风格，例如：六岁女孩，双丸子头，灰色古装…',
    hint: '每张设定图一次生成面部特写＋正/侧/背全身，固定 16:9；单人参考图从同张设定图提取。可补充自己的风格要求。',
  },
  {
    value: 'scene',
    label: '场景图',
    placeholder: '描述地点、时间和氛围，例如：深山石阶，清晨薄雾，阳光穿过树林…',
    hint: '自动套用场景模板，生成没有人物的环境图；可补充光线、构图和风格要求。',
  },
  {
    value: 'prop',
    label: '道具图',
    sheet: true,
    placeholder: '描述道具的外形、材质和风格，例如：生锈的旧铁剑，麻布缠绕剑柄…',
    hint: '自动套用道具模板：正/侧/背三视图、白色背景，固定 16:9；可补充自己的细节要求。',
  },
];

export function imageMode(data) {
  return IMAGE_MODES.find((mode) => mode.value === data.imageMode) || IMAGE_MODES[0];
}

export function imageRatio(data) {
  return imageMode(data).sheet ? '16:9' : data.ratio || '16:9';
}

export const IMAGE_COUNTS = [1, 2, 4];

export function imageCount(data) {
  return IMAGE_COUNTS.includes(data.imageCount) ? data.imageCount : 1;
}
