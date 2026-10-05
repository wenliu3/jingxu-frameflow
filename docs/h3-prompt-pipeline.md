# Ref2VA 提示词编排（2026-10-04）

当前视频描述、前情文字、所选图片编号与文字外观锚点进入文本模型；模型按 Ref2VA 规则精要写英文正文、环境声和视觉保留标记。代码校验后组装六段式，再在提交与工作流接线时复核。H3 节点直接接收这份完整提示词，不再加基础模式的三段外壳。

规则精要是项目维护的转述，按实际工作流加载对应部分，并非调用官方托管的预处理服务。来源：[官方 Skill](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md)、[Ref2VA 指南](https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt)。

## 意图与素材

- 当前描述和前情分别传入，前情不用于决定当前镜头数。未要求切镜时默认保持连续镜头，明确切镜时给出时间轴；请求过密时限制镜头数并返回提醒。
- 给文本模型的是素材编号、名称、已有外观文字设定和视频来源信息，未使用视觉模型分析图片或前面的视频。文字前情只用于叙事上下文；Ref2VA 的视频参考和续拍由后端提取参考片段、尾帧并接入 H3 工作流，见 [视频参考与续拍](video-continuation-plan.md)。
- 参考关系按实际意图选择 `fully_preserved`、`partially_preserved`、`attribute_transfer` 或 `weak_reference`。换衣或改变环境时可部分保留，而非固定锁死所有外观；角色无需每镜全部出现。
- Ref2VA 风格说明置于首镜前，正文建议 350–500 英文词。篇幅偏短只提醒，不为了凑词数添加剧情。原文对白和可见文字保留。

## 校验与失败处理

严格检查六段字段的顺序、完整性、非空值；镜头编号连续，首镜无时间戳，后续 `At MM:SS.mmm,` 严格递增且在请求时长内。保留小数时长与毫秒，不取整。

素材标签必须来自实际提供的图片、主体与视频槽位。未提供的引用及当前未接入的音频标签会被拒绝；对白块中的字面文字不会被误当作素材标签或切镜语法。视觉保留标记不能使用音频保留值。

模型输出不合规时自动纠正一次；仍错误则停止提交。英文直出跳过文本模型，自由正文自动补首镜与六段式，完整六段式通过校验后原样保留。直接调用生成接口也必须通过同样检查。旧 CLI 调用只有正文时，Provider 仍按 Ref2VA 组装六段式。

最后切点距结尾不足两秒、镜头过密、正文偏短或缺少风格开场属于质量提醒。编排提醒显示在视频输入框，并随画布与本次候选版本保存。

## 验证

```sh
python -m unittest dev.api.test_h3_prompt_policy dev.api.test_video_contract
python dev/api/_verify_segment_prompt.py
python dev/api/_verify_r2v_workflow.py
npm run test:browser --prefix web
```

默认不调用真实文本或视频模型。测试验证规则与请求链路，生成画面质量仍需使用真实模型评估。
