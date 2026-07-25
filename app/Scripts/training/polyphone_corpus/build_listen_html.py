# -*- coding: utf-8 -*-
"""生成 listen_check_v20.html: 20 句(均来自 v20 520 训练集, 多为 minority 读法)
4 列对比: base(无LoRA) / lora_0400 / lora_0800 / lora_1200. 音频路径走 pz junction 防中文乱码.
"""
import json, html

SENT = r"D:/AI/Build/多音字/listen_sentences.json"
OUT  = r"D:/AI/Build/多音字/listen_check_v20.html"
AUDIO_ROOT = ""   # 相对路径: wav 已复制到 HTML 同目录的 base/0400/0800/1200/ 下

# key -> (考察字, 正确读法(红), 常见误读, 说明)
ANN = {
 "d01_ding":  ("丁",  "zhēng", "dīng", "伐木丁丁"),
 "d02_mo":    ("万",  "mò",    "wàn",  "万俟(复姓)"),
 "d03_sang":  ("丧",  "sàng",  "sāng", "丧失(丢掉)"),
 "d04_chu":   ("处",  "chǔ",   "chù",  "相处/处理"),
 "d05_li":    ("丽",  "lí",    "lì",   "丽水(地名)"),
 "d06_yue":   ("乐",  "yuè",   "lè",   "乐器/音乐"),
 "d07_sheng": ("乘",  "shèng", "chéng","千乘(兵车)"),
 "d08_yu":    ("予",  "yú",    "yǔ",   "予(我)"),
 "d09_qing":  ("亲",  "qìng",  "qīn",  "亲家"),
 "d10_qiu":   ("仇",  "qiú",   "chóu", "仇(姓)大夫"),
 "d11_zong":  ("从",  "zòng",  "cóng", "合从(通纵)"),
 "d12_ling":  ("令",  "líng",  "lìng", "令狐(复姓)"),
 "d13_ang":   ("仰",  "áng",   "yǎng", "仰首"),
 "d14_jie":   ("价",  "jiè",   "jià",  "价人(善人)"),
 "d15_ren":   ("任",  "rén",   "rèn",  "任(姓)先生"),
 "d16_kuai":  ("会",  "kuài",  "huì",  "会计"),
 "d17_zhuan": ("传",  "zhuàn", "chuán","传记"),
 "d18_ba":    ("伯",  "bà",    "bó",   "五伯(霸)"),
 "d19_he":    ("何",  "hè",    "hé",   "何(扛)重担"),
 "d20_jia":   ("假",  "jià",   "jiǎ",  "暑假"),
}

sents = json.load(open(SENT, encoding="utf-8"))

blocks = []
for i, (key, sent) in enumerate(sents, 1):
    char, rd, wrong, note = ANN[key]
    audio = f"{AUDIO_ROOT}{{ver}}/{key}.wav"
    block = f'''<div class="block">
  <p class="sent"><span class="idx">{i:02d}</span>{html.escape(sent)}</p>
  <p class="py">考察「{char}」：<span class="red">{rd}</span>（非常见读法；常误读为 <b>{wrong}</b>）—— {note}</p>
  <div class="row">
    <div class="cell"><b>base 无LoRA</b><audio controls src="{audio.format(ver='base')}"></audio></div>
    <div class="cell"><b>lora_0400</b><audio controls src="{audio.format(ver='0400')}"></audio></div>
    <div class="cell"><b>lora_0800</b><audio controls src="{audio.format(ver='0800')}"></audio></div>
    <div class="cell"><b>lora_1200</b><audio controls src="{audio.format(ver='1200')}"></audio></div>
  </div>
</div>'''
    blocks.append(block)

html_doc = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>多音字 LoRA 听测(520训练集·难句·4检查点)</title>
<style>
body{{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:1000px;margin:20px auto;padding:0 16px;color:#222}}
h1{{font-size:20px;margin-bottom:6px}}
.note{{background:#fff8e1;border-left:4px solid #ffc107;padding:10px 14px;font-size:13px;line-height:1.6;border-radius:4px}}
.block{{border:1px solid #e0e0e0;border-radius:8px;padding:12px 14px;margin:12px 0;background:#fafafa}}
.sent{{font-size:17px;margin:0 0 4px}}
.py{{color:#666;font-size:13px;margin:0 0 10px}}
.red{{color:#d32f2f;font-weight:700}}
.row{{display:flex;flex-wrap:wrap;gap:10px}}
.cell{{flex:1;min-width:200px}}
.cell b{{font-size:12px;color:#555;display:block;margin-bottom:3px}}
audio{{width:100%;height:36px}}
.idx{{color:#1565c0;font-weight:700;margin-right:6px}}
</style>
</head>
<body>
<h1>多音字 LoRA 听测 · 520 训练集难句 · 4 检查点对照</h1>
<div class="note">
这 20 句全部取自最终 <b>v20 的 520 句训练集</b>，且都是<b> minority / 非常见读法</b>（base 模型大概率读错）。
逐句对比 <span class="red">base（无 LoRA）</span> 与 <b>lora_0400 / 0800 / 1200</b>：
<ul>
  <li>若 <b>base 错、1200 对</b> → 该 LoRA <b>确实学到了</b>这个读法（且看 400→800→1200 是否渐进修正）。</li>
  <li>若 <b>四个版本全错</b> → 该读法在 520 里也几乎没有样本（印证"数据缺口"假设），正是后续要补的。</li>
  <li>若 <b>base 就对</b> → 该读法模型本来就会，LoRA 无可见增量（属正常）。</li>
</ul>
听红色字即可。音频生成中（4 个检查点各 20 句），刷新页面即可听到已生成的部分。
</div>

{chr(10).join(blocks)}

</body>
</html>'''

with open(OUT, "w", encoding="utf-8") as f:
    f.write(html_doc)
print(f"[ok] 写出 {OUT} ({len(blocks)} 块)")
