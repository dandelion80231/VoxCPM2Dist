import json, sys
from funasr import AutoModel

wav = sys.argv[1] if len(sys.argv) > 1 else "D:/AI/Build/pz/verify_lora_1200/bank.wav"
model_id = "iic/Speech_seaco_paraformer_large_asr_nat_zh-cn-16k-common-vocab8404-py - py"
# 上面的 model_id 含空格是笔误保护；实际用下面这行
model_id = "iic/Speech_seaco_paraformer_large_asr_nat_zh-cn-16k-common-vocab8404-py"
print("loading", model_id, flush=True)
model = AutoModel(model=model_id, device="cuda:0", disable_update=True)
print("model loaded", flush=True)
res = model.generate(input=wav, sentence_timestamp=True)
print("RAW:", json.dumps(res, ensure_ascii=False))
